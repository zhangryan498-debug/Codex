#!/usr/bin/env python3
"""从已落盘的**原始响应**重建归一化快照 —— 零 API 花费。

为什么需要这个
--------------
TikHub 原始响应缓存 24h 内可复用；即使过期，本地 `_raw/<date>/*.json` 也一直在。
改口径（时长单位、region 语义、entity_type、字段映射）时**不该重新花钱采样**，
而应从原始体重算。

用法
----
    python3 -u scripts/rebuild_from_raw.py --date 2026-11-06
    python3 -u scripts/rebuild_from_raw.py --date 2026-11-06 --dry-run

行为
----
1. 读 `_raw/<date>/*.json`，按文件名还原数据集与请求参数（keyword 等）。
2. 用 `tikhub_recharge_sampling` 里**同一个**归一化器重算，避免两套口径。
3. 写 `snapshots/<date>-douyin-search/` 与 `snapshots/<date>-tikhub-content/`，
   **按 entity_id 去重合并**（同实体多关键词命中时保留字段更全的一条，
   并合并 provenance.keyword 列表）。
4. 打印统计 + 自检断言；任一断言不成立即非 0 退出。

纪律
----
* 本脚本**不发任何网络请求**。
* 不覆盖 2026-11-05 及更早的冻结快照（只处理 `--date` 指定的那一天）。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = pathlib.Path(__file__).resolve().parent
PIPE = HERE.parent
sys.path.insert(0, str(HERE))

from tikhub_recharge_sampling import (  # noqa: E402
    ENDPOINTS, PLATFORM_REGION, _extract_next_cursor, normalize,
)

RAW_ROOT = PIPE / "data" / "_raw"
SNAP_ROOT = PIPE / "snapshots"

# 数据集 → 平台。抖音侧数据集是 P5/P6/P7，TikTok 侧是 P1。
DOUYIN_PREFIXES = ("P5_", "P6_", "P7_")

# 归一化器 → entity_type（与采样脚本同一张表，避免两处各写一份）
ENTITY_TYPE = {
    "aweme": "aweme", "challenge": "challenge", "creator": "creator",
    "creator_info": "creator", "kol_price": "kol_price",
    "trend": "trend", "trend_word": "trend_word", "live": "live",
}


def dataset_of(fname: str) -> Optional[str]:
    """从落盘文件名还原数据集名：`P5_video_search_ID.json` → `P5_video_search`。

    ⚠️ **必须长度优先**：同名不同站的包（`P1_live_search` 与 `P1_live_search_US`）
    前缀包含关系，按字典序取首个匹配会把 US 包误判成印尼包。
    → 收集所有匹配，取**最长**的那个。
    """
    stem = re.sub(r"_\d+\.json$", "", fname)
    best = None
    for ds in ENDPOINTS:
        if stem == ds or stem.startswith(ds + "_"):
            if best is None or len(ds) > len(best):
                best = ds
    if best:
        return best
    # 兜底：无下划线分隔的前缀匹配，同样取最长
    for ds in ENDPOINTS:
        if stem.startswith(ds) and (best is None or len(ds) > len(best)):
            best = ds
    return best


def platform_of(ds: str) -> str:
    return "tiktok" if ds.startswith("P1_") else "douyin"


def merge_record(old: Optional[dict], new: dict) -> dict:
    """同实体合并：保留字段更全的一条，keyword 归集合并。"""
    if old is None:
        return new
    def fullness(rec: dict) -> int:
        m = rec.get("metrics") or {}
        e = rec.get("extra") or {}
        return (sum(1 for v in m.values() if v is not None)
                + sum(1 for v in e.values() if v is not None))
    base, other = (old, new) if fullness(old) >= fullness(new) else (new, old)
    # 标题择优：非空优先
    if not base.get("title") and other.get("title"):
        base = dict(base)
        base["title"] = other["title"]
    # metrics：逐字段取非 None
    bm = dict(base.get("metrics") or {})
    om = other.get("metrics") or {}
    for k, v in om.items():
        if bm.get(k) is None and v is not None:
            bm[k] = v
    base = dict(base)
    base["metrics"] = bm
    # provenance.keyword：并集（注意已有值可能是逗号串，先拆再并，否则重复项会累积）
    pk = set()
    for rec in (old, new):
        k = (rec.get("provenance") or {}).get("keyword")
        if k:
            pk.update(x.strip() for x in str(k).split(",") if x.strip())
    if pk:
        base = dict(base)
        base["provenance"] = dict(base.get("provenance") or {})
        base["provenance"]["keyword"] = ",".join(sorted(pk))
    # region 始终保留（合并时两源应一致，不一致说明归一口径有问题）
    reg = (old.get("provenance") or {}).get("region") or \
        (new.get("provenance") or {}).get("region")
    if reg:
        base = dict(base)
        base["provenance"] = dict(base.get("provenance") or {})
        base["provenance"]["region"] = reg
    return base


def rebuild(date: str, dry_run: bool = False) -> int:
    raw_dir = RAW_ROOT / date
    if not raw_dir.is_dir():
        print(f"❌ 原始响应目录不存在：{raw_dir}")
        return 1

    files = sorted(raw_dir.glob("*.json"))
    if not files:
        print(f"❌ {raw_dir} 下没有原始响应")
        return 1

    # 找出每个数据集对应的端点契约与归一化器
    buckets: Dict[Tuple[str, str], Dict[str, dict]] = {}
    # (dataset, region) → {entity_id: record}
    skipped = 0
    for fp in files:
        ds = dataset_of(fp.name)
        if not ds or ds not in ENDPOINTS:
            skipped += 1
            continue
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        spec = ENDPOINTS[ds]
        platform = platform_of(ds)
        # region 口径：抖音恒 CN；TikTok 取该端点实际使用的站位
        # （region_fixed 优先 —— 如 hashtag 端点只支持 US/GB/FR/JP/VN/SG，
        #   且 hashtag_search 本身不接受 region 参数、返回默认 US 站话题）。
        if platform == "douyin":
            region = PLATFORM_REGION["douyin"]
        else:
            region = spec.get("region_fixed") or "ID"
        recs = normalize(payload, spec["norm"], region)
        if not recs:
            continue
        # 溯源还原：raw 响应**自带 params**（含 keyword），
        # 不依赖落盘文件名里的 hash —— 否则重建后检索词归因会全丢。
        kw = (payload.get("params") or {}).get("keyword")
        if kw:
            for rec in recs:
                rec.setdefault("provenance", {})["keyword"] = kw
        # region 写入 provenance（归一化器只把 region 放进 extra.extra.region，
        # 但 provenance 也要有同一口径，跨报告取数时只读 provenance.region）
        for rec in recs:
            rec.setdefault("provenance", {})["region"] = region
        key = (ds, region)
        bucket = buckets.setdefault(key, {})
        for rec in recs:
            eid = rec["entity_id"]
            bucket[eid] = merge_record(bucket.get(eid), rec)

    if dry_run:
        total = sum(len(b) for b in buckets.values())
        print(f"（dry-run）原始文件 {len(files)} 个｜可识别 {len(files)-skipped} 个"
              f"｜去重后记录 {total} 条")
        for (ds, region), b in sorted(buckets.items()):
            print(f"  {ds} [{region}] → {len(b)} 条")
        return 0

    # 写盘
    tiktok_out = SNAP_ROOT / f"{date}-tikhub-content"
    douyin_out = SNAP_ROOT / f"{date}-douyin-search"
    for d in (tiktok_out, douyin_out):
        d.mkdir(parents=True, exist_ok=True)
        # 清掉该目录下本脚本管理的 jsonl（重建 = 覆盖写）
        for f in d.glob("*.jsonl"):
            f.unlink()

    stats: Dict[str, int] = {}
    for (ds, region), bucket in sorted(buckets.items()):
        platform = platform_of(ds)
        out_dir = tiktok_out if platform == "tiktok" else douyin_out
        fp = (out_dir / f"{ds}_{region}.jsonl" if platform == "tiktok" and region
              else out_dir / f"{ds}.jsonl")
        spec = ENDPOINTS[ds]
        etype = ENTITY_TYPE.get(spec["norm"], "unknown")
        with fp.open("w", encoding="utf-8") as fh:
            for rec in bucket.values():
                fh.write(json.dumps({
                    "schema_version": "1.0", "platform": platform, "dataset": ds,
                    "entity_type": etype,
                    "entity_id": rec["entity_id"], "title": rec["title"],
                    "metrics": rec["metrics"], "extra": rec["extra"],
                    "provenance": rec.get("provenance") or {},
                }, ensure_ascii=False) + "\n")
        stats[f"{ds}[{region}]"] = len(bucket)

    print(f"✅ 从原始响应重建完成（零 API 花费）")
    print(f"   原始文件 {len(files)} 个｜无法识别 {skipped} 个")
    for k, v in sorted(stats.items()):
        print(f"   {k:36s} {v:>5} 条")

    # ---- 自检断言 ----
    errs: List[str] = []
    for (ds, region), bucket in buckets.items():
        platform = platform_of(ds)
        # ① region 语义：抖音恒 CN 且文件名不带后缀；TikTok 必须带 region
        if platform == "douyin" and region != "CN":
            errs.append(f"{ds}: 抖音 region 应为 CN，实为 {region}")
        if platform == "tiktok" and not region:
            errs.append(f"{ds}: TikTok 样本缺 region")
        # ② entity_type 正确
        spec = ENDPOINTS[ds]
        etype = ENTITY_TYPE.get(spec["norm"], "unknown")
        if spec["norm"] == "aweme" and etype != "aweme":
            errs.append(f"{ds}: 作品数据集 entity_type 应为 aweme，实为 {etype}")
        # ③ 时长合理性（app 形态毫秒：>10000ms 即 >10 秒）
        if spec["norm"] == "aweme":
            for rec in bucket.values():
                d = (rec.get("metrics") or {}).get("duration_ms")
                if d is not None and d > 600000:
                    errs.append(f"{ds}: 时长 {d}ms 超过 10 分钟上限，疑似单位错误")
                    break
    if errs:
        print("\n❌ 自检失败：")
        for e in errs:
            print("   -", e)
        return 1
    print("\n✅ 自检通过（region 语义 / entity_type / 时长量级）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="从原始响应零成本重建归一化快照")
    ap.add_argument("--date", required=True, help="原始响应日期目录，如 2026-11-06")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return rebuild(args.date, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
