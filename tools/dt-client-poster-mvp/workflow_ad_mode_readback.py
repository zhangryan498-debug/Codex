"""Read an archived ad report from the Mall or Live role without granting any authority."""

from datetime import datetime
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_ads import TOOL_ID as SOURCE_TOOL
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import check, identify, task_id

SHOP = "shop-ad-mode-readback"
LIVE = "live-ad-mode-readback"
TOOLS = (SHOP, LIVE)
MODES = {SHOP: {"CN": "qianchuan_product", "PH": "product_gmv_max"},
         LIVE: {"CN": "qianchuan_live", "PH": "live_gmv_max_target_roi"}}
MODE_LABELS = {"qianchuan_product":"千川商品", "qianchuan_live":"千川直播",
               "product_gmv_max":"Product GMV Max", "tiktok_purchase_ads":"External Purchase Ads",
               "live_gmv_max_target_roi":"LIVE GMV Max Target ROI"}
FILES = {"json": "ad-mode-readback.json", "receipt": "ad-mode-readback-receipt.json", "report": "ad-mode-readback.md"}
IDENTITY = ("account_id", "shop_id", "plan_id", "product_id", "sku_id", "room_id")
PERIOD = ("window_start", "window_end")
AUTHORITY = dict(execution_authorized=False, actual_account_rights_verified=False,
                 actual_ad_action_taken=False, platform_settings_changed=False,
                 model_called=False, provider_called=False)


class WorkflowError(ValueError):
    pass


def _instant(value):
    try:
        at = datetime.fromisoformat(value)
        if at.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError):
        raise WorkflowError("时间需要显式时区。") from None
    return at


def bind(site, tool, payload, load):
    check(payload, "site currency scope job_id owner evaluated_at expected source", "投放原件收件")
    if (payload["site"], payload["currency"], payload["scope"]) != (site, ROUTES[site][1], SYNTHETIC):
        raise WorkflowError("仅接收同站同币种的本地合成原件。")
    identify(payload["job_id"], "收件编号")
    term(payload["owner"], "测试收件人")
    at = _instant(payload["evaluated_at"])
    expected = payload["expected"]
    check(expected, "mode " + " ".join(IDENTITY + PERIOD), "所需归档范围")
    if expected["mode"] != MODES[tool][site]:
        raise WorkflowError("所需模式不属于当前站点与接收岗位。")
    for key in IDENTITY:
        if key not in ("sku_id", "room_id") or expected[key] is not None:
            identify(expected[key], key)
    if tool == SHOP and expected["room_id"] is not None:
        raise WorkflowError("商品模式不绑定直播间。")
    starts, ends = (_instant(expected[key]) for key in PERIOD)
    if not starts < ends <= at:
        raise WorkflowError("收件时点晚于或等于已结束的观察窗口。")
    source = payload["source"]
    summary = {"state":"PENDING_SOURCE", "expected":expected, "observed_mode":None,
               "mode_verified":False, "bound_report":None, "pending_fields":["source"],
               "conflicts":[]}
    if source is None:
        return {}, summary
    check(source, "task_id receipt_sha256 result_sha256", "封存原件校验")
    task_id(source["task_id"], "上游任务")
    for key in ("receipt_sha256", "result_sha256"):
        identify(source[key], key, r"[a-f0-9]{64}")
    data, pin = load(source["task_id"], site, SOURCE_TOOL)
    if pin != source:
        return {}, {**summary, "state":"RETURNED_SOURCE_HASH", "pending_fields":[], "conflicts":["source_hash"]}
    pins = {pin["task_id"]: pin}
    if data["data_origin"] != SYNTHETIC or data["execution_authorized"] is not False:
        raise WorkflowError("投放原件缺少离线范围证明。")
    basis, windows = data["measurement_basis"], data["facts"]
    mode = basis["channel"]
    summary["observed_mode"] = mode
    if mode != expected["mode"]:
        return pins, {**summary, "state":"RETURNED_MODE_MISMATCH", "pending_fields":[], "conflicts":["mode"]}
    if not windows or any(_instant(window["captured_at"]) > at for window in windows):
        return pins, {**summary, "state":"RETURNED_SOURCE_TIMING", "pending_fields":[], "conflicts":["capture_after_readback"]}
    actual = {key: basis.get(key) for key in IDENTITY}
    actual.update(window_start=windows[0]["start"], window_end=windows[-1]["end"])
    conflicts = [key for key in IDENTITY if expected[key] is not None and actual[key] is not None
                 and expected[key] != actual[key]]
    conflicts += [key for key in PERIOD if _instant(actual[key]) != _instant(expected[key])]
    if conflicts:
        return pins, {**summary, "state":"RETURNED_IDENTITY_MISMATCH", "pending_fields":[], "conflicts":conflicts}
    required = ("sku_id", "room_id") if tool == LIVE else ("sku_id",)
    pending = [key for key in required if actual[key] is None or expected[key] is None]
    return pins, {**summary, "state":"PENDING_IDENTITY" if pending else "OFFLINE_MODE_REPORT_RECEIVED",
                  "mode_verified":True, "pending_fields":pending,
                  "bound_report":{**actual,"platform":basis["platform"],"timezone":basis["timezone"],
                                  "windows":[{key:w[key] for key in ("start","end","captured_at","source_id")}
                                             for w in windows]}}


