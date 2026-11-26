"""TikHub 凭据形状自检 —— 在发出任何计费请求之前拦住"被截断的 token"。

## 为什么需要它（2026-11-05 实测教训）

TikHub 的 API Token 是 **base64 形态**：字符集 `A-Za-z0-9+/`，尾部 `=` 补位，
**总长度必为 4 的倍数**。

本项目曾收到一把 **59 位**的 token（正确长度为 60）—— 复制环节丢了开头的字母 `i`。
服务端只做查表比对，查不到就回 `Invalid API token`，于是排查一度滑向
"是不是 MCP 没配好 / 域名不对 / header 名不对"这些**全是错误方向**的假设。

**教训**：拿到 401 的第一件事不是调 MCP 配置，而是**看 token 的形状**。
形状不对 → 去本机找原始副本（浏览器缓存 / 终端历史 / 下载目录），不要靠穷举猜。

## 用法

    from tikhub_auth import assert_token_shape, token_shape_report

    report = token_shape_report(token)   # 只体检，不抛错
    assert_token_shape(token)            # 形状不对直接 SystemExit
"""

from __future__ import annotations

import re
from typing import Any, Dict

# TikHub token 允许的字符集（标准 base64 字母表 + 补位符）
_B64_ALPHABET = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")

# 疑似"真实长度"的候选：合法 base64 总长度只能是 4 的倍数
_VALID_TOTAL_LENS = (40, 44, 48, 52, 56, 60, 64, 68, 72, 76, 80)


def token_shape_report(token: str) -> Dict[str, Any]:
    """体检一张 TikHub token，返回形状判定（不抛错）。"""
    raw = token or ""
    stripped = raw.strip()
    report: Dict[str, Any] = {
        "raw_length": len(raw),
        "length": len(stripped),
        "has_whitespace_edge": raw != raw.strip(),
        "alphabet_ok": bool(_B64_ALPHABET.match(stripped)),
        "ends_with_padding": stripped.endswith("="),
        "length_mod4": len(stripped) % 4,
        "data_len_mod4": len(stripped.rstrip("=")) % 4,
        "verdict": "ok",
        "reason": "",
    }
    if not stripped:
        report["verdict"] = "empty"
        report["reason"] = "token 为空"
        return report
    if report["has_whitespace_edge"]:
        report["verdict"] = "whitespace"
        report["reason"] = "首尾有空白字符，读取 .env 或复制时带进来了"
        return report
    if not report["alphabet_ok"]:
        report["verdict"] = "alphabet"
        report["reason"] = "含有 base64 字母表以外的字符（可能是被截断的片段或换行）"
        return report
    # base64 数据字符数 %4 == 1 一定非法 —— 这是"少抄了字符"最可靠的指纹
    if report["data_len_mod4"] == 1:
        report["verdict"] = "truncated"
        report["reason"] = (
            f"数据字符数 {report['length'] - stripped.count('=')} 个，%4 == 1，"
            "**base64 长度非法 → 极可能被截断**（TikHub 的 token 总长必为 4 的倍数）"
        )
        return report
    if stripped.endswith("=") and report["length_mod4"] != 0:
        report["verdict"] = "truncated"
        report["reason"] = (
            f"带补位符但总长度 {report['length']} 不是 4 的倍数 → 极可能被截断"
        )
        return report
    if report["length"] not in _VALID_TOTAL_LENS:
        report["verdict"] = "suspect_length"
        report["reason"] = (
            f"总长度 {report['length']} 不在常见的合法长度集合 {list(_VALID_TOTAL_LENS)} 中，"
            "建议到 Dashboard 重新复制一次"
        )
    return report


def assert_token_shape(token: str, env_name: str = "TIKHUB_TOKEN") -> Dict[str, Any]:
    """形状不合法就立刻报错。返回体检结果供调用方记录。"""
    report = token_shape_report(token)
    if report["verdict"] in ("ok", "suspect_length"):
        return report
    raise SystemExit(
        f"❌ {env_name} 形状校验未通过（{report['verdict']}）：{report['reason']}\n"
        f"   实际长度 {report['length']}，字符集合法={report['alphabet_ok']}，"
        f"数据字符数%4={report['data_len_mod4']}\n"
        "   → 这不是 MCP 没配好，是**凭据本身残缺**。正确做法：\n"
        "     1) 到 TikHub Dashboard 重新复制 API Token；或\n"
        "     2) 用 `python3 scripts/tikhub_key_check.py` 从剪贴板直读（零转录误差）；或\n"
        "     3) 从本机浏览器缓存找原始副本：\n"
        "        ~/Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari/WebKitCache/"
    )
