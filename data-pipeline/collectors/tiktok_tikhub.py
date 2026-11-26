"""TikTok Shop 东南亚数据采集器（经 TikHub API，需要 token）。

为什么走托管 API 而不是自建直连：
  本机到 tiktok.com / ads.tiktok.com 的 DNS 被污染（ads.tiktok.com 解析到
  174.132.167.252，属于 Meta IP 段），出网直接失败；TikHub / Apify 的 API 域名
  在境内可达，只需一个 API key。

覆盖 TikTok Shop 电商侧的核心数据，直接对应选品 / 货架 / 竞品三个岗位：
  · 热卖商品榜      fetch_hot_selling_products_list
  · 商品搜索        fetch_search_products_list
  · 商品详情        fetch_product_detail
  · 商品评论        fetch_product_reviews_v2
  · 类目列表        fetch_products_category_list
  · 类目下商品      fetch_products_by_category_id
  · 店铺商品列表    fetch_seller_products_list
  · 搜索词建议      fetch_search_word_suggestion_v2

站点（region code）：ID 印尼 / TH 泰国 / VN 越南 / MY 马来 / PH 菲律宾
（US、GB、BR 官方暂不支持）

## 计费与免费额度（2026-11-05 实测）

TikHub 凭据已修复并通过鉴权。TikTok 侧共 164 个端点，扫描 shop/ads 类 30 个后发现
**19 个 `allow_free_credit=true`（$0.001/次，吃账户免费额度）**，
因此 **TikTok Shop 商品侧与广告素材库现在可以零充值跑通**；
仍需充值的�� `tiktok/web`、`tiktok/app` 的关键词检索类端点。

| 端点 | 单价 | 吃免费额度 |
|---|---|---|
| shop/web/fetch_hot_selling_products_list（热卖榜） | $0.001 | ✅ |
| shop/web/fetch_search_products_list（商品搜索） | $0.001 | ✅ |
| shop/web/fetch_product_detail / _v2 / _v3（商品详情） | $0.001 | ✅ |
| shop/web/fetch_products_category_list（类目） | $0.001 | ✅ |
| shop/web/fetch_products_by_category_id（类目商品） | $0.001 | ✅ |
| shop/web/fetch_search_word_suggestion_v2（搜索词） | $0.001 | ✅ |
| ads/search_ads、ads/get_top_ads_spotlight（**竞品广告素材库**） | $0.001 | ✅ |

## 分站不混算（硬要求，2026-11-05 修正）

东南亚五站流量结构、类目结构、价格带完全不同，**禁止跨站合并统计**。
因此 dataset 名一律带 region 后缀（`product_ID` / `product_MY` …）——
早期版本 dataset 名固定为 `product`，会把多站数据混进同一个文件，已修正。

用法：
    export TIKHUB_TOKEN=xxx
    python3 collectors/tiktok_tikhub.py --region ID --keyword "beauty blender"
    python3 collectors/tiktok_tikhub.py --region ID --hot --category
    python3 collectors/tiktok_tikhub.py --ads "hoodie" --ads-country ID
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from base import append_manifest, http_get_json, http_post_json, make_record, now_cst, write_records  # noqa: E402
from tikhub_auth import assert_token_shape  # noqa: E402

COLLECTOR = "tiktok_tikhub"
BASE = "https://api.tikhub.io/api/v1/tiktok/shop/web"
#: ads 端点不在 shop/web 下，单独一个基址
BASE_TIKTOK = "https://api.tikhub.io/api/v1/tiktok"

#: 本项目锁定的东南亚站点。region code 是 TikHub 的约定，不是国家名。
REGIONS = {
    "ID": "印尼",
    "TH": "泰国",
    "VN": "越南",
    "MY": "马来",
    "PH": "菲律宾",
}

TOKEN_ENV = "TIKHUB_TOKEN"


def get_token() -> str:
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if not token:
        return ""
    # 形状守卫：被截断的 token 在本地就报错（2026-11-05 曾因此误诊为"鉴权没配好"）
    assert_token_shape(token, TOKEN_ENV)
    return token


def _save_raw(path: str, params: Dict[str, Any], body: Any) -> None:
    """原始响应落盘。

    TikHub 的响应可缓存 24 小时，但**重新解析原始体不额外计费** —— 额度紧张时
    改了口径直接从原始体重算，不要再花一次钱重采。
    """
    try:
        import hashlib
        d = pathlib.Path(__file__).resolve().parent.parent / "data" / "tiktok" / "_raw"
        d.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
        (d / f"{path.replace('/', '_')}_{key}.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def call(path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    token = get_token()
    if not token:
        return {"_error": f"缺少 {TOKEN_ENV}"}
    q = "&".join(f"{k}={v}" for k, v in params.items() if v not in (None, ""))
    url = f"{BASE}/{path}"
    if q:
        url += "?" + q
    r = http_get_json(url, headers={"Authorization": f"Bearer {token}"}, timeout=30, retries=2)
    if not r["ok"]:
        return {"_error": r["error"]}
    body = r.get("json") or {}
    _save_raw(path, params, body)
    if isinstance(body, dict) and "detail" in body:
        det = body["detail"]
        if isinstance(det, dict):
            return {"_error": f"{det.get('code')}: {det.get('message')}"}
    return body


def _items(payload) -> List[dict]:
    """从 TikHub 响应里取出商品列表。

    TikHub 的 `data` 形状随端点而变（有的直接是 list，有的裹在
    `data.search_result` / `data.items` / `data.products` 里），而且不同端点
    连字段名都不同。所以这里不写死路径，而是**递归找第一个"像商品"的字典列表**：
    字典里同时出现 id 类字段和 title 类字段才算。
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)

    ID_KEYS = ("product_id", "productId", "id", "item_id")
    TITLE_KEYS = ("title", "product_name", "name", "productName")

    def looks_like_product(d: dict) -> bool:
        return (any(k in d for k in ID_KEYS)) and (any(k in d for k in TITLE_KEYS))

    def find(node, depth=0):
        if depth > 8:
            return None
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if dicts and sum(1 for x in dicts if looks_like_product(x)) >= max(1, len(dicts) // 2):
                return dicts
            for x in node[:12]:
                got = find(x, depth + 1)
                if got:
                    return got
            return None
        if isinstance(node, dict):
            # 优先看常见容器名
            for k in ("products", "items", "product_list", "search_result", "list",
                      "data", "result", "records"):
                if k in node:
                    got = find(node[k], depth + 1)
                    if got:
                        return got
            for v in node.values():
                got = find(v, depth + 1)
                if got:
                    return got
        return None

    found = find(data)
    return found if found else []


def _first_num(d: Any, keys) -> Optional[float]:
    """从 dict 里取第一个数值型字段（字符串数字也认）。"""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.replace(",", "").replace(".", "", 0) if False else v)
            except ValueError:
                continue
    return None


