#!/usr/bin/env python3
"""TikHub 充值准数测算。

## 已实测的计费事实（2026-11-05）

1. **`calculate_price` 本身不计费** —— OpenAPI 原文：「本接口不计费。响应 JSON 中的
   message/message_zh 是全局默认文案（「本次请求将被计费」），对本接口不适用，请忽略」。
   实测连打 3 次后 `balance`/`free_credit` 纹丝不动；当日用量核对显示
   `total_request_per_day=388` 而 `free_credit_usage=$0.018`（= 18 次 $0.001 数据调用），
   差额全部是免费的 calculate_price / get_user_info / get_user_daily_usage。
   → **整表扫描端点计费属性是零成本的**，可以放心全量摸清再决定充多少。

2. **`request_per_day` 是计费阶梯参数，不是配额上限。** OpenAPI 里 default=1、example=100000。
   传入不同值会改变 total_price，用来估算日调用量对应的实际花费。

3. **阶梯折扣（仅对 `allow_discount=true` 的端点生效）**，实测 `tiktok/web/fetch_post_detail`
   （基准价 $0.001）：

   | request_per_day | total_price | 单次均价 | 占基准价 |
   |---|---|---|---|
   | 1,000 | $1.00 | $0.00100 | 100% |
   | 5,000 | $4.60 | $0.00092 | 92% |
   | 10,000 | $8.60 | $0.00086 | 86% |
   | 20,000 | $15.60 | $0.00078 | 78% |
   | 30,000 | $21.60 | $0.00072 | 72% |
   | 50,000 | $31.60 | $0.00063 | 63.2% |

   阶梯区间：0–1000 无折扣，1000–5000 折 10%，5000–10000 折 20%，
   10000–20000 折 30%，20000–30000 折 40%，30000+ 折 50%。
   → **同样量集中一天跑完比摊到一个月省 37%**。

4. **限流 1 req/s/路由**，超限返回 429（Retry-After: 1）。单端点日调用量越大，跑得越久。

5. **免费额度**：`free_credit` 初始约 $0.020（2026-11-05 已用 $0.018，剩 $0.002）。
   `allow_free_credit=true` 的端点吃这份额度；`false` 的直接扣余额。

## 用法

    python3 scripts/tikhub_budget_plan.py
    python3 scripts/tikhub_budget_plan.py --plan plan.json --json out.json
"""
from __future__ import annotations

import argparse
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
PIPE = HERE.parent
SNAP = PIPE / "snapshots"

# 计费阶梯（实测自 tiktok/web/fetch_post_detail，与 pricing_tiers 一致）
TIERS = [(0, 1000, 0.0), (1000, 5000, 10.0), (5000, 10000, 20.0),
         (10000, 20000, 30.0), (20000, 30000, 40.0), (30000, 10**9, 50.0)]


def tiered_cost(base: float, rpd: int, allow_discount: bool) -> float:
    """按阶梯计算总成本；端点不可打折时退化为线性。"""
    if not allow_discount:
        return base * rpd
    total, left = 0.0, rpd
    for lo, hi, disc in TIERS:
        if left <= 0:
            break
        n = min(left, hi - lo)
        total += n * base * (1 - disc / 100.0)
        left -= n
    return total


def load_pricing() -> dict:
    """endpoint -> {price, free, discount}"""
    out = {}
    p = SNAP / "2026-11-05-tikhub" / "_tikhub_endpoint_pricing.json"
    if p.exists():
        for r in json.loads(p.read_text(encoding="utf-8")):
            out[r["endpoint"]] = {"price": r["price"], "free": bool(r.get("free")),
                                  "discount": bool(r.get("discount"))}
    p = SNAP / "_tikhub_pricing_tiktok_all.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["endpoint"]] = {"price": r["price"], "free": bool(r.get("free")),
                                      "discount": bool(r.get("discount"))}
    return out


