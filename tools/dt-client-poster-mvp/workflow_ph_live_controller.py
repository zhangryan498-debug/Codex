"""Bind an existing PH local risk result to a controller's synthetic PHP evidence."""
from hashlib import sha256
import json
import re

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode
from workflow_live_rehearsal import amount, check, identify, task_id

TOOL = "ph-live-controller-risk-intake"
TOOLS = (TOOL,)
FILES = {"json": "ph-live-controller.json", "report": "ph-live-controller.md",
         "receipt": "ph-live-controller-receipt.json"}
RECEIVER = "tiktok-live-ops"
SPAN = ("start_seconds", "end_seconds")
PRICE_KEYS = ("reference", "product_id", "conditions_id", "currency", "amount_minor", *SPAN)


class WorkflowError(ValueError):
    pass


def price_row(row, source):
    check(row, " ".join(PRICE_KEYS), "合成价格证据")
    for key in ("reference", "product_id", "conditions_id"):
        identify(row[key], key)
    amount(row["amount_minor"], "PHP 分", 1, 10**12, False)
    if row["currency"] != "PHP":
        raise WorkflowError("菲律宾收件只接受明确用 PHP 计价的合成价格。")
    a, b = (row[k] for k in SPAN)
    if (type(a) not in (int, float) or type(b) not in (int, float)
            or not source["start_seconds"] <= a < b <= source["end_seconds"]):
        raise WorkflowError("价格证据时段必须落在同一原片选定范围内。")


def bind(site, data, fetch):
    check(data, "site scope job_id receiver owner recording_task_id source_result_sha256 source_identity cue_id claim observed_price",
          "菲律宾场控收件")
    if (site, data["site"], data["scope"], data["receiver"]) != ("PH", "PH", SYNTHETIC, RECEIVER):
        raise WorkflowError("请选择菲律宾场控及明确合成范围。")
    for key in ("job_id", "owner"):
        identify(data[key], key)
    task_id(data["recording_task_id"], "直播证据任务号")
    digest = data["source_result_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise WorkflowError("请填写原直播证据结果 SHA-256。")
    source, pin = fetch(data["recording_task_id"])
    identity = source["source_identity"]
    if (source["scope"] != SYNTHETIC or not source["observation_is_simulated"]
            or (identity["platform"], identity["country"]) != ("tiktok", "PH")
            or identity != data["source_identity"] or pin["result_sha256"] != digest):
        raise WorkflowError("直播来源的站点、文件、时段或结果版本不匹配，且本轮限合成片。")
    if not any(cue["id"] == data["cue_id"] and cue["scope"] == "price" for cue in source["declared_cues"]):
        raise WorkflowError("价格补充须对应原同版价格提示。")
    claim, observed = data["claim"], data["observed_price"]
    price_row(claim, identity)
    if observed is not None:
        price_row(observed, identity)
    return {"recording": pin}, source


def receipt(result, files):
    return {**{k: result[k] for k in ("task_id", "site", "tool_id", "upstream", "price_evidence_state",
                                      "execution_authorized")},
            "result_sha256": sha256(files["json"]).hexdigest(), "report_sha256": sha256(files["report"]).hexdigest()}


def run_workflow(project, site, tool, data, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("此次收件只引用已保存的直播证据。")
    pins, source = linked
    claim, observed = data["claim"], data["observed_price"]
    usable = (observed is not None
              and all(claim[k] == observed[k] for k in ("product_id", "conditions_id", "currency"))
              and observed["start_seconds"] <= claim["start_seconds"] < claim["end_seconds"] <= observed["end_seconds"])
    result = {
        **{k: data[k] for k in ("site", "scope", "job_id", "receiver", "owner", "source_identity", "cue_id")},
        "task_id": folder.name, "tool_id": tool, "upstream": pins, "currency": "PHP",
        "source_reference": source["source_reference"], "original_cue_summary": source["result"]["decisions"],
        "synthetic_claim": claim, "synthetic_observed_price": observed,
        "price_evidence_state": "PH_FIXTURE_PAIR_AVAILABLE_FOR_LOCAL_REVIEW" if usable else "PENDING_SAME_SOURCE_PRICE",
        "summary": "同品同条件的合成 PHP 价格材料已齐，等待本机复核。" if usable
        else "待核：缺少覆盖同品同条件与时段的 PHP 价格材料。",
        "price_claim_accuracy_verified": False, "semantic_accuracy_verified": False,
        "fixture_fields_are_synthetic": True, "human_review_required": True,
        "official_enforcement_evaluated": False, "operator_signoff_verified": False,
        "execution_authorized": False, "provider_called": False, "model_called": False,
    }
    text = (f"# {data['job_id']} · 菲律宾直播场控收件\n\n{result['summary']}\n"
            f"原直播任务 {pins['recording']['task_id']} · {pins['recording']['result_sha256']}\n"
            "价格标注为合成材料。保留原风险提示，真实含义与事实待核。\n")
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
        raise WorkflowError("菲律宾直播收件回执无法复核。")


def public_tools():
    return [{"id": TOOL, "sites": ["PH"], "role_suffix": "live-ops",
             "label": "菲律宾场控收件 · 直播风险证据（合成本机）",
             "description": "引用原同站、同片、同时段结果，为已标注价格提示附加合成 PHP 材料并保留原提示。",
             "input_example": {"site": "PH", "scope": SYNTHETIC, "job_id": "PH-CONTROLLER-DEMO",
                               "receiver": RECEIVER, "owner": "PH-LIVE-CONTROLLER-TEST",
                               "recording_task_id": "填入已完成的直播合成证据任务号",
                               "source_result_sha256": "填入原结果 SHA-256", "source_identity": "复制原 source_identity",
                               "cue_id": "price", "claim": {"reference": "SYN-CLAIM", "product_id": "SYN-PH-SKU",
                               "conditions_id": "SYN-BASE", "currency": "PHP", "amount_minor": 19900,
                               "start_seconds": 0.5, "end_seconds": 1.0}, "observed_price": None}}]

