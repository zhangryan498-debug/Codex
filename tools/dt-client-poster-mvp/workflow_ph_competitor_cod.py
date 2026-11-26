"""PH competitor COD handoff for a sealed offline rehearsal."""

from hashlib import sha256
import json
from fractions import Fraction

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, task_id

SOURCE = "ph-competitor-offline-cod-evidence"
INTAKE = "ph-shop-offline-cod-intake"
TOOLS = (SOURCE, INTAKE)
FILES = {"json": "cod-evidence.json", "report": "cod-evidence.md", "receipt": "cod-receipt.json"}
CASE_ID = "PH-ECO-05"
CASE_SHA256 = "b69186593f22dad3637a19785c832dfc3ce0a48a7385d3a4623c75590b6e35e9"
COMMON = "site scope currency handoff_id version"


class WorkflowError(ValueError):
    pass


def fixture_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def bind(site, tool, payload, fetch):
    fields = "author archived_fixture" if tool == SOURCE else "receiver source_task_id source_result_sha256"
    check(payload, COMMON + " " + fields, "菲律宾 COD 本机交接")
    if (site, payload["site"], payload["scope"], payload["currency"]) != ("PH", "PH", SYNTHETIC, "PHP"):
        raise WorkflowError("仅使用菲律宾站、PHP 的合成离线证据。")
    identify(payload["handoff_id"], "交接编号")
    amount(payload["version"], "证据版次", 1, 10**6, False)
    if tool == SOURCE:
        term(payload["author"], "具名竞品测试角色")
        if fixture_hash(payload["archived_fixture"]) != CASE_SHA256:
            raise WorkflowError("离线演练只接收已封存的 PH-ECO-05 合成原件。")
        return {"case_sha256": CASE_SHA256}, None

    term(payload["receiver"], "具名商城测试角色")
    task_id(payload["source_task_id"], "竞品证据任务")
    identify(payload["source_result_sha256"], "竞品产物哈希", r"[a-f0-9]{64}")
    original, pin = fetch(payload["source_task_id"], site, SOURCE)
    identity = ("site", "scope", "currency", "handoff_id", "version")
    if (any(original[key] != payload[key] for key in identity)
            or original["state"] != "SYNTHETIC_COD_RESEARCH_READY"
            or payload["source_result_sha256"] != pin["result_sha256"]
            or original["source_kind"] != "sealed_synthetic_fixture"
            or payload["receiver"].strip().casefold() == original["author"].strip().casefold()):
        raise WorkflowError("上游编号、版本、哈希或独立收件角色不匹配。")
    return {"competitor": pin, "case_sha256": CASE_SHA256}, original


def request_key(payload):
    return f"{payload['handoff_id']}:{payload['version']}"


def _fraction(numerator, denominator):
    value = Fraction(numerator, denominator)
    return {"numerator": numerator, "denominator": denominator, "exact": str(value)}


