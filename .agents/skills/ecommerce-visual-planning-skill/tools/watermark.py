#!/usr/bin/env python3
"""给展示图加水印、压缩、归档到 assets/showcase/。

原图不入库——本脚本只输出压缩后的带水印版本。

用法：
    python3 tools/watermark.py _raw/kv1.png --name kv1-mood --text "概念图 · 请勿商用"
    python3 tools/watermark.py _raw/*.png --text "概念图"        # 批量，自动编号

选项：
    --text     水印文字，默认「概念图 · 请勿商用」
    --name     输出文件名（不含扩展名），单文件时可用
    --max-kb   目标体积上限，默认 200
    --long-edge 长边像素上限，默认 1200
    --opacity  水印不透明度 0-255，默认 46（约 18%）
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets" / "showcase"

# macOS 自带中文字体，按可用性顺序尝试
FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
]


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def add_watermark(img: Image.Image, text: str, opacity: int) -> Image.Image:
    """对角平铺水印。平铺而非单角标注，是因为单角的裁掉就没了。"""
    img = img.convert("RGBA")
    w, h = img.size

    font = load_font(max(18, w // 34))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    tw, th = right - left, bottom - top
    step_x, step_y = tw + w // 8, th + h // 10

    # 画在放大的临时层上再旋转，避免旋转后边角出现空白
    diag = int(math.hypot(w, h))
    tile = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    tdraw = ImageDraw.Draw(tile)
    for y in range(0, diag, step_y):
        offset = (y // step_y % 2) * (step_x // 2)  # 隔行错开
        for x in range(-step_x, diag, step_x):
            tdraw.text((x + offset, y), text, font=font, fill=(255, 255, 255, opacity))
            tdraw.text(
                (x + offset + 1, y + 1), text, font=font, fill=(0, 0, 0, opacity // 3)
            )

    tile = tile.rotate(30, resample=Image.BICUBIC)
    layer.paste(tile.crop(((diag - w) // 2, (diag - h) // 2, (diag + w) // 2, (diag + h) // 2)), (0, 0))
    return Image.alpha_composite(img, layer)


def save_under_limit(img: Image.Image, out: Path, max_kb: int) -> int:
    """逐步降质量直到满足体积上限。"""
    rgb = img.convert("RGB")
    for quality in (88, 82, 76, 70, 64, 58, 50):
        rgb.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        kb = out.stat().st_size // 1024
        if kb <= max_kb:
            return kb
    return out.stat().st_size // 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="+", type=Path)
    ap.add_argument("--text", default="概念图 · 请勿商用")
    ap.add_argument("--name")
    ap.add_argument("--max-kb", type=int, default=200)
    ap.add_argument("--long-edge", type=int, default=1200)
    ap.add_argument("--opacity", type=int, default=46)
    args = ap.parse_args()

    if args.name and len(args.sources) > 1:
        print("FAIL  --name 只能用于单个文件", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for i, src in enumerate(args.sources, 1):
        if not src.is_file():
            print(f"FAIL  找不到 {src}", file=sys.stderr)
            return 1

        img = Image.open(src)
        before = src.stat().st_size // 1024

        img.thumbnail((args.long_edge, args.long_edge), Image.LANCZOS)
        img = add_watermark(img, args.text, args.opacity)

        stem = args.name or f"{src.stem}"
        out = OUT_DIR / f"{stem}.jpg"
        after = save_under_limit(img, out, args.max_kb)

        flag = "" if after <= args.max_kb else "  ⚠️ 超出上限"
        print(f"{src.name}  {before}KB → {out.relative_to(ROOT)}  {after}KB{flag}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
