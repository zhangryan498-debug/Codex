"""数据管道基础层：统一 schema、HTTP 取数、落盘。

设计原则：
1. 所有采集器输出**同一套 schema**，让 20 个 Agent 用同一种方式读数据。
2. 每次采集都带 `provenance`（来源 URL、抓取时刻、口径），让数据可追溯、可复核。
3. 采集失败不抛异常打断整批，而是记录到 manifest，交给上层判断。

只用标准库，Python 3.9+ 可直接运行。
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

CST = timezone(timedelta(hours=8))

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def now_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    return c


def http_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    retries: int = 3,
    referer: str = "",
    gzip_ok: bool = True,
) -> Dict[str, Any]:
    """带重试的 GET。返回 {"ok", "status", "body", "url", "error"}。"""
    h = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip" if gzip_ok else "identity",
        "Connection": "keep-alive",
    }
    if referer:
        h["Referer"] = referer
    if headers:
        h.update(headers)

    last_err = ""
    last_status = 0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h, method="GET")
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                enc = "utf-8"
                ct = resp.headers.get("Content-Type", "")
                if "charset=" in ct:
                    enc = ct.split("charset=")[-1].split(";")[0].strip()
                try:
                    body = raw.decode(enc, errors="replace")
                except LookupError:
                    body = raw.decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "status": resp.status,
                    "body": body,
                    "url": url,
                    "error": "",
                }
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            last_status = e.code
            if e.code in (401, 403, 404, 429):
                break  # 鉴权/风控类错误重试无意义
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (attempt + 1))

    return {"ok": False, "status": last_status, "body": "", "url": url, "error": last_err}


def http_get_json(url: str, **kw) -> Dict[str, Any]:
    r = http_get(url, **kw)
    if not r["ok"]:
        return r
    r["json"] = _safe_json(r["body"])
    return r


def http_post(url: str, body: Any = None, headers: Optional[Dict[str, str]] = None,
              timeout: int = 20, retries: int = 3) -> Dict[str, Any]:
    """带重试的 POST（JSON body）。返回结构与 :func:`http_get` 一致。"""
    h = {
        "User-Agent": DEFAULT_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }
    if headers:
        h.update(headers)
    payload = json.dumps(body if body is not None else {}).encode("utf-8")

    last_err = ""
    last_status = 0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=payload, headers=h, method="POST")
            with urllib.request.urlopen(req, timeout=timeout, context=_ctx()) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                enc = "utf-8"
                ct = resp.headers.get("Content-Type", "")
                if "charset=" in ct:
                    enc = ct.split("charset=")[-1].split(";")[0].strip()
                try:
                    text = raw.decode(enc, errors="replace")
                except LookupError:
                    text = raw.decode("utf-8", errors="replace")
                return {"ok": True, "status": resp.status, "body": text,
                        "url": url, "error": ""}
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}"
            last_status = e.code
            if e.code in (401, 403, 404, 429):
                break  # 鉴权/风控类错误重试无意义
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(1.5 * (attempt + 1))

    return {"ok": False, "status": last_status, "body": "", "url": url, "error": last_err}


def http_post_json(url: str, body: Any = None, **kw) -> Dict[str, Any]:
    r = http_post(url, body=body, **kw)
    if not r["ok"]:
        return r
    r["json"] = _safe_json(r["body"])
    return r


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- schema

SCHEMA_VERSION = "1.0"


def make_record(
    platform: str,
    dataset: str,
    entity_type: str,
    entity_id: str,
    title: str,
    metrics: Dict[str, Any],
    source_url: str,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """统一记录结构。所有采集器都必须产出这个形状。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,          # douyin | tiktok
        "dataset": dataset,            # hot_list | product | creator | video | comment ...
        "entity_type": entity_type,    # keyword | aweme | user | product | shop
        "entity_id": entity_id,
        "title": title,
        "metrics": metrics,            # 数值型指标，统一放在这里
        "extra": extra or {},          # 非数值型补充信息
        "provenance": {
            "source_url": source_url,
            "fetched_at": now_cst(),
            "collector": "",            # 由具体采集器回填
        },
    }


# ---------------------------------------------------------------- 落盘


