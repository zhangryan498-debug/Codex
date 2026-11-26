"""Lead intake of a pinned synthetic ad admission receipt for offline planning."""
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_admission import AD
from workflow_lead_weekly import encode
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL = "lead-ad-admission-plan-intake"
TOOLS = (TOOL,)
FILES = {"json": "ad-lead-plan.json", "receipt": "ad-lead-plan-receipt.json"}
IDENTITY = ("account_id", "shop_id", "sku_id", "sku_revision")
BASE = "site scope currency job_id actor as_of admission_task_id admission_receipt_sha256"
DETAIL = "identity window_start window_end requested_base_minor"


class WorkflowError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def digest(raw):
    return sha256(raw).hexdigest()


def actor_for(site):
    return f"{ROUTES[site][0]}-lead-qa"


def request_key(p):
    return p["job_id"]


def cod_observation(site, prior, at, start, end):
    if site != "PH":
        return None
    source = prior["ph_fulfillment"]
    if not source or source["state"] != "OBSERVED_SYNTHETIC_CLOSED":
        return {"state": "PENDING_FIXTURE_EVIDENCE", "covers_plan_window": False}
    raw = source["raw"]
    observed, expires = stamp(raw["observed_at"]), stamp(raw["valid_until"])
    return {
        "state": "MATURE_FIXTURE_EVIDENCE_READY" if observed <= at < expires else "FIXTURE_EVIDENCE_EXPIRED",
        "source_ref": raw["source_ref"], "sku_id": raw["sku_id"], "sku_revision": raw["revision"],
        "observed_at": raw["observed_at"], "valid_until": raw["valid_until"],
        "covers_plan_window": observed <= start < end <= expires,
        "real_fulfillment_verified": False,
    }


def bind(site, tool, p, artifact):
    check(p, BASE + " " + DETAIL, "负责人广告计划接件")
    require((p["site"], p["scope"], p["currency"], p["actor"]) ==
            (site, SYNTHETIC, ROUTES[site][1], actor_for(site)), "负责人测试角色、站点、货币与范围须对应。")
    identify(p["job_id"], "计划编号")
    task_id(p["admission_task_id"], "准入任务")
    identify(p["admission_receipt_sha256"], "准入回执校验值", r"[a-f0-9]{64}")
    claimed = p["identity"]
    check(claimed, " ".join(IDENTITY), "计划账户、店铺、SKU 及修订")
    for name in IDENTITY[:-1]:
        identify(claimed[name], name)
    amount(claimed["sku_revision"], "SKU 修订", 1, 10**6, False)
    amount(p["requested_base_minor"], "申请基础预算最小货币单位", 1, 10**12, False)
    at, start, end = (stamp(p[field]) for field in ("as_of", "window_start", "window_end"))
    try:
        ident = p["admission_task_id"]
        raw = artifact(ident, "json")
        receipt = artifact(ident, "receipt")
        source = json.loads(raw)
    except (ValueError, KeyError, TypeError, OSError):
        raise WorkflowError("准入任务回执缺失或无法核实。") from None
    require((source.get("tool_id"), source.get("site"), source.get("scope"), source.get("currency")) ==
            (AD, site, SYNTHETIC, p["currency"]), "准入任务站点或范围不匹配。")
    require(digest(receipt) == p["admission_receipt_sha256"], "准入回执校验值不匹配。")
    require(claimed == {name: source["identity"][name] for name in IDENTITY}, "账户、店铺、SKU 或修订不匹配。")
    require(source["state"] == "SYNTHETIC_GATE_CHECKED", "源准入未通过合成证据核对。")
    require([p["window_start"], p["window_end"]] == source["window"], "须引用准入已核观察窗。")
    require(stamp(source["as_of"]) <= at <= start < end, "接件时刻早于准入，或准入计划观察窗已开始/过期。")
    budget = source["budget"]
    approved = budget["approval"]
    require(stamp(approved["from_at"]) <= at < stamp(approved["until"]), "合成资金审批窗已失效。")
    base = budget["buckets"]["base"]["bucket_remaining_minor"]
    require(type(base) is int and p["requested_base_minor"] <= base
            and budget["can_borrow_between_buckets"] is False, "超过基础预算可用额度；品牌与储备额度不可挪用。")
    pins = {"admission": {"task_id": ident, "result_sha256": digest(raw), "receipt_sha256": digest(receipt),
                          "window": source["window"], "identity": claimed, "scope": SYNTHETIC}}
    summary = {
        "state": "FIXTURE_PLAN_RECEIVED", "observation_window": source["window"],
        "goal": source["goal"], "channel": source["channel"], "roi_basis": source["roi"]["target_basis"],
        "budget": {"currency": p["currency"], "bucket": "base", "requested_minor": p["requested_base_minor"],
                   "fixture_base_available_minor": base, "borrow_brand_or_reserve": False,
                   "reservation_made": False, "approval_source_ref": approved["source_ref"]},
        "ph_cod_observation": cod_observation(site, source, at, start, end),
        "office_plan_state": "HOLD_REAL_CONTROLS",
        "real_permissions": "pending", "real_fiscal_controls": "pending",
        "real_spendable_cash_minor": None, "real_pause_or_resume_thresholds": None,
        "activation_authorized": False, "model_called": False, "provider_called": False,
    }
    return pins, summary


def receipt(result, raw):
    return {**{name: result[name] for name in ("site", "scope", "task_id", "tool_id", "actor", "upstream",
              "input_sha256", "state", "office_plan_state", "activation_authorized")}, "result_sha256": digest(raw)}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    pins, summary = linked
    result = {**summary, "task_id": folder.name, "tool_id": tool, "site": site, "scope": SYNTHETIC,
              "job_id": p["job_id"], "actor": p["actor"], "as_of": p["as_of"],
              "input_sha256": digest(encode(p)), "upstream": pins}
    raw = encode(result)
    artifacts = {"json": raw, "receipt": encode(receipt(result, raw))}
    paths = {kind: folder / name for kind, name in FILES.items()}
    for kind, path in paths.items():
        with path.open("xb") as stream:
            stream.write(artifacts[kind])
    return result, paths


def verify_saved(task, files):
    result = task["result"]
    require(task["local_files"] == FILES and json.loads(files["json"]) == result
            and (result["task_id"], result["site"], result["tool_id"]) ==
            (task["id"], task["site"], task["source"]["tool_id"])
            and result["upstream"] == task["source"]["role_packet_pins"]
            and json.loads(files["receipt"]) == receipt(result, files["json"]),
            "负责人离线计划与存档回执不一致。")


def public_tools():
    example = {site: {
        "site": site, "scope": SYNTHETIC, "currency": ROUTES[site][1], "job_id": "", "actor": actor_for(site),
        "as_of": "", "admission_task_id": "", "admission_receipt_sha256": "",
        "identity": {"account_id": "", "shop_id": "", "sku_id": "", "sku_revision": 1},
        "window_start": "", "window_end": "", "requested_base_minor": 1,
    } for site in ROUTES}
    return [{"id": TOOL, "sites": list(ROUTES), "role_suffix": "lead", "label": "负责人｜领取广告准入并编制离线计划",
             "description": "读取同站同修订准入回执和已核观察窗，记录基础预算内的合成计划，实际投放与财务控制仍待核。",
             "input_example": example["CN"], "input_examples": example, "max_media_files": 0}]
