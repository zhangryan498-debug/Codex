#!/usr/bin/env python3
"""发布前校验：检查 skill 包的结构完整性与关键约定是否齐全。

用法：
    python3 tools/verify_skill.py

退出码 0 表示通过，1 表示存在阻断项。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "SKILL.md",
    "SKILL.en.md",
    "README.md",
    "README.en.md",
    "LICENSE",
    "CONTRIBUTING.md",
    "references/compliance-rules.md",
    "examples/README.md",
    ".claude-plugin/marketplace.json",
]

# 每个文件必须出现的关键约定，缺失说明 SOP 被改坏了
REQUIRED_MARKERS = {
    "SKILL.md": [
        "name: ecommerce-visual-planning",
        "不要用于",
        "转化驱动力",
        "风格锁",
        "确认门 1",
        "确认门 2",
        "五维独立自审",
        "references/compliance-rules.md",
        "交付前检查清单",
    ],
    "SKILL.en.md": [
        "name: ecommerce-visual-planning",
        "Conversion Driver",
        "Style Lock",
        "Gate 1",
        "Gate 2",
        "Five-Dimension",
    ],
    "references/compliance-rules.md": [
        "【法】",
        "【推】",
        "【验】",
        "普通食品",
        "上线前检查清单",
    ],
}

# 四种驱动力必须在中英文 SKILL 里都有主图任务分配
DRIVERS_CN = ["视觉驱动型", "痛点驱动型", "理性驱动型", "情感价值型"]
DRIVERS_EN = ["Aesthetic-Driven", "Pain-Point-Driven", "Rational-Driven", "Emotion-Driven"]

errors: list[str] = []
warnings: list[str] = []


def read(rel: str) -> str | None:
    path = ROOT / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def check_files() -> dict[str, str]:
    contents: dict[str, str] = {}
    for rel in REQUIRED_FILES:
        text = read(rel)
        if text is None:
            errors.append(f"缺少文件：{rel}")
        else:
            contents[rel] = text
    return contents


def check_markers(contents: dict[str, str]) -> None:
    for rel, markers in REQUIRED_MARKERS.items():
        text = contents.get(rel)
        if text is None:
            continue
        for marker in markers:
            if marker not in text:
                errors.append(f"{rel} 缺少关键约定：{marker!r}")


def check_frontmatter(contents: dict[str, str]) -> None:
    for rel in ("SKILL.md", "SKILL.en.md"):
        text = contents.get(rel)
        if text is None:
            continue
        if not text.startswith("---\n"):
            errors.append(f"{rel} 缺少 YAML frontmatter")
            continue
        end = text.find("\n---\n", 4)
        if end == -1:
            errors.append(f"{rel} frontmatter 未闭合")
            continue
        fm = text[4:end]
        for key in ("name:", "description:"):
            if key not in fm:
                errors.append(f"{rel} frontmatter 缺少 {key}")
        # description 必须声明用途边界，否则模型会越界使用
        if "不用于" not in fm and "Do not use" not in fm:
            errors.append(f"{rel} frontmatter 的 description 未声明用途边界")


def check_drivers(contents: dict[str, str]) -> None:
    cn = contents.get("SKILL.md", "")
    en = contents.get("SKILL.en.md", "")
    for driver in DRIVERS_CN:
        if cn.count(driver) < 2:
            errors.append(f"SKILL.md 中 {driver!r} 出现少于 2 次，可能缺少主图或详情页分配")
    for driver in DRIVERS_EN:
        if driver not in en:
            errors.append(f"SKILL.en.md 缺少驱动力：{driver}")


def check_gates(contents: dict[str, str]) -> None:
    """两道确认门是这套 SOP 的核心，暂停句丢失等于门形同虚设。"""
    cn = contents.get("SKILL.md", "")
    if cn.count("暂停句") < 2:
        errors.append("SKILL.md 的两道确认门缺少暂停句（应出现 2 次「暂停句」）")


def check_marketplace(contents: dict[str, str]) -> None:
    raw = contents.get(".claude-plugin/marketplace.json")
    if raw is None:
        return
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"marketplace.json 不是合法 JSON：{exc}")
        return
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append("marketplace.json 缺少 plugins 数组")
        return
    for plugin in plugins:
        for key in ("name", "source", "description"):
            if key not in plugin:
                errors.append(f"marketplace.json 的 plugin 缺少字段：{key}")


def check_compliance_todo(contents: dict[str, str]) -> None:
    """品类层 F 是留给使用者填自己品类的，未填时提醒而非阻断。"""
    text = contents.get("references/compliance-rules.md", "")
    if "<你的品类>" in text:
        warnings.append("compliance-rules.md 的「品类层 F」尚未填写自己的品类规则")
    if re.search(r"【验】", text) is None:
        warnings.append("compliance-rules.md 没有任何【验】标记，平台规则可能未标注来源等级")


def main() -> int:
    contents = check_files()
    check_markers(contents)
    check_frontmatter(contents)
    check_drivers(contents)
    check_gates(contents)
    check_marketplace(contents)
    check_compliance_todo(contents)

    for warning in warnings:
        print(f"warn  {warning}")
    for error in errors:
        print(f"FAIL  {error}")

    if errors:
        print(f"\n{len(errors)} 项阻断，{len(warnings)} 项提醒。")
        return 1
    print(f"\n通过。{len(warnings)} 项提醒。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
