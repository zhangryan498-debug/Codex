"""Preserve a nine-role weekly reporting snapshot with immutable local source receipts."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from role_tools_extra import _load
from workflow_live_rehearsal import amount, check, identify, stamp, task_id


class WorkflowError(ValueError):
    pass


REPORT = "lead-weekly-report"
ROLES = {
    "selection": "选品", "shop-ops": "商城", "campaign-ops": "活动",
    "live-ops": "直播", "visual-designer": "视觉", "video-director": "短视频编导",
    "video-teardown": "短视频拆解", "ad-buyer": "广告", "competitor-analyst": "竞品",
}
INTAKES = {"weekly-input-" + role: role for role in ROLES}
TOOLS = {**INTAKES, REPORT: "lead"}
FILES = {"json": "weekly-result.json", "report": "weekly-report.md", "receipt": "weekly-receipt.json"}
COMMON = "site scope currency week_start week_end observed_at version previous_task_id"
CN = ("paid_value", "settled_after_refunds", "eligible_orders", "refunded_orders", "live_settled", "ad_spend")
PH = ("placed_orders", "signed_orders", "refused_orders", "open_orders", "placed_value", "signed_value",
      "confirmed_collected", "matched_refunds", "actual_remittance", "pending_value", "ad_attributed_net", "ad_spend")
MCN = ("published_videos", "observed_days", "hit_videos")
REPORT_ROLES = {"complete", "in_progress", "template_returned"}


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def digest(data):
    return sha256(encode(data)).hexdigest()


def term(value, label, nullable=False):
    if value is None and nullable:
        return
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 300
            or any(ord(c) < 32 for c in value)):
        raise WorkflowError(label + "须为可读的单行记录，未知填 null。")


def metric_block(obj, fields, cutoff):
    check(obj, "source_ref observed_at " + " ".join(fields), "经营口径快照")
    term(obj["source_ref"], "统计来源", True)
    if (obj["source_ref"] is None) != (obj["observed_at"] is None):
        raise WorkflowError("统计来源与快照时点须同时明确。")
    if obj["observed_at"] is not None and stamp(obj["observed_at"]) > cutoff:
        raise WorkflowError("统计快照晚于报告截止。")
    for name in fields:
        amount(obj[name], name)
    if obj["source_ref"] is None and any(obj[k] is not None for k in fields):
        raise WorkflowError("未附来源的统计字段须留空。")


def validate(site, tool, inp):
    if site not in ROUTES or tool not in TOOLS:
        raise WorkflowError("周报入口仅支持已选站点的指定岗位。")
    more = ("submission_id record_id source_ref status metric action owner due_at" if tool in INTAKES
            else "report_id source_task_ids self_operated mcn")
    check(inp, COMMON + " " + more, "周报任务")
    if (inp["site"], inp["currency"], inp["scope"]) != (site, ROUTES[site][1], SYNTHETIC):
        raise WorkflowError("只接受本地合成证据、本选站点及其原币。")
    version = inp["version"]
    amount(version, "版本", 1, 1000000, False)
    previous = inp["previous_task_id"]
    if (version == 1) != (previous is None):
        raise WorkflowError("修订必须引用同记录上一版任务。")
    if previous is not None:
        task_id(previous, "上一版")
    start, end, at = (stamp(inp[k]) for k in ("week_start", "week_end", "observed_at"))
    if (end - start != timedelta(days=7) or start.weekday() != 0
            or start.time() != start.time().replace(hour=0, minute=0, second=0, microsecond=0)
            or at < start or (tool == REPORT and at < end)):
        raise WorkflowError("需提供本地周一零点起的连续七日；周报截止不得早于周末。")
    if tool in INTAKES:
        for k in ("record_id", "submission_id"):
            identify(inp[k], k)
        for k in ("source_ref", "action", "owner"):
            term(inp[k], k, True)
        if inp["status"] not in tuple(REPORT_ROLES):
            raise WorkflowError("岗位状态仅限已完成、进行中、模板退回。")
        if inp["due_at"] is not None:
            stamp(inp["due_at"])
        if inp["metric"] is not None:
            metric = inp["metric"]
            check(metric, "name value unit", "岗位指标")
            term(metric["name"], "指标")
            term(metric["unit"], "单位")
            if metric["unit"] in {"CNY", "PHP", "CNY_minor", "PHP_minor"} and not metric["unit"].startswith(inp["currency"]):
                raise WorkflowError("岗位金额币种必须与本站一致。")
            amount(metric["value"], "指标值")
        return "weekly-input:" + site + ":" + tool + ":" + inp["submission_id"]
    identify(inp["report_id"], "周报编号")
    refs = inp["source_task_ids"]
    if not isinstance(refs, list) or len(refs) > 90:
        raise WorkflowError("周报引用最多 90 条本地岗位任务。")
    for ref in refs:
        task_id(ref, "岗位来源")
    metric_block(inp["self_operated"], CN if site == "CN" else PH, at)
    metric_block(inp["mcn"], MCN, at)
    return "weekly-report:" + site + ":" + inp["report_id"] + ":" + str(version)


def bind(site, tool, inp, load):
    key = validate(site, tool, inp)
    pins = {}
    prev = None
    if inp["previous_task_id"]:
        ident = inp["previous_task_id"]
        prev, pins[ident] = load(ident, site, (tool,))
        identity = "report_id" if tool == REPORT else "record_id"
        if (prev[identity] != inp[identity] or prev["version"] + 1 != inp["version"]
                or (prev["week_start"], prev["week_end"]) != (inp["week_start"], inp["week_end"])
                or stamp(prev["observed_at"]) >= stamp(inp["observed_at"])):
            raise WorkflowError("上一版的记录、周区间、版本或时点不匹配。")
    entries = {}
    if tool == REPORT:
        for ident in dict.fromkeys(inp["source_task_ids"]):
            row, pins[ident] = load(ident, site, INTAKES)
            if (row["week_start"], row["week_end"]) != (inp["week_start"], inp["week_end"]):
                raise WorkflowError("周报引用了其他周的岗位任务。")
            entries[ident] = row
        if prev:
            missing_history = set(prev["source_task_ids"]).difference(entries)
            if missing_history:
                raise WorkflowError("修订必须保留旧版岗位来源，可通过新回执改变当前结论。")
    return key, (entries, prev, pins)


def receipt_record(inp, tool):
    fields = ("record_id", "source_ref", "status", "metric", "action", "owner", "due_at")
    row = {k: inp[k] for k in fields}
    missing = [k for k in ("source_ref", "action", "owner", "due_at") if inp[k] is None]
    if inp["metric"] is None or inp["metric"]["value"] is None:
        missing.append("metric")
    state = ("RETURNED" if inp["status"] == "template_returned" else
             "VERIFIED_OFFLINE" if inp["status"] == "complete" and not missing else "INCOMPLETE")
    fingerprint = digest({k: v for k, v in inp.items() if k != "submission_id"})
    return {**row, "role": INTAKES[tool], "missing_fields": missing,
            "state": state, "fingerprint": fingerprint, "submission_id": inp["submission_id"]}


def ratio(n, d):
    if n is None or d is None or d <= 0:
        return None
    return {"numerator": n, "denominator": d,
            "value": str((Decimal(n) / Decimal(d)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))}


def finances(site, data, mcn):
    conflicts = []
    derived = {}
    if site == "CN":
        for label, n, d in (("refund_order_rate", "refunded_orders", "eligible_orders"),
                            ("live_share_of_settled", "live_settled", "settled_after_refunds"),
                            ("ad_spend_to_settled", "ad_spend", "settled_after_refunds")):
            a, b = data[n], data[d]
            if label != "ad_spend_to_settled" and a is not None and b is not None and a > b:
                conflicts.append(label)
                derived[label] = None
            else:
                derived[label] = ratio(a, b)
    else:
        total = data["placed_orders"]
        parts = ("signed_orders", "refused_orders", "open_orders")
        parts_known = total is not None and all(data[k] is not None for k in parts)
        mismatch = parts_known and sum(data[k] for k in parts) != total
        if total is not None and any(data[k] is not None and data[k] > total for k in parts):
            mismatch = True
        if mismatch:
            conflicts.append("order_cohort_does_not_reconcile")
        for k in parts:
            derived[k + "_rate"] = ratio(data[k], total) if parts_known and not mismatch else None
        gross, refunds = data["confirmed_collected"], data["matched_refunds"]
        if gross is not None and refunds is not None and refunds > gross:
            conflicts.append("matched_refunds_exceed_confirmed_collections")
        derived["retained_after_matched_refunds"] = gross - refunds if (
            gross is not None and refunds is not None and gross >= refunds) else None
        derived["ad_attributed_roas"] = ratio(data["ad_attributed_net"], data["ad_spend"])
    if mcn["published_videos"] is not None and mcn["hit_videos"] is not None and mcn["hit_videos"] > mcn["published_videos"]:
        conflicts.append("mcn_hits_exceed_published")
    derived_mcn = {
        "videos_per_observed_day": ratio(mcn["published_videos"], mcn["observed_days"]),
        "hits_per_published_video": None if "mcn_hits_exceed_published" in conflicts else ratio(mcn["hit_videos"], mcn["published_videos"]),
    }
    missing = [group + "." + name for group, block in (("self_operated", data), ("mcn", mcn))
               for name, value in block.items() if value is None]
    return {"self_operated": {"reported": data, **derived}, "mcn": {"reported": mcn, **derived_mcn},
            "conflicts": conflicts, "missing_fields": missing, "gross_net_remittance_kept_separate": True,
            "synthetic_assumptions_only": True}


def summarize(site, inp, entries, prev):
    chosen = {}
    cutoff = stamp(inp["observed_at"])
    counted = set()
    duplicates = len(inp["source_task_ids"]) - len(entries)
    history = {}
    disagreements = []
    late = []
    for ident, row in entries.items():
        logical = (row["role"], row["record_id"])
        identity = (*logical, row["version"], row["fingerprint"])
        if identity in counted:
            duplicates += 1
            continue
        counted.add(identity)
        if stamp(row["observed_at"]) > cutoff:
            late.append(ident)
            continue
        history.setdefault(logical, {}).setdefault(row["version"], []).append(row)
    for (role, _), versions in history.items():
        newest = max(versions)
        competing = any(len(rows) > 1 for rows in versions.values())
        if competing:
            disagreements.extend(r["task_id"] for rows in versions.values() if len(rows) > 1 for r in rows)
        chosen.setdefault(role, []).append((versions[newest][0], competing))
    roles = {}
    for role in ROLES:
        alternatives = chosen.get(role, [])
        if not alternatives:
            roles[role] = {"state": "MISSING", "owner": None, "due_at": None, "task_id": None,
                           "action": "向该岗位补索周报、责任人和期限。"}
        elif len(alternatives) > 1 or alternatives[0][1]:
            roles[role] = {"state": "CONFLICT", "owner": None, "due_at": None, "task_id": None,
                           "action": "由该岗位确认同一记录或修订，不采用冲突值。",
                           "candidate_task_ids": [r["task_id"] for r, _ in alternatives]}
        else:
            row = alternatives[0][0]
            fields = ("state", "owner", "due_at", "task_id", "version", "record_id", "source_ref", "metric", "action", "missing_fields")
            roles[role] = {k: row[k] for k in fields}
    finance = finances(site, inp["self_operated"], inp["mcn"])
    accepted = sum(row["state"] == "VERIFIED_OFFLINE" for row in roles.values())
    state = "READY_OFFLINE" if accepted == len(ROLES) and not finance["conflicts"] and not finance["missing_fields"] else "PENDING"
    diff = {}
    if prev:
        for role, new in roles.items():
            old = prev["roles"][role]
            if old != new:
                diff[role] = {k: {"from": old.get(k), "to": new.get(k)} for k in ("state", "task_id", "owner", "due_at")}
    return {"report_id": inp["report_id"], "state": state, "roles": roles, "accepted_roles": accepted,
            "total_roles": len(ROLES), "duplicate_rows_skipped": duplicates, "late_task_ids": late,
            "conflicting_task_ids": disagreements, "source_task_ids": list(entries), "changes_from_previous": diff,
            "previous_report_task_id": prev["task_id"] if prev else None,
            "finance_changed_from_previous": prev["finance"] != finance if prev else None,
            "finance": finance, "operating_models_separate": True}


def safe_md(text):
    return str(text if text is not None else "待确认").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|")


def markdown(result):
    if result["tool_id"] != REPORT:
        return f"# {safe_md(result['role'])} · {safe_md(result['state'])}\n\n" + encode(result).decode()
    lines = [f"# {result['site']} 九岗周报 · {result['report_id']} v{result['version']}", "",
             f"结论：{result['state']}。{result['accepted_roles']}/{result['total_roles']} 岗有完整离线来源。",
             "资料为本地合成。金额按原币最小单位记录，实收、回款、归因和 MCN 各自保留原口径。", "",
             "| 岗位 | 状态 | 负责人 | 截止 (+08:00) | 行动 | 来源 |", "|---|---|---|---|---|---|"]
    for role, label in ROLES.items():
        row = result["roles"][role]
        cells = (label, row["state"], row["owner"], row["due_at"], row["action"], row.get("source_ref"))
        lines.append("| " + " | ".join(map(safe_md, cells)) + " |")
    return "\n".join(lines) + "\n\n" + encode(result).decode()


def make_receipt(data, raw, report):
    keys = ("task_id", "site", "tool_id", "input_sha256", "upstream", "version", "state")
    return {**{k: data[k] for k in keys}, "result_sha256": sha256(raw).hexdigest(),
            "report_sha256": sha256(report).hexdigest(), "synthetic_offline": True, "execution_authorized": False}


def run_workflow(project, site, tool, inp, folder, assets, progress, *, linked):
    if assets or tool not in TOOLS:
        raise WorkflowError("仅接受已保存的本站本地岗位输入。")
    entries, prev, pins = linked
    result = summarize(site, inp, entries, prev) if tool == REPORT else receipt_record(inp, tool)
    result.update({k: inp[k] for k in COMMON.split()})
    result.update(task_id=folder.name, tool_id=tool, input_sha256=digest(inp), upstream=pins,
                  execution_authorized=False, model_called=False, provider_called=False)
    if prev is not None and result["previous_task_id"] != prev["task_id"]:
        raise WorkflowError("修订来源与绑定记录不一致。")
    raw, body = encode(result), markdown(result).encode()
    paths = {k: folder / name for k, name in FILES.items()}
    for k, b in {"json": raw, "report": body, "receipt": encode(make_receipt(result, raw, body))}.items():
        with paths[k].open("xb") as sink:
            sink.write(b)
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or (result["task_id"], result["site"], result["tool_id"]) != (task["id"], task["site"], task["source"]["tool_id"])
            or result["upstream"] != task["source"]["weekly_pins"]
            or json.loads(files["receipt"]) != make_receipt(result, files["json"], files["report"])):
        raise WorkflowError("岗位输入或周报文件与本地回执不一致。")


def public_tools():
    return [{"id": tool, "sites": list(ROUTES), "role_suffix": role, "input_example": {},
             "label": "九岗周报" if tool == REPORT else "周报来源收件 · " + ROLES[role],
             "description": "本站合成离线证据、可核对版本与待确认事项。"} for tool, role in TOOLS.items()]

