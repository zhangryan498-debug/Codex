"""Local trial plans and cohort reviews, driven only by frozen synthetic inputs."""

from datetime import timedelta
from fractions import Fraction
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, ratio, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL = "selection-offline-trial"
TOOLS = (TOOL,)
FILES = {"json": "selection-trial.json", "report": "selection-trial.md", "receipt": "selection-trial-receipt.json"}
CHANNELS = {"CN": ("organic", "qianchuan", "creator"), "PH": ("short_video", "affiliate", "ads", "live")}
METRICS = {"CN": ("ctr_bps", "cvr_bps", "paid_orders", "net_roi_bps"),
           "PH": ("signed_gmv_minor", "net_roi_bps")}


class WorkflowError(ValueError):
    pass


def validate_plan(site, p, cutoff):
    amount(p["window_days"], "观察天数", 1, 60, False)
    amount(p["batch_cap_minor"], "测品批次预算硬上限", 0, 10**12, False)
    task_id(p["finance_task_id"], "本机合成测算")
    term(p["owner"], "负责人")
    choices = p["channel_choices"]
    check(choices, " ".join(CHANNELS[site]), "本站测品渠道")
    for name, r in choices.items():
        check(r, "state reason", f"渠道 {name}")
        if r["state"] not in ("selected", "conditional", "not_selected"):
            raise WorkflowError("渠道须标选用、条件待核或不选用。")
        term(r["reason"], "选路理由")
    criteria = p["criteria"]
    check(criteria, "metric success_at_least failure_at_most sample_unit min_sample max_orders min_signed_rate_bps elimination_rule",
          "前置成功与失败线")
    if criteria["metric"] not in METRICS[site] or criteria["sample_unit"] not in (
        ("orders",) if site == "PH" else ("orders", "impressions")
    ):
        raise WorkflowError("主指标或样本分母与站点不符。")
    low, high = (criteria[k] for k in ("failure_at_most", "success_at_least"))
    lo = -10**12 if criteria["metric"] == "net_roi_bps" else 0
    hi = 10000 if criteria["metric"] in ("ctr_bps", "cvr_bps") else 10**12
    amount(low, "失败线", lo, hi, False)
    amount(high, "成功线", lo, hi, False)
    if low >= high:
        raise WorkflowError("失败线须低于成功线。")
    amount(criteria["min_sample"], "最低样本", 1, 10**12, False)
    amount(criteria["max_orders"], "最多下单数", 1, 10**12, site == "CN")
    if criteria["sample_unit"] == "orders" and criteria["max_orders"] is not None and criteria["min_sample"] > criteria["max_orders"]:
        raise WorkflowError("最低订单样本超过控量上限。")
    if site == "PH":
        amount(criteria["min_signed_rate_bps"], "签收门槛", 1, 10000, False)
    elif criteria["min_signed_rate_bps"] is not None:
        raise WorkflowError("抖音测品不套用菲律宾签收门槛。")
    term(criteria["elimination_rule"], "淘汰和再测规则")
    arms = p["arms"]
    if not isinstance(arms, dict) or not 1 <= len(arms) <= 6:
        raise WorkflowError("每批使用一至六个具名渠道组。")
    cohort_keys = []
    for name, r in arms.items():
        identify(name, "渠道组编号")
        check(r, "channel starts_at cap_minor material_ref comparison_basis cohort_key", "测试组")
        identify(r["cohort_key"], "预先绑定的订单样本编号")
        c = r["channel"]
        if c not in choices or choices[c]["state"] != "selected":
            raise WorkflowError("只能给已选的本站渠道建组。")
        if stamp(r["starts_at"]) <= cutoff:
            raise WorkflowError("先保存合成评判标准，再开始窗口。")
        amount(r["cap_minor"], "渠道预算上限", 0, p["batch_cap_minor"], False)
        term(r["material_ref"], "合成素材或配置出处")
        term(r["comparison_basis"], "商品、报价和采样条件")
        cohort_keys.append(r["cohort_key"])
    if len(set(cohort_keys)) != len(arms):
        raise WorkflowError("分组须预先绑定不同合成样本。")


