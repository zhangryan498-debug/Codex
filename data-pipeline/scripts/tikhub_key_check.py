#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TikHub key 体检 / 落盘 / 联通性自检 / 残缺凭据找回（零复制误差）。

用法：
    python3 scripts/tikhub_key_check.py              # 从剪贴板直读 key 并自检
    python3 scripts/tikhub_key_check.py --recover    # 凭据疑似残缺时，从浏览器缓存找回原版

脚本从 Windows/macOS 剪贴板读取 key；默认仅本地检查，真实调用必须显式 --online。历史行为说明：脚本读取 key，不经过任何手工转录，
因此不会丢字符、不会多空格、不会大小写错。

它会：
    · 体检 key 的长度 / sha256 前 16 位 / base64 长度合法性（残缺立刻拦下）
    · 写入 data-pipeline/.env（600 权限，已在 .gitignore 内）
    · 实测 REST 通道（api.tikhub.io / api.tikhub.dev）
    · 实测 MCP 通道 —— **用真实 tools/call**，不是握手
      （握手与 tools/list **不校验凭据**，垃圾 token 也能过，不能当凭据有效的证据）
    · 打印账户余额 / 免费额度，并做一次真实取数冒烟（话题搜索「秋冬穿搭」）

2026-11-05 实测教训：本项目曾拿到一把**少首位字符**的 59 位 token，
服务端一律回 `Invalid API token`，排查一度滑向"MCP 没配好"的错误方向。
所以这里的自检顺序是：**先形状、再 REST、后 MCP 真调用**，最后才花钱取数。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
COLLECTORS = ROOT / "collectors"
SMOKE_PATH = ROOT.parent / "work/tikhub_smoke.json"
sys.path.insert(0, str(COLLECTORS))

from tikhub_auth import token_shape_report  # noqa: E402

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
CACHE_DIRS = [
    "~/Library/Containers/com.apple.Safari/Data/Library/Caches/com.apple.Safari/WebKitCache",
    "~/Library/Caches/com.google.Chrome",
    "~/Library/Application Support/Google/Chrome",
    "~/Library/Caches/com.microsoft.Edge",
    "~/Library/Caches/com.brave.Browser",
]


def read_clipboard() -> str:
    try:
        command = (["powershell", "-NoProfile", "-Command",
                    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-Clipboard -Raw"]
                   if sys.platform == "win32" else ["pbpaste"])
        out = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=10)
        return out.stdout or ""
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  读剪贴板失败：{e}")
        return ""


def http_status(url: str, token: str, method: str = "GET",
                body: bytes | None = None, timeout: int = 30) -> tuple:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "User-Agent": UA,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, {}, f"{type(e).__name__}: {e}"


# ------------------------------------------------------------------ 恢复模式
def iter_cache_candidates(hint: str | None = None):
    """逐个目录、逐个文件吐出 base64 形态的候选凭据（生成器，边扫边用）。

    过滤条件：① 文件里出现 tikhub 相关字样，或包含当前残缺 token 的前 8 位指纹；
    ② 串是合法 base64 且总长为 4 的倍数。
    """
    import base64

    pat = re.compile(rb"[A-Za-z0-9+/]{50,}={0,2}")
    fingerprint = (hint or "")[:8].encode()
    seen = set()
    for d in CACHE_DIRS:
        root = pathlib.Path(os.path.expanduser(d))
        if not root.exists():
            continue
        print(f"  … 扫描 {d}")
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                if p.stat().st_size > 40_000_000:
                    continue
                data = p.read_bytes()
            except Exception:  # noqa: BLE001
                continue
            if b"tikhub" not in data.lower() and (not fingerprint or fingerprint not in data):
                continue
            for m in pat.finditer(data):
                s = m.group().decode("ascii", "ignore")
                if len(s) % 4 != 0 or len(s) < 50 or s in seen:
                    continue
                try:
                    base64.b64decode(s, validate=True)
                except Exception:  # noqa: BLE001
                    continue
                seen.add(s)
                yield s, str(p)


