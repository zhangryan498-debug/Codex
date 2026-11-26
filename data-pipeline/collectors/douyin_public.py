"""抖音公开数据采集器（零凭据）。

覆盖**不需要 a_bogus 签名**的抖音公开接口：
  · 热榜词条   /aweme/v1/web/hot/search/list/   → 选题、趋势、词库
  · 热榜视频   /web/api/v2/hotsearch/billboard/aweme/
  · 热门音乐   /web/api/v2/hotsearch/billboard/music/
  · 热词榜     /web/api/v2/hotsearch/billboard/word/

这些接口是抖店、巨量算数、编导选题、拆解分析四个方向的高频输入，
且**不需要登录态、不需要 Cookie、不需要任何 API Key**。

需要签名的接口（搜索 / 用户主页 / 评论 / 商品）走 dtk_selfhost 采集器，
或经 TikHub / Apify 等托管通道。

用法：
    python3 collectors/douyin_public.py                # 采全部
    python3 collectors/douyin_public.py --dataset hot_list
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from base import (  # noqa: E402
    append_manifest,
    http_get_json,
    make_record,
    now_cst,
    write_records,
)

COLLECTOR = "douyin_public"

ENDPOINTS = {
    # dataset: (接口名, URL 模板, 描述)
    "hot_list": (
        "热榜词条",
        "https://www.douyin.com/aweme/v1/web/hot/search/list/"
        "?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1",
    ),
    "hot_words": (
        "热词榜",
        "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/",
    ),
    "hot_music": (
        "热门音乐",
        "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/music/",
    ),
    "hot_aweme": (
        "热榜视频",
        "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/aweme/",
    ),
}

REFERER = "https://www.douyin.com/"


def _dig(obj, *path, default=None):
    cur = obj
    for p in path:
        if isinstance(cur, dict):
            cur = cur.get(p)
        elif isinstance(cur, list) and isinstance(p, int) and len(cur) > p:
            cur = cur[p]
        else:
            return default
        if cur is None:
            return default
    return cur


def parse_hot_list(payload) -> list:
    """热榜词条：data.word_list"""
    words = _dig(payload, "data", "word_list", default=None)
    if words is None:
        words = _dig(payload, "word_list", default=None)
    if not words:
        return []
    out = []
    for w in words:
        wid = w.get("word_id") or w.get("sentence_id") or w.get("word")
        out.append(
            make_record(
                platform="douyin",
                dataset="hot_list",
                entity_type="keyword",
                entity_id=str(wid),
                title=str(w.get("word") or ""),
                metrics={
                    "hot_value": w.get("hot_value"),
                    "position": w.get("position"),
                    "label": w.get("label"),
                    "event_time": w.get("event_time"),
                    "view_count": w.get("view_count"),
                    # 选题与选品最直接的三个量：话题视频量、讨论视频量、图文/文章数
                    "video_count": w.get("video_count"),
                    "discuss_video_count": w.get("discuss_video_count"),
                    "article_detail_count": w.get("article_detail_count"),
                    "max_rank": w.get("max_rank"),
                    "word_type": w.get("word_type"),
                },
                source_url=ENDPOINTS["hot_list"][1],
                extra={
                    "word_cover": _dig(w, "word_cover", "url_list", default=[]),
                    "sentence_id": w.get("sentence_id"),
                    "group_id": w.get("group_id"),
                    "sentence_tag": w.get("sentence_tag"),
                    "is_n1": w.get("is_n1"),
                    "label_url": w.get("label_url"),
                },
            )
        )
    return out


def parse_hot_words(payload) -> list:
    """热词榜：字段仅 word / hot_value / label，排名由列表顺序决定。"""
    words = _dig(payload, "word_list", default=[]) or []
    out = []
    for i, w in enumerate(words, 1):
        out.append(
            make_record(
                platform="douyin",
                dataset="hot_words",
                entity_type="keyword",
                entity_id=str(w.get("word") or ""),
                title=str(w.get("word") or ""),
                metrics={
                    "hot_value": w.get("hot_value"),
                    "position": i,
                    "label": w.get("label"),
                },
                source_url=ENDPOINTS["hot_words"][1],
                extra={"active_time": payload.get("active_time") if isinstance(payload, dict) else None},
            )
        )
    return out


def parse_hot_music(payload) -> list:
    """热门音乐：title/author/duration 嵌在 music_info 内，hot_value 在外层。"""
    items = _dig(payload, "music_list", default=[]) or []
    out = []
    for i, m in enumerate(items, 1):
        info = m.get("music_info") or {}
        out.append(
            make_record(
                platform="douyin",
                dataset="hot_music",
                entity_type="music",
                entity_id=str(info.get("id") or info.get("mid") or info.get("title") or ""),
                title=str(info.get("title") or ""),
                metrics={
                    "hot_value": m.get("hot_value"),
                    "position": i,
                    "label": m.get("label"),
                    "rank_diff": m.get("rank_diff"),
                    "duration": info.get("duration"),
                    "use_count": info.get("use_count"),
                },
                source_url=ENDPOINTS["hot_music"][1],
                extra={
                    "author": info.get("author"),
                    "status": info.get("status"),
                    "cover": _dig(info, "cover_large", "url_list", default=[]),
                    "play_url": _dig(info, "play_url", "url_list", default=[]),
                },
            )
        )
    return out


def parse_hot_aweme(payload) -> list:
    items = _dig(payload, "aweme_list", default=[]) or []
    out = []
    for a in items:
        stat = a.get("statistics") or {}
        author = a.get("author") or {}
        out.append(
            make_record(
                platform="douyin",
                dataset="hot_aweme",
                entity_type="aweme",
                entity_id=str(a.get("aweme_id") or ""),
                title=str(a.get("desc") or a.get("caption") or ""),
                metrics={
                    "digg_count": stat.get("digg_count"),
                    "comment_count": stat.get("comment_count"),
                    "share_count": stat.get("share_count"),
                    "collect_count": stat.get("collect_count"),
                    "play_count": stat.get("play_count"),
                    "duration_ms": a.get("duration"),
                    "create_time": a.get("create_time"),
                },
                source_url=ENDPOINTS["hot_aweme"][1],
                extra={
                    "author_nickname": author.get("nickname"),
                    "author_sec_uid": author.get("sec_uid"),
                    "author_uid": author.get("uid"),
                    "music_title": _dig(a, "music", "title"),
                },
            )
        )
    return out


PARSERS = {
    "hot_list": parse_hot_list,
    "hot_words": parse_hot_words,
    "hot_music": parse_hot_music,
    "hot_aweme": parse_hot_aweme,
}


def collect(dataset: str) -> list:
    name, url = ENDPOINTS[dataset]
    r = http_get_json(url, referer=REFERER, timeout=20, retries=3)
    if not r["ok"]:
        print(f"  ❌ {dataset:10s} {name}: {r['error']}")
        return []
    payload = r.get("json")
    if payload is None:
        print(f"  ❌ {dataset:10s} {name}: 返回非 JSON（{len(r['body'])} 字节）")
        return []
    recs = PARSERS[dataset](payload)
    for rec in recs:
        rec["provenance"]["collector"] = COLLECTOR
        rec["provenance"]["endpoint"] = name
    print(f"  ✅ {dataset:10s} {name}: {len(recs)} 条")
    return recs


def main():
    ap = argparse.ArgumentParser(description="抖音公开数据采集（零凭据）")
    ap.add_argument("--dataset", choices=sorted(ENDPOINTS), help="只采一个数据集")
    ap.add_argument("--date", help="归档日期 YYYY-MM-DD，默认今天")
    args = ap.parse_args()

    targets = [args.dataset] if args.dataset else list(ENDPOINTS)
    print(f"抖音公开数据采集 — {now_cst()}")
    all_recs, manifest = [], []
    for ds in targets:
        recs = collect(ds)
        all_recs.extend(recs)
        if recs:
            paths = write_records(recs, "douyin", ds, date_str=args.date)
            manifest.append({
                "platform": "douyin", "dataset": ds, "collector": COLLECTOR,
                "records": len(recs), "fetched_at": now_cst(),
                "credential": "none", "paths": paths,
                "source_urls": [ENDPOINTS[ds][1]],
            })
    if manifest:
        p = append_manifest(manifest)
        print(f"\nmanifest: {p}")
    print(f"合计 {len(all_recs)} 条")


if __name__ == "__main__":
    main()