def _cohort(row):
    delivered, rejected, pending = row["matured_delivered"], row["matured_rejected"], row["pending"]
    matured = delivered + rejected
    revenue = row["provided_seller_confirmed_goods_revenue_per_delivered"] * 100
    common = row["packaging_per_attempt"] + row["outbound_shipping_per_attempt"] + row["ad_acquisition_cost_per_attempt"]
    fees = {name: revenue * percent // 100 for name, percent in row["fee_rates_percent"].items()}
    delivered_contribution = revenue - (row["goods_cost"] + common) * 100 - sum(fees.values())
    rejected_cost = (common + row["reverse_shipping_per_rejection"]) * 100
    mature_contribution = delivered * delivered_contribution - rejected * rejected_cost
    recognized = revenue * delivered
    return {
        "shop_id": row["shop_id"], "source_id": row["source_id"], "attempts": row["attempts"],
        "matured_denominator": matured, "matured_delivered": delivered, "terminal_rejected": rejected,
        "pending": pending, "recognized_goods_revenue_minor": recognized,
        "named_delivered_fees_minor": {name: charge * delivered for name, charge in fees.items()},
        "matured_contribution_minor": mature_contribution,
        "contribution_per_matured_attempt_minor": _fraction(mature_contribution, matured),
        "contribution_margin": _fraction(mature_contribution, recognized),
        "delivery_rate": _fraction(delivered, matured), "terminal_rejection_rate": _fraction(rejected, matured),
        "pending_share": _fraction(pending, row["attempts"]),
        "scenario_lower_minor": mature_contribution - pending * rejected_cost,
        "scenario_upper_minor": mature_contribution + pending * delivered_contribution,
        "break_even_delivery_rate": _fraction(rejected_cost, delivered_contribution + rejected_cost),
    }


def _receipt(result, files):
    return {
        "task_id": result["task_id"], "site": result["site"], "scope": result["scope"],
        "tool_id": result["tool_id"], "handoff_id": result["handoff_id"], "version": result["version"],
        "state": result["state"], "upstream": result["upstream"], "input_sha256": result["input_sha256"],
        "result_sha256": sha256(files["json"]).hexdigest(), "report_sha256": sha256(files["report"]).hexdigest(),
        "execution_authorized": False, "personnel_signoff_verified": False,
    }


def run_workflow(project, site, tool, payload, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("该本机交接不接收附件。")
    pins, original = linked
    result = {key: payload[key] for key in COMMON.split()}
    result.update({
        "task_id": folder.name, "tool_id": tool, "upstream": pins,
        "source_kind": "sealed_synthetic_fixture", "source_case_id": CASE_ID,
        "input_sha256": sha256(encode(payload)).hexdigest(),
        "execution_authorized": False, "personnel_signoff_verified": False,
        "model_called": False, "provider_called": False,
        "real_evidence": dict.fromkeys(("settlement", "refunds", "refused_delivery", "pending", "commission", "maturity")),
    })
    if tool == SOURCE:
        case = payload["archived_fixture"]
        body = case["input"]
        result.update(state="SYNTHETIC_COD_RESEARCH_READY", author=payload["author"],
                      cohort_window=body["cohort_window"], as_of=body["as_of"],
                      fixture_definitions=body["fixed_test_definitions"],
                      cohorts=[_cohort(row) for row in body["shop_rows"]])
        actor = f"竞品测试角色 {result['author']}"
    else:
        keys = ("author", "cohort_window", "as_of", "fixture_definitions", "cohorts")
        result.update({key: original[key] for key in keys})
        result.update(state="SYNTHETIC_COD_RECEIVED", receiver=payload["receiver"],
                      source_task_id=original["task_id"])
        actor = f"商城测试角色 {result['receiver']}，接收竞品 {result['author']} 的任务 {result['source_task_id']}"
    lines = [f"# 菲律宾 COD 成熟队列合成交接 | {result['handoff_id']} v{result['version']}", "",
             actor, "", "范围：PH-ECO-05 封存合成。真实回款、退款、拒收、待决、佣金和成熟窗均待核。",
             "仅供本机研究留档。平台动作授权及真人签收均未确认。", "",
             f"合成队列 {result['cohort_window'][0]} 至 {result['cohort_window'][1]}，截点 {result['as_of']}。"]
    for row in result["cohorts"]:
        lines.append(f"- {row['shop_id']}：成熟 {row['matured_denominator']}，"
                     f"签收 {row['matured_delivered']}，拒收 {row['terminal_rejected']}，待决 {row['pending']}；"
                     f"成熟确认收入及贡献（PHP 分）{row['recognized_goods_revenue_minor']} / {row['matured_contribution_minor']}。")
    binary = {"json": encode(result), "report": ("\n".join(lines) + "\n").encode("utf-8")}
    binary["receipt"] = encode(_receipt(result, binary))
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
            or json.loads(files["receipt"]) != _receipt(result, files)):
        raise WorkflowError("COD 产物、来源或收件回执无法核对。")


def public_tools():
    return [
        {"id": SOURCE, "sites": ["PH"], "role_suffix": "competitor-analyst",
         "label": "菲律宾 COD 成熟队列合成证据", "description": "登记封存的 PH-ECO-05 离线演练；真实结算保持待核。",
         "input_example": {"site": "PH", "scope": SYNTHETIC, "currency": "PHP", "handoff_id": "PH-C11-QA",
                           "version": 1, "author": "PH-COMP-QA", "archived_fixture": None}},
        {"id": INTAKE, "sites": ["PH"], "role_suffix": "shop-ops",
         "label": "菲律宾商城 COD 证据收件", "description": "核对独立测试角色、竞品任务、版次和结果哈希后收件。",
         "input_example": {"site": "PH", "scope": SYNTHETIC, "currency": "PHP", "handoff_id": "PH-C11-QA",
                           "version": 1, "receiver": "PH-MALL-QA", "source_task_id": None, "source_result_sha256": None}},
    ]

