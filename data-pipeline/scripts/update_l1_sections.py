#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把「L1 访客签名」的描述更新为 2026-11-05 实测后的能力边界。

变化要点：
1. L1 **不再需要用户给分享链接**——推荐流 `module/feed` 自举，每页 15 条真作品，
   可无限翻页，作品里带作者 sec_uid、话题 cid（经 related 的 cha_list）。
2. L1 **不再需要等「先调一次 profile」**——缺的是 `UIFID_TEMP` 预热，采集器已内置。
3. 新增能力：作品详情（单条权威统计）、话题真实体量（view_count/user_count）、
   创作者作品列表、评论。
4. 搜索端点仍需登录；热榜视频榜仍返回空列表——这两个不用试。

用法：
    python3 update_l1_sections.py            # dry-run
    python3 update_l1_sections.py --apply
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime-agents"
MIRROR = ROOT / "agents"

# 通用能力段，插在各岗位 L1 那行之后（替换原 L1 那一行）
COMMON = (
    "  · **推荐流入口**：`/aweme/v1/web/module/feed/` —— **不需要任何种子 ID、不需要登录、不需要用户给链接**，"
    "每页 15 条真作品、可无限翻页，作品自带作者 `sec_uid`、话题、互动数据。\n"
    "  · **话题真实体量**：`challenge/detail` 给 `view_count` / `user_count` —— "
    "这是唯一能回答「这个话题有多大、有多挤」的公开端点（L0 热榜的 `video_count` 恒为 1–15，"
    "**不能当量级用**）。\n"
    "  · **作品详情 / 创作者作品列表**：`aweme/detail`、`aweme/post` —— "
    "**需先预热拿到 `UIFID_TEMP`**（采集器已内置 `warmup()`，缺的是预热不是算法）。\n"
    "  · **评论**：`comment/list` —— 拆解岗拿用户真实顾虑的一手素材。\n"
    "  · **搜索端点需登录**（status 2483），热榜视频榜返回空列表——这两条路不通，别再试。"
)

# 各岗位原 L1 行 → 新 L1 行
REPLACEMENTS = {
    "douyin-campaign-ops": (
        "- **L1 访客签名**：话题详情与相关作品量，反映话题竞争度。",
        "- **L1 访客签名**：话题详情与相关作品量，反映话题竞争度。\n" + COMMON,
    ),
    "douyin-competitor-analyst": (
        "- **L1 访客签名**：作者主页（粉丝/获赞/作品数/简介/商业等级）、话题详情、相关作品——竞品账号侧的主数据。",
        "- **L1 访客签名**：作者主页（粉丝/获赞/作品数/简介/商业等级）、话题详情、相关作品——竞品账号侧的主数据。\n" + COMMON,
    ),
    "douyin-lead": (
        "- **L1 访客签名**：给定抖音分享链接即可解锁作者主页、话题、相关作品、评论。",
        "- **L1 访客签名**：**已完全跑通，不再需要用户提供分享链接**——作者主页、话题、相关作品、评论全部可自动采集。\n" + COMMON,
    ),
    "douyin-live-ops": (
        "- **L1 访客签名**：直播状态、观众榜、直播房间信息。",
        "- **L1 访客签名**：直播状态、观众榜、直播房间信息。\n" + COMMON,
    ),
    "douyin-selection": (
        "- **L1 访客签名**：给定一个抖音商品/话题分享链接，可取该商品相关作品与话题详情，评估内容是否已围绕该品铺开。",
        "- **L1 访客签名**：**已完全跑通，不再需要用户提供分享链接**——可取商品相关作品、话题详情与真实体量，"
        "评估内容是否已围绕该品铺开。\n" + COMMON,
    ),
    "douyin-shop-ops": (
        "- **L1 访客签名**：话题详情、相关作品可反映该关键词下真实的内容供给密度。",
        "- **L1 访客签名**：话题详情、相关作品可反映该关键词下真实的内容供给密度。\n" + COMMON,
    ),
    "douyin-video-director": (
        "- **L1 访客签名**：作者主页、话题详情、相关作品、评论——这是拆解与借鉴的主数据。",
        "- **L1 访客签名**：作者主页、话题详情、相关作品、评论——这是拆解与借鉴的主数据。\n" + COMMON,
    ),
    "douyin-video-teardown": (
        "- **L1 访客签名（拆解岗的主通道）**：作者主页、相关作品、评论列表、话题详情——拆解所需的「作品 + 结构 + 互动数据」全部在这里。",
        "- **L1 访客签名（拆解岗的主通道）**：作者主页、相关作品、评论列表、话题详情——拆解所需的「作品 + 结构 + 互动数据」全部在这里。\n" + COMMON,
    ),
    "douyin-visual-designer": (
        "- **L1 访客签名**：话题详情与相关作品，可看该话题下真实在用的视觉样式。",
        "- **L1 访客签名**：话题详情与相关作品，可看该话题下真实在用的视觉样式。\n" + COMMON,
    ),
    "douyin-ad-buyer": (
        "- **L1/L2**：抖音投放相关端点（趋势、内容消耗/互动趋势）；TikTok 侧 `tiktok/ads/*` 与 `tiktok/web/fetch_content_*_trend`。",
        "- **L1/L2**：抖音投放相关端点（趋势、内容消耗/互动趋势）；TikTok 侧 `tiktok/ads/*` 与 `tiktok/web/fetch_content_*_trend`。\n"
        "  · 投流前的内容与达人筛选依据（话题真实体量、相关作品互动量、创作者粉丝量级）走 L1：\n"
        + COMMON,
    ),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    dry = not args.apply

    roots = [RUNTIME] + ([MIRROR] if MIRROR.exists() else [])
    total = 0
    for root in roots:
        for name, (old, new) in REPLACEMENTS.items():
            md = root / name / "agent.md"
            if not md.exists():
                continue
            text = md.read_text(encoding="utf-8")
            if old not in text:
                print(f"{'DRY' if dry else 'APPLY'}  {root.name}/{name}  未命中")
                continue
            text = text.replace(old, new, 1)
            if not dry:
                md.write_text(text, encoding="utf-8")
            total += 1
            print(f"{'DRY' if dry else 'APPLY'}  {root.name}/{name}  ✅")

    print(f"\n处理 {total} 个 agent.md  mode={'dry-run' if dry else 'apply'}")
    if total == 0 and not dry:
        print("⚠️ 一处都没命中", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