def recover(missing_hint: str | None = None) -> str | None:
    """在缓存里逐个验证候选凭据，返回第一个被服务端接受的（命中即停）。"""
    print("缓存里逐个验证候选凭据…")
    n = 0
    for s, src in iter_cache_candidates(missing_hint):
        n += 1
        code, _, body = http_status(
            "https://api.tikhub.io/api/v1/tikhub/user/get_user_info", s)
        mark = "✅ 命中" if code == 200 else "  "
        print(f"  {mark} #{n} len={len(s)} sha={hashlib.sha256(s.encode()).hexdigest()[:12]}  {src[-58:]}")
        if code == 200:
            if missing_hint:
                diff = ""
                try:
                    import difflib
                    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
                            None, missing_hint, s).get_opcodes():
                        if tag != "equal":
                            diff = (f"与手上的残缺版本相比：差异在位置 {i1}，"
                                    f"正确内容 {s[j1:j2]!r}")
                except Exception:  # noqa: BLE001
                    pass
                if diff:
                    print(f"\n✅ 找回可用凭据！{diff}")
            return s
    print(f"（共验证 {n} 个候选，均未被接受）")
    return None


# ------------------------------------------------------------------ 主流程
def describe(token: str) -> None:
    print("=== 凭据体检 ===")
    rep = token_shape_report(token)
    print(f"  长度        : {rep['length']}")
    print(f"  sha256 前16 : {hashlib.sha256(token.encode()).hexdigest()[:16]}")
    print(f"  首 10 字符  : {token[:10]}")
    print(f"  末 10 字符  : {token[-10:]}")
    print(f"  字符集合法  : {rep['alphabet_ok']}")
    print(f"  base64 判定 : {rep['verdict']} — {rep['reason'] or '长度合法'}")
    if rep["verdict"] in ("truncated", "alphabet", "whitespace", "empty"):
        print("\n❌ 凭据形状不合法，大概率是**复制环节残缺**（不是 MCP 没配好）。")
        print("   处理办法（按推荐顺序）：")
        print("     1) 直接运行本脚本的 --recover：从本机浏览器缓存找回原版（零转录误差）")
        print("     2) 到 TikHub Dashboard 重新复制一次，再运行本脚本")
        print("     3) 手工核对：正确的 key 总长必为 4 的倍数（抖音侧实测为 60）")


def rest_check(token: str) -> tuple:
    print("\n=== REST 通道自检（真实鉴权：get_user_info）===")
    ok = False
    balance = None
    hosts = ("https://api.tikhub.io", "https://api.tikhub.dev")
    for i, host in enumerate(hosts):
        if i:
            # TikHub 限流 1 req/s/路由：连打两个域名会撞 429，那是限流不是凭据问题
            time.sleep(1.5)
        code, _, body = http_status(f"{host}/api/v1/tikhub/user/get_user_info", token)
        msg = ""
        try:
            d = json.loads(body)
            msg = d["detail"]["message"][:90]
        except Exception:  # noqa: BLE001
            msg = body[:110]
        if code == 200:
            try:
                ud = json.loads(body)["user_data"]
                balance = ud
                msg = (f"key={json.loads(body)['api_key_data']['api_key_name']} "
                       f"balance={ud.get('balance')} free_credit={ud.get('free_credit')}")
            except Exception:  # noqa: BLE001
                pass
        elif code == 429:
            mark, msg = "⏳", "请求限流（1 req/s），非凭据问题，跳过该域名"
        else:
            mark = "❌"
        print(f"  {mark if code != 200 else '✅'} {host:26s} {code}  {msg}")
        ok = ok or code == 200
    return ok, balance


def mcp_check(token: str) -> tuple:
    """MCP 必须用真实 tools/call 验证 —— 握手与 tools/list 不校验凭据。

    用 tikhub 命名空间的 health_check：它是真实调用（不是握手），
    且返回体里带账户余额，能同时证明"鉴权通过"与"能取到数"。
    """
    print("\n=== MCP 通道自检（真实 tools/call，不用握手冒充）===")
    from tikhub_mcp import TikHubMCP
    try:
        c = TikHubMCP("tikhub", token=token)
        c.connect()
        res = c.call("health_check", {})
    except SystemExit as e:
        print(f"  ❌ {str(e)[:200]}")
        return False, {}
    except Exception as e:  # noqa: BLE001
        print(f"  ❌ {type(e).__name__}: {str(e)[:180]}")
        return False, {}
    blob = json.dumps(res, ensure_ascii=False)
    if any(k in blob for k in ("unauthorized", "rejected", '"status":401', '"status": 401')):
        print(f"  ❌ tools/call 被拒：{blob[:220]}")
        return False, {}
    if "402" in blob and "Insufficient balance" in blob:
        # 402 出现在鉴权之后 → 凭据本身已通过校验，只是账户余额不足
        print(f"  ✅ tools/call 鉴权通过（余额不足 402）：{blob[:180]}")
        return True, res
    print(f"  ✅ tools/call 通过：{blob[:200]}")
    return True, res


