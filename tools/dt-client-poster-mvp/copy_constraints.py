"""Shared text limits for the CN and PH portrait poster layouts."""

from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class LineLimit:
    characters: int
    width_units: int


MAX_LINES = 2
_DEFAULT_GLYPH_UNITS = 8
SOURCE_TITLE_LIMIT = 46
LAYER_FIELDS = {"T01": "title", "T02": "subtitle"}
_FIELDS = {
    "title": LineLimit(28, 112),
    "subtitle": LineLimit(40, 168),
}
_LABELS = {"title": "标题", "subtitle": "副标题"}

# Eighths of an em, rounded up from the maximum measured Arial/YaHei
# advance or right ink at 25/37 px. Other printable glyphs cost one em.
# 14/21 em leave room inside the respective 556 px drawing widths.
# This is an early filter. The PSD engine still checks actual shaping and ink.
_ASCII_WIDTH_CLASSES = {
    3: " !'(),.:;I[]`ijlt{|}",
    4: '"*-/J\\frsz',
    5: "$0123456789?FLTZ_acehknuvxy",
    6: "#+<=>ABCEKPRSUVXY^bdgopq~",
    7: "DGHNOQw",
    8: "%&Mm",
    9: "@W",
}
_ASCII_WIDTHS = {
    character: units
    for units, characters in _ASCII_WIDTH_CLASSES.items()
    for character in characters
}


class CopyConstraintError(ValueError):
    """Copy cannot fit the supported portrait text contract."""


def public_limits():
    return {
        field: {
            "max_lines": MAX_LINES,
            "max_line_characters": bound.characters,
            "max_characters": MAX_LINES * bound.characters + (MAX_LINES - 1),
            "max_line_units": bound.width_units,
        }
        for field, bound in _FIELDS.items()
    }


def copy_schema():
    properties = {}
    for field, limits in public_limits().items():
        count = limits["max_line_characters"]
        line = rf"[^\r\n]{{1,{count}}}"
        properties[field] = {
            "type": "string",
            "minLength": 1,
            "maxLength": limits["max_characters"],
            "pattern": rf"^{line}(\n{line})?$",
        }
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def model_width_guidance():
    groups = ", ".join(f"{units}: {characters!r}" for units, characters in _ASCII_WIDTH_CLASSES.items())
    title = _FIELDS["title"]
    subtitle = _FIELDS["subtitle"]
    return (
        "Use one or two non-empty lines per field. Put an explicit JSON newline only at a natural break. "
        f"Per-line maximums are {title.characters} characters and {title.width_units} width units for title, "
        f"{subtitle.characters} characters and {subtitle.width_units} width units for subtitle. "
        f"Prefer shorter copy to leave room. Every non-ASCII character costs {_DEFAULT_GLYPH_UNITS} units. "
        f"ASCII character groups (units: characters): {groups}. Never pad a line. "
        f"Chinese is limited to {title.width_units // _DEFAULT_GLYPH_UNITS} title or "
        f"{subtitle.width_units // _DEFAULT_GLYPH_UNITS} subtitle "
        "characters per line. Wide Latin letters need more room than small letters."
    )


def normalize_copy(value, field):
    bound = _FIELDS[field]
    label = _LABELS[field]
    if not isinstance(value, str):
        raise CopyConstraintError(f"{label}必须是文本。")
    lines = value.split("\n")
    if any(not line.strip() or not all(char.isprintable() for char in line) for line in lines):
        raise CopyConstraintError(f"{label}不能留空或包含不可显示字符。")
    if len(lines) > MAX_LINES or any(len(line) > bound.characters for line in lines):
        raise CopyConstraintError(
            f"{label}最多 {MAX_LINES} 行，每行最多 {bound.characters} 个字符。请缩短或在合适位置换行。"
        )
    normalized = [line.strip() for line in lines]
    if any(sum(_ASCII_WIDTHS.get(char, _DEFAULT_GLYPH_UNITS) for char in line) > bound.width_units for line in normalized):
        raise CopyConstraintError(f"{label}有一行过宽。请缩短文字或在合适位置换行后重试。")
    return "\n".join(normalized)
