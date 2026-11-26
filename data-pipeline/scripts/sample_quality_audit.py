#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-06 充值采样快照的数据质量审计（零 API 调用）。

审计对象
--------
· `snapshots/2026-11-06-tikhub-content/` —— TikTok 内容侧新采样
  （作品搜索 / 话题搜索 / 作品详情 / 话题详情 / 话题作品 / 首页推荐流 / 趋势词 / 探索页）
· `snapshots/2026-11-06-douyin-search/` —— 抖音侧新采样
  （垂类作品检索 / 垂类话题检索 / 星图达人 / 星图达人详情 / 星图报价 / 多词热度）

为什么必须审计
--------------
TikHub 的响应**每个端点结构都不一样**，且同一端点下 `app` 与 `web` 两种形态字段名不同
（`aweme_id`/`id`、`statistics`/`stats`、`play_count`/`playCount`）。
归一化一旦漏字段，**下游报告会把「字段缺失」当成「数据为 0」**，属于最隐蔽的一类假绿。
本脚本逐数据集断言「哪些字段真的拿到了、拿到多少」，供下游报告引用为样本口径。

用法：
    python3 -u scripts/sample_quality_audit.py
    python3 -u scripts/sample_quality_audit.py --json snapshots/_sample_quality_2026-11-06.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

HERE = pathlib.Path(__file__).resolve().parent
PIPE = HERE.parent
DAY = "2026-11-06"
TIKTOK_DIR = PIPE / "snapshots" / f"{DAY}-tikhub-content"
DOUYIN_DIR = PIPE / "snapshots" / f"{DAY}-douyin-search"

# 各数据集的关键字段与硬检查
DATASET_SPEC = {
    # TikTok 内容侧
    "P1_video_search": {
        "platform": "tiktok", "kind": "aweme",
        "required": ["play_count", "digg_count", "comment_count", "collect_count"],
        "optional": ["share_count", "duration_ms", "create_time"],
    },
    "P1_hashtag_search": {
        "platform": "tiktok", "kind": "challenge",
        "required": [], "optional": ["play_count", "video_count"],
    },
    "P1_one_video": {
        "platform": "tiktok", "kind": "aweme",
        "required": ["play_count", "digg_count"], "optional": ["duration_ms"],
        # 按 aweme_id 回填详情，**天然无关键词参数** → 无检索词溯源（正常，非缺陷）
        "no_keyword_ok": True,
    },
    "P1_hashtag_detail": {
        "platform": "tiktok", "kind": "challenge",
        "required": [], "optional": ["play_count", "video_count"],
    },
    "P1_hashtag_video_list": {
        "platform": "tiktok", "kind": "aweme",
        "required": ["play_count"], "optional": ["digg_count"],
    },
    "P1_home_feed": {
        "platform": "tiktok", "kind": "aweme",
        "required": ["play_count", "digg_count"], "optional": ["duration_ms"],
        # 首页推荐流**不接受关键词参数** → 无检索词溯源（正常，非缺陷）
        "no_keyword_ok": True,
    },
    "P1_trending_words": {
        "platform": "tiktok", "kind": "trend_word",
        "required": [], "optional": ["heat"],
        # 该端点只返回词与类型，**不返回热度值** → heat 必然为空（正常，非缺陷）
        "heat_absent_ok": True,
        "no_keyword_ok": True,
    },
    "P1_explore_post": {
        "platform": "tiktok", "kind": "aweme",
        "required": ["play_count"], "optional": ["digg_count"],
    },
    # 直播侧（2026-11-06 新增，直播运营岗数据前置）
    # ⚠️ 必须用显式文件清单：**前缀 glob 会同时命中 ID 站与 US 站两个文件**
    #    （P1_live_search*.jsonl → P1_live_search_ID + P1_live_search_US_US），
    #    导致两站混算、印尼站行数虚高 58 行、字段填充率被稀释。
    "P1_live_search": {
        "platform": "tiktok", "kind": "live",
        # 直播搜索端点**不返回同时在看人数** → viewer_count 结构性缺失，
        # 可用量化指标是 products_count（挂车商品数）
        "required": ["products_count"], "optional": ["is_live_has_products"],
        "viewer_absent_ok": True,
        "files": ["P1_live_search_ID.jsonl"],
    },
    "P1_live_search_US": {
        "platform": "tiktok", "kind": "live",
        "required": ["products_count"], "optional": ["is_live_has_products"],
        "viewer_absent_ok": True,
        "files": ["P1_live_search_US_US.jsonl"],
    },
    # 抖音侧
    "P5_video_search": {
        "platform": "douyin", "kind": "aweme",
        "required": ["play_count", "digg_count"], "optional": ["duration_ms", "comment_count"],
    },
    "P5_challenge_search": {
        "platform": "douyin", "kind": "challenge",
        "required": [], "optional": ["play_count", "video_count"],
    },
    "P6_search_creator": {
        "platform": "douyin", "kind": "creator",
        "required": ["follower_count"], "optional": ["price_20_60", "cpm_suggest_price"],
    },
    "P6_author_base_info": {
        "platform": "douyin", "kind": "creator_info",
        "required": [], "optional": [],
    },
    "P6_kol_price": {
        "platform": "douyin", "kind": "kol_price",
        "required": ["price_min", "price_max"], "optional": [],
    },
    "P7_keyword_trend": {
        "platform": "douyin", "kind": "trend",
        "required": ["points"], "optional": ["index_max", "index_mean"],
    },
}

