"""Offline warehouse evidence handoff to a separate mall draft intake."""
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

WAREHOUSE = "warehouse-listing-stock-record"
SHOP = "shop-listing-stock-intake"
TOOLS = (WAREHOUSE, SHOP)
FILES = {"json": "listing-stock.json", "report": "listing-stock.md", "receipt": "listing-stock-receipt.json"}
SUBJECT = ("site", "scope", "warehouse_id", "sku_id", "revision")
ITEM = ("source_ref", "observed_at", "units")
OWNER = {
    "CN": ("CN-WAREHOUSE-TEST", "CN-MALL-TEST"),
    "PH": ("PH-WAREHOUSE-TEST", "PH-MALL-TEST"),
}
STATE = {
    "positive": "库存为正",
    "zero": "已核实可售净量为 0",
    "unknown": "待核：缺少盘点、预留或有效期证据",
    "stale": "待核：库存证据已失效或尚未生效",
    "conflict": "待核：盘点与预留口径冲突",
}


class WorkflowError(ValueError):
    pass


def subject(data):
    for key in ("warehouse_id", "sku_id"):
        identify(data[key], key)
    amount(data["revision"], "版本", 1, 10**6, False)
    return {key: data[key] for key in SUBJECT}


def record(data, expected, label):
    if data is None:
        return None
    check(data, " ".join((*SUBJECT, *ITEM)), label)
    if subject(data) != expected:
        raise WorkflowError(f"{label}的站点、仓位、SKU、修订版或证据范围不一致。")
    identify(data["source_ref"], label + "来源")
    stamp(data["observed_at"])
    amount(data["units"], label + "数量")
    return data


def bind(site, tool, data, fetch):
    base = " ".join(SUBJECT) + " job_id owner as_of"
    check(data, base + (" count reservation expires_at" if tool == WAREHOUSE else " warehouse_task_id"),
          "库存交接")
    if data["scope"] != SYNTHETIC or site not in ROUTES or data["site"] != site:
        raise WorkflowError("请选择本次合成库存证据所属的站点。")
    expected = subject(data)
    identify(data["job_id"], "任务标识")
    identify(data["owner"], "测试岗位身份")
    when = stamp(data["as_of"])
    if tool == WAREHOUSE:
        count = record(data["count"], expected, "实盘记录")
        reserve = record(data["reservation"], expected, "预留记录")
        observed = [stamp(row["observed_at"]) for row in (count, reserve) if row is not None]
        if observed and max(observed) > when:
            raise WorkflowError("盘点或预留记录晚于仓库交接时间。")
        if data["expires_at"] is not None:
            expires = stamp(data["expires_at"])
            if observed and expires <= max(observed):
                raise WorkflowError("库存证据失效时间须晚于盘点及预留快照。")
        return {}, None
    task_id(data["warehouse_task_id"], "仓库记录任务号")
    upstream, pin = fetch(data["warehouse_task_id"], site, WAREHOUSE)
    if any(upstream[key] != expected[key] for key in SUBJECT):
        raise WorkflowError("仓库记录和商城任务的站点、仓位、SKU 或修订版不一致。")
    if when < stamp(upstream["as_of"]):
        raise WorkflowError("商城收件时间早于仓库交接时间。")
    if upstream["owner"] == data["owner"]:
        raise WorkflowError("独立收件须使用商城测试岗位身份。")
    return {"warehouse": pin}, upstream


def assessment(warehouse, when):
    count, reservation, expiry = (warehouse[key] for key in ("count", "reservation", "expires_at"))
    if count is None or reservation is None or expiry is None:
        return "unknown", None
    gross, held = count["units"], reservation["units"]
    if stamp(when) >= stamp(expiry):
        return "stale", None
    if gross is None or held is None:
        return "unknown", None
    # This flow uses mirrored WMS snapshots with a common observation cutoff.
    if stamp(count["observed_at"]) != stamp(reservation["observed_at"]) or count["source_ref"] == reservation["source_ref"] or held > gross:
        return "conflict", None
    net = gross - held
    return ("positive" if net else "zero"), net


