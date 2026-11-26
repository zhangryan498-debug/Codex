"""Offline listening-note import, role takeout, and annotated risk rehearsal."""
from collections import defaultdict
from hashlib import sha256
import json
import math
import re

from role_tools import SYNTHETIC
from role_tools_extra import _load

IMPORT = "teardown-listening-review-import"
RECEIVE = "live-listening-review-receive"
RISK = "live-risk-context-rehearsal"
TOOLS = (IMPORT, RECEIVE, RISK)
FILES = {"json": "listening-review.json", "report": "listening-review.md"}
SHA = re.compile("[a-f0-9]{64}")
TASK = re.compile("[a-f0-9]{32}")
CODE = re.compile("[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


class WorkflowError(ValueError):
    pass


def public_tools():
    return [
        _tool(IMPORT, "video-teardown", "拆解员 · 导入原机稿与听审更正",
              "分别保留机稿与已有更正，生成只供本机取件的时码审阅件。"),
        _tool(RECEIVE, "live-ops", "直播运营 · 领取同版听审件",
              "填写拆解任务编号、版本与 JSON 文件校验值。取件由本机记录，不能代替真人签收。",
              {"source_task_id": "0" * 32, "version": 1, "source_sha256": "0" * 64}),
        _tool(RISK, "live-ops", "直播运营 · 结构化风险演练",
              "只接收带时段和语境标注的合成案例。按主播或评论、引用或否定以及同商品同条件价判断，并保留独立风险。"),
    ]


def _tool(ident, role, label, description, example=None):
    return {"id": ident, "sites": ["CN"], "role_suffix": role, "label": label,
            "description": description, "input_example": example}


def _fields(value, keys):
    if type(value) is not dict or value.keys() != set(keys.split()):
        raise WorkflowError("输入字段不符合听审表约定。")
    return value


def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _text(value):
    return type(value) is str and 0 < len(value.strip()) <= 2000


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _listen(p):
    _fields(p, "scope source_reference recording_sha256 owner_note_sha256 version machine_windows corrections")
    if (p["scope"] != "historical_owner_notes_local_only" or not _matches(CODE, p["source_reference"])
            or not _matches(SHA, p["recording_sha256"]) or not _matches(SHA, p["owner_note_sha256"])
            or type(p["version"]) is not int or p["version"] != 1):
        raise WorkflowError("请保留本机历史听审来源及文件哈希，首次导入只接受版本 1。")
    windows, corrections = p["machine_windows"], p["corrections"]
    if type(windows) is not list or not 1 <= len(windows) <= 20 or type(corrections) is not list:
        raise WorkflowError("请分开提供机稿时段和人工更正。")
    by_line = defaultdict(list)
    for correction in corrections:
        _fields(correction, "line original replacement")
        number = correction["line"]
        if type(number) is not int or number < 1 or not all(_text(correction[k]) for k in ("original", "replacement")):
            raise WorkflowError("每条批注需给出原机稿行号、被更正文字和替换文字。")
        by_line[number].append(correction)
    rows, seen, clocks = [], set(), {}
    for window in windows:
        _fields(window, "id source_sha256 rows")
        key = window["id"]
        if (not _matches(CODE, key) or key in seen or not _matches(SHA, window["source_sha256"])
                or type(window["rows"]) is not list or not 1 <= len(window["rows"]) <= 100):
            raise WorkflowError("机稿时段需为唯一编号并保留原文件哈希。")
        seen.add(key)
        clocks[key] = window["source_sha256"]
        last_start = 0
        for row in window["rows"]:
            _fields(row, "line start_seconds end_seconds text")
            number, a, b = row["line"], row["start_seconds"], row["end_seconds"]
            if (type(number) is not int or number != len(rows) + 1 or not _time(a) or not _time(b)
                    or not last_start <= a < b or not _text(row["text"])):
                raise WorkflowError("机稿行号、原片时码或文字无法对应。")
            last_start = a
            original = row["text"]
            resolved = original
            edits = by_line.pop(number, [])
            seen_edits = set()
            for edit in edits:
                old = edit["original"]
                if old in seen_edits or original.count(old) != 1 or resolved.count(old) != 1 or old == edit["replacement"]:
                    raise WorkflowError("更正须能唯一定位到原机稿文字。")
                seen_edits.add(old)
                resolved = resolved.replace(old, edit["replacement"], 1)
            rows.append({"window": key, "line": number, "start_seconds": a, "end_seconds": b,
                         "machine": original, "human_correction": resolved if edits else None,
                         "correction_state": "existing_owner_correction" if edits else "no_row_annotation",
                         "edits": edits})
    if by_line:
        raise WorkflowError("有批注找不到原机稿行。")
    return {"scope": p["scope"], "source_reference": p["source_reference"], "version": p["version"],
            "recording_sha256": p["recording_sha256"], "owner_note_sha256": p["owner_note_sha256"],
            "asr_by_window_sha256": clocks, "rows": rows, "statistics": {
                "rows": len(rows), "corrected_rows": sum(bool(row["edits"]) for row in rows),
                "corrections": len(corrections), "asr_accuracy": None},
            "source_files_replayed": False, "independent_gold": False}


def receive_input(p):
    _fields(p, "source_task_id version source_sha256")
    if (not _matches(TASK, p["source_task_id"]) or type(p["version"]) is not int
            or p["version"] < 1 or not _matches(SHA, p["source_sha256"])):
        raise WorkflowError("请填写实际拆解任务号、版本和同版 JSON 文件哈希。")
    return p


def receive(source, data, p):
    receive_input(p)
    packet = json.loads(data)
    if (source.get("kind"), source.get("site"), source.get("status"),
            source.get("source", {}).get("tool_id"), source.get("id")) != (
            "role_tool", "CN", "ready", IMPORT, p["source_task_id"]):
        raise WorkflowError("只支持领取已完成的中国站拆解听审件。")
    verify_saved(source, {"json": data, "report": _render(packet)})
    if packet["version"] != p["version"] or sha256(data).hexdigest() != p["source_sha256"]:
        raise WorkflowError("原听审件的版本或文件哈希与领取请求不符。")
    return {"source_task_id": source["id"], "source_sha256": p["source_sha256"],
            "source_reference": packet["source_reference"], "recording_sha256": packet["recording_sha256"],
            "version": packet["version"], "receipt_kind": "local_workbench_takeout",
            "receiving_role": "douyin-live-ops", "human_signature": False,
            "statistics": packet["statistics"], "rows": packet["rows"]}


def _price(claim, evidence):
    if evidence is None:
        return "awaiting_same_product_price_evidence"
    # Require price at the entire observed statement, never a nearby or earlier price.
    if (evidence["product_id"] != claim["product_id"] or evidence["conditions"] != claim["conditions"]
            or not evidence["start_seconds"] <= claim["start_seconds"] <= claim["end_seconds"] <= evidence["end_seconds"]):
        return "awaiting_same_product_price_evidence"
    return "actual_mismatch_in_fixture" if evidence["amount_minor"] != claim["amount_minor"] else "matches_fixture"


def _price_row(row):
    _fields(row, "product_id conditions amount_minor start_seconds end_seconds")
    a, b = row["start_seconds"], row["end_seconds"]
    if (not _matches(CODE, row["product_id"]) or not _text(row["conditions"]) or not _time(a) or not _time(b)
            or not a < b or type(row["amount_minor"]) is not int or row["amount_minor"] < 1):
        raise WorkflowError("价格要关联明确的商品、人民币分、适用条件和时段。")


def _risk(project, p):
    _fields(p, "scope fixture_id events")
    if (p["scope"] != SYNTHETIC or not _matches(CODE, p["fixture_id"])
            or type(p["events"]) is not list or not 1 <= len(p["events"]) <= 30):
        raise WorkflowError("当前语境演练只接受带编号的合成案例。")
    fixture_digest = sha256(json.dumps(p["events"], sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    policy = _load(project, "live_brand_visual_silence_v2")
    decisions = []
    ids = set()
    for row in p["events"]:
        _fields(row, "id speaker context utterance scope independent_risk observation start_seconds end_seconds claim observed_price")
        claim, evidence = row["claim"], row["observed_price"]
        if (not _matches(CODE, row["id"]) or row["id"] in ids or not _text(row["utterance"])
                or row["speaker"] not in ("host", "viewer", "unknown")
                or row["context"] not in ("affirmed", "quoted", "negated", "unknown")
                or row["scope"] not in ("brand_identity_authorization", "price", "performance_claim", "off_platform")
                or row["independent_risk"] is not None and type(row["independent_risk"]) is not bool
                or row["observation"] not in ("consistent", "inconsistent", "unknown")):
            raise WorkflowError("事件角色、上下文、风险类型或画面状态不明确。")
        if (row["scope"] == "price") != (claim is not None) or claim is None and evidence is not None:
            raise WorkflowError("价格类事件需要把口播和画面价格分别填入。")
        a, b = row["start_seconds"], row["end_seconds"]
        if not _time(a) or not _time(b) or not a < b:
            raise WorkflowError("事件需要可定位的起止时刻。")
        ids.add(row["id"])
        direct_host = (row["speaker"], row["context"]) == ("host", "affirmed")
        attribution = ("assess_host_claim" if direct_host else
                       "awaiting_attribution_or_context" if "unknown" in (row["speaker"], row["context"])
                       else "not_direct_host_claim")
        price_status = None
        if claim is not None:
            _price_row(claim)
            if (claim["start_seconds"], claim["end_seconds"]) != (a, b):
                raise WorkflowError("口播价格和事件时间窗不符。")
            if evidence is not None:
                _price_row(evidence)
            price_status = _price(claim, evidence) if direct_host else attribution
        scope = {"platform": "douyin", "country": "CN", "recording_sha256": fixture_digest,
                 "start_seconds": a, "end_seconds": b}
        observation = {**scope, "origin": "human_local", "visual_observed": True,
                       "speech_observed": True, "visual_status": "sufficient", "relation": row["observation"]}
        alert = {k: row[k] for k in ("id", "scope", "independent_risk")}
        if not direct_host and alert["independent_risk"] is False:
            alert["independent_risk"] = None
        decision, = policy.evaluate(scope, observation, [alert])["decisions"]
        decisions.append({**row, "attribution": attribution, "price_status": price_status,
                          "action": decision["action"], "reason": decision["reason"]})
    return {"scope": p["scope"], "fixture_id": p["fixture_id"], "fixture_sha256": fixture_digest,
            "policy_version": policy.POLICY_VERSION, "decisions": decisions, "logic_only": True,
            "semantic_accuracy_verified": False, "official_enforcement_evaluated": False}


def _render(packet):
    lines = ["# " + next(tool["label"] for tool in public_tools() if tool["id"] == packet["tool_id"]),
             "", "任务号 " + packet["task_id"], ""]
    if "rows" in packet:
        if "source_task_id" in packet:
            lines.extend(["来源任务 " + packet["source_task_id"], "文件 SHA-256 " + packet["source_sha256"], ""])
        lines.extend(["原片时码。未标注表示没有逐行更正，不能据此认定机稿准确。", ""])
        for row in packet["rows"]:
            lines.append(f"- {row['window']} #{row['line']} {row['start_seconds']:.2f}–{row['end_seconds']:.2f} 秒")
            lines.append("  - 原机稿 " + row["machine"])
            correction = row["human_correction"]
            lines.append("  - 已有人工更正 " + correction if correction is not None else "  - 人工更正 未标注")
    else:
        lines.extend(["数据及标注为合成案例。结果用于检查判断逻辑。", ""])
        roles = {"host": "主播", "viewer": "评论", "unknown": "待核"}
        contexts = {"affirmed": "直接陈述", "quoted": "引用", "negated": "否定", "unknown": "待核"}
        prices = {"actual_mismatch_in_fixture": "同商品同条件价不一致",
                  "matches_fixture": "同商品同条件价一致",
                  "awaiting_same_product_price_evidence": "待核同商品同条件同时间窗价格",
                  "not_direct_host_claim": "非主播直接报价格",
                  "awaiting_attribution_or_context": "待核发言人或语境"}
        for row in packet["decisions"]:
            action = "静默对应提醒" if row["action"] == "silent" else "保留提醒"
            price = prices.get(row["price_status"], "未比较价格")
            lines.append(f"- {row['id']} {roles[row['speaker']]} · {contexts[row['context']]} · {action} · {price}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def run_workflow(project, site, tool, p, task_dir, assets, progress, linked=None):
    if site != "CN" or tool not in TOOLS or assets:
        raise WorkflowError("当前只处理中国站的本机结构化听审。")
    result = {IMPORT: lambda: _listen(p), RECEIVE: lambda: linked,
              RISK: lambda: _risk(project, p)}[tool]()
    result = {**result, "task_id": task_dir.name, "site": site, "tool_id": tool,
              "external_calls": 0, "model_called": False, "execution_authorized": False}
    paths = {kind: task_dir / file for kind, file in FILES.items()}
    with paths["json"].open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    with paths["report"].open("xb") as stream:
        stream.write(_render(result))
    return result, paths


def verify_saved(task, files):
    result = json.loads(files["json"])
    if (result != task["result"] or result["task_id"] != task["id"] or result["site"] != task["site"]
            or result["tool_id"] != task["source"]["tool_id"] or files["report"] != _render(result)):
        raise WorkflowError("听审文件未通过同版核验。")