def data_root() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _merge_metrics(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """同一 entity 的两份记录合并：数值型取 max，其余取新值。

    取 max 而不是取新值，是因为抖音会**偶发把评论数截断**（实测同一作品
    feed 卡片报 10、`aweme/detail` 报 10205）。取新值会把正确值覆盖成错值，
    取 max 则两种情况都能保住正确的一侧。
    """
    out = dict(old or {})
    for k, v in (new or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            prev = out.get(k)
            if isinstance(prev, (int, float)) and not isinstance(prev, bool):
                out[k] = max(prev, v)
            else:
                out[k] = v
        else:
            out[k] = v
    return out


def write_records(
    records: List[Dict[str, Any]],
    platform: str,
    dataset: str,
    date_str: Optional[str] = None,
    root: Optional[str] = None,
    merge: bool = True,
) -> Dict[str, str]:
    """写出 JSONL + CSV，并登记 manifest。返回落盘路径。

    `merge=True`（默认）时**与当日已有文件合并**而不是整文件覆盖：
    同 `entity_id` 保留字段更全/更新的那一条，数值指标取 max。

    为什么必须合并：2026-11-05 实测到**两个采集进程并发写同一个 dataset
    路径时互相覆盖**——后写的赢，先写的样本直接消失，投手岗的报告因此出现
    「脚本 87 条 / 报告 120 条」的自检不匹配。采集输出是**累积资产**，
    覆盖式写入会让任何一轮采样都可能被下一轮抹掉。
    """
    date_str = date_str or today_cst()
    root = root or data_root()
    outdir = os.path.join(root, platform, dataset, date_str)
    os.makedirs(outdir, exist_ok=True)

    jsonl = os.path.join(outdir, f"{dataset}.jsonl")
    existing: Dict[str, Dict[str, Any]] = {}
    if merge and os.path.exists(jsonl):
        try:
            with open(jsonl, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue
                    eid = str(r.get("entity_id") or "")
                    if not eid:
                        continue
                    prev = existing.get(eid)
                    existing[eid] = r if prev is None else {
                        **prev,
                        "metrics": _merge_metrics(prev.get("metrics"), r.get("metrics")),
                        "provenance": {**(prev.get("provenance") or {}),
                                       **(r.get("provenance") or {})},
                    }
        except Exception:  # noqa: BLE001
            existing = {}

    for r in records:
        eid = str(r.get("entity_id") or "")
        if not eid:
            continue
        prev = existing.get(eid)
        if prev is None:
            existing[eid] = r
        else:
            existing[eid] = {
                **prev,
                "title": r.get("title") or prev.get("title", ""),
                "metrics": _merge_metrics(prev.get("metrics"), r.get("metrics")),
                "extra": {**(prev.get("extra") or {}), **(r.get("extra") or {})},
                "provenance": {**(prev.get("provenance") or {}),
                               **(r.get("provenance") or {})},
            }

    merged = list(existing.values())
    # 按 entity_id 排序，保证多次运行后文件稳定可 diff
    merged.sort(key=lambda r: str(r.get("entity_id") or ""))

    with open(jsonl, "w", encoding="utf-8") as f:
        for r in merged:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    csv_path = os.path.join(outdir, f"{dataset}.csv")
    if merged:
        cols: List[str] = []
        for r in merged:
            for k in r:
                if k not in cols:
                    cols.append(k)
        metric_keys: List[str] = []
        for r in merged:
            for k in r.get("metrics", {}):
                if k not in metric_keys:
                    metric_keys.append(k)
        header = ["schema_version", "platform", "dataset", "entity_type", "entity_id",
                  "title", "fetched_at", "source_url"] + metric_keys
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            for r in merged:
                row = [r.get("schema_version", ""), r.get("platform", ""), r.get("dataset", ""),
                       r.get("entity_type", ""), r.get("entity_id", ""), r.get("title", ""),
                       r.get("provenance", {}).get("fetched_at", ""),
                       r.get("provenance", {}).get("source_url", "")]
                m = r.get("metrics", {})
                row += [m.get(k, "") for k in metric_keys]
                w.writerow(row)

    return {"jsonl": jsonl, "csv": csv_path, "dir": outdir,
            "written": len(records), "total_after_merge": len(merged)}


def append_manifest(entries: List[Dict[str, Any]], root: Optional[str] = None) -> str:
    root = root or data_root()
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "manifest.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