def smoke(token: str) -> int:
    print("\n=== 真实取数冒烟（会消耗额度，只跑 1 次）===")
    from tikhub_mcp import TikHubMCP
    c = TikHubMCP("douyin", token=token)
    c.connect()
    res = c.call("douyin_search_fetch_challenge_search_v2", {
        "keyword": "秋冬穿搭", "cursor": 0, "sort_type": "0", "publish_time": "0",
        "filter_duration": "0", "content_type": "0", "search_id": "", "backtrace": ""})
    txt = json.dumps(res, ensure_ascii=False)
    SMOKE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SMOKE_PATH.write_text(txt, encoding="utf-8")
    blob = txt
    if "402" in blob and "Insufficient balance" in blob:
        print("  ⚠️ 鉴权通过，但账户余额不足（402）：该端点不接受免费额度。")
        print("     → 充值入口：https://user.tikhub.io/users/add_credit")
        print("     → 完整响应已存 work/tikhub_smoke.json")
        return 0
    if any(k in blob for k in ("unauthorized", "rejected", '"status":401')):
        print(f"  ❌ 取数被拒（鉴权）：{txt[:300]}")
        return 3
    items = []

    def walk(o, d=0):
        if d > 6 or not isinstance(o, (dict, list)):
            return
        if isinstance(o, list):
            for x in o:
                walk(x, d + 1)
            return
        if "cha_name" in o or ("cid" in o and "view_count" in o):
            items.append(o)
        for v in o.values():
            walk(v, d + 1)

    walk(res)
    print(f"  话题条目 {len(items)} 条，前 5：")
    for it in items[:5]:
        print(f"    cid={it.get('cid')}  {it.get('cha_name')}  "
              f"view={it.get('view_count')} user={it.get('user_count')}")
    print("\n完整响应已存 work/tikhub_smoke.json")
    return 0


def save_token(token: str) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8-sig").splitlines() if ENV_PATH.exists() else []
    lines = [line for line in lines if not re.match(r"\s*(?:export\s+)?TIKHUB_TOKEN\s*=", line)]
    lines.append(f"TIKHUB_TOKEN={token}")
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(ENV_PATH, 0o600)
    print(f"\n✅ 已更新 {ENV_PATH} 中的 TIKHUB_TOKEN，保留其他配置")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recover", action="store_true",
                    help="从本机浏览器缓存找回原版凭据（凭据残缺时用）")
    ap.add_argument("--no-smoke", action="store_true", help="跳过取数冒烟")
    ap.add_argument("--online", action="store_true", help="明确启用联网鉴权和真实调用（可能消耗额度）")
    args = ap.parse_args()

    if args.recover and sys.platform != "darwin":
        print("浏览器缓存恢复仅为原 macOS 历史工具；本机请从控制台复制凭据。")
        return 2

    if args.recover:
        hint = None
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text().splitlines():
                if line.startswith("TIKHUB_TOKEN="):
                    hint = line.split("=", 1)[1].strip()
        print("=== 浏览器缓存凭据找回 ===")
        got = recover(hint)
        if not got:
            print("\n❌ 缓存里没找到可用凭据。请到 TikHub Dashboard 重新复制 API Token，"
                  "再运行 python3 scripts/tikhub_key_check.py")
            return 2
        save_token(got)
        token = got
    else:
        raw = read_clipboard()
        lines = [l.strip().strip("`").strip() for l in raw.splitlines() if l.strip()]
        if not lines:
            print("❌ 剪贴板是空的。请在浏览器里选中 API Key 并复制后重试；")
            print("   或运行本脚本 --recover 从浏览器缓存找回原版。")
            return 1
        token = max(lines, key=len)
        describe(token)
        rep = token_shape_report(token)
        if rep["verdict"] in ("truncated", "alphabet", "whitespace", "empty"):
            return 2
        save_token(token)

    if not args.online:
        print("本地凭据形状检查完成；未联网。需要真实验证时显式添加 --online。")
        return 0
    rest_ok, balance = rest_check(token)
    if not rest_ok:
        print("\n❌ REST 鉴权失败 → 服务端查不到这把凭据。")
        print("   请回 TikHub 控制台确认：① 复制的是 API Token（不是 Key ID / 邮箱）"
              "② token 处于启用状态 ③ 账号已完成邮箱验证。")
        return 2
    if balance:
        print(f"\n  账户余额 balance={balance.get('balance')}  "
              f"免费额度 free_credit={balance.get('free_credit')}  "
              f"账户状态 active={balance.get('is_active')}")

    mcp_ok, _ = mcp_check(token)
    if not args.no_smoke:
        return smoke(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
