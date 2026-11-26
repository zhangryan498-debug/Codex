#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TikHub 充值后采样执行器（探针契约驱动 + 预算熔断）

2026-11-06 实测校正（**凭印象写的端点名/参数名错了 5 处**，探针阶段全部暴露出来）
------------------------------------------------------------------------------
| 端点                                  | 我第一版写的错法      | 正确写法                     |
|--------------------------------------|----------------------|------------------------------|
| tiktok/web/fetch_search_video         | cursor / 参数 400    | **整个端点被搜索侧封堵**，改用 |
|                                      |                      | app/v3/fetch_video_search_result |
| tiktok/app/v3/fetch_general_search_result | 返回 result_status=4 | 同上（该端点在本环境无结果）   |
| tiktok/app/v3/fetch_hashtag_detail    | challenge_id → 422   | **ch_id**                    |
| tiktok/app/v3/fetch_hashtag_video_list| challenge_id → 422   | **ch_id** + region           |
| douyin/xingtu_v2/get_author_base_info | sec_uid → 422        | **o_author_id**              |
| douyin/xingtu/kol_service_price_v1    | sec_uid/product_id   | **kolId + platformChannel**  |
| douyin/search/fetch_video_search_v2   | GET → 405            | **POST + body**              |
| douyin/index/fetch_multi_keyword_hot_trend | GET → 405        | **POST + query + 日期必填**  |

结论沉淀：**计费属性可用 ≠ 取得到数据**。TikHub 报价表说 `allow_free_credit=true` 只说明
「这个端点可以走免费额度」，不代表「这个端点在本环境能返回内容」。搜索类端点在本机返回空，
但**作品详情 / 首页推荐流 / 话题搜索 / 趋势词 / 音乐榜 / 探索页**全部可用——所以 TikTok
内容侧路径改成「推荐流 + 话题搜索 + 详情回填」，不依赖搜索。

用法
----
    python3 -u scripts/tikhub_recharge_sampling.py            # 零成本计划预览
    python3 -u scripts/tikhub_recharge_sampling.py --probe    # 阶段 A 探针（≈$0.06）
    python3 -u scripts/tikhub_recharge_sampling.py --apply    # 阶段 B 满跑（≈$4.58）
    python3 -u scripts/tikhub_recharge_sampling.py --apply --only P1
    python3 -u scripts/tikhub_recharge_sampling.py --apply --cap 1.0

纪律
----
1. **先查余额**：balance=0 直接退出（TikHub 返 402，不做无谓请求）。
2. **限流**：1 req/s/路由，同路由连续调用间隔 ≥1.2s。
3. **原始响应落盘**：TikHub 响应缓存 24 小时，改口径从原始体重算**不再花钱**。
4. **预算熔断**：`--cap` 封顶累计花费，超了立刻停并报账。
5. **不覆盖冻结快照**：新样本写独立目录 `snapshots/<date>-*`。
6. **分站不混算**：TikTok 样本带 region，dataset 名带 region 后缀。
7. **凭据零泄漏**：token 只从环境变量读，不落盘、不打印。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote

HERE = pathlib.Path(__file__).resolve().parent
PIPE = HERE.parent
sys.path.insert(0, str(PIPE / "collectors"))

from base import http_get_json, http_post_json, now_cst, today_cst  # noqa: E402

API = "https://api.tikhub.io/api/v1"
TOKEN_ENV = "TIKHUB_TOKEN"
CST = timezone = None  # noqa: F841  (占位，避免误 import timedelta 时用错)
from datetime import timezone as _tz  # noqa: E402
SLEEP = 1.2

REGIONS = ["ID", "TH", "VN", "MY", "PH"]

# 平台 → region 语义（2026-11-06 订正）
#
# **抖音是 CN 单站平台**，不存在 ID/TH/VN/MY/PH 分站。
# 此前抖音侧采样把 TikTok 的 region 常量一并带上，导致抖音数据被标成 `region=ID`
# 并落成 `P5_video_search_ID.jsonl` —— 这在跨平台比对时会被误读成「印尼站样本」。
# 纪律：
#   · 抖音侧 region 恒为 `CN`，dataset 文件名**不带** region 后缀（后缀是分站概念）。
#   · TikTok 侧保留 region 后缀，且**分站不混算**。
PLATFORM_REGION = {"douyin": "CN", "tiktok": None}  # None = 按请求的 region 落

