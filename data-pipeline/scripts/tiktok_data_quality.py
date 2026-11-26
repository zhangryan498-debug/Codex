#!/usr/bin/env python3
"""TikTok 冻结快照数据质量审计。

2026-11-05 快照共 247 行，但其中 100 行的 metrics 为空 `{}` —— 这是早期
`norm_product` 归一化 bug（按 price/rating_score 取值，真实字段是
product_price_info.sale_price_decimal / rate_info.score）留下的空壳行。
归一化 bug 已修（见 collectors/tiktok_tikhub.py:195-235），空壳行是历史遗留，
补不回来（原始响应已被同 params 的后续调用覆盖，重采需消耗 TikHub 额度）。

因此对外口径必须区分：
  · 总行数 247（含 100 空壳）
  · **可用样本 147**（商品热卖 100 + MY 搜索 30 + 广告素材 17）

本脚本对冻结快照做审计并输出机读结果，**零 API 调用**。
下游任何统计脚本都必须先过滤空壳行，并对可用样本数做断言。

用法：
    python3 scripts/tiktok_data_quality.py
    python3 scripts/tiktok_data_quality.py --dir snapshots/2026-11-05-tiktok --json out.json
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # 项目根

CURRENCY = {"ID": "IDR", "TH": "THB", "VN": "VND", "MY": "MYR", "PH": "PHP"}


def load_dataset(path: pathlib.Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def is_usable(row: dict) -> bool:
    """可用判定：广告素材无价格字段，按自身口径判定；商品必须能抽出价格。"""
    if row.get("entity_type") == "ad":
        return row.get("metrics", {}).get("ctr") is not None
    return row.get("metrics", {}).get("price") is not None


def audit_product(rows: list[dict]) -> dict:
    good = [r for r in rows if is_usable(r)]
    bad = [r for r in rows if not is_usable(r)]
    prices = [r["metrics"]["price"] for r in good if r["metrics"].get("price") is not None]
    sold = [r["metrics"]["sold_count"] for r in good if r["metrics"].get("sold_count") is not None]
    rating = [r["metrics"]["rating"] for r in good if r["metrics"].get("rating") is not None]
    # 按抓取批次切分：同批次内 product_id 是否重复（跨批次是否重复）
    by_fetch = collections.defaultdict(list)
    for r in rows:
        by_fetch[r["provenance"].get("fetched_at")].append(r)
    batches = []
    for ts, rs in sorted(by_fetch.items(), key=lambda kv: str(kv[0])):
        ids = [r["entity_id"] for r in rs]
        batches.append({
            "fetched_at": ts,
            "rows": len(rs),
            "usable": sum(1 for r in rs if is_usable(r)),
            "unique_entity_id": len(set(ids)),
            "source_url_tail": (rs[0]["provenance"].get("source_url") or "")[-60:],
        })
    id_sets = [set(r["entity_id"] for r in rs) for rs in by_fetch.values()]
    cross_overlap = 0
    for i in range(len(id_sets)):
        for j in range(i + 1, len(id_sets)):
            cross_overlap += len(id_sets[i] & id_sets[j])
    return {
        "rows": len(rows),
        "usable": len(good),
        "empty_shell": len(bad),
        "unique_entity_id": len(set(r["entity_id"] for r in rows)),
        "duplicate_rows_within_dataset": len(rows) - len(set(r["entity_id"] for r in rows)),
        "cross_batch_entity_id_overlap": cross_overlap,
        "batches": batches,
        "price": {
            "min": min(prices) if prices else None,
            "median": statistics.median(prices) if prices else None,
            "max": max(prices) if prices else None,
        },
        "sold_count": {
            "min": min(sold) if sold else None,
            "median": statistics.median(sold) if sold else None,
            "max": max(sold) if sold else None,
            "sum": sum(sold) if sold else None,
            "explicit_zero": sum(1 for r in good if r["metrics"].get("sold_count") == 0),
        },
        "rating": {
            "median": statistics.median(rating) if rating else None,
            "count": len(rating),
        },
    }


def audit_ads(rows: list[dict]) -> dict:
    good = [r for r in rows if is_usable(r)]
    ctr = [r["metrics"]["ctr"] for r in good if r["metrics"].get("ctr") is not None]
    like = [r["metrics"]["like"] for r in good if r["metrics"].get("like") is not None]
    dur = [r["metrics"]["duration_ms"] for r in good if r["metrics"].get("duration_ms") is not None]
    cost = [r["metrics"]["cost"] for r in good if r["metrics"].get("cost") is not None]
    return {
        "rows": len(rows),
        "usable": len(good),
        "empty_shell": len(rows) - len(good),
        "ctr": {"min": min(ctr) if ctr else None, "median": statistics.median(ctr) if ctr else None,
                "max": max(ctr) if ctr else None},
        "like": {"median": statistics.median(like) if like else None, "max": max(like) if like else None},
        "duration_ms": {"median": statistics.median(dur) if dur else None,
                        "max": max(dur) if dur else None},
        "cost": {"min": min(cost) if cost else None, "max": max(cost) if cost else None},
        "countries": sorted({r["extra"].get("country_code") for r in rows}),
        "keywords": sorted({r["extra"].get("keyword") for r in rows if r["extra"].get("keyword")}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="snapshots/2026-11-05-tiktok",
                    help="冻结快照目录（相对 data-pipeline/）")
    ap.add_argument("--json", help="机读结果输出路径")
    args = ap.parse_args()

    snap = (ROOT / "data-pipeline" / args.dir)
    if not snap.is_dir():
        snap = pathlib.Path(args.dir)
    if not snap.is_dir():
        print(f"❌ 快照目录不存在: {args.dir}", file=sys.stderr)
        return 2

    result: dict = {"snapshot_dir": str(snap), "datasets": {}, "totals": {}}
    for sub in sorted(snap.iterdir()):
        if not sub.is_dir():
            continue
        jf = sub / f"{sub.name}.jsonl"
        if not jf.exists():
            continue
        rows = load_dataset(jf)
        key = sub.name
        if key.startswith("ads_material"):
            result["datasets"][key] = audit_ads(rows)
        elif key.startswith("product"):
            result["datasets"][key] = audit_product(rows)
        else:
            result["datasets"][key] = {"rows": len(rows), "usable": len(rows), "empty_shell": 0}

    total_rows = sum(d["rows"] for d in result["datasets"].values())
    total_usable = sum(d["usable"] for d in result["datasets"].values())
    total_shell = sum(d["empty_shell"] for d in result["datasets"].values())
    result["totals"] = {
        "rows": total_rows,
        "usable": total_usable,
        "empty_shell": total_shell,
        "hot_selling_usable": sum(
            d["usable"] for k, d in result["datasets"].items()
            if k.startswith("product_") and not d.get("batches")),
        "note": ("总行数含早期归一化 bug 遗留的空壳行；对外口径一律用 usable。"
                 "空壳行不可用于任何统计。"),
    }

    print(f"快照目录: {snap}")
    print(f"{'dataset':22}{'总行':>6}{'可用':>6}{'空壳':>6}  唯一ID  跨批次重叠")
    for k, d in result["datasets"].items():
        print(f"{k:22}{d['rows']:6}{d['usable']:6}{d['empty_shell']:6}  "
              f"{d.get('unique_entity_id', '-'):>5}  {d.get('cross_batch_entity_id_overlap', '-'):>6}")
    t = result["totals"]
    print(f"\n合计: 总行 {t['rows']} | 可用 {t['usable']} | 空壳 {t['empty_shell']}")
    print("对外口径: 可用样本 =", t["usable"])

    # 断言：空壳行必须被识别出来，且可用样本数与实测一致
    assert t["empty_shell"] == 100, f"空壳行数异常: {t['empty_shell']} != 100"
    assert t["usable"] == 147, f"可用样本数异常: {t['usable']} != 147"
    assert t["rows"] == 247, f"总行数异常: {t['rows']} != 247"
    for k, d in result["datasets"].items():
        if k.startswith("product_"):
            assert d["duplicate_rows_within_dataset"] == 0, f"{k} 存在重复 entity_id"
    print("\n✅ 断言全部通过（总行 247 / 可用 147 / 空壳 100 / 无重复 ID）")

    if args.json:
        out = pathlib.Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"机读结果: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
