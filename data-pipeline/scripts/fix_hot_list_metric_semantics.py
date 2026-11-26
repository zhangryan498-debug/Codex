#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校正 12 个 Agent 提示词中「热榜 video_count / discuss_video_count」的错误口径。

2026-11-05 实测结论（原始 API 逐字段比对）：
  - hot_value            900万-1200万   → 热度值，词条之间可横向比大小 ✅
  - video_count          1-15            → 热点快照关联样本视频条数，不是话题作品总量 ❌
  - discuss_video_count  恒为 1           → 平台占位字段，无信息量 ❌
  - article_detail_count 0               → 图文/文章数，几乎全 0 ❌

纪律：热榜只能用于①词条相对热度排序 ②话题方向（趋势/选题/配乐），
      任何量级判断必须走 L1 话题详情 / L2 商品榜 / L3 店铺数据。

用法：
    python3 fix_hot_list_metric_semantics.py            # 先 dry-run 看命中
    python3 fix_hot_list_metric_semantics.py --apply     # 真正写入
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_AGENTS = ROOT / "runtime-agents"
MIRROR_AGENTS = ROOT / "agents"

# --- 抖音两岗：直接描述字段用途，逐条替换 ---
DOUYIN_REPLACEMENTS = [
    (
        "- **L0 零凭据**：`python3 collectors/douyin_public.py --dataset hot_list` —— "
        "热榜词条带 `video_count`（话题视频量）与 `discuss_video_count`（讨论量），"
        "是抖音侧判断「话题是否在起量」的最快信号。",
        "- **L0 零凭据**：`python3 collectors/douyin_public.py --dataset hot_list` —— "
        "热榜词条带 `hot_value`（热度值，900 万–1200 万量级，**词条之间可横向比大小**）。"
        "⚠️ **不要用 `video_count` / `discuss_video_count` 判断量级**："
        "实测 `video_count` 恒在 1–15（是热点快照关联的样本条数，不是话题作品总量），"
        "`discuss_video_count` 恒为 1（平台占位字段，无信息量），"
        "**把这两个数当「有多少人在做」的证据是错的**。"
        "热榜只能用于：① 词条相对热度排序 ② 话题方向（趋势/选题/配乐）。"
        "真实量级必须走 L1 话题详情、L2 商品榜或 L3 店铺数据。",
    ),
    (
        "- **L0 零凭据**：`python3 collectors/douyin_public.py` 全量采集 —— "
        "热榜词条的热度、`video_count`、`discuss_video_count` 可用于判断活动话题的外部声量基线。",
        "- **L0 零凭据**：`python3 collectors/douyin_public.py` 全量采集 —— "
        "热榜词条的 `hot_value` 热度值可用于判断活动话题的**相对热度**。"
        "⚠️ **不要用 `video_count` / `discuss_video_count` 做声量基线**："
        "实测 `video_count` 恒在 1–15（热点快照关联样本条数，非话题作品总量），"
        "`discuss_video_count` 恒为 1（平台占位字段，无信息量），"
        "拿它们当量级证据会得到完全错误的结论。"
        "真实声量基线必须走 L1 话题详情、L2 达人参与数据或 L3 活动后实收。",
    ),
]

# --- TikTok 十岗：跨平台参照段统一替换 ---
TIKTOK_OLD = (
    "> **可立即使用的跨平台参照**：抖音侧零凭据通道已跑通（热榜词条带 `video_count` / `discuss_video_count`，\n"
    "> 热词榜带热度，热门音乐带作者与榜变），可用于**同品类在中文市场的热度与讨论密度**，\n"
    "> 作为 TikTok 选品/选题的**方向性交叉验证**。\n"
    "> **但抖音热榜绝不能当 TikTok 结论用**——两平台流量结构、内容形态与用户心智完全不同，\n"
    "> 跨平台数据只能提示「这个品类有没有讨论度」，不能替代 TikTok 侧的判断。"
)

TIKTOK_NEW = (
    "> **可立即使用的跨平台参照**：抖音侧零凭据通道已跑通（热榜词条带 `hot_value` 热度值，\n"
    "> 热词榜带热度，热门音乐带作者与榜变），可用于**同品类在中文市场的相对热度**，\n"
    "> 作为 TikTok 选品/选题的**方向性交叉验证**。\n"
    "> ⚠️ **抖音热榜的 `video_count` / `discuss_video_count` 不能当量级用**：\n"
    "> 实测 `video_count` 恒在 1–15（热点快照关联样本条数，非话题作品总量），\n"
    "> `discuss_video_count` 恒为 1（平台占位字段，无信息量）。\n"
    "> **但抖音热榜绝不能当 TikTok 结论用**——两平台流量结构、内容形态与用户心智完全不同，\n"
    "> 跨平台数据只能提示「这个品类有没有热度」，不能替代 TikTok 侧的判断。"
)

DOUYIN_AGENTS = ["douyin-selection", "douyin-campaign-ops"]


def patch_file(path: pathlib.Path, replacements, dry: bool) -> int:
    text = path.read_text(encoding="utf-8")
    hits = 0
    for old, new in replacements:
        if old in text:
            hits += text.count(old)
            text = text.replace(old, new)
    if hits and not dry:
        path.write_text(text, encoding="utf-8")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = ap.parse_args()
    dry = not args.apply

    roots = [RUNTIME_AGENTS]
    if MIRROR_AGENTS.exists():
        roots.append(MIRROR_AGENTS)

    total = 0
    for root in roots:
        for agent_dir in sorted(root.iterdir()):
            if not agent_dir.is_dir():
                continue
            md = agent_dir / "agent.md"
            if not md.exists():
                continue
            name = agent_dir.name
            if name in DOUYIN_AGENTS:
                reps = DOUYIN_REPLACEMENTS
            elif name.startswith("tiktok-"):
                reps = [(TIKTOK_OLD, TIKTOK_NEW)]
            else:
                continue
            hits = patch_file(md, reps, dry)
            if hits:
                total += hits
                print(f"{'DRY' if dry else 'APPLY'}  {root.name}/{name}  替换 {hits} 处")
            else:
                print(f"{'DRY' if dry else 'APPLY'}  {root.name}/{name}  未命中（跳过）")

    print(f"\n合计替换 {total} 处  mode={'dry-run' if dry else 'apply'}")
    if total == 0 and not dry:
        print("⚠️ 一处都没命中，请检查原文是否已改过", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