# ---------------------------------------------------------------- 端点契约
ENDPOINTS: Dict[str, Dict[str, Any]] = {
    # ---- TikTok 内容侧（P1）
    "P1_video_search": {
        "path": "/tiktok/app/v3/fetch_video_search_result", "price": 0.001,
        "method": "GET", "where": "query", "calls": 240, "norm": "aweme",
        "probe": {"keyword": "mens hoodie", "offset": 0, "count": 20,
                  "sort_type": 0, "publish_time": 0, "region": "ID"},
    },
    "P1_hashtag_search": {
        "path": "/tiktok/app/v3/fetch_hashtag_search_result", "price": 0.001,
        "method": "GET", "where": "query", "calls": 240, "norm": "challenge",
        # **该端点不接受 region 参数**（OpenAPI 只有 keyword/offset/count），
        # 服务端按默认站位 US 返回。此前落盘成 `P1_hashtag_search_ID.jsonl`
        # 属误标 —— 数据实际是 US 站话题，不是印尼站。
        "probe": {"keyword": "menswear", "offset": 0, "count": 20},
        "region_fixed": "US",
    },
    "P1_home_feed": {
        "path": "/tiktok/web/fetch_home_feed", "price": 0.001,
        "method": "POST", "where": "body", "calls": 200, "norm": "aweme",
        "probe": {"count": 20, "region": "ID"},
    },
    "P1_one_video": {
        "path": "/tiktok/app/v3/fetch_one_video", "price": 0.001,
        "method": "GET", "where": "query", "calls": 200, "norm": "aweme",
        "probe": {"aweme_id": ""},
    },
    "P1_hashtag_detail": {
        "path": "/tiktok/app/v3/fetch_hashtag_detail", "price": 0.001,
        "method": "GET", "where": "query", "norm": "challenge",
        "calls": 100,
        # **region 枚举受限（2026-11-06 实测）**：OpenAPI 标注该端点 region 仅支持
        # US / GB / FR / JP / VN / SG（默认 US）。传 ID（印尼）→ 服务端 HTTP 400。
        # 与 `fetch_video_search_result` 不同（后者 region 无枚举限制，ID 可用）。
        # → 本端点改用受支持站位；**东南亚五站的话题详情/话题作品取不到**，
        #   这是一条真实的通道限制，不是采样没跑够（已记入 data-channel.md）。
        #   站位取 US：ch_id 来自 `fetch_hashtag_search_result`（该端点无 region 参数，
        #   服务端按默认站位 US 返回），跨站传 ch_id 会查不到。
        "probe": {"ch_id": "", "region": "US"},
        "region_fixed": "US",
    },
    "P1_hashtag_video_list": {
        "path": "/tiktok/app/v3/fetch_hashtag_video_list", "price": 0.001,
        "method": "GET", "where": "query", "norm": "aweme",
        "calls": 100,
        # 同上：region 枚举受限（US/GB/FR/JP/VN/SG），ID 会 400；站位取 US。
        "probe": {"ch_id": "", "cursor": 0, "count": 20, "region": "US"},
        "region_fixed": "US",
    },
    "P1_trending_words": {
        "path": "/tiktok/web/fetch_trending_searchwords", "price": 0.001,
        "method": "GET", "where": "query", "calls": 100, "norm": "trend_word",
        "probe": {},
    },
    "P1_explore_post": {
        "path": "/tiktok/web/fetch_explore_post", "price": 0.001,
        "method": "GET", "where": "query", "calls": 100, "norm": "aweme",
        "probe": {"categoryType": "120", "count": 16},
    },
    # ---- TikTok 直播侧（L1）—— 2026-11-06 为直播运营岗补数据前置 ----
    "P1_live_search": {
        "path": "/tiktok/app/v3/fetch_live_search_result", "price": 0.001,
        "method": "GET", "where": "query", "calls": 120, "norm": "live",
        # region 参数无枚举限制（OpenAPI 仅标默认 US，未限定枚举），
        # 与 hashtag 类端点不同 —— 用印尼站试。
        "probe": {"keyword": "menswear", "offset": 0, "count": 20, "region": "ID"},
    },
    "P1_live_search_US": {
        "path": "/tiktok/app/v3/fetch_live_search_result", "price": 0.001,
        "method": "GET", "where": "query", "calls": 48, "norm": "live",
        # US 站直播：东南亚站的直播间商品端点需自带 cookie 而取不到，
        # 故另采 US 站作**结构参照与商品链路验证**，明确标注站位。
        "probe": {"keyword": "menswear", "offset": 0, "count": 20, "region": "US"},
        "region_fixed": "US",
    },
    "P1_live_recommend": {
        "path": "/tiktok/web/fetch_live_recommend", "price": 0.001,
        "method": "GET", "where": "query", "calls": 40, "norm": "live",
        "probe": {"related_live_tag": "fashion"},
    },
    "P1_live_product": {
        "path": "/tiktok/app/v3/fetch_live_room_product_list", "price": 0.001,
        "method": "GET", "where": "query", "calls": 60, "norm": "live_product",
        # 需要 room_id + author_id，均取自直播搜索结果
        "probe": {"room_id": "", "author_id": "", "page_size": 15,
                  "offset": 0, "region": "ID"},
    },
    # ---- 抖音垂类检索（P5）
    "P5_video_search": {
        "path": "/douyin/search/fetch_video_search_v2", "price": 0.01,
        "method": "POST", "where": "body", "calls": 180, "norm": "aweme",
        "probe": {"keyword": "男装秋冬穿搭", "cursor": 0, "sort_type": "0",
                  "publish_time": "0", "filter_duration": "0",
                  "content_type": "0", "search_id": "", "backtrace": ""},
    },
    "P5_challenge_search": {
        "path": "/douyin/search/fetch_challenge_search_v2", "price": 0.01,
        "method": "POST", "where": "body", "calls": 60, "norm": "challenge",
        "probe": {"keyword": "男装秋冬穿搭", "cursor": 0, "sort_type": "0",
                  "publish_time": "0", "filter_duration": "0",
                  "content_type": "0", "search_id": "", "backtrace": ""},
    },
    # ---- 星图达人（P6）
    "P6_search_creator": {
        "path": "/douyin/xingtu_v2/search_creator", "price": 0.002,
        "method": "GET", "where": "query", "calls": 80, "norm": "creator",
        "probe": {"keyword": "男装", "seach_type": 2, "page": 1,
                  "sort_field": "score", "sort_type": 2,
                  "task_category": 1, "marketing_target": 1},
    },
    "P6_author_base_info": {
        "path": "/douyin/xingtu_v2/get_author_base_info", "price": 0.002,
        "method": "GET", "where": "query", "calls": 80, "norm": "creator_info",
        "probe": {"o_author_id": "", "platform_source": 1, "platform_channel": 1},
    },
    "P6_kol_price": {
        "path": "/douyin/xingtu/kol_service_price_v1", "price": 0.02,
        "method": "GET", "where": "query", "calls": 20, "norm": "kol_price",
        "probe": {"kolId": "", "platformChannel": "_1"},
    },
    # ---- 多词热度（P7）
    "P7_keyword_trend": {
        "path": "/douyin/index/fetch_multi_keyword_hot_trend", "price": 0.003,
        "method": "POST", "where": "query", "calls": 60, "norm": "trend",
        "probe": {"keyword_list": "男装秋冬穿搭,男装外套", "start_date": "",
                  "end_date": "", "app_name": "aweme"},
    },
}