def norm_product(p: dict, region: str, source_url: str) -> Dict[str, Any]:
    # 实测（2026-11-05）TikTok Shop 商品字段名：
    #   product_price_info / sold_info / rate_info / seller_info
    # 早期版本只认 price / sales_info / review，导致价格与销量全是 None。
    # 实测真实结构（2026-11-05，VN 站样本）：
    #   product_price_info = {"sale_price_decimal":"109000", "origin_price_decimal":"299000",
    #                         "currency_name":"VND", "discount_decimal":0.64, ...}
    #   rate_info          = {"score":4.6, "review_count":"3602"}
    price = p.get("product_price_info") or p.get("price_info") or p.get("price") or {}
    if not isinstance(price, dict):
        price = {"price": price}
    sold = p.get("sold_info") or p.get("sales_info") or {}
    sales = _first_num(sold, ("sold_count", "sold_cnt", "sales", "sold_num",
                              "history_sold_count"))
    if sales is None:
        sales = _first_num(p, ("sold_count", "history_sold_count"))
    rating = p.get("rate_info") or p.get("review") or p.get("rating") or {}
    if isinstance(rating, dict):
        rating = (_first_num(rating, ("score", "rating_score", "rating_star",
                                      "average_rating"))
                  or rating.get("rating_star") or rating.get("average_rating"))
    seller = p.get("seller_info") or p.get("seller") or {}
    return make_record(
        platform="tiktok",
        dataset=f"product_{region}",
        entity_type="product",
        entity_id=str(p.get("product_id") or p.get("id") or ""),
        title=str(p.get("title") or p.get("product_name") or "")[:200],
        metrics={
            "price": _first_num(price, ("sale_price_decimal", "min_price", "price",
                                        "sale_price", "min_sale_price"))
                     or p.get("price"),
            "original_price": _first_num(price, ("origin_price_decimal", "original_price")),
            "discount_pct": _first_num(price, ("discount_decimal", "discount_rate",
                                               "discount")),
            "sold_count": sales,
            "rating": rating,
            "review_count": (_first_num(p.get("rate_info") or {}, ("review_count",))
                             or p.get("review_count") or p.get("sold_comment")),
            "currency": (p.get("product_price_info") or {}).get("currency_name"),
            "stock": p.get("stock_info", {}).get("stock") if isinstance(p.get("stock_info"), dict) else None,
            "commission_rate": p.get("commission_rate"),
            "affiliate_sold_count": p.get("affiliate_sold_count"),
        },
        source_url=source_url,
        extra={
            "region": region,
            "region_name": REGIONS.get(region, region),
            "seller_id": (seller.get("seller_id") if isinstance(seller, dict) else None)
                        or p.get("seller_id"),
            "shop_name": ((seller.get("shop_name") or seller.get("nick_name"))
                          if isinstance(seller, dict) else None) or p.get("shop_name"),
            "category_id": p.get("category_id"),
            "category_name": p.get("category_name"),
        },
    )


