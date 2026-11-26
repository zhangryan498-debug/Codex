"""TikHub MCP 采集器 —— 抖音 / TikTok 侧托管数据通道。

## 鉴权真相（2026-11-05 终版，此前有一个错误结论，已作废）

**TikHub 的 REST 与 MCP 是同一套凭据校验**，同一个 `Authorization: Bearer <token>` 两边通用。

| 通道 | 端点 | 结果 |
|---|---|---|
| REST（海外） | `api.tikhub.io/api/v1/...` | ✅ 200 |
| REST（大陆） | `api.tikhub.dev/api/v1/...` | ✅ 200 |
| **MCP** | `mcp.tikhub.io/{platform}/mcp` | ✅ 200 |

⚠️ **曾经写过「MCP 可用、REST 不可用」，是误判，已作废。**
误判来源有两条，都值得记住：

1. **MCP 的握手成功是假阳性**：`initialize` 与 `tools/list` **不校验凭据**，
   用垃圾 token 也能拿到 319 个工具清单；**只有 `tools/call` 才校验**，
   且它会把 token 原样转交上游 REST 校验。**"能握手"从来不是凭据有效的证据。**
2. **当时那把 token 本身是残的**：长度 59，正确长度 60 —— 复制时丢了开头的字母 `i`。
   残缺 token 在 REST 侧查表即判 invalid，于是产生了"REST 坏了"的错觉。

**教训**：拿到 401 先做 **token 形状自检**（长度 + base64 合法性），
再考虑"MCP 配没配好"。形状不对时**先去找本机原始副本**（浏览器缓存 / 终端历史），
不要靠穷举猜 —— 穷举还可能因为并发过快吃 429 而漏掉真身。

## MCP 调用协议（2024-11-05）

```
1. POST /douyin/mcp  {"jsonrpc":"2.0","id":1,"method":"initialize",
                      "params":{"protocolVersion":"2024-11-05",...}}
   → 响应头 Mcp-Session-Id 必须保存
2. POST /douyin/mcp  {"jsonrpc":"2.0","method":"notifications/initialized"}
3. POST /douyin/mcp  {"jsonrpc":"2.0","id":2,"method":"tools/list"}
4. POST /douyin/mcp  {"jsonrpc":"2.0","id":3,"method":"tools/call",
                      "params":{"name":"<tool>","arguments":{...}}}
```
每次调用都要带同一个 `Mcp-Session-Id` 和 `Authorization: Bearer <token>`。
响应是 SSE 格式（`event: message\ndata: {...}`），要剥掉前缀再解析 JSON。

## 注意：计费

TikHub 按次计费。**额度紧张时不要盲目跑**——先 `tools/list`（不计费）看清工具，
再挑最需要的调用。本采集器因此把 `--list` 和 `--call` 分开，
并且 `--call` 只支持显式指定工具与参数。

用法：
    export TIKHUB_TOKEN=xxx
    python3 collectors/tikhub_mcp.py --list                    # 列工具（不计费）
    python3 collectors/tikhub_mcp.py --schema <tool>            # 看某工具的参数（不计费）
    python3 collectors/tikhub_mcp.py --call <tool> --arg k=v ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from base import append_manifest, make_record, now_cst, write_records  # noqa: E402
from tikhub_auth import assert_token_shape  # noqa: E402

COLLECTOR = "tikhub_mcp"
TOKEN_ENV = "TIKHUB_TOKEN"
MCP_HOSTS = {
    "tikhub": "https://mcp.tikhub.io/tikhub/mcp",   # 账户/用量/价格等 TikHub 自有工具
    "douyin": "https://mcp.tikhub.io/douyin/mcp",
    "tiktok": "https://mcp.tikhub.io/tiktok/mcp",
    "xiaohongshu": "https://mcp.tikhub.io/xiaohongshu/mcp",
    "kuaishou": "https://mcp.tikhub.io/kuaishou/mcp",
}
PROTOCOL_VERSION = "2024-11-05"


def get_token() -> str:
    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if not token:
        raise SystemExit(f"❌ 未设置 {TOKEN_ENV}")
    # 形状守卫：被截断的 token 在本地就报错，别把"凭据残缺"误诊成"MCP 没配好"
    assert_token_shape(token, TOKEN_ENV)
    return token


class TikHubMCP:
    """最小 MCP 客户端（Streamable HTTP）。"""

    def __init__(self, platform: str = "douyin", token: Optional[str] = None,
                 verbose: bool = False):
        self.platform = platform
        self.url = MCP_HOSTS.get(platform)
        if not self.url:
            raise SystemExit(f"❌ 未知平台 {platform}，可选：{', '.join(MCP_HOSTS)}")
        self.token = token or get_token()
        if not self.token:
            raise SystemExit(f"❌ 未设置 {TOKEN_ENV}")
        self.session_id: Optional[str] = None
        self.verbose = verbose
        self._id = 0

    # ---------------------------------------------------------- 底层
    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", "replace")
            raise SystemExit(f"❌ MCP HTTP {e.code}: {body_txt[:300]}")
        except Exception as e:  # noqa: BLE001
            raise SystemExit(f"❌ MCP 请求失败：{type(e).__name__}: {e}")
        return self._parse(raw)

    @staticmethod
    def _parse(raw: str) -> Dict[str, Any]:
        """SSE 响应：取 `data:` 行拼 JSON；非 SSE 直接解析。"""
        raw = raw.strip()
        if raw.startswith("{") or raw.startswith("["):
            try:
                return json.loads(raw)
            except Exception:  # noqa: BLE001
                pass
        chunks = []
        for line in raw.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunks.append(line[5:].strip())
        if not chunks:
            return {"_raw": raw[:400]}
        try:
            return json.loads("\n".join(chunks))
        except Exception:  # noqa: BLE001
            return {"_raw": raw[:400]}

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None,
             notify: bool = False) -> Dict[str, Any]:
        self._id += 1
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not notify:
            payload["id"] = self._id
        if params is not None:
            payload["params"] = params
        res = self._post(payload)
        if "error" in res:
            err = res["error"]
            raise SystemExit(f"❌ MCP {method} 失败：{err.get('code')} {err.get('message')}")
        return res.get("result", {})

    # ---------------------------------------------------------- 会话
    def connect(self) -> None:
        self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "dt-pipeline", "version": "1.0"},
        })
        self._rpc("notifications/initialized", notify=True)
        if self.verbose:
            print(f"   MCP 已连接 {self.url}（session={self.session_id}）")

    def list_tools(self) -> List[dict]:
        return self._rpc("tools/list").get("tools", [])

    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        res = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        # MCP 工具结果通常在 content[] 里（text 类型），也可能直接有 structuredContent
        if "structuredContent" in res:
            return res["structuredContent"]
        parts = []
        for c in res.get("content", []) or []:
            if c.get("type") == "text":
                parts.append(c.get("text", ""))
        txt = "\n".join(parts)
        try:
            return json.loads(txt)
        except Exception:  # noqa: BLE001
            return {"_text": txt}


# ------------------------------------------------------------------ 工具检索
def find_tools(tools: List[dict], *keywords: str) -> List[dict]:
    out = []
    for t in tools:
        blob = f"{t.get('name','')} {t.get('description','')}".lower()
        if all(k.lower() in blob for k in keywords):
            out.append(t)
    return out


def tool_schema(t: dict) -> str:
    lines = [f"### {t.get('name')}", (t.get("description") or "").strip()[:400], ""]
    schema = t.get("inputSchema") or {}
    props = schema.get("properties") or {}
    req = set(schema.get("required") or [])
    if props:
        lines.append("参数：")
        for k, v in props.items():
            typ = v.get("type") or v.get("anyOf", [{}])[0].get("type", "?")
            lines.append(f"  - {k}{'*' if k in req else ''}: {typ} — {str(v.get('description',''))[:90]}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="TikHub MCP 采集器")
    ap.add_argument("--platform", default="douyin", choices=sorted(MCP_HOSTS))
    ap.add_argument("--list", action="store_true", help="列出全部工具（不计费）")
    ap.add_argument("--grep", help="按关键词过滤工具名/描述（不计费）")
    ap.add_argument("--schema", help="打印某工具的参数契约（不计费）")
    ap.add_argument("--call", help="调用某工具（计费！）")
    ap.add_argument("--arg", action="append", default=[],
                    help="工具参数 k=v，可多次；v 以 @ 开头则从文件读 JSON")
    ap.add_argument("--save", help="把调用结果存到这个 dataset（落 JSONL）")
    ap.add_argument("--proxy")
    args = ap.parse_args()

    print(f"TikHub MCP — {args.platform} — {now_cst()}")
    c = TikHubMCP(args.platform, verbose=True)
    c.connect()

    tools = c.list_tools()
    print(f"可用工具 {len(tools)} 个\n")

    if args.list or (not args.call and not args.schema):
        for t in tools:
            print(f"  {t.get('name'):<62} {(t.get('description') or '')[:70]}")
        print()
        if args.grep:
            print("=== 关键词命中 ===")
            for t in find_tools(tools, *args.grep.split(",")):
                print(f"  {t.get('name'):<62} {(t.get('description') or '')[:70]}")
        return

    if args.schema:
        for t in tools:
            if t.get("name") == args.schema:
                print(tool_schema(t))
                return
        print(f"❌ 没有名为 {args.schema} 的工具")
        return

    if args.call:
        arguments: Dict[str, Any] = {}
        for a in args.arg:
            k, _, v = a.partition("=")
            if v.startswith("@"):
                with open(v[1:], encoding="utf-8") as f:
                    arguments[k] = json.load(f)
            else:
                try:
                    arguments[k] = json.loads(v)
                except Exception:  # noqa: BLE001
                    arguments[k] = v
        print(f"▶ 调用 {args.call}  args={arguments}")
        res = c.call(args.call, arguments)
        out = json.dumps(res, ensure_ascii=False, indent=2)
        print(out[:3000])
        if args.save:
            records = []
            payload = res.get("data", res) if isinstance(res, dict) else res
            items = payload if isinstance(payload, list) else [payload]
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    continue
                records.append(make_record(
                    platform=args.platform, dataset=args.save,
                    entity_type=it.get("entity_type") or "item",
                    entity_id=str(it.get("id") or it.get("aweme_id") or i),
                    title=str(it.get("title") or it.get("desc") or it.get("name") or "")[:200],
                    metrics={k: v for k, v in it.items() if isinstance(v, (int, float))},
                    source_url=f"mcp://{args.platform}/{args.call}",
                    extra={"tool": args.call, "raw_keys": sorted(it.keys())[:30]},
                ))
            if records:
                paths = write_records(records, args.platform, args.save)
                append_manifest([{"platform": args.platform, "dataset": args.save,
                                  "collector": COLLECTOR, "records": len(records),
                                  "fetched_at": now_cst(),
                                  "credential": "TIKHUB_TOKEN via MCP",
                                  "paths": paths}])
                print(f"\n✅ 落盘 {len(records)} 条 → {paths['jsonl']}")


if __name__ == "__main__":
    main()