# 满跑阶段的关键词与配额
#
# 2026-11-06 实测延迟：TikTok 搜索 2–6s/次、抖音搜索 3.4s/次、星图 2.4s/次
# → 1760 次的满额计划要跑 2 小时。改为**时间盒 + 价值优先**：
#   ① 抖音侧先跑（星图达人报价 / 垂类作品 / 热度 = 新数据价值最高）
#   ② TikTok 内容侧靠后（视频搜索 / 话题搜索 / 详情回填 / 推荐流 / 趋势词 / 探索页）
#   ③ 页数收敛到够用：单端点 8 页 × 6 词 ≈ 48 次
# 合计 ≈ 399 次 / ≈ $1.49 / 约 25 分钟
RUN_PLAN = {
    # ---- 抖音侧（价值优先，先跑）
    "P6_search_creator": {"keywords": ["男装", "穿搭", "秋冬穿搭", "街头风"],
                           "pages": 6},
    "P6_author_base_info": {"from": "search_creator", "count": 24},
    "P6_kol_price": {"from": "search_creator", "count": 20},
    "P5_video_search": {"keywords": ["男装秋冬穿搭", "男装外套", "男装卫衣",
                                     "男装打底衫", "男生通勤穿搭", "男生秋冬风"],
                        "pages": 8},
    "P5_challenge_search": {"keywords": ["男装秋冬穿搭", "男装外套", "男装卫衣"],
                            "pages": 8},
    "P7_keyword_trend": {"batches": 3, "keywords_per_batch": 20},
    # ---- TikTok 内容侧
    "P1_video_search": {"keywords": ["mens hoodie", "mens winter jacket",
                                     "mens sweater", "men winter outfit",
                                     "mens streetwear", "mens oversize"],
                        "pages": 8},
    "P1_hashtag_search": {"keywords": ["menswear", "mensoutfit", "streetwear",
                                       "mensfashion", "winterfashion",
                                       "menshoodie"], "pages": 8},
    "P1_one_video": {"from": "video_search+home_feed", "count": 40},
    "P1_hashtag_detail": {"from": "hashtag_search", "count": 25},
    "P1_hashtag_video_list": {"from": "hashtag_search", "count": 25},
    "P1_home_feed": {"regions": REGIONS, "pages": 6},
    "P1_trending_words": {"times": 20},
    "P1_explore_post": {"count": 20},
    # ---- 直播侧（2026-11-06 新增，直播运营岗数据前置）----
    # 探针实测：`fetch_live_search_result` 传 region=ID **可用**（status 200，18 条），
    # 与 hashtag 类端点（region 枚举受限）不同 → 直播搜索可按东南亚站取。
    "P1_live_search": {"keywords": ["menswear", "mens outfit", "fashion",
                                    "streetwear", "ootd", "hoodie"],
                       "pages": 6},
    # US 站直播：印尼站直播搜索**可用**（status 200），
    # 但 `fetch_live_room_product_list` 文档明说「非 US 地区需自带 Cookie，
    # 否则无法获取数据」→ **东南亚站的直播间商品取不到**。
    # → 另采 US 站直播作为**结构参照与商品链路验证**，明确标注站位，
    #    **不得当作东南亚站结论**。
    "P1_live_search_US": {"keywords": ["menswear", "fashion", "streetwear",
                                       "outfit haul"], "pages": 6,
                          "region": "US"},
    "P1_live_recommend": {"times": 10},
}

TREND_KEYWORDS = [
    "男装秋冬穿搭", "男装外套", "男装卫衣", "男装打底衫", "男生通勤穿搭",
    "男生秋冬风", "男士穿搭", "男装夹克", "男装大衣", "男装毛衣",
    "秋冬叠穿", "宽松卫衣", "落肩卫衣", "加绒打底衫", "男生工装裤",
    "秋冬男生穿搭技巧", "男装百搭", "男生ootd", "男装显瘦", "秋冬保暖穿搭",
]


# ---------------------------------------------------------------- 基础工具
def get_token() -> str:
    t = (os.environ.get(TOKEN_ENV) or "").strip()
    if not t:
        print(f"❌ 未设置 {TOKEN_ENV}。来源：data-pipeline/.env（600 权限，已 gitignore）")
        sys.exit(2)
    return t


def call(path: str, params: Dict[str, Any], token: str,
         method: str = "GET", where: str = "query") -> Dict[str, Any]:
    url = f"{API}{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        if where == "query":
            q = "&".join(f"{k}={quote(str(v), safe=',:')}"
                         for k, v in params.items() if v not in (None, ""))
            if q:
                url += "?" + q
        if method.upper() == "GET":
            r = http_get_json(url, headers=headers, timeout=60, retries=2)
        else:
            r = http_post_json(url, body=(params if where == "body" else None),
                               headers=headers, timeout=60, retries=2)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 0, "data": None, "error": f"{type(exc).__name__}: {exc}"}
    status = r.get("status", 0)
    body = r.get("json")
    if body is None:
        body = r.get("body") or r.get("text")
    err = r.get("error")
    if isinstance(body, dict) and body.get("detail"):
        d = body["detail"]
        err = d.get("message") if isinstance(d, dict) else str(d)
    return {"ok": bool(r.get("ok")) and 200 <= status < 300, "status": status,
            "data": body, "error": err}


def balance_check(token: str) -> Optional[Dict[str, Any]]:
    r = call("/tikhub/user/get_user_info", {}, token)
    if not r["ok"] or not isinstance(r["data"], dict):
        return None
    ud = r["data"].get("user_data") or {}
    return {"balance": ud.get("balance"), "free_credit": ud.get("free_credit")}


def walk(node: Any, depth: int = 0):
    """递归遍历任意结构。"""
    if depth > 9:
        return
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v, depth + 1)


# ---------------------------------------------------------------- 归一化
def _num(x) -> Optional[float]:
    """数值化。**字符串数字也要转**——星图 attribute_datas 的 follower /
    assign_cpm_suggest_price 实测是字符串（"223625" / "83"），
    只认 int/float 会把真实值判成 None（= 下游把缺失当 0）。"""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        s = x.strip().replace(",", "")
        if not s or s.lower() in ("none", "null", "nan", "-"):
            return None
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _extract_next_cursor(payload: Any) -> Optional[int]:
    """从抖音搜索响应里取**服务端下发的下一页游标**。

    实测路径：`data.business_config.next_page.cursor`（首个分页游标 = 8），
    配套 `data.business_config.has_more` 表示是否还有下一页。
    硬算 `pg * count` 会让服务端返回 HTTP 400 —— 每关键词只能拿到第 1 页。
    """
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    bc = data.get("business_config")
    if not isinstance(bc, dict):
        return None
    if bc.get("has_more") in (0, False):
        return None
    nxt = bc.get("next_page")
    if isinstance(nxt, dict):
        c = nxt.get("cursor")
        if isinstance(c, (int, float)):
            return int(c)
    return None


def _aweme_complete(node: dict) -> bool:
    """判断一个 aweme 节点是否**字段完整**（可安全作为记录来源）。

    2026-11-06 实测：`fetch_one_video` 的响应里同一个 `aweme_id` 会在**三个层级**
    各出现一次 —— 浅层只有 `{"aweme_id": "..."}`，深层才有完整 `statistics` / `video`。
    walk 是深度优先，先命中浅层 → 若按「首次命中即采用」，字段最全的真节点会被挤掉，
    导致 67 条记录指标全 null（审计 Q-P1_one_video-req 失败）。
    → 改为：**只有字段完整的节点才可作为记录**，不完整的直接跳过继续往下走。
    """
    if not isinstance(node, dict):
        return False
    st = node.get("statistics") or node.get("stats") or {}
    if not st:
        return False
    # 至少要有一个可用的互动指标
    return any(st.get(k) is not None for k in
               ("play_count", "playCount", "digg_count", "diggCount",
                "comment_count", "commentCount"))