def validate_observation(site, p, cutoff):
    task_id(p["plan_task_id"], "冻结计划")
    rows = p["observations"]
    if not isinstance(rows, dict) or not 1 <= len(rows) <= 6:
        raise WorkflowError("每个具名组提供一条可追踪观察。")
    extra = ("signed_orders terminal_failures open_orders signed_gmv_minor" if site == "PH"
             else "impressions clicks paid_orders")
    for key, r in rows.items():
        identify(key, "观察组编号")
        check(r, "observed_at cohort_key source_ref spent_minor placed_orders net_contribution_minor " + extra, "窗口快照")
        if stamp(r["observed_at"]) > cutoff:
            raise WorkflowError("不能使用截点后的证据。")
        identify(r["cohort_key"], "订单样本编号")
        term(r["source_ref"], "合成观察出处", True)
        amount(r["net_contribution_minor"], "已知合成净贡献", -10**12, 10**12)
        for k in ("spent_minor", "placed_orders", *extra.split()):
            amount(r[k], k)


def bind(site, tool, p, fetch):
    kind = p.get("kind") if isinstance(p, dict) else None
    shared = "kind site currency scope batch_id sku_id revision as_of "
    if kind not in ("plan", "snapshot"):
        raise WorkflowError("请选择先存计划或后评观察。")
    check(p, shared + ("window_days batch_cap_minor finance_task_id owner channel_choices criteria arms" if kind == "plan"
                       else "plan_task_id observations"), "测品任务")
    if (p["site"], p["currency"], p["scope"]) != (site, ROUTES[site][1], SYNTHETIC):
        raise WorkflowError("只使用同一站点、本币及明确合成的测品数据。")
    for k in ("batch_id", "sku_id"):
        identify(p[k], k)
    amount(p["revision"], "计划版本", 1, 10**6, False)
    cutoff = stamp(p["as_of"])
    if kind == "plan":
        validate_plan(site, p, cutoff)
        source, pin = fetch(p["finance_task_id"], site, "selection-profit-sensitivity")
        if (source["scope"], source["currency"], source["sku_id"]) != (SYNTHETIC, p["currency"], p["sku_id"]):
            raise WorkflowError("选品测算与本站样本 SKU 不一致。")
        return {"finance": pin}, source
    validate_observation(site, p, cutoff)
    source, pin = fetch(p["plan_task_id"], site, TOOL)
    identity = ("scope", "batch_id", "sku_id", "revision")
    if source["kind"] != "plan" or any(p[k] != source[k] for k in identity) or cutoff <= stamp(source["as_of"]):
        raise WorkflowError("快照须引用原冻结批次、版本和 SKU，并晚于计划。")
    plan = source["plan"]
    if p["observations"].keys() != plan["arms"].keys():
        raise WorkflowError("快照须保留全部测试组，缺字段应写 null。")
    if len({r["cohort_key"] for r in p["observations"].values()}) != len(p["observations"]):
        raise WorkflowError("同一样本编号不可在两组重复计数。")
    for name, row in p["observations"].items():
        start=stamp(plan["arms"][name]["starts_at"])
        capture=stamp(row["observed_at"])
        if row["cohort_key"] != plan["arms"][name]["cohort_key"]:
            raise WorkflowError("观察样本编号必须与该组原冻结编号一致。")
        if cutoff>=start and capture != min(cutoff,start+timedelta(days=plan["window_days"])):
            raise WorkflowError("观察必须对齐当前截点或已关闭的固定窗口，不混入早晚快照。")
    return {"plan": pin}, source


def request_key(p):
    if p["kind"] == "plan":
        return f'{p["batch_id"]}:plan'
    return f'{p["batch_id"]}:snapshot:{p["revision"]}:{stamp(p["as_of"]).timestamp()}'


def budget(spent, cap):
    if spent is None:
        return "PENDING_SPEND"
    if spent >= cap:
        return "PAUSE_SPEND_RECOMMENDED"
    return "WITHIN_SYNTHETIC_CAP"


