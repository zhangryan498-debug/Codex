"""Transfer a frozen human-observed shop anchor without granting release authority."""
from hashlib import sha256
import json

from workflow_lead_weekly import encode
from workflow_live_rehearsal import check, identify, task_id

TEARDOWN = "cn-teardown-human-anchor-record"
DIRECTOR = "cn-director-human-anchor-preflight"
SELECTION = "cn-selection-human-anchor-intake"
TOOLS = (TEARDOWN, DIRECTOR, SELECTION)
ROUTES = {TEARDOWN: ("video-teardown", "拆解登记既有人审商品入口"),
          DIRECTOR: ("video-director", "编导预检既有人审商品入口"),
          SELECTION: ("selection", "选品接收既有人审商品入口")}
FILES = {"json": "human-anchor.json", "report": "human-anchor.md", "receipt": "human-anchor-receipt.json"}
SCOPE = "authorized_local_historical_anchor_only"
FROZEN_FIXTURE_SHA256 = "f62002c0de34cab555f2c631f534f8ae2d13e58e4d72f8a0ad26c37eb9f56efa"
FROZEN_ANCHOR_PAYLOAD_SHA256 = "9160bfc3d9941b130069eb9f35971f3767ededcd16be302aa466197161f3462d"
PREDECESSOR = {DIRECTOR: TEARDOWN, SELECTION: DIRECTOR}


class WorkflowError(ValueError):
    pass


def digest(data):
    return sha256(data).hexdigest()


def binding(approved):
    return {"reference_id": approved["reference_id"], "receipt_sha256": approved["receipt_sha256"],
            "artifact_sha256": FROZEN_FIXTURE_SHA256}


def bind(site, tool, p, fetch, fixture):
    fields = "site scope job_id reference_id evidence_sha256 work_id"
    if tool != TEARDOWN:
        fields += " source_task_id source_result_sha256"
    check(p, fields, "历史人工商品入口")
    if (site, p["site"], p["scope"]) != ("CN", "CN", SCOPE):
        raise WorkflowError("只接收中国站的已留档历史人工商品入口记录。")
    identify(p["job_id"], "任务编号")
    if digest(fixture) != FROZEN_FIXTURE_SHA256:
        raise WorkflowError("已审批的原件摘要发生变化。")
    approved = json.loads(fixture)
    if (p["reference_id"], p["evidence_sha256"], p["work_id"]) != (
            approved["reference_id"], approved["receipt_sha256"], approved["work_id"]):
        raise WorkflowError("证据编号、原回执 SHA-256 或同源作品不一致。")
    pins = {"human_anchor": binding(approved)}
    if tool != TEARDOWN:
        task_id(p["source_task_id"], "原工作台任务")
        source, pin = fetch(p["source_task_id"], site, PREDECESSOR[tool])
        if (p["source_result_sha256"] != pin["result_sha256"]
                or source["anchor"] != approved
                or source["state"] != "PENDING_PLATFORM_EVIDENCE"):
            raise WorkflowError("原任务同版哈希或证据范围不一致。")
        pins["source"] = pin
    return pins, approved


def compose(ident, tool, job, pins, approved, input_hash):
    return {"task_id": ident, "tool_id": tool, "job_id": job, "site": "CN", "scope": SCOPE,
            "role": "douyin-" + ROUTES[tool][0], "upstream": pins, "anchor": approved,
            "input_sha256": input_hash, "state": "PENDING_PLATFORM_EVIDENCE",
            "listing_id": None, "sku_id": None, "publish_product_id": None,
            "platform_identity_verified": False, "actual_rights_verified": False,
            "source_media_included": False, "actual_recipient_confirmation": False,
            "execution_authorized": False, "provider_called": False, "model_called": False,
            "missing": ["same_work_platform_verified_listing_id", "same_listing_variant_id",
                        "approved_source_media_and_rights_for_actual_use"],
            "blocked_actions": ["actual_publication", "platform_transfer", "commercial_media_reuse"]}


def render(r):
    anchor = r["anchor"]
    return ("# " + ROUTES[r["tool_id"]][1] + "\n\n"
            + "已留档人审确认同一作品的商品入口、商品页及单只选项。\n\n"
            + f"作品 {anchor['work_id']} · {anchor['product_label']}\n\n"
            + f"原人工回执 SHA-256 {anchor['receipt_sha256']}\n\n"
            + "平台商品 ID、SKU ID、可用原片和商业权利待证，发布、分发和商用保持阻断。\n").encode("utf8")


def receipt(r, raw, report):
    return {k: r[k] for k in ("task_id", "tool_id", "site", "state", "role", "scope", "upstream",
                             "input_sha256", "actual_recipient_confirmation", "execution_authorized",
                             "provider_called", "model_called")} | {
             "result_sha256": digest(raw), "report_sha256": digest(report)}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("此流程仅交接证据摘要。")
    pins, approved = linked
    result = compose(folder.name, tool, p["job_id"], pins, approved, digest(encode(p)))
    raw, report = encode(result), render(result)
    blobs = {"json": raw, "report": report, "receipt": encode(receipt(result, raw, report))}
    paths = {kind: folder / filename for kind, filename in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(blobs[kind])
    return result, paths


def verify_saved(task, files):
    r = task["result"]
    if (task["site"] != "CN" or task["local_files"] != FILES
            or (r["task_id"], r["tool_id"]) != (task["id"], task["source"]["tool_id"])
            or r["upstream"] != task["source"]["role_packet_pins"]
            or digest(encode(r["anchor"])) != FROZEN_ANCHOR_PAYLOAD_SHA256
            or r["upstream"]["human_anchor"] != binding(r["anchor"])
            or r != compose(task["id"], r["tool_id"], r["job_id"], r["upstream"], r["anchor"], r["input_sha256"])
            or json.loads(files["json"]) != r or files["report"] != render(r)
            or json.loads(files["receipt"]) != receipt(r, files["json"], files["report"])):
        raise WorkflowError("历史人工入口记录及回执不一致。")


def public_tools():
    return [{"id": tool, "sites": ["CN"], "role_suffix": role, "label": label,
             "description": "同版接件保留已证明的人工路径及待核字段，商业用途保持阻断。",
             "input_example": {"site": "CN", "scope": SCOPE, "job_id": "", "reference_id": "",
                               "evidence_sha256": "", "work_id": "", **(
                                 {"source_task_id": "", "source_result_sha256": ""} if tool != TEARDOWN else {})},
             "max_media_files": 0}
            for tool, (role, label) in ROUTES.items()]
