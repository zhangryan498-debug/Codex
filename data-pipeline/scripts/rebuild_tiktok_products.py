#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从落盘的原始响应重建 TikTok 商品数据集（**不消耗额度**）。

为什么需要：TikHub 按次计费，额度紧张时改抽取口径不应该重新采一次。
采集器会把每次调用的原始响应存到 `data/tiktok/_raw/`，本脚本读原始体、
用当前抽取器重算数据集 —— 零 API 调用。

用法：
    python3 scripts/rebuild_tiktok_products.py
    python3 scripts/rebuild_tiktok_products.py --dry-run
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "collectors"))

from base import write_records  # noqa: E402
import tiktok_tikhub as tt  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    raw_dir = HERE / "data" / "tiktok" / "_raw"
    # 注意：不要写 `fetch_*_products_list_*.json` + `fetch_hot_selling_products_list_*.json`
    # 两个模式 —— 前者已覆盖后者，同一份文件会被匹配两次，行数直接翻倍。
    # （2026-11-05 实测：6 份原始响应被当成 11 份，每站重建出 40 条而实际只有 20 个商品。）
    files = sorted(glob.glob(str(raw_dir / "fetch_*_products_list_*.json")))
    files = list(dict.fromkeys(files))  # 去重保序
    if not files:
        print("❌ 没有找到原始响应，请先跑一次采集器")
        return 1
    print(f"读到 {len(files)} 份原始响应（零 API 调用）")

    by_ds = {}
    for f in files:
        payload = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
        params = payload.get("params") or {}
        region = params.get("region") or "?"
        router = (payload.get("router") or "")
        if "hot_selling" in router:
            items = tt._items(payload)
            ds = f"product_{region}"
            recs = [tt.norm_product(p, region, f"{tt.BASE}/fetch_hot_selling_products_list"
                                    f"?region={region}") for p in items]
        elif "search_products" in router:
            items = tt._items(payload)
            ds = f"product_{region}"
            recs = [tt.norm_product(p, region, f"{tt.BASE}/fetch_search_products_list"
                                    f"?search_word={params.get('search_word')}&region={region}")
                    for p in items]
        else:
            continue
        by_ds.setdefault(ds, []).extend(recs)

    total = 0
    dropped_total = 0
    for ds, recs in sorted(by_ds.items()):
        # 空壳行守卫：metrics 全空的记录不可用于任何统计，直接剔除并报数。
        # 历史教训：2026-11-05 快照的 247 行里有 100 行是这样的空壳行（早期
        # norm_product 按 price/rating_score 取值、真实字段是
        # product_price_info.sale_price_decimal / rate_info.score）。
        kept, dropped = [], []
        seen = set()
        for r in recs:
            m = r.get("metrics") or {}
            if m.get("price") is None:
                dropped.append(r)
                continue
            eid = r.get("entity_id")
            if eid in seen:
                continue  # 同一商品被多份原始响应重复覆盖，只留一份
            seen.add(eid)
            kept.append(r)
        if dropped:
            print(f"  ⚠️ {ds}: 剔除 {len(dropped)} 条空壳行（metrics 无价格字段）")
        raw_len = len(recs)
        if len(kept) < raw_len - len(dropped):
            print(f"  ⚠️ {ds}: 去重 {raw_len - len(dropped) - len(kept)} 条重复商品")
        priced = sum(1 for r in kept if r["metrics"].get("price"))
        sold = sum(1 for r in kept if r["metrics"].get("sold_count"))
        print(f"  {ds}: {len(kept)} 条可用（有价 {priced} / 有销量 {sold}）")
        if args.dry_run:
            total += len(kept)
            dropped_total += len(dropped)
            continue
        paths = write_records(kept, "tiktok", ds)
        total += len(kept)
        dropped_total += len(dropped)
        print(f"    → 落盘 {paths['jsonl']}")
    print(f"\n共重建 {total} 条可用记录（剔除空壳 {dropped_total} 条）")
    if dropped_total:
        print("提示：空壳行说明原始响应里存在 metrics 为空的样本，"
              "可用 scripts/tiktok_data_quality.py 复算口径")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
