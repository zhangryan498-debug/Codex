"""Read a saved PH competitor stock observation at the separate mall role."""

from hashlib import sha256
import json

from role_tools import PROVIDER, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, task_id

SOURCE = "fastmoss-stock-reconciliation"
TOOL = "ph-shop-competitor-stock-readback"
TOOLS = (TOOL,)
FILES = {"json": "stock-receipt-result.json", "report": "stock-readback.md", "receipt": "stock-handoff-receipt.json"}
COMMON = "site scope job_id handoff_version receiver source_task_id source_result_sha256 snapshot_sha256 product_id sku_id"


class WorkflowError(ValueError):
    pass


def request_key(payload):
    return f"{payload['job_id']}:{payload['handoff_version']}"


def bind(site, payload, fetch):
    check(payload, COMMON, "菲律宾商城竞品库存接件")
    if site != "PH" or payload["site"] != site or payload["scope"] not in (PROVIDER, SYNTHETIC):
        raise WorkflowError("仅接菲律宾站获准历史观测或明确合成来源。")
    identify(payload["job_id"], "接件编号")
    amount(payload["handoff_version"], "接件版次", 1, 10**6, False)
    term(payload["receiver"], "商城测试角色")
    task_id(payload["source_task_id"], "竞品任务编号")
    for key in ("source_result_sha256", "snapshot_sha256"):
        identify(payload[key], key, r"[a-f0-9]{64}")
    for key in ("product_id", "sku_id"):
        identify(payload[key], key)
    original, pin = fetch(payload["source_task_id"], site)
    data = original["result"]
    fingerprint = data["source"]["canonical_payload_sha256"]
    if (original["scope"] != payload["scope"] or data["product_id"] != payload["product_id"]
            or payload["source_result_sha256"] != pin["result_sha256"]
            or payload["snapshot_sha256"] != fingerprint):
        raise WorkflowError("竞品来源、范围、商品、快照版本或产物哈希不一致。")
    if original["scope"] == PROVIDER:
        raise WorkflowError("获准的供应商历史快照和绑定留在原工作台。独立工作区仅复现合成接件。")
    sku = next((row for row in data["skus"] if row["sku_id"] == payload["sku_id"]), None)
    if sku is None:
        raise WorkflowError("该快照未返回本次精确 SKU，不能拿同名项代替。")
    warehouse = data.get("warehouse_readback")
    if warehouse is not None and (original["scope"] != SYNTHETIC or warehouse["subject"]["sku_id"] != payload["sku_id"]):
        raise WorkflowError("仓库记录范围或 SKU 与来源不符。")
    return {"competitor": pin, "snapshot_sha256": fingerprint}, (data, sku, warehouse)


def receipt(result, binary):
    return {key: result[key] for key in (
        "task_id", "site", "scope", "job_id", "handoff_version", "receiver", "upstream",
        "state", "input_sha256", "execution_authorized", "personnel_signoff_verified"
    )} | {"result_sha256": sha256(binary["json"]).hexdigest(), "report_sha256": sha256(binary["report"]).hexdigest()}


def run_workflow(project, site, tool, payload, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("读取已存竞品任务，无需新附件。")
    pin, (source, sku, warehouse) = linked
    real = payload["scope"] == PROVIDER
    same_source_conflicts = [row for row in source["conflicts"] if sku["sku_id"] in row.get("sku_ids", ())]
    synthetic_net = warehouse["net_sellable_units_synthetic"] if warehouse is not None else None
    warehouse_state = "SYNTHETIC_ZERO" if synthetic_net == 0 else "SYNTHETIC_POSITIVE" if synthetic_net else "UNKNOWN"
    result = {key: payload[key] for key in COMMON.split()}
    result.update(task_id=folder.name, tool_id=tool, upstream=pin,
                  input_sha256=sha256(encode(payload)).hexdigest(),
                  state="ARCHIVED_OBSERVATION_RECEIVED" if real else "SYNTHETIC_READBACK_RECEIVED",
                  source_kind="archived_provider_report" if real else "synthetic_offline",
                  provider_or_fixture_observation={
                      "reported_sku_units": sku["stock_units"], "reported_sku_state": sku["stock_state"],
                      "same_source_conflicts": same_source_conflicts, "source_values_modified": False,
                      "archived_observed_on": "2026-11-16" if real else None,
                      "archived_reference_window_days": 28 if real else None,
                  },
                  warehouse_determination={"state": warehouse_state, "synthetic_warehouse": warehouse,
                                           "real_warehouse_verified": False, "real_sku_revision": None,
                                           "real_net_sellable": None},
                  marketplace_action_allowed=False, execution_authorized=False, personnel_signoff_verified=False,
                  model_called=False, provider_called=False)
    lines = [f"# 菲律宾商城竞品库存证据接件 | {result['job_id']} v{result['handoff_version']}", "",
             f"商城测试收件角色 {result['receiver']}。竞品任务 {result['source_task_id']}；快照 {result['snapshot_sha256']}。",
             f"商品 {result['product_id']}，SKU {result['sku_id']}。",
             f"来源 {'历史供应商观测（2026-11-16）' if real else '合成竞品视图'}。SKU 明细 {sku['stock_units']}；同源冲突 {len(same_source_conflicts)}。",
             f"仓库状态 {warehouse_state}，合成净量 {synthetic_net if synthetic_net is not None else '未知'}。",
             "真实仓库、真实商品修订、真实净可售仍未知。此接件无平台动作授权，也无真人签收。"]
    binary = {"json": encode(result), "report": ("\n".join(lines) + "\n").encode("utf-8")}
    binary["receipt"] = encode(receipt(result, binary))
    paths = {key: folder / filename for key, filename in FILES.items()}
    for key, path in paths.items():
        with path.open("xb") as output:
            output.write(binary[key])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or result["upstream"] != task["source"]["role_packet_pins"]
            or (result["task_id"], result["site"], result["tool_id"]) !=
            (task["id"], task["site"], task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(result, files)):
        raise WorkflowError("竞品库存接件凭据无法核对。")


def public_tools():
    return [{"id": TOOL, "sites": ["PH"], "role_suffix": "shop-ops",
             "label": "菲律宾商城竞品库存证据接件",
             "description": "读取同站同 SKU 的已有快照，分别保留来源冲突、合成净量和真实仓库未知。",
             "input_example": {"site": "PH", "scope": PROVIDER, "job_id": "PH-C12-QA", "handoff_version": 1,
                               "receiver": "PH-MALL-QA", "source_task_id": None, "source_result_sha256": None,
                               "snapshot_sha256": None, "product_id": None, "sku_id": None}}]