def check_arm(site, obs, arm, plan):
    starts = stamp(arm["starts_at"])
    ended = stamp(obs["observed_at"]) >= starts + timedelta(days=plan["window_days"])
    source = bool(obs["source_ref"]) and stamp(obs["observed_at"]) >= starts
    spent = obs["spent_minor"] if source else None
    placed = obs["placed_orders"] if source else None
    c = plan["criteria"]
    budget_state = budget(spent, arm["cap_minor"])
    figures = {"placed_orders": placed, "spent_minor": spent}
    flags = []
    if site == "PH":
        signed, failed, opened = (obs[k] if source else None for k in ("signed_orders", "terminal_failures", "open_orders"))
        known = None not in (placed, signed, failed, opened)
        balances = known and signed + failed + opened == placed
        if known and not balances:
            flags.append("cohort_counts_conflict")
        matured = ended and balances and opened == 0
        denom = placed
        gm = obs["signed_gmv_minor"] if matured else None
        if gm is not None and signed == 0 and gm != 0:
            flags.append("gmv_without_signed_orders")
            gm = None
        figures.update(signed_orders=signed, terminal_failures=failed, open_orders=opened,
                       terminal_order_count=signed+failed if balances else None,
                       signed_progress=ratio(signed, placed) if balances else None,
                       final_signed_rate=ratio(signed, placed) if matured else None,
                       final_cost_per_signed_minor=ratio(spent, signed) if matured else None,
                       signed_gmv_minor=gm)
    else:
        impressions, clicks, paid = (obs[k] if source else None for k in ("impressions", "clicks", "paid_orders"))
        for numerator, denominator in ((clicks, impressions), (paid, clicks), (paid, placed)):
            if None not in (numerator, denominator) and numerator > denominator:
                flags.append("funnel_counts_conflict")
                break
        figures.update(impressions=impressions, clicks=clicks, paid_orders=paid,
                       ctr=ratio(clicks, impressions) if not flags else None,
                       cvr=ratio(paid, clicks) if not flags else None)
        matured = ended and source
        denom = placed if c["sample_unit"] == "orders" else impressions
    enough = denom is not None and denom >= c["min_sample"]
    contribution = obs["net_contribution_minor"] if matured else None
    figures["net_contribution_minor"] = contribution
    figures["net_roi"] = ratio(contribution, spent)
    name = c["metric"]
    plain_key = {"ctr_bps": "ctr", "cvr_bps": "cvr", "net_roi_bps": "net_roi"}.get(name)
    if plain_key:
        data = figures[plain_key]
        score = Fraction(data["numerator"] * 10000, data["denominator"]) if data else None
    else:
        v = figures[name]
        score = Fraction(v) if v is not None else None
    sr = figures.get("final_signed_rate")
    if flags:
        state = "PENDING_CONFLICT"
    elif not matured:
        state = "PENDING_MATURITY"
    elif not enough:
        state = "PENDING_MINIMUM_SAMPLE"
    elif score is None or (site == "PH" and sr is None):
        state = "PENDING_METRIC"
    elif score <= c["failure_at_most"] or (
        sr is not None and Fraction(sr["numerator"] * 10000, sr["denominator"]) < c["min_signed_rate_bps"]
    ):
        state = "ELIMINATION_REVIEW_CANDIDATE"
    elif score >= c["success_at_least"]:
        state = "PASS_REVIEW_CANDIDATE"
    else:
        state = "RETEST_REVIEW_CANDIDATE"
    return {"observed": obs, "metrics": figures, "sample_state": "ENOUGH" if enough else "PENDING",
            "cohort_mature": bool(matured), "evaluation_state": state, "budget_state": budget_state,
            "order_cap_reached": c["max_orders"] is not None and placed is not None and placed >= c["max_orders"],
            "observed_window_end": (starts + timedelta(days=plan["window_days"])).isoformat(),
            "main_metric_value": ratio(score.numerator, score.denominator) if score is not None and matured and enough and not flags else None,
            "flags": flags}


def signed_comparison(rows, arms):
    if len(rows) < 2:
        return {"state": "NOT_REQUESTED_FOR_SINGLE_ARM", "ranking": None}
    if any(not r["cohort_mature"] or r["sample_state"] != "ENOUGH" or r["flags"] for r in rows.values()):
        return {"state": "PENDING_MATURE_COMPARABLE_COHORTS", "ranking": None}
    if len({r["comparison_basis"] for r in arms.values()}) != 1:
        return {"state": "NOT_COMPARABLE", "ranking": None}
    rates = {k: r["metrics"]["final_signed_rate"] for k, r in rows.items()}
    if any(v is None for v in rates.values()):
        return {"state": "PENDING_MATURE_COMPARABLE_COHORTS", "ranking": None}
    groups = {}
    for name, r in rates.items():
        groups.setdefault(Fraction(r["numerator"], r["denominator"]), []).append(name)
    return {"state": "TIED_ON_SIGNED_RATE" if len(groups) == 1 else "OBSERVED_SIGNED_RATE_ORDER",
            "ranking": [sorted(groups[s]) for s in sorted(groups, reverse=True)],
            "causal_channel_lift": None}