def collect_hot_selling(region: str, count: int = 50) -> List[Dict[str, Any]]:
    path = "fetch_hot_selling_products_list"
    payload = call(path, {"region": region, "count": count})
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 热卖商品: {payload['_error']}")
        return []
    url = f"{BASE}/{path}?region={region}&count={count}"
    out = [norm_product(p, region, url) for p in _items(payload)]
    print(f"  ✅ 热卖商品 {region}: {len(out)} 条")
    return out


def collect_search(region: str, keyword: str, count: int = 30) -> List[Dict[str, Any]]:
    path = "fetch_search_products_list"
    payload = call(path, {"search_word": keyword, "offset": 0, "region": region})
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 商品搜索: {payload['_error']}")
        return []
    url = f"{BASE}/{path}?search_word={keyword}&region={region}"
    out = [norm_product(p, region, url) for p in _items(payload)][:count]
    print(f"  ✅ 商品搜索 '{keyword}' @{region}: {len(out)} 条")
    return out


def collect_category_products(region: str, category_id: int, count: int = 30) -> List[Dict[str, Any]]:
    path = "fetch_products_by_category_id"
    payload = call(path, {"category_id": category_id, "offset": 0, "region": region})
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 类目商品: {payload['_error']}")
        return []
    url = f"{BASE}/{path}?category_id={category_id}&region={region}"
    out = [norm_product(p, region, url) for p in _items(payload)][:count]
    print(f"  ✅ 类目 {category_id} 商品 @{region}: {len(out)} 条")
    return out


def collect_reviews(region: str, product_id: str, count: int = 30) -> List[Dict[str, Any]]:
    path = "fetch_product_reviews_v2"
    payload = call(path, {"product_id": product_id, "page_start": 0, "region": region})
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 商品评论: {payload['_error']}")
        return []
    url = f"{BASE}/{path}?product_id={product_id}&region={region}"
    items = _items(payload)
    out = []
    for r in items[:count]:
        out.append(make_record(
            platform="tiktok", dataset=f"product_review_{region}", entity_type="review",
            entity_id=str(r.get("review_id") or product_id),
            title=str(r.get("content") or r.get("review_content") or "")[:300],
            metrics={"rating": r.get("rating"), "like_count": r.get("like_count"),
                     "reply_count": r.get("reply_count"), "create_time": r.get("create_time")},
            source_url=url,
            extra={"region": region, "product_id": product_id,
                   "buyer_name": r.get("buyer_name"), "images": r.get("images")},
        ))
    print(f"  ✅ 商品评论 {product_id} @{region}: {len(out)} 条")
    return out