def _receipt(r, raw, report):
    return {**{key:r[key] for key in ("task_id", "tool_id", "site", "state", "upstream", "input_sha256")},
            "result_sha256":sha256(raw).hexdigest(), "report_sha256":sha256(report).hexdigest(),
            "execution_authorized":False}


def run_workflow(project, site, tool, payload, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("只读取工作台已封存的投放报告。")
    pins, summary = linked
    r = {**summary, **AUTHORITY, **{key:payload[key] for key in ("site","scope","currency","job_id","owner","evaluated_at")},
         "task_id":folder.name, "tool_id":tool, "receipt_version":1, "upstream":pins,
         "input_sha256":sha256(encode(payload)).hexdigest()}
    raw = encode(r)
    mode, observed = r["expected"]["mode"], r["observed_mode"]
    lines = [f"# {site} | {r['job_id']} | {r['state']}", "",
             f"请求模式 {MODE_LABELS[mode]}。原件模式 {MODE_LABELS.get(observed, observed) if observed else '未知'}。", "",
             "此回执仅核对离线合成原件。实际账号权限、计划设置与投放均未验证。", ""]
    if r["pending_fields"]:
        fields = {"source":"同模式原件","sku_id":"SKU","room_id":"直播间"}
        lines.append("待核 " + "、".join(fields[field] for field in r["pending_fields"]) + "。")
    report = ("\n".join(lines) + "\n\n" + raw.decode()).encode()
    paths = {key:folder/name for key,name in FILES.items()}
    for key, value in {"json":raw, "report":report, "receipt":encode(_receipt(r,raw,report))}.items():
        with paths[key].open("xb") as output:
            output.write(value)
    return r, paths


def verify_saved(task, files):
    r = task["result"]
    if (task["local_files"] != FILES or json.loads(files["json"]) != r or r["upstream"] != task["source"]["role_packet_pins"]
            or (r["task_id"],r["site"],r["tool_id"]) != (task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"]) != _receipt(r,files["json"],files["report"])):
        raise WorkflowError("归档投放收件文件与回执不一致。")


def public_tools():
    return [
        {"id":tool, "sites":list(ROUTES), "role_suffix":role, "label":label,
         "description":"核对本地投放原件的模式、账户、计划、商品与时段，缺失的 SKU 或直播间保持待核。",
         "input_example":{}, "max_media_files":0}
        for tool, role, label in ((SHOP,"shop-ops","商城｜核对商品投放归档"), (LIVE,"live-ops","直播｜核对直播投放归档"))
    ]