def receipt(result, artifacts):
    keys = ("task_id", "tool_id", "job_id", *SUBJECT, "owner", "as_of", "upstream",
            "stock_state", "available_units", "execution_authorized")
    return {**{key: result[key] for key in keys},
            "result_sha256": sha256(artifacts["json"]).hexdigest(),
            "report_sha256": sha256(artifacts["report"]).hexdigest()}


def run_workflow(project, site, tool, data, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("本地库存交接只接收合成的结构化证据。")
    pins, upstream = linked
    warehouse = data if tool == WAREHOUSE else upstream
    state, units = assessment(warehouse, data["as_of"])
    issue = tool == WAREHOUSE
    result = {
        **subject(data), **{key: data[key] for key in ("job_id", "owner", "as_of")},
        **{key: warehouse[key] for key in ("count", "reservation", "expires_at")},
        "task_id": folder.name, "tool_id": tool, "upstream": pins,
        "stock_state": state, "stock_reason": STATE[state], "available_units": units,
        "status": "WAREHOUSE_RECORDED" if issue else ("LOCAL_DRAFT_READY" if units else "DRAFT_BLOCKED"),
        "listing_draft": {**subject(data), "sellable_units": units,
                          "status": "synthetic_offline_draft"} if not issue and units else None,
        "execution_authorized": False, "model_called": False, "shop_api_called": False,
    }
    lines = [
        f"# {data['job_id']} · {'仓库发出' if issue else '商城独立收件'}",
        "", "合成离线测试。商城正库存仅可形成此流程的本机上架草稿。",
        f"站点 {site} · 仓位 {data['warehouse_id']} · SKU {data['sku_id']} · 第 {data['revision']} 版",
        f"测试岗位 {data['owner']} · {data['as_of']}",
        f"库存判断：{result['stock_reason']}。可售净量：{units if units is not None else '未知'}。",
    ]
    if not issue:
        lines.append(f"仓库记录 {pins['warehouse']['task_id']}。"
                     + ("本机上架草稿已就绪。" if units else "已拦截本机上架草稿。"))
    artifacts = {"json": encode(result), "report": ("\n".join(lines) + "\n").encode("utf-8")}
    artifacts["receipt"] = encode(receipt(result, artifacts))
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(artifacts[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    if (task["local_files"] != FILES or result != json.loads(files["json"])
            or result["upstream"] != task["source"]["role_packet_pins"]
            or (result["task_id"], result["site"], result["tool_id"]) !=
               (task["id"], task["site"], task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(result, files)):
        raise WorkflowError("库存交接回执无法复核。")


def public_tools():
    examples = {}
    for site, (warehouse_owner, shop_owner) in OWNER.items():
        common = {"site": site, "scope": SYNTHETIC, "warehouse_id": f"{site}-TEST-WH",
                  "sku_id": f"{site}-TEST-SKU", "revision": 1}
        at = "2026-11-21T10:00:00+08:00"
        examples[site] = {
            WAREHOUSE: {**common, "job_id": f"{site}-DEMO-WAREHOUSE", "owner": warehouse_owner,
                        "as_of": at, "expires_at": "2026-11-21T12:00:00+08:00",
                        "count": {**common, "source_ref": "TEST-COUNT", "observed_at": at, "units": 24},
                        "reservation": {**common, "source_ref": "TEST-RESERVE", "observed_at": at, "units": 6}},
            SHOP: {**common, "job_id": f"{site}-DEMO-MALL", "owner": shop_owner,
                   "as_of": at, "warehouse_task_id": "填入已完成的仓库任务号"},
        }
    return [
        {"id": tool, "sites": list(ROUTES), "role_suffix": role, "label": label,
         "description": description, "input_examples": {site: cases[tool] for site, cases in examples.items()}}
        for tool, role, label, description in (
            (WAREHOUSE, "selection", "仓库库存记录发出（合成本机）", "记录同站、同仓、同 SKU、同版盘点和预留，并将生成的任务号交给商城。"),
            (SHOP, "shop-ops", "商城库存收件与草稿检查（合成本机）", "填写仓库任务号和本岗位信息，领用同版库存证据并查看草稿判断。"),
        )
    ]

