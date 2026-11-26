"""Read a pinned PH selection certification decision into the ad review queue."""
from hashlib import sha256
import json
import re

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode
from workflow_live_rehearsal import check, identify, stamp, task_id

TOOL = "ph-ad-certification-evidence-intake"
TOOLS = (TOOL,)
FILES = {"json": "ph-cert-intake.json", "report": "ph-cert-intake.md", "receipt": "ph-cert-intake-receipt.json"}
SUBJECT = ("category", "holder_id", "sku_id", "sku_revision")
WINDOW = ("planned_from", "planned_until")
RECEIVER = "tiktok-ad-buyer"
STATUS = {
    "SYNTHETIC_MAPPING_CHECKED": ("SYNTHETIC_RECEIVED_FOR_REVIEW", "合成映射收件完成，只供本机复核。"),
    "PENDING_EVIDENCE": ("PENDING_EVIDENCE", "待核：证据缺失、未知或未覆盖该窗口。"),
    "BLOCKED_SYNTHETIC": ("BLOCKED_SYNTHETIC", "已拦截：合成合规证据范围或结论有冲突。"),
}


class WorkflowError(ValueError):
    pass


def bind(site, data, fetch):
    check(data, " ".join(("site", "scope", "job_id", "owner", "receiver", "as_of", *SUBJECT, *WINDOW,
                          "certification_task_id", "source_result_sha256")), "认证证据收件")
    if (site, data["site"], data["scope"], data["receiver"]) != ("PH", "PH", SYNTHETIC, RECEIVER):
        raise WorkflowError("选择菲律宾站及对应投放测试岗位。")
    for key in ("job_id", "owner"):
        identify(data[key], key)
    task_id(data["certification_task_id"], "选品认证任务号")
    digest = data["source_result_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WorkflowError("请填写选品原回执中的结果 SHA-256。")
    source, original, pin = fetch(data["certification_task_id"])
    if (digest != pin["result_sha256"] or source["scope"] != SYNTHETIC
            or source["actual_document_validity_verified"] is not False
            or source["execution_authorized"] is not False
            or type(data["sku_revision"]) is not int
            or any(data[key] != original[key] for key in (*SUBJECT, *WINDOW))):
        raise WorkflowError("收件只接受同一 PH 类目、持有人、SKU 修订、计划窗口和认证结果版本。")
    if data["owner"] == original["owner"]:
        raise WorkflowError("请用独立投放测试身份收件。")
    if not stamp(original["as_of"]) <= stamp(data["as_of"]) <= stamp(data["planned_from"]):
        raise WorkflowError("请在选品原评估后及原计划开始前收件。")
    return {"certification": pin}, source


def receipt(result, files):
    return {**{key: result[key] for key in ("task_id", "site", "job_id", "tool_id", "receiver",
                                         "state", "upstream", "execution_authorized")},
            "result_sha256": sha256(files["json"]).hexdigest(), "report_sha256": sha256(files["report"]).hexdigest()}


def run_workflow(project, site, tool, data, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("收件只接受工作台内已完成的合成认证任务。")
    pins, source = linked
    status, summary = STATUS[source["state"]]
    result = {
        **{key: data[key] for key in ("site", "scope", "job_id", "owner", "receiver", "as_of", *SUBJECT, *WINDOW)},
        "task_id": folder.name, "tool_id": tool, "state": status, "summary": summary, "upstream": pins,
        "source_state": source["state"], "source_assessed_at": source["as_of"],
        "fixture_rule": source["fixture_rule"], "evidence_rows": source["requirement_evidence"],
        "original_rule_reference": source["local_project_requirement_source"],
        "evidence_needed": source["evidence_needed"], "hard_reasons": source["hard_reasons"],
        "actual_category_requirements_verified": False, "actual_document_validity_verified": False,
        "actual_ad_action_taken": False, "actual_listing_authorized": False, "actual_trial_authorized": False,
        "listing_and_trial_blocked": True, "execution_authorized": False, "model_called": False,
        "provider_called": False,
    }
    text = (
        f"# {data['job_id']} · PH 投放认证证据收件\n\n{summary}\n"
        f"类目 {data['category']} · 持有人 {data['holder_id']} · SKU {data['sku_id']} v{data['sku_revision']}\n"
        f"同源窗口 {data['planned_from']} 至 {data['planned_until']}\n"
        f"原任务 {pins['certification']['task_id']} · {pins['certification']['result_sha256']}\n"
        "真实认证、试投和上架仍待授权。\n"
    )
    files = {"json": encode(result), "report": text.encode("utf-8")}
    files["receipt"] = encode(receipt(result, files))
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(files[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or result["upstream"] != task["source"]["role_packet_pins"]
            or (result["task_id"], result["site"], result["tool_id"]) !=
               (task["id"], task["site"], task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(result, files)):
        raise WorkflowError("菲律宾认证证据收件回执无法复核。")


def public_tools():
    return [{
        "id": TOOL, "sites": ["PH"], "role_suffix": "ad-buyer",
        "label": "菲律宾投放收件 · 选品认证证据（合成本机）",
        "description": "用原选品任务号和回执领用同站、类目、持有人、SKU 修订及时间窗口的合成认证结果。",
        "input_example": {
            "site": "PH", "scope": SYNTHETIC, "job_id": "PH-CERT-AD-DEMO", "owner": "PH-AD-TEST",
            "receiver": RECEIVER, "as_of": "2026-11-30T12:00:00+08:00", "category": "SYN-CATEGORY-09",
            "holder_id": "SYN-PH-HOLDER-09", "sku_id": "SYN-PH-SKU-09", "sku_revision": 2,
            "planned_from": "2026-12-03T12:00:00+08:00", "planned_until": "2026-12-04T12:00:00+08:00",
            "certification_task_id": "填写选品认证任务号", "source_result_sha256": "填写原回执结果 SHA-256",
        },
    }]