# ── 作业计划：每个能力包 → (端点, 每日调用次数, 持续天数) ──────────────────
# 调用量依据：各端点单次返回 20–30 条；五站 × 关键词矩阵的常规采样规模。
PLAN: dict = {
    "P1 TikTok 内容侧作品样本（拆解岗命门）": [
        ("/api/v1/tiktok/web/fetch_search_video", 120, 2),
        ("/api/v1/tiktok/app/v3/fetch_general_search_result", 120, 2),
        ("/api/v1/tiktok/app/v3/fetch_one_video", 100, 2),
        ("/api/v1/tiktok/app/v3/fetch_hashtag_detail", 50, 2),
        ("/api/v1/tiktok/app/v3/fetch_hashtag_video_list", 50, 2),
    ],
    "P2 TikTok 达人作品列表（现需付费）": [
        ("/api/v1/tiktok/app/v3/fetch_user_post_videos", 60, 2),
    ],
    "P3 TikTok 商品评价（退货原因）": [
        ("/api/v1/tiktok/shop/web/fetch_product_reviews_v2", 80, 2),
    ],
    "P4 TikTok 竞品广告素材库": [
        ("/api/v1/tiktok/ads/search_ads", 50, 2),
    ],
    "P5 抖音垂类作品检索（L1 泛内容流补缺）": [
        ("/api/v1/douyin/search/fetch_video_search_v2", 90, 2),
        ("/api/v1/douyin/search/fetch_challenge_search_v2", 30, 2),
    ],
    "P6 抖音星图达人检索与报价（补赞粉比之外的商业指标）": [
        ("/api/v1/douyin/xingtu_v2/search_creator", 40, 2),
        ("/api/v1/douyin/xingtu_v2/get_author_base_info", 40, 2),
        ("/api/v1/douyin/xingtu/kol_service_price_v1", 20, 1),
    ],
    "P7 抖音多关键词热度趋势": [
        ("/api/v1/douyin/index/fetch_multi_keyword_hot_trend", 30, 2),
    ],
    "P8 榜单族日常维护（8 榜 × 3 页 × 2 关键词）": [
        ("/api/v1/douyin/billboard/fetch_hot_total_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_video_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_low_fan_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_high_fan_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_high_play_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_high_like_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_topic_list", 6, 30),
        ("/api/v1/douyin/billboard/fetch_hot_total_hot_word_list", 6, 30),
    ],
    "P9 TikTok 五站热卖榜日常维护": [
        ("/api/v1/tiktok/shop/web/fetch_hot_selling_products_list", 5, 30),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="机读结果输出路径")
    ap.add_argument("--free-credit", type=float, default=0.002, help="现有免费额度")
    args = ap.parse_args()

    pricing = load_pricing()
    print(f"已载入 {len(pricing)} 个端点报价\n")

    rows, grand, free_calls, paid_calls = [], 0.0, 0, 0
    for pkg, items in PLAN.items():
        pkg_cost, pkg_calls, pkg_paid = 0.0, 0, 0
        detail = []
        for ep, rpd, days in items:
            info = pricing.get(ep)
            if not info:
                detail.append((ep, None, "报价未收录", 0, 0))
                continue
            calls = rpd * days
            cost = tiered_cost(info["price"], rpd, info["discount"]) * days
            pkg_cost += cost
            pkg_calls += calls
            if info["free"]:
                free_calls += calls
            else:
                paid_calls += calls
                pkg_paid += cost
            detail.append((ep, info["price"], "免费额度" if info["free"] else "扣余额",
                           calls, cost))
        grand += pkg_cost
        rows.append({"package": pkg, "cost": pkg_cost, "calls": pkg_calls, "detail": detail})
        print(f"■ {pkg}")
        print(f"    小计 ${pkg_cost:.4f}（{pkg_calls} 次调用，其中 {pkg_paid:.4f} 需扣余额）")
        for ep, price, kind, calls, cost in detail:
            if price is None:
                print(f"      ⚠️ {ep} —— 报价未收录")
                continue
            mark = "✔" if kind == "免费额度" else " "
            print(f"      {mark} ${price:<7} {calls:>6} 次  ${cost:<10.4f} {kind}  {ep}")
        print()

    print("=" * 78)
    print(f"合计：{sum(r['calls'] for r in rows)} 次调用，总成本 ${grand:.4f}")
    print(f"  其中吃免费额度：{free_calls} 次 | 需扣余额：{paid_calls} 次")
    print(f"  现有免费额度 ${args.free_credit} → 需充值 ≈ ${max(0.0, grand - args.free_credit):.4f}")
    print("=" * 78)

    minimal = sum(r["cost"] for r in rows
                  if not r["package"].startswith("P8") and not r["package"].startswith("P9"))
    tiers = [
        ("A 最小可用 · P1–P7 一次性采完（2 天内集中跑）", minimal),
        ("B 推荐 · P1–P9 跑满 30 天（含榜单日常维护）", grand),
        ("C 半年常态 · B × 6 个月", grand * 6),
        ("D 全年常态 · B × 12 个月 × 1.5（三能力包重跑 + 冗余）", grand * 12 * 1.5),
    ]
    print("\n充值档位建议（TikHub 最低充值额以官网为准，以下按 $5 起算）：")
    for name, amt in tiers:
        need = max(0.0, amt - args.free_credit)
        topup = max(5.0, round(need / 5) * 5)
        print(f"  {name:46} 需 ${need:>7.2f}  → 建议充 ${topup:>6.0f}")
    print(f"\n  ⚠️ 说明：$0.002 剩余免费额度可覆盖 {int(args.free_credit / 0.001)} 次 $0.001 调用，")
    print(f"     对整体预算影响可忽略；**真正卡住的是 balance=0，余额为 0 时任何端点都调不动。**")

    if args.json:
        out = pathlib.Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "packages": rows,
            "total_cost": grand,
            "free_calls": free_calls,
            "paid_calls": paid_calls,
            "free_credit": args.free_credit,
            "suggested_topup": max(5.0, round(max(0.0, grand - args.free_credit) / 5) * 5),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n机读结果：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