def collect_search_suggestions(region: str, word: str) -> List[Dict[str, Any]]:
    path = "fetch_search_word_suggestion_v2"
    payload = call(path, {"search_word": word, "region": region})
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 搜索词建议: {payload['_error']}")
        return []
    url = f"{BASE}/{path}?search_word={word}&region={region}"
    items = _items(payload)
    out = [make_record(
        platform="tiktok", dataset=f"search_suggestion_{region}", entity_type="keyword",
        entity_id=str(x.get("keyword") or x.get("word") or x.get("query") or ""),
        title=str(x.get("keyword") or x.get("word") or x.get("query") or "")[:100],
        metrics={"score": x.get("score"), "rank": x.get("rank")},
        source_url=url, extra={"region": region, "seed": word},
    ) for x in items]
    print(f"  ✅ 搜索词建议 '{word}' @{region}: {len(out)} 条")
    return out



def collect_product_detail(region: str, product_id: str, seller_id: str = "") -> List[Dict[str, Any]]:
    """商品详情（分站）。"""
    path = "fetch_product_detail"
    params = {"product_id": product_id, "region": region}
    if seller_id:
        params["seller_id"] = seller_id
    payload = call(path, params)
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 商品详情: {payload['_error']}")
        return []
    url = f"{BASE}/{path}?product_id={product_id}&region={region}"
    items = _items(payload) or ([payload["data"]] if isinstance(payload.get("data"), dict)
                               and isinstance(payload["data"].get("product_detail"), dict) else [])
    out = [norm_product(p, region, url) for p in items if isinstance(p, dict)]
    print(f"  ✅ 商品详情 {product_id} @{region}: {len(out)} 条")
    return out


def collect_categories(region: str) -> List[Dict[str, Any]]:
    """类目列表（分站）。注意：实测 ID 不支持，US/SG/MY 可用。"""
    path = "fetch_products_category_list"
    payload = call(path, {"region": region})
    if isinstance(payload, dict) and payload.get("_error"):
        print(f"  ❌ 类目列表: {payload['_error']}")
        return []
    data = payload.get("data", payload)
    items = data if isinstance(data, list) else (data.get("categories") or [] if isinstance(data, dict) else [])
    out = []
    for c in items or []:
        if not isinstance(c, dict):
            continue
        cid = c.get("id") or c.get("category_id")
        if not cid:
            continue
        out.append(make_record(
            platform="tiktok", dataset=f"category_{region}", entity_type="category",
            entity_id=str(cid),
            title=str(c.get("name") or c.get("local_name") or "")[:120],
            metrics={"level": c.get("level")},
            source_url=f"{BASE}/{path}?region={region}",
            extra={"region": region, "region_name": REGIONS.get(region, region),
                   "parent_id": c.get("parent_id"), "is_leaf": c.get("is_leaf")},
        ))
    print(f"  ✅ 类目列表 @{region}: {len(out)} 条")
    return out


def collect_ads(keyword: str, country_code: str = "ID", industry: str = "",
                period: int = 180, page: int = 1, limit: int = 20) -> List[Dict[str, Any]]:
    """竞品广告素材库（TikTok Ads Library）。

    这是**竞品创意研究**的入口：素材标题、品牌、投放成本、CTR、点赞、时长。
    注意：广告库与 Shop 是两套体系，按 country_code 分站，**不得与 Shop 数据混算**。
    """
    body: Dict[str, Any] = {"country_code": country_code, "period": period,
                            "page": page, "limit": limit, "order_by": "for_you"}
    if keyword:
        body["keyword"] = keyword
    if industry:
        body["industry"] = industry
    token = get_token()
    r = http_post_json(f"{BASE_TIKTOK}/ads/search_ads", body=body,
                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                       timeout=45, retries=2)
    if not r.get("ok"):
        print(f"  ❌ 广告素材库: {r.get('error')}")
        return []
    payload = r.get("json") or {}
    if isinstance(payload, dict) and payload.get("detail"):
        det = payload["detail"]
        msg = det.get("message") if isinstance(det, dict) else str(det)
        print(f"  ❌ 广告素材库: {msg}")
        return []
    data = payload.get("data", payload)
    materials = []
    if isinstance(data, dict):
        inner = data.get("data", data)
        materials = inner.get("materials") if isinstance(inner, dict) else (inner if isinstance(inner, list) else [])
    elif isinstance(data, list):
        materials = data
    out = []
    for a in materials or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("id") or a.get("ad_id")
        if not aid:
            continue
        vi = a.get("video_info") or {}
        dur = vi.get("duration") if isinstance(vi, dict) else None
        out.append(make_record(
            platform="tiktok", dataset=f"ads_material_{country_code}", entity_type="ad",
            entity_id=str(aid),
            title=str(a.get("ad_title") or "")[:200],
            metrics={"ctr": a.get("ctr"), "cost": a.get("cost"),
                     "like": a.get("like"), "favorite": a.get("favorite"),
                     "duration_ms": int(dur * 1000) if isinstance(dur, (int, float)) else None},
            source_url=(vi.get("video_url") or vi.get("cover_url") or "") if isinstance(vi, dict) else "",
            extra={"country_code": country_code, "brand_name": a.get("brand_name"),
                   "industry_key": a.get("industry_key"), "objective_key": a.get("objective_key"),
                   "keyword": keyword, "period_days": period},
        ))
    print(f"  ✅ 广告素材库 '{keyword}' @{country_code}: {len(out)} 条")
    return out