def _bool(x):
    """布尔值归一化：保留 True/False 原值；非布尔数字按 0/1 判别；
    缺失返回 None（**不把缺失当 False**）。"""
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return bool(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return None


def norm_live_product(node: dict, region: str) -> Optional[dict]:
    """直播间挂载的商品（直播运营岗核心数据）。

    实测结构：`product_list[]` / `products[]` / `items[]` 里每项带
    `product_id` / `title` / `price` / `price_info` / `sold` 等。
    字段名不固定 → **只取能识别的**，缺失一律 None，**不把 0 当真值**。
    """
    if not isinstance(node, dict):
        return None
    pid = (node.get("product_id") or node.get("id")
           or node.get("productId") or node.get("item_id"))
    if not pid:
        return None
    pi = node.get("price_info") or node.get("priceInfo") or node.get("price") or {}

    def pick(*keys):
        for src in (node, pi):
            for k in keys:
                v = src.get(k)
                if v is not None:
                    return v
        return None

    return {
        "entity_id": str(pid),
        "title": str(node.get("title") or node.get("product_name")
                     or node.get("name") or "")[:150],
        "metrics": {
            "price": _num(pick("price", "min_price", "price_value")),
            "original_price": _num(pick("original_price", "market_price",
                                        "origin_price")),
            "sold": _num(pick("sold", "sold_count", "sales", "sold_cnt")),
            "stock": _num(pick("stock", "stock_count", "available_stock")),
            "discount": _num(pick("discount", "discount_percent")),
        },
        "extra": {
            "region": region,
            "image_url": first_url_safe(node.get("cover")
                                        or node.get("image")
                                        or node.get("img_url")),
            "category_id": node.get("category_id"),
            "raw_keys": sorted(node)[:40],
        },
    }


def first_url_safe(v):
    """取 {uri,url_list} 结构的第一个 URL，或裸字符串。非 URL 一律 None。"""
    if isinstance(v, dict):
        ul = v.get("url_list") or v.get("urlList")
        if isinstance(ul, list) and ul:
            return ul[0]
    elif isinstance(v, str) and v.startswith("http"):
        return v
    return None


def norm_live(node: dict, region: str) -> Optional[dict]:
    """TikTok 直播间条目。

    2026-11-06 新增（直播运营岗数据前置）。**实测结构**：
      路径 `data.data[].lives` —— `lives` 是**单个直播对象**（不是 list 容器）。

      ⚠️ **印尼站与 US 站字段布局不同**（实测）：
        · ID 站：`room_id` / `nickname` / `unique_id` / `uid` / `room_cover`
          **直接挂在 live 对象上**
        · US 站：同样这些字段**挂在 `author` 子对象下**
      → 取值时**两处都查**，否则一站全 null。

      另：US 站的 live 对象上有 `products_info` / `right_products` /
      `bottom_products` 三个商品相关字段（印尼站为 None）。
    """
    if not isinstance(node, dict):
        return None
    au = node.get("author") or {}

    def pick(*keys):
        """两站兼容取值：live 顶层优先，其次 author。"""
        for src in (node, au):
            for k in keys:
                v = src.get(k)
                if v is not None:
                    return v
        return None

    rid = pick("room_id", "room_id_str", "roomId")
    if not rid:
        return None
    nickname = pick("nickname") or pick("room_title") or ""
    room_title = pick("room_title")

    def first_url(v):
        if isinstance(v, dict):
            ul = v.get("url_list") or v.get("urlList")
            if isinstance(ul, list) and ul:
                return ul[0]
        elif isinstance(v, str) and v:
            return v
        return None

    cover = (first_url(pick("room_cover")) or first_url(pick("cover_url"))
             or first_url(pick("avatar_larger")))

    # 粉丝数：`followers_detail` 可能是 dict（含 follower_count）或数字
    fd = pick("followers_detail")
    if isinstance(fd, dict):
        fol = _num(fd.get("follower_count") or fd.get("count")
                   or fd.get("followerCount"))
    else:
        fol = _num(fd)
    if fol is None:
        fol = _num(pick("follower_count", "followerCount"))

    return {
        "entity_id": str(rid),
        "title": str(nickname)[:120],
        "metrics": {
            "viewer_count": _num(pick("user_count", "viewer_count",
                                     "viewerCount", "watcher_count",
                                     "current_user_count")),
            "live_duration": _num(pick("live_duration", "duration",
                                       "liveDuration")),
            "title_count": _num(pick("title_count")),
            "follower_count": fol,
            # **2026-11-06 实测：直播侧唯二的可用量化指标** ——
            #   products_count        = 该直播间挂载的商品数
            #   is_live_has_products  = 是否在带货（0/1）
            # 直播搜索端点**不返回同时在看人数**（user_count/viewer_count 全 null），
            # → 直播热度指标只能靠挂车字段间接推断，**不得编造观看人数**。
            "products_count": _num(pick("products_count", "productsCount")),
            # **布尔字段单独保留**：`_num` 按纪律排除 bool（避免把 True 当 1 参与均值），
            # 但「是否在带货」本身是有效的分类信号 → 保留为 True/False 原值。
            "is_live_has_products": _bool(pick("is_live_has_products",
                                               "isLiveHasProducts")),
        },
        "extra": {
            "region": region,
            "room_title": room_title,
            "uid": pick("uid"),
            "unique_id": pick("unique_id", "uniqueId"),
            "nickname": nickname,
            "cover_url": cover,
            "search_user_desc": pick("search_user_desc"),
            "has_products_info": bool(pick("products_info", "right_products",
                                           "bottom_products")),
            "tags": pick("cha_list", "user_tags"),
            "raw_keys": sorted(node)[:40],
        },
    }

def norm_aweme(node: dict, region: str) -> Optional[dict]:
    """抖音/TikTok 作品节点 → 统一记录。

    两种实测形态：
      · app API：`aweme_id` + `statistics{play_count,...}` + `create_time`
      · web API：`id` + `stats{playCount,...}` + `createTime`（首页流/探索页）
    """
    aweme_id = node.get("aweme_id")
    st = node.get("statistics") or {}
    create_time = node.get("create_time")
    if not aweme_id:
        # web 形态：id + stats
        if not node.get("id") or not ("desc" in node or "stats" in node):
            return None
        aweme_id = node.get("id")
        st = node.get("stats") or st
        create_time = node.get("createTime")
    if not aweme_id:
        return None
    st = st or {}
    au = node.get("author") or {}
    vid = node.get("video") or {}
    dur = vid.get("duration") if isinstance(vid, dict) else None
    web_shape = not node.get("aweme_id")

    # **封面图 URL（2026-11-06 新增，美工岗数据前置）**
    # 实测 app 形态封面在 `video.cover` / `video.origin_cover` / `video.dynamic_cover`，
    # 结构为 `{'url_list': [...], 'height':..., 'width':...}`。
    # → 取第一个可用的 url_list 首条；**只取 URL，不下载图片**（保持零网络与合规）。
    cover_url = None
    if isinstance(vid, dict):
        for ck in ("cover", "origin_cover", "dynamic_cover"):
            cv = vid.get(ck)
            if isinstance(cv, dict):
                ul = cv.get("url_list") or cv.get("urlList")
                if isinstance(ul, list) and ul:
                    cover_url = ul[0]
                    break
            elif isinstance(cv, str) and cv:
                cover_url = cv
                break

    # **单位判定（2026-11-06 实测订正）**：
    #   app 形态的 `video.duration` 原始值**已经是毫秒**，此前注释误判为「秒」并
    #   乘了 1000，导致 9934（真值 9.9 秒）被记成 9,934,000 ms（≈2.8 小时）。
    #   证据一：抖音 app 样本 min=6734 / max=483934，按毫秒读是 6.7–484 秒（合理）；
    #           按秒读是 1.9 小时–5.6 天（不合理）。
    #   证据二：TikTok app 样本 204 个取值 max=562700，按毫秒读是 562 秒（合理）。
    #   证据三：历史榜单快照 duration_ms 同样是 7067–299724 量级，与 app 原始值同源同单位。
    #   → app 形态直接取原值即为毫秒，**不再乘 1000**。
    # web 形态的 video.duration 实测出现 0/153/200 这类值（首页流、探索页），
    # 单位无法确认，**不产出时长**，宁可留空也不给错单位。
    duration_ms = None if web_shape else (
        int(dur) if isinstance(dur, (int, float)) else None)

    def pick(*keys):
        for k in keys:
            v = st.get(k)
            if v is not None:
                return v
        return None

    return {
        "entity_id": str(aweme_id),
        "title": (node.get("desc") or "")[:200],
        "metrics": {
            "play_count": _num(pick("play_count", "playCount")),
            "digg_count": _num(pick("digg_count", "diggCount")),
            "comment_count": _num(pick("comment_count", "commentCount")),
            "collect_count": _num(pick("collect_count", "collectCount")),
            "share_count": _num(pick("share_count", "shareCount",
                                     "repost_count", "repostCount")),
            "duration_ms": duration_ms,
        },
        "extra": {
            "region": region,
            "create_time": create_time,
            "author_id": au.get("unique_id") or au.get("uid") or au.get("sec_uid"),
            "author_name": au.get("nickname"),
            "author_followers": _num(au.get("follower_count")
                                     or au.get("followerCount")),
            "region_of_author": au.get("region"),
            "shape": "web" if web_shape else "app",
            "cover_url": cover_url,
        },
    }


def norm_challenge(node: dict, region: str) -> Optional[dict]:
    """话题/挑战。

    抖音垂类话题搜索（`fetch_challenge_search_v2`）的指标**直接挂在
    `challenge_info` 节点上**，不在 `statistics`：
        challenge_info.view_count = 3310662
        challenge_info.user_count  = 761
    此前只读 `statistics`/`stats` → 60 条记录指标全为 null（审计 Q-P5_challenge_search-nonzero 失败）。
    → 指标源改为 `challenge_info` 优先，再回落 `statistics`/`stats`。
    """
    if not (node.get("challenge_id") or node.get("cid")):
        return None
    ci = node.get("challenge_info") or {}
    st = node.get("statistics") or node.get("stats") or {}

    def pick(*keys):
        for src in (ci, st, node):
            for k in keys:
                v = src.get(k)
                if v is not None:
                    return v
        return None

    return {
        "entity_id": str(node.get("challenge_id") or node.get("cid")),
        "title": (node.get("title") or node.get("cha_name")
                  or ci.get("cha_name") or "")[:200],
        "metrics": {
            "play_count": _num(pick("view_count", "play_count")),
            "video_count": _num(pick("video_count", "publish_count")),
            "follower_count": _num(pick("user_count", "follower_count")),
        },
        "extra": {"region": region, "challenge_info": ci or None},
    }


def norm_creator(node: dict, region: str) -> Optional[dict]:
    """星图达人条目：star_id + attribute_datas（粉丝/报价/CPM）+ task_infos（价格档）。"""
    sid = node.get("star_id") or (node.get("attribute_datas") or {}).get("id")
    if not sid:
        return None
    ad = node.get("attribute_datas") or {}
    prices = []
    for ti in (node.get("task_infos") or []):
        for pi in (ti.get("price_infos") or []):
            if pi.get("price") is not None:
                prices.append({"price": pi.get("price"),
                               "task_category": ti.get("task_category"),
                               "video_type": pi.get("video_type"),
                               "start_time": pi.get("start_time")})
    return {
        "entity_id": str(sid),
        "title": (ad.get("nick_name") or "")[:120],
        "metrics": {
            "follower_count": _num(ad.get("follower")),
            "cpm_suggest_price": _num(ad.get("assign_cpm_suggest_price")),
            "price_20_60": _num(ad.get("price_20_60")),
            "price_infos_count": len(prices),
            "ecom_video_product_num_30d": _num(ad.get("ecom_video_product_num_30d")),
            "fans_increment_within_30d": _num(ad.get("fans_increment_within_30d")),
            "core_user_id": ad.get("core_user_id"),
            "is_superstar": ad.get("is_superstar"),
            "ecom_level": ad.get("author_ecom_level"),
        },
        "extra": {"region": region, "star_id": str(sid),
                  "price_infos": prices,
                  "city": ad.get("city"), "gender": ad.get("gender"),
                  "ecom_enabled": ad.get("e_commerce_enable")},
    }


def norm_creator_info(node: dict, region: str) -> Optional[dict]:
    """星图达人详情（`get_author_base_info`）。

    实测字段名与旧假设完全不同（此前 24 条记录 metrics 全空）：
      · 标识：`unique_id` / `sec_uid` / `core_user_id` / `id`（**没有** `o_author_id`）
      · 粉丝：`follower`（不是 `follower_count`）
      · 昵称：`nick_name`（不是 `nickname`）
      · 均播：`avg_play`、最低报价：`lowest_price`、等级：`grade`
      · 地域：`city`/`province`、性别：`gender`、电商：`e_commerce_enable`
    旧代码读的 `follower_count`/`total_favorited`/`aweme_count`/`nickname`
    在真实响应里**一个都不存在** → 指标全 null。
    """
    oid = (node.get("unique_id") or node.get("core_user_id")
           or node.get("id") or node.get("sec_uid"))
    if not oid:
        return None
    m = {k: _num(node.get(k)) for k in
         ("follower", "avg_play", "lowest_price", "total_favorited",
          "aweme_count", "video_count", "favoriting_count", "heart_count",
          "comment_count", "share_count")}
    # 兼容旧字段名（不同端点版本可能并存）
    for old, new in (("follower_count", "follower"),):
        if m.get(new) is None and node.get(old) is not None:
            m[new] = _num(node.get(old))
    metrics = {k: v for k, v in m.items() if v is not None}
    nickname = node.get("nick_name") or node.get("nickname") or ""
    return {"entity_id": str(oid), "title": nickname[:120],
            "metrics": metrics,
            "extra": {"region": region,
                      "core_user_id": node.get("core_user_id"),
                      "sec_uid": node.get("sec_uid"),
                      "unique_id": node.get("unique_id"),
                      "city": node.get("city"), "province": node.get("province"),
                      "gender": node.get("gender"),
                      "ecom_enabled": node.get("e_commerce_enable"),
                      "author_type": node.get("author_type"),
                      "grade": node.get("grade"),
                      "is_star": node.get("is_star"),
                      "raw_keys": sorted(node)[:40]}}


def norm_kol_price(node: dict, region: str) -> Optional[dict]:
    prices = []
    def collect(o, depth=0):
        if depth > 6:
            return
        if isinstance(o, dict):
            if "price" in o and isinstance(o.get("price"), (int, float)):
                prices.append({"price": o.get("price"),
                               "task_category": o.get("task_category"),
                               "video_type": o.get("video_type")})
            for v in o.values():
                collect(v, depth + 1)
        elif isinstance(o, list):
            for v in o:
                collect(v, depth + 1)
    collect(node)
    if not prices:
        return None
    return {"entity_id": str(node.get("kolId") or node.get("kol_id") or "kol"),
            "title": "", "metrics": {"price_min": min(p["price"] for p in prices),
                                     "price_max": max(p["price"] for p in prices),
                                     "price_records": len(prices)},
            "extra": {"region": region, "price_infos": prices[:20]}}


def norm_trend(node: dict, region: str) -> Optional[dict]:
    kw = node.get("keyword")
    if not kw:
        return None
    series = node.get("hot_list") or []
    idx = [_num(x.get("index")) for x in series if isinstance(x, dict)]
    idx = [x for x in idx if x is not None]
    return {
        "entity_id": str(kw),
        "title": str(kw),
        "metrics": {
            "points": len(idx),
            "index_max": max(idx) if idx else None,
            "index_min": min(idx) if idx else None,
            "index_mean": round(sum(idx) / len(idx), 2) if idx else None,
            "average": _num(node.get("average")),
            "last_average": _num(node.get("last_average")),
            "last_year_average": _num(node.get("last_year_average")),
        },
        "extra": {"region": region, "series": series[:60]},
    }


def norm_trend_word(node: dict, region: str) -> Optional[dict]:
    w = (node.get("word") or node.get("keyword") or node.get("trend")
         or node.get("trendingSearchWord"))
    if not w:
        return None
    return {"entity_id": str(w), "title": str(w),
            "metrics": {"heat": _num(node.get("heat") or node.get("hot_value"))},
            "extra": {"region": region,
                      "word_type": node.get("trendingSearchWordType"),
                      "raw_keys": sorted(node)[:20]}}


NORMS = {"aweme": norm_aweme, "challenge": norm_challenge, "creator": norm_creator,
         "creator_info": norm_creator_info, "kol_price": norm_kol_price,
         "trend": norm_trend, "trend_word": norm_trend_word, "live": norm_live,
         "live_product": norm_live_product}


def normalize(payload: Any, kind: str, region: str) -> List[dict]:
    """按归一器把响应拆成标准记录。"""
    fn = NORMS[kind]
    out, seen = [], set()
    if kind == "creator":
        data = payload.get("data") if isinstance(payload, dict) else None
        for a in (data or {}).get("authors", []) if isinstance(data, dict) else []:
            rec = fn(a, region)
            if rec and rec["entity_id"] not in seen:
                seen.add(rec["entity_id"])
                out.append(rec)
        return out
    if kind == "trend":
        data = payload.get("data") if isinstance(payload, dict) else None
        for t in ((data or {}).get("hot_list", []) if isinstance(data, dict) else []):
            rec = fn(t, region)
            if rec and rec["entity_id"] not in seen:
                seen.add(rec["entity_id"])
                out.append(rec)
        return out
    if kind == "live":
        # **live 归一化必须显式走 `data.data[].lives` 路径**，不能用通用 walk：
        #   `room_id` / `nickname` / `uid` 在 **`author` 子对象**下，
        #   而 `products_count` / `is_live_has_products` 在 **live 对象顶层**。
        # walk 深度优先会先命中 author 节点 → 顶层挂车字段全部丢失
        # （实测：products_count 全部为 null）。
        # → 直接遍历 data.data[]，把每个元素当 live 对象处理。
        data = payload.get("data") if isinstance(payload, dict) else None
        inner = data.get("data") if isinstance(data, dict) else None
        for item in (inner if isinstance(inner, list) else []):
            lv = item.get("lives") if isinstance(item, dict) else None
            if not isinstance(lv, dict):
                continue
            try:
                rec = fn(lv, region)
            except Exception:  # noqa: BLE001
                rec = None
            if rec and rec["entity_id"] not in seen:
                seen.add(rec["entity_id"])
                out.append(rec)
        return out
    for node in walk(payload):
        if kind == "aweme" and not (node.get("aweme_id")
                                    or (node.get("id") and ("desc" in node or "stats" in node))):
            continue
        # 字段不完整的 aweme 节点直接跳过（浅层壳节点会让深层真节点被挤掉）
        if kind == "aweme" and not _aweme_complete(node):
            continue
        if kind == "challenge" and not (node.get("challenge_id") or node.get("cid")):
            continue
        if kind == "live" and not (node.get("room_id") or node.get("room_id_str")
                                   or node.get("roomId")):
            continue
        if kind == "live_product" and not (
                node.get("product_id") or node.get("productId")
                or node.get("item_id")
                or (node.get("id") and ("price" in node or "title" in node))):
            continue
        if kind == "trend_word" and not (node.get("word") or node.get("keyword")
                                         or node.get("trendingSearchWord")):
            continue
        try:
            rec = fn(node, region)
        except Exception:  # noqa: BLE001
            rec = None
        if rec and rec["entity_id"] not in seen:
            seen.add(rec["entity_id"])
            out.append(rec)
    return out


# ---------------------------------------------------------------- 探针
def probe(token: str, out_dir: pathlib.Path) -> int:
    print(f"\n=== 阶段 A · 端点探针（{len(ENDPOINTS)} 个端点各 1 次）===")
    today = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    contract: Dict[str, Any] = {"probed_at": now_cst(), "endpoints": {}}
    for name, spec in ENDPOINTS.items():
        params = dict(spec["probe"])
        if name == "P7_keyword_trend":
            params["start_date"] = start
            params["end_date"] = today
        r = call(spec["path"], params, token, spec["method"], spec["where"])
        recs = normalize(r["data"], spec["norm"], "ID") if r["ok"] else []
        contract["endpoints"][name] = {
            "path": spec["path"], "method": spec["method"], "price": spec["price"],
            "probe_params": params, "ok": r["ok"], "status": r["status"],
            "error": r.get("error"), "record_count": len(recs),
            "sample_keys": sorted(recs[0].keys()) if recs else [],
        }
        mark = "✅" if r["ok"] and recs else ("⚠️" if r["ok"] else "❌")
        print(f"  {mark} {name:24s} {spec['path']:52s} status={r['status']} "
              f"记录={len(recs)}" + (f" err={str(r['error'])[:50]}" if not r["ok"] else ""))
        time.sleep(SLEEP)
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "_tikhub_probe_contract.json"
    p.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")
    ok_n = sum(1 for v in contract["endpoints"].values() if v["ok"] and v["record_count"] > 0)
    print(f"\n探针契约：{p}｜有数据端点 {ok_n}/{len(ENDPOINTS)}")
    return 0


# ---------------------------------------------------------------- 满跑
class Runner:
    def __init__(self, token: str, cap: float, raw_dir: pathlib.Path,
                 max_minutes: float = 45.0):
        self.token = token
        self.cap = cap
        self.spend = 0.0
        self.calls = 0
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.max_minutes = max_minutes
        self.start = time.time()
        self.next_cursor: Optional[int] = None
        self.consec_fail = 0
        self.blocked: set = set()

    @property
    def elapsed_min(self) -> float:
        return (time.time() - self.start) / 60.0

    def hit(self, name: str, params: Dict[str, Any], region: str) -> List[dict]:
        spec = ENDPOINTS[name]
        if self.spend + spec["price"] > self.cap:
            print(f"  🛑 预算熔断：已花 ${self.spend:.3f}，本包 {name} 不再调用")
            return []
        if self.elapsed_min > self.max_minutes:
            print(f"  🛑 时间盒熔断：已跑 {self.elapsed_min:.1f} 分钟，"
                  f"本包 {name} 不再调用")
            return []
        r = call(spec["path"], params, self.token, spec["method"], spec["where"])
        self.calls += 1
        self.spend += spec["price"]
        time.sleep(SLEEP)
        if not r["ok"]:
            self.consec_fail += 1
            print(f"    ❌ {name}: {str(r.get('error'))[:80]}")
            # **连续失败熔断**：同一端点连错 8 次基本是参数契约错了
            # （不是网络抖动），继续调只是白烧钱 → 立刻停掉这个包。
            if self.consec_fail >= 8:
                print(f"    🛑 {name} 连续失败 {self.consec_fail} 次，判定参数契约错误，"
                      f"本包停止调用")
                self.blocked.add(name)
            return []
        self.consec_fail = 0
        # 原始响应落盘（可缓存 24h，改口径不用再花钱）
        h = abs(hash(json.dumps(params, sort_keys=True, ensure_ascii=False))) % 10 ** 12
        (self.raw_dir / f"{name}_{h}.json").write_text(
            json.dumps(r["data"], ensure_ascii=False), encoding="utf-8")
        # 提取服务端下发的下一页游标（抖音搜索 v2 必需）
        self.next_cursor = _extract_next_cursor(r["data"])
        recs = normalize(r["data"], spec["norm"], region)
        # 溯源：把本次请求的检索词打到记录上，否则多关键词样本无法归因。
        # P7 用 keyword_list（逗号串），但它每个 hot_list 项自带 keyword，
        # 归一化后 entity_id 就是词本身，溯源天然完整——不要写逗号串。
        kw = params.get("keyword")
        if kw:
            for rec in recs:
                rec["_keyword"] = kw
        return recs


def main() -> int:
    ap = argparse.ArgumentParser(description="TikHub 充值后采样执行器")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--cap", type=float, default=5.0, help="累计花费上限（美元）")
    ap.add_argument("--max-minutes", type=float, default=45.0, help="墙钟时间盒（分钟）")
    args = ap.parse_args()

    out_dir = PIPE / "snapshots"
    if not args.probe and not args.apply:
        print("=== 充值后采样计划（零成本预览）===")
        tc = tsum = 0.0
        for name, v in ENDPOINTS.items():
            plan = RUN_PLAN.get(name, {})
            if "keywords" in plan and "pages" in plan:
                n = len(plan["keywords"]) * plan["pages"]
            elif plan.get("times"):
                n = plan["times"]
            elif plan.get("count"):
                n = plan["count"]
            elif plan.get("batches"):
                n = plan["batches"]
            else:
                n = len(plan.get("regions", [])) * plan.get("pages", 0)
            tc += n
            tsum += n * v["price"]
            print(f"  {name:24s} {v['path']:54s} {n:>4} 次 × ${v['price']}"
                  f" = ${n * v['price']:.3f}")
        print(f"  合计 {int(tc)} 次 / ${tsum:.2f}（实测 2–6s/次，约 {int(tc*4/60)} 分钟）")
        print("  时间盒与预算熔断：--max-minutes / --cap")
        return 0

    token = get_token()
    bal = balance_check(token)
    if bal is None:
        print("❌ 余额自检失败，不发起任何采样调用")
        return 1
    print(f"余额自检：balance={bal['balance']} free_credit={bal['free_credit']}")
    if (bal["balance"] or 0) <= 0:
        print("❌ balance=0，先在官网充值")
        return 1
    if args.probe:
        return probe(token, out_dir)

    day = today_cst()
    tiktok_out = out_dir / f"{day}-tikhub-content"
    douyin_out = out_dir / f"{day}-douyin-search"
    for d in (tiktok_out, douyin_out):
        d.mkdir(parents=True, exist_ok=True)
    raw_dir = PIPE / "data" / "_raw" / day
    runner = Runner(token, args.cap, raw_dir, args.max_minutes)

    aweme_ids: List[str] = []
    ch_ids: List[str] = []
    star_ids: List[str] = []
    writers: Dict[str, Any] = {}

    def write(ds: str, rec: dict, platform: str, endpoint: str, region: str,
              keyword: Optional[str] = None):
        out_dir_ = tiktok_out if platform == "tiktok" else douyin_out
        # 文件名后缀：TikTok 挂 region（分站不混算）；抖音是 CN 单站，**不挂**。
        if platform == "tiktok" and region:
            fp = out_dir_ / f"{ds}_{region}.jsonl"
        else:
            fp = out_dir_ / f"{ds}.jsonl"
        # entity_type：按**实际归一器**判定，不靠 dataset 名里是否含 "aweme"。
        # 此前 P5_video_search 名中无 "aweme" 子串 → 被误标成 challenge，
        # 而它明明是作品（aweme）数据集。
        etype = {"aweme": "aweme", "challenge": "challenge",
                 "creator": "creator", "creator_info": "creator",
                 "kol_price": "kol_price", "trend": "trend",
                 "trend_word": "trend_word", "live": "live",
                 "live_product": "live_product"}.get(
                     ENDPOINTS[ds]["norm"], "unknown")
        prov = {"fetched_at": now_cst(), "endpoint": endpoint, "region": region}
        if keyword:
            prov["keyword"] = keyword
        with fp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "schema_version": "1.0", "platform": platform, "dataset": ds,
                "entity_type": etype,
                "entity_id": rec["entity_id"], "title": rec["title"],
                "metrics": rec["metrics"], "extra": rec["extra"],
                "provenance": prov,
            }, ensure_ascii=False) + "\n")
        writers[ds] = writers.get(ds, 0) + 1

    def harvest(recs: List[dict]):
        for r in recs:
            if r["entity_id"] not in aweme_ids and len(aweme_ids) < 2000:
                aweme_ids.append(r["entity_id"])
            if r["entity_id"] not in ch_ids and len(ch_ids) < 1000:
                ch_ids.append(r["entity_id"])

    for name, plan in RUN_PLAN.items():
        if args.only and not name.startswith(args.only):
            continue
        spec = ENDPOINTS[name]
        if name in runner.blocked:
            print(f"\n  ▶ {name}：已因连续失败被熔断，跳过")
            continue
        print(f"\n  ▶ {name}（计划 {spec['calls']} 次 / ${spec['price']} 每次）")
        got: List[dict] = []
        # region_fixed：该端点的 region 枚举受限（如 hashtag 类只支持
        # US/GB/FR/JP/VN/SG），必须用受支持站位，否则每次调用都 400 白烧钱。
        # plan 里的 region 可**按包覆盖**（如 US 站直播单独一包）。
        region = plan.get("region") or spec.get("region_fixed") or "ID"
        if plan.get("region"):
            spec = dict(spec)
            spec["probe"] = dict(spec["probe"])
            spec["probe"]["region"] = plan["region"]
        if "keywords" in plan and "pages" in plan:
            for kw in plan["keywords"]:
                # 抖音搜索 v2 的 cursor 是**服务端下发的游标**，
                # 实测响应在 `data.business_config.next_page.cursor`（首个分页游标 = 8），
                # 且 `data.business_config.has_more` 标记是否还有下一页。
                # 此前用 `pg * count`（=20/40…）硬算游标 → 服务端不认 → HTTP 400，
                # 每关键词只拿到第 1 页（7~8 条），白花钱。
                # → 改为：首轮 cursor=0，拿到响应里的 next_page.cursor 继续翻页。
                cursor = 0
                for pg in range(plan["pages"]):
                    params = dict(spec["probe"])
                    params["keyword"] = kw
                    if "offset" in params:
                        # TikTok 侧是 offset 翻页（实测 offset=60/140/200 均正常）
                        params["offset"] = pg * int(params.get("count", 20))
                    if "cursor" in params:
                        params["cursor"] = cursor
                    if "page" in params:
                        params["page"] = pg + 1
                    r = runner.hit(name, params, region)
                    if not r:
                        break
                    got += r
                    if len(r) < 5:
                        break
                    nxt = runner.next_cursor
                    if not nxt:
                        break
                    cursor = nxt
        elif name == "P1_home_feed":
            for rg in plan["regions"]:
                for pg in range(plan["pages"]):
                    params = dict(spec["probe"])
                    params["region"] = rg
                    params["count"] = 20 + pg
                    r = runner.hit(name, params, rg)
                    if not r:
                        break
                    got += r
        elif name == "P1_one_video":
            for vid in aweme_ids[:plan["count"]]:
                params = dict(spec["probe"])
                params["aweme_id"] = vid
                got += runner.hit(name, params, region)
        elif name in ("P1_hashtag_detail", "P1_hashtag_video_list"):
            for hid in ch_ids[:plan["count"]]:
                params = dict(spec["probe"])
                params["ch_id"] = hid
                got += runner.hit(name, params, region)
        elif name == "P1_trending_words":
            for _ in range(plan["times"]):
                got += runner.hit(name, dict(spec["probe"]), region)
        elif name == "P1_explore_post":
            for i in range(plan["count"]):
                params = dict(spec["probe"])
                params["count"] = 16
                got += runner.hit(name, params, region)
        elif name == "P6_author_base_info":
            for sid in star_ids[:plan["count"]]:
                params = dict(spec["probe"])
                params["o_author_id"] = sid
                got += runner.hit(name, params, region)
        elif name == "P6_kol_price":
            for sid in star_ids[:plan["count"]]:
                params = dict(spec["probe"])
                params["kolId"] = sid
                got += runner.hit(name, params, region)
        elif name == "P7_keyword_trend":
            today = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=14)).strftime("%Y%m%d")
            per = plan["keywords_per_batch"]
            for b in range(plan["batches"]):
                batch = TREND_KEYWORDS[b * per:(b + 1) * per]
                if not batch:
                    break
                params = dict(spec["probe"])
                params["keyword_list"] = ",".join(batch)
                params["start_date"] = start
                params["end_date"] = today
                got += runner.hit(name, params, region)
        # 写盘
        platform = "tiktok" if name.startswith("P1_") else "douyin"
        # region 语义：抖音恒为 CN 单站；TikTok 按请求 region（分站不混算）
        if platform == "douyin":
            region = PLATFORM_REGION["douyin"]
        ds = name
        for rec in got:
            write(ds, rec, platform, spec["path"], region,
                  keyword=rec.get("_keyword"))
        if name == "P6_search_creator":
            for rec in got:
                if rec["entity_id"] not in star_ids:
                    star_ids.append(rec["entity_id"])
        if name.startswith("P1_"):
            harvest(got)
        print(f"    → {len(got)} 条记录，累计 {runner.calls} 次 / ${runner.spend:.3f}")

    print(f"\n合计调用 {runner.calls} 次，花费 ${runner.spend:.3f}")
    print("落盘：")
    for d in (tiktok_out, douyin_out):
        for f in sorted(d.glob("*.jsonl")):
            n = sum(1 for _ in f.open(encoding="utf-8"))
            print(f"   {f.relative_to(PIPE)}  {n} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