checks: list[dict] = []


def chk(code: str, name: str, ok: bool, detail: str) -> bool:
    checks.append({"编号": code, "断言": name, "结果": "通过" if ok else "不通过",
                   "依据": detail})
    return ok


def load(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def audit() -> dict:
    report: dict = {"day": DAY, "datasets": {}, "checks": checks}
    total_rows = 0
    for ds, spec in DATASET_SPEC.items():
        base = TIKTOK_DIR if spec["platform"] == "tiktok" else DOUYIN_DIR
        # 显式文件清单优先（避免前缀 glob 跨站命中）；无清单才用 glob
        if spec.get("files"):
            files = [base / fn for fn in spec["files"] if (base / fn).exists()]
        else:
            files = sorted(base.glob(f"{ds}*.jsonl"))
        rows = []
        for f in files:
            rows += load(f)
        total_rows += len(rows)
        ids = [r.get("entity_id") for r in rows]
        uniq = len(set(ids))
        field_hits = {}
        for f in spec["required"] + spec["optional"]:
            n = sum(1 for r in rows
                    if r.get("metrics", {}).get(f) is not None)
            field_hits[f] = n
        required_missing = [f for f in spec["required"] if field_hits.get(f, 0) == 0]
        regions = Counter((r.get("extra") or {}).get("region") for r in rows)
        prov_regions = Counter((r.get("provenance") or {}).get("region") for r in rows)
        etypes = Counter(r.get("entity_type") for r in rows)
        kws = set()
        for r in rows:
            k = (r.get("provenance") or {}).get("keyword")
            if k:
                kws.update(x.strip() for x in str(k).split(",") if x.strip())
        report["datasets"][ds] = {
            "platform": spec["platform"], "rows": len(rows), "unique_ids": uniq,
            "files": [str(f.relative_to(PIPE)) for f in files],
            "field_present": field_hits,
            "required": spec["required"], "optional": spec["optional"],
            "required_missing": required_missing,
            "regions": dict(regions),
            "provenance_regions": dict(prov_regions),
            "entity_types": dict(etypes),
            "keywords": sorted(kws),
            "zero_metric_rows": sum(
                1 for r in rows
                if not any(v is not None for v in (r.get("metrics") or {}).values())),
        }
        if rows:
            chk(f"Q-{ds}-rows", f"{ds} 采样行数 > 0", len(rows) > 0, f"{len(rows)} 行")
            chk(f"Q-{ds}-dup", f"{ds} entity_id 无重复",
                uniq == len(rows), f"唯一 {uniq} / 总 {len(rows)}")
            chk(f"Q-{ds}-req", f"{ds} 必填字段全部拿到",
                not required_missing,
                "缺失：" + ("、".join(required_missing) if required_missing else "无"))
            zero = report["datasets"][ds]["zero_metric_rows"]
            if spec.get("heat_absent_ok"):
                # 该端点结构上就不返回热度值 → 指标为空属正常，
                # 「非空壳」按「词条本身有效」判定，不能按有指标判定
                chk(f"Q-{ds}-nonzero", f"{ds} 存在有效词条（该端点不返回热度值）",
                    len(rows) > 0, f"{len(rows)} 条词，字段：heat 不由该端点提供")
            else:
                chk(f"Q-{ds}-nonzero", f"{ds} 存在有指标的行（不是全空壳）",
                    zero < len(rows), f"全空指标 {zero} 行 / 总 {len(rows)}")
            # ---- 2026-11-06 新增：归一化口径 4 项 ----
            # ① region 语义：抖音恒 CN 单站；TikTok 必须带分站 region
            want = "CN" if spec["platform"] == "douyin" else None
            if want:
                chk(f"Q-{ds}-region", f"{ds} region 语义正确（抖音=CN 单站）",
                    set(regions) == {want}, f"实测 {dict(regions)}")
            else:
                chk(f"Q-{ds}-region", f"{ds} TikTok 样本带分站 region",
                    bool(prov_regions) and None not in prov_regions,
                    f"provenance.region={dict(prov_regions)}")
            # ② entity_type 与数据集类型一致（此前靠 dataset 名含 'aweme' 猜，会错标）
            want_et = {"aweme": "aweme", "challenge": "challenge",
                        "creator": "creator", "creator_info": "creator",
                        "kol_price": "kol_price", "trend": "trend",
                        "trend_word": "trend_word", "live": "live"}.get(spec["kind"])
            if want_et:
                chk(f"Q-{ds}-etype", f"{ds} entity_type 正确（={want_et}）",
                    set(etypes) == {want_et}, f"实测 {dict(etypes)}")
            # ③ 时长量级：毫秒口径下短视频不应超过 10 分钟
            durs = [r["metrics"].get("duration_ms") for r in rows
                    if (r.get("metrics") or {}).get("duration_ms")]
            over = [d for d in durs if d > 600000]
            if spec["kind"] == "live":
                # 直播条目无 duration 字段 → 时长断言不适用，
                # 改查「挂车商品数是否在合理量级」
                pcs = [r["metrics"].get("products_count") for r in rows
                       if (r.get("metrics") or {}).get("products_count")]
                chk(f"Q-{ds}-dur", f"{ds} 挂车商品数量级合理（≤500）",
                    all(p <= 500 for p in pcs),
                    f"有效 {len(pcs)} 条")
            else:
                chk(f"Q-{ds}-dur", f"{ds} 时长量级合理（≤10 分钟，单位=毫秒）",
                    not over,
                    f"有效 {len(durs)} 条，超限 {len(over)} 条"
                    + (f"，例 {over[0]}" if over else ""))
            # ④ 检索词溯源：多关键词采样的数据集必须能归因到词
            #    （该端点本身不接受关键词参数时属正常，用 no_keyword_ok 豁免）
            if spec["kind"] in ("aweme", "challenge"):
                if spec.get("no_keyword_ok"):
                    chk(f"Q-{ds}-kw", f"{ds} 检索词溯源豁免（端点不接受关键词参数）",
                        True, "该端点无 keyword 参数，属正常")
                else:
                    chk(f"Q-{ds}-kw", f"{ds} 记录带检索词溯源（provenance.keyword）",
                        len(kws) > 0,
                        f"{len(kws)} 个关键词：{'、'.join(sorted(kws)[:6])}")
            # ⑤ 端点不返回热度值时，nonzero 检查按「词本身有效」判定
        else:
            chk(f"Q-{ds}-rows", f"{ds} 采样行数 > 0", False, "该数据集未采到数据")

    report["total_rows"] = total_rows
    chk("Q-total", "本次采样总行数 > 100", total_rows > 100, f"共 {total_rows} 行")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    print(f"=== 2026-11-06 采样快照质量审计（零 API）===")
    rep = audit()
    for ds, d in rep["datasets"].items():
        req = "、".join(d["required"]) or "—"
        miss = ("❌ 缺 " + "、".join(d["required_missing"])) if d["required_missing"] else "✅"
        print(f"  {ds:24s} {d['rows']:>5} 行  唯一 {d['unique_ids']:>5}  "
              f"必填[{req}] {miss}  分站 {d['regions']}")
    print(f"\n总行数：{rep['total_rows']}")
    failed = [c for c in checks if c["结果"] != "通过"]
    for c in failed:
        print(f"  ❌ {c['编号']}：{c['断言']}｜{c['依据']}")
    print(f"断言 {len(checks)} 项，通过 {len(checks)-len(failed)} 项，失败 {len(failed)} 项")
    if args.json:
        out = pathlib.Path(args.json)
        if not out.is_absolute():
            out = PIPE / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
        print(f"已写出：{out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