def snapshot(site, p, plan):
    rows = {k: check_arm(site, r, plan["arms"][k], plan) for k, r in p["observations"].items()}
    known = [r["metrics"]["spent_minor"] for r in rows.values() if r["metrics"]["spent_minor"] is not None]
    # A partial known subtotal can already prove the hard cap is exhausted.
    partial = sum(known)
    total = partial if len(known) == len(rows) else None
    gate = budget(total, plan["batch_cap_minor"])
    if known and partial >= plan["batch_cap_minor"]:
        gate = "PAUSE_SPEND_RECOMMENDED"
    pause = gate == "PAUSE_SPEND_RECOMMENDED" or any(r["budget_state"] == "PAUSE_SPEND_RECOMMENDED" for r in rows.values())
    return {"state": "SNAPSHOT_RECORDED", "plan_task_id": p["plan_task_id"], "arms": rows,
            "comparison": signed_comparison(rows, plan["arms"]) if site == "PH" else {"state": "NOT_A_CAUSAL_CHANNEL_COMPARISON", "ranking": None},
            "budget": {"known_spend_minor": partial if known else None, "total_spend_minor": total,
                       "batch_cap_minor": plan["batch_cap_minor"], "batch_state": gate,
                       "pause_further_spend_advice": pause,
                       "non_spend_observation_may_continue": True}}


def receipt(r, files):
    return {"task_id": r["task_id"], "site": r["site"], "input_sha256": r["input_sha256"], "upstream": r["upstream"],
            "result_sha256": sha256(files["json"]).hexdigest(), "report_sha256": sha256(files["report"]).hexdigest(),
            "execution_authorized": False}


def run_workflow(project, site, tool, p, folder, assets, progress, *, linked):
    if assets:
        raise WorkflowError("测品本机演练仅使用合成数据及其明确引用。")
    pins, source = linked
    if p["kind"] == "plan":
        result = {"state": "PLAN_FROZEN", "plan": p,
                  "finance_context": {"state": source["state"], "floor_met": source["floor_met"],
                                      "sku_id": source["sku_id"], "trial_revision_validated_by_finance": False}}
        lines = [f"负责人：{p['owner']} · 合成观察期：{p['window_days']} 天 · 批次预算上限：{p['batch_cap_minor']} 最小货币单位", "",
                 f"评估指标：{p['criteria']['metric']} · 最低样本：{p['criteria']['min_sample']} · 成功线：{p['criteria']['success_at_least']} · 失败线：{p['criteria']['failure_at_most']}",
                 f"淘汰与再测：{p['criteria']['elimination_rule']}", "",
                 *[f"- {k}：{v['state']}，{v['reason']}" for k,v in p["channel_choices"].items()]]
    else:
        result = snapshot(site, p, source["plan"])
        lines = [f"关联计划任务：{p['plan_task_id']}", "",
                 "| 组 | 样本状态 | 表现结论 | 预算提示 |", "| --- | --- | --- | --- |",
                 *[f"| {k} | {v['sample_state']} | {v['evaluation_state']} | {v['budget_state']} |" for k,v in result["arms"].items()],
                 "", f"批次预算：{result['budget']['batch_state']} · 签收比较：{result['comparison']['state']}"]
        if site == "PH":
            def show(value):
                return f"{value['numerator']}/{value['denominator']}（{value['value']}）" if value else "待核"
            for name,row in result["arms"].items():
                metrics=row["metrics"]
                lines.append(f"- {name} · 成熟签收率 {show(metrics['final_signed_rate'])} · 每签收单成本（最小货币单位） {show(metrics['final_cost_per_signed_minor'])}")
    r = {**{k: p[k] for k in ("kind", "site", "currency", "scope", "batch_id", "sku_id", "revision", "as_of")},
         **result, "tool_id": tool, "task_id": folder.name, "upstream": pins, "input_sha256": sha256(encode(p)).hexdigest(),
         "execution_authorized": False, "budget_changed": False, "promotion_executed": False, "model_called": False}
    contents = {"json": encode(r), "report": ("\n".join(
        [f"# {site} 测品 · {p['batch_id']}", "", "合成案例，仅供本机评审。币种、时间窗和上下限仅为本次输入。", "", *lines, ""]
    )).encode()}
    contents["receipt"] = encode(receipt(r, contents))
    paths = {k: folder / name for k, name in FILES.items()}
    for k, path in paths.items():
        with path.open("xb") as stream:
            stream.write(contents[k])
    return r, paths


def verify_saved(task, files):
    r = task["result"]
    if (task["local_files"] != FILES or r != json.loads(files["json"])
            or r["upstream"] != task["source"]["role_packet_pins"]
            or (r["task_id"], r["site"], r["tool_id"]) != (task["id"], task["site"], task["source"]["tool_id"])
            or json.loads(files["receipt"]) != receipt(r, files)):
        raise WorkflowError("测品回执或计划绑定与本机产物不符。")


def public_tools():
    return [{"id": TOOL, "sites": ["CN", "PH"], "role_suffix": "selection", "label": "测品计划与复盘",
             "description": "本机先冻结渠道、窗口和阈值，再按同批次收件。仅接受合成数据。", "input_example": {}}]