def main():
    ap = argparse.ArgumentParser(description="TikTok Shop 东南亚采集（TikHub，需 token）")
    ap.add_argument("--region", default="ID", choices=sorted(REGIONS), help="站点，默认印尼 ID")
    ap.add_argument("--keyword", help="商品搜索词")
    ap.add_argument("--hot", action="store_true", help="拉热卖商品榜")
    ap.add_argument("--count", type=int, default=50, help="热卖榜条数（1–100）")
    ap.add_argument("--category", type=int, help="按类目 ID 拉商品")
    ap.add_argument("--reviews", help="拉指定商品评论")
    ap.add_argument("--suggest", help="拉搜索词建议")
    ap.add_argument("--detail", help="拉指定商品详情 product_id")
    ap.add_argument("--categories", action="store_true", help="拉类目列表")
    ap.add_argument("--ads", help="拉竞品广告素材库（关键词）")
    ap.add_argument("--ads-country", default="ID", help="广告素材库国家码")
    ap.add_argument("--ads-industry", default="", help="广告素材库行业")
    ap.add_argument("--ads-period", type=int, default=180, help="广告投放周期（天）")
    args = ap.parse_args()

    if not get_token():
        print(f"❌ 未设置 {TOKEN_ENV}。")
        print("   获取方式：https://api.tikhub.io → 注册 → 控制台取 API Key")
        print("   设置后重试：export TIKHUB_TOKEN=你的key")
        raise SystemExit(2)

    print(f"TikTok Shop 采集 — {REGIONS[args.region]} ({args.region}) — {now_cst()}")
    records: List[Dict[str, Any]] = []
    if args.hot:
        records += collect_hot_selling(args.region, args.count)
    if args.keyword:
        records += collect_search(args.region, args.keyword)
    if args.category:
        records += collect_category_products(args.region, args.category)
    if args.reviews:
        records += collect_reviews(args.region, args.reviews)
    if args.suggest:
        records += collect_search_suggestions(args.region, args.suggest)
    if args.detail:
        records += collect_product_detail(args.region, args.detail)
    if args.categories:
        records += collect_categories(args.region)
    if args.ads:
        records += collect_ads(args.ads, args.ads_country, args.ads_industry,
                               args.ads_period)

    if records:
        by_ds: Dict[str, list] = {}
        for r in records:
            by_ds.setdefault(r["dataset"], []).append(r)
        manifest = []
        for ds, rs in by_ds.items():
            paths = write_records(rs, "tiktok", ds)
            manifest.append({"platform": "tiktok", "dataset": ds, "collector": COLLECTOR,
                             "records": len(rs), "fetched_at": now_cst(),
                             "credential": "TikHub API token", "paths": paths,
                             "region": args.region})
            print(f"  落盘 {ds}: {len(rs)} 条 → {paths['jsonl']}")
        append_manifest(manifest)
    else:
        print("  ⚠️ 未取到数据")


if __name__ == "__main__":
    main()
