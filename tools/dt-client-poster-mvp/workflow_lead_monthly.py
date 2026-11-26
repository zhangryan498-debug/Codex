"""Compute comparable local monthly reviews with pinned weekly context and advisory budgets."""

from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
import workflow_cod_compensation
from role_tools_extra import _load
from workflow_lead_weekly import encode, ratio, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL = "lead-monthly-review"
FILES = {"json": "monthly-review.json", "report": "monthly-review.md", "receipt": "monthly-receipt.json"}
COMMON = ("visits", "listed_skus", "in_stock_skus", "videos", "video_orders", "pre_ad_contribution",
          "ad_spend", "ad_attributed_net", "recognized_refunds")
CN = COMMON + ("paid_orders", "paid_value", "settled_after_refunds", "refunded_orders")
PH = COMMON + ("placed_orders", "signed_orders", "refused_orders", "open_orders", "placed_value",
               "signed_value", "confirmed_collected", "actual_remittance", "pending_value",
               "local_language_videos", "affiliate_orders", "refunded_signed_orders")
STEPS = {
    "CN": {"traffic": "purchase_rate", "assortment": "in_stock_rate", "content": "orders_per_video",
           "ads": "attributed_return", "fulfillment_after_sales": "refund_order_rate"},
    "PH": {"market_assortment": "in_stock_rate", "fulfillment_payment": "cod_sign_rate",
           "content_localization": "local_content_rate", "affiliate_distribution": "affiliate_order_rate",
           "ads": "attributed_return", "after_sales_refunds": "refund_order_rate"},
}
DIMENSIONS = ("channel", "sku", "content")
BUDGET_ITEMS = ("ads", "creative", "diagnostics")
BASIS = ("shop_id", "assortment_scope", "traffic_definition", "financial_definition",
         "refund_definition", "ad_definition", "attribution_days", "maturation_days")
CONTEXT = ("assortment", "content", "budget")


class WorkflowError(ValueError):
    pass


def difference(a, b):
    return None if a is None or b is None else a - b


def calendar_window(p, evaluated):
    first, end, captured = (stamp(p[k]) for k in ("start", "end", "captured_at"))
    expected = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    if (first.day != 1 or first.time() != first.replace(hour=0, minute=0, second=0, microsecond=0).time()
            or end != expected or not end <= captured <= evaluated):
        raise WorkflowError("仅接受已结束的自然月、月首半开区间和审核截止内的快照。")
    return first, end, captured


def validate_snapshot(site, obj, at):
    check(obj, "start end captured_at source_ref basis observed_days metrics breakdowns", "月度快照")
    lo, hi, captured = calendar_window(obj, at)
    term(obj["source_ref"], "月度快照来源")
    check(obj["basis"], " ".join(BASIS), "口径")
    for k in BASIS:
        if k.endswith("_days"):
            amount(obj["basis"][k], k, 0, 365)
        else:
            term(obj["basis"][k], k, True)
    days = (hi - lo).days
    amount(obj["observed_days"], "已覆盖日数", 0, days)
    fields = CN if site == "CN" else PH
    check(obj["metrics"], " ".join(fields), "本站月度指标")
    for name in fields:
        amount(obj["metrics"][name], name, -10**12 if name == "pre_ad_contribution" else 0)
    views = obj["breakdowns"]
    if not isinstance(views, list) or len(views) > len(DIMENSIONS):
        raise WorkflowError("渠道、SKU、内容只接收独立的拆分视图。")
    seen = set()
    for view in views:
        check(view, "dimension exhaustive rows", "月度拆分视图")
        k = view["dimension"]
        if k not in DIMENSIONS or k in seen or not isinstance(view["rows"], list) or not 1 <= len(view["rows"]) <= 150:
            raise WorkflowError("拆分维度不明、重复或行数超出范围。")
        seen.add(k)
        if view["exhaustive"] not in (None, True, False) or (view["exhaustive"] is not None and type(view["exhaustive"]) is not bool):
            raise WorkflowError("需明确拆分是否穷尽同一总体。")
        for row in view["rows"]:
            check(row, "key amount_minor source_ref", "拆分记录")
            identify(row["key"], "拆分对象")
            amount(row["amount_minor"], "拆分金额")
            term(row["source_ref"], "拆分来源", True)
    return lo, hi, captured


def check_actions(rows, at):
    check(rows, " ".join(CONTEXT), "月度前三项动作")
    for k, row in rows.items():
        check(row, "action expected_impact verify owner due_at", "岗位动作")
        for name in ("action", "expected_impact", "verify", "owner"):
            term(row[name], name)
        if stamp(row["due_at"]) < at:
            raise WorkflowError("月度措施的复核时点不早于报告截止。")


def validate_budget(p):
    check(p, "plan_id cap_minor source_ref strategy_goal baseline proposal control", "预算重排")
    identify(p["plan_id"], "预算对象")
    amount(p["cap_minor"], "预算约束")
    term(p["strategy_goal"], "策略目标")
    term(p["source_ref"], "预算来源", True)
    for k in ("baseline", "proposal"):
        check(p[k], " ".join(BUDGET_ITEMS), "预算分项")
        for v in p[k].values():
            amount(v, "预算项")
    if p["control"] is not None:
        c = p["control"]
        check(c, "site currency shop_id plan_id field_path source_ref observed_at valid_until minor", "模拟控件读回")
        for k in ("site", "currency", "shop_id", "plan_id", "field_path", "source_ref"):
            term(c[k], k, k=="source_ref")
        if stamp(c["observed_at"]) >= stamp(c["valid_until"]):
            raise WorkflowError("控件读回未提供有效时间范围。")
        amount(c["minor"], "模拟控件读回")


def validate(site, obj):
    tail = " cash" if site == "PH" else ""
    if site == "PH" and "cod_compensation" in obj:
        tail += " cod_compensation"
    check(obj, "site scope currency review_id evaluated_at previous current weekly_task_ids "
               "factor_checks actions budget environment" + tail, "月度经营复盘")
    if (obj["site"], obj["scope"], obj["currency"]) != (site, SYNTHETIC, ROUTES[site][1]):
        raise WorkflowError("只接受本站、原币、明确标记的合成历史快照。")
    identify(obj["review_id"], "月报编号")
    at = stamp(obj["evaluated_at"])
    bounds = {k: validate_snapshot(site, obj[k], at) for k in ("previous", "current")}
    if bounds["previous"][1] != bounds["current"][0]:
        raise WorkflowError("基线与本期必须是相邻自然月。")
    refs = obj["weekly_task_ids"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 10:
        raise WorkflowError("需提供对应月份至少一份本站周报上下文。")
    for ident in refs:
        task_id(ident, "月报所引周报")
    check(obj["factor_checks"], " ".join(STEPS[site]), "站点归因步骤")
    for row in obj["factor_checks"].values():
        check(row, "hypothesis countercheck owner due_at", "原因验证")
        for k in ("hypothesis", "countercheck", "owner"):
            term(row[k], k, k=="hypothesis")
        if stamp(row["due_at"]) < at:
            raise WorkflowError("归因补证截止不能早于报告截止。")
    check_actions(obj["actions"], at)
    check(obj["environment"], "state source_ref observed_at impact owner next_check_at", "平台环境影响")
    env = obj["environment"]
    if env["state"] not in ("not_verified", "synthetic_case"):
        raise WorkflowError("平台环境证据只支持标明待核或给出合成情境。")
    for k in ("source_ref", "impact", "owner"):
        term(env[k], k, k=="source_ref")
    if env["state"]=="synthetic_case" and (env["observed_at"] is None or env["source_ref"] is None):
        raise WorkflowError("合成变化必须给出来源及时点。")
    if ((env["observed_at"] is not None and stamp(env["observed_at"]) > at)
            or stamp(env["next_check_at"]) < at):
        raise WorkflowError("平台变化取证或复核时间无效。")
    validate_budget(obj["budget"])
    if site == "PH":
        cash = obj["cash"]
        check(cash, "bank_spendable_minor committed_minor reserve_minor monthly_burn_minor observed_at source_ref", "菲律宾可动用现金")
        for k in ("bank_spendable_minor","committed_minor","reserve_minor","monthly_burn_minor"):
            amount(cash[k], k)
        term(cash["source_ref"], "银行/现金账来源", True)
        if cash["observed_at"] is not None and not bounds["current"][1] <= stamp(cash["observed_at"]) <= at:
            raise WorkflowError("现金快照必须在本月结账后和报告截止前。")
    if site == "PH" and obj.get("cod_compensation") is not None:
        try:
            workflow_cod_compensation.validate(obj["cod_compensation"],obj["current"]["basis"]["shop_id"])
        except workflow_cod_compensation.WorkflowError as error:
            raise WorkflowError(str(error)) from None
    return bounds


def bind(site, obj, weekly_source):
    bounds = validate(site, obj)
    evidence = []
    pins = {}
    seen = set()
    for ident in dict.fromkeys(obj["weekly_task_ids"]):
        row, pins[ident] = weekly_source(ident, site, ("lead-weekly-report",))
        w = (row["week_start"], row["week_end"])
        if w in seen:
            raise WorkflowError("月报同一周只能绑定一份明确版本的周报。")
        seen.add(w)
        start, end = map(stamp, w)
        slots = [k for k, (a, b, _) in bounds.items() if a <= start < end <= b]
        if len(slots) != 1 or stamp(row["observed_at"]) > stamp(obj["evaluated_at"]):
            raise WorkflowError("周报必须完整落在任一月内且在截止时点前。跨月周不可自动摊分。")
        evidence.append({"task_id": ident, "report_id": row["report_id"], "version": row["version"],
                         "state": row["state"], "accepted_roles": row["accepted_roles"], "month": slots[0],
                         "used_as_monthly_totals": False})
    return bounds, evidence, pins


def breakdown(views, headline):
    result = {}
    for view in views:
        keys = {}
        conflict = set()
        duplicates = 0
        for row in view["rows"]:
            key = row["key"]
            if key in keys:
                if keys[key]["amount_minor"] != row["amount_minor"]:
                    conflict.add(key)
                else:
                    duplicates += 1
                if keys[key]["source_ref"] is None:
                    keys[key] = row
            else:
                keys[key] = row
        known = all(v["amount_minor"] is not None and v["source_ref"] is not None for v in keys.values())
        total = sum(v["amount_minor"] for v in keys.values()) if known and not conflict else None
        comparable = view["exhaustive"] is True and total is not None and headline is not None
        state = ("CONFLICT" if conflict or (comparable and headline != total) else
                 "RECONCILED" if comparable else "PENDING")
        result[view["dimension"]] = {"state": state, "subtotal_minor": total, "headline_minor": headline,
                                     "duplicate_rows": duplicates, "conflicting_keys": sorted(conflict),
                                     "coverage_confirmed": view["exhaustive"], "source_rows": view["rows"]}
    return result


def snapshot(site, p, boundaries):
    m = p["metrics"]
    start, end, captured = boundaries
    maturity = p["basis"]["maturation_days"]
    mature = maturity is not None and captured >= end+timedelta(days=maturity)
    covered = p["observed_days"] == (end-start).days
    errors = []

    def bounded_rate(label, numerator, denominator):
        n, d = m[numerator], m[denominator]
        if n is not None and d is not None and n > d:
            errors.append(label)
            return None
        return ratio(n, d)

    indicators = {"in_stock_rate": bounded_rate("in_stock_rate","in_stock_skus","listed_skus"),
                  "orders_per_video": ratio(m["video_orders"],m["videos"]),
                  "attributed_return": ratio(m["ad_attributed_net"],m["ad_spend"])}
    money = {"pre_ad_contribution": m["pre_ad_contribution"], "ad_spend": m["ad_spend"],
             "post_ad_contribution": difference(m["pre_ad_contribution"],m["ad_spend"])}
    if site == "CN":
        headline = m["settled_after_refunds"]
        money.update(paid_gmv=m["paid_value"], settled_gmv_after_refunds=headline,
                     recognized_refunds=m["recognized_refunds"], contribution_margin=ratio(money["post_ad_contribution"],headline))
        indicators.update(purchase_rate=ratio(m["paid_orders"],m["visits"]),
                          refund_order_rate=bounded_rate("refund_order_rate","refunded_orders","paid_orders"))
    else:
        headline = m["signed_value"]
        n = m["placed_orders"]
        parts = [m[x] for x in ("signed_orders", "refused_orders", "open_orders")]
        complete = n is not None and None not in parts
        if n is not None and (sum(x for x in parts if x is not None) > n or (complete and sum(parts) != n)):
            errors.append("cod_cohort_does_not_reconcile")
        rates = [ratio(x, n) if complete and "cod_cohort_does_not_reconcile" not in errors else None for x in parts]
        indicators.update(zip(("cod_sign_rate","cod_refusal_rate","cod_open_rate"),rates))
        indicators.update(local_content_rate=bounded_rate("local_content_rate","local_language_videos","videos"),
                          affiliate_order_rate=bounded_rate("affiliate_order_rate","affiliate_orders","signed_orders"),
                          refund_order_rate=bounded_rate("refund_order_rate","refunded_signed_orders","signed_orders"))
        collected = difference(m["confirmed_collected"],m["recognized_refunds"])
        if collected is not None and collected < 0:
            errors.append("refunds_exceed_confirmed_collection")
            collected = None
        money.update(ordered_gmv=m["placed_value"], signed_gmv=headline, gross_confirmed_collections=m["confirmed_collected"],
                     recognized_refunds=m["recognized_refunds"], net_retained_collections=collected,
                     actual_remittance=m["actual_remittance"], pending_value=m["pending_value"],
                     contribution_margin=ratio(money["post_ad_contribution"],collected))
    return {"window": [p["start"],p["end"]], "source_ref":p["source_ref"], "captured_at":p["captured_at"],
            "basis":p["basis"], "financials":money, "indicators":indicators, "metrics":m,
            "observed_days":p["observed_days"], "calendar_days":(end-start).days,
            "mature":mature, "full_coverage":covered, "conflicts":errors,
            "missing_fields":[k for k,v in m.items() if v is None], "breakdowns":breakdown(p["breakdowns"],headline),
            "headline_source":"settled_after_refunds" if site=="CN" else "signed_value",
            "independent_dimensions_not_added":True}


def measure_diff(a, b, reasons):
    if reasons or a is None or b is None:
        return {"previous": a, "current": b, "delta": None, "state": "NOT_COMPARABLE", "reasons": reasons or ["missing_or_zero_denominator"]}
    if isinstance(a,dict):
        # Derive the difference from original numerator/denominator. Round only the final display value.
        delta = ratio(b["numerator"]*a["denominator"] - a["numerator"]*b["denominator"],
                      a["denominator"]*b["denominator"])["value"]
    else:
        delta = b-a
    return {"previous":a,"current":b,"delta":delta,"state":"OBSERVED_CHANGE","reasons":[]}


def liquidity(p):
    known = p["source_ref"] is not None and p["observed_at"] is not None
    free = difference(p["bank_spendable_minor"],p["committed_minor"]) if known else None
    headroom = difference(free,p["reserve_minor"])
    return {"source":p,"cash_after_commitments_minor":free,
            "cash_runway_months":ratio(free,p["monthly_burn_minor"]) if free is not None and free >= 0 else None,
            "max_new_budget_after_reserve_minor":max(0,headroom) if headroom is not None else None,
            "cash_deficit_before_new_budget_minor":max(0,-headroom) if headroom is not None else None,
            "pending_inflows_included":False,"remittance_added_to_bank_again":False}


def plan_budget(site, obj, current, cash, comparable):
    plan = obj["budget"]
    total = lambda line: sum(line.values()) if None not in line.values() else None
    before, proposed = (total(plan[k]) for k in ("baseline","proposal"))
    cap = plan["cap_minor"] if plan["source_ref"] is not None else None
    cash_cap = cash["max_new_budget_after_reserve_minor"] if cash else None
    capacity = min(cap,cash_cap) if site=="PH" and None not in (cap,cash_cap) else cap if site=="CN" else None
    blocking = ([k for k,lim in (("budget_cap_exceeded",cap),("cash_and_reserve_insufficient",cash_cap))
                 if proposed is not None and lim is not None and proposed > lim])
    if cash and cash["cash_deficit_before_new_budget_minor"]:
        blocking.append("existing_cash_reserve_shortfall")
    c = plan["control"]
    at = stamp(obj["evaluated_at"])
    control_known = bool(c and c["source_ref"] and c["minor"] is not None and
                         (c["site"],c["currency"],c["shop_id"],c["plan_id"],c["field_path"])==
                         (site,obj["currency"],current["basis"]["shop_id"],plan["plan_id"],"ads.monthly_budget_minor")
                         and stamp(c["observed_at"]) <= at < stamp(c["valid_until"]))
    pending = (proposed is None or before is None or capacity is None or not control_known or not comparable)
    state = "BLOCKED" if blocking else "PENDING" if pending else "CONDITIONAL_DRAFT"
    return {"state":state,"strategy_goal":plan["strategy_goal"],"previous_plan":plan["baseline"],"proposed_draft":plan["proposal"],
            "delta_by_allocation_minor":{k:difference(plan["proposal"][k],plan["baseline"][k]) for k in BUDGET_ITEMS},
            "baseline_total_minor":before,"proposed_total_minor":proposed,"budget_cap_minor":cap,"cap_source_ref":plan["source_ref"],
            "effective_capacity_minor":capacity,"cash_gap_minor":max(0,proposed-cash_cap) if None not in (proposed,cash_cap) else None,
            "blocking_reasons":blocking,"observed_control_minor":c["minor"] if control_known else None,
            "control_readback":c,"control_state":"SYNTHETIC_MATCHED" if control_known else "UNKNOWN",
            "required_readback":None if control_known else {"owner":obj["actions"]["budget"]["owner"],
                "due_at":obj["actions"]["budget"]["due_at"],"parameter":"ads.monthly_budget_minor",
                "site":site,"plan_id":plan["plan_id"],"shop_id":current["basis"]["shop_id"],"as_of":obj["evaluated_at"]},
            "execution_receipt":None,"strategy_is_instruction":False}


def compute(site, obj, linked):
    bounds, week, pins = linked
    previous, current = (snapshot(site,obj[k],bounds[k]) for k in ("previous","current"))
    a, b = (s["basis"] for s in (previous,current))
    common_basis = []
    for k in ("shop_id","assortment_scope"):
        if a[k] is None or b[k] is None or a[k] != b[k]:
            common_basis.append(k+"_mismatch_or_missing")
    if not previous["full_coverage"] or not current["full_coverage"]:
        common_basis.append("followup_period_coverage_incomplete")
    financial_basis = common_basis + [k+"_mismatch_or_missing" for k in ("financial_definition","refund_definition","maturation_days")
                                     if a[k] is None or b[k] is None or a[k]!=b[k]]
    if not previous["mature"] or not current["mature"]:
        financial_basis.append("immature_month")
    ad_basis = financial_basis + [k+"_mismatch_or_missing" for k in ("ad_definition","attribution_days")
                                  if a[k] is None or b[k] is None or a[k]!=b[k]]
    financials = {}
    for k, value in current["financials"].items():
        finance_reasons = financial_basis[:]
        if k in ("net_retained_collections","contribution_margin") and any("refunds_exceed_confirmed_collection" in s["conflicts"] for s in (previous,current)):
            finance_reasons.append("refunds_exceed_confirmed_collection")
        financials[k] = measure_diff(previous["financials"][k],value,finance_reasons)
        if k!="contribution_margin":
            financials[k]["per_observed_day"] = measure_diff(
                ratio(previous["financials"][k],previous["observed_days"]),ratio(value,current["observed_days"]),finance_reasons)
    diagnostics = []
    for stage,metric in STEPS[site].items():
        reasons = (ad_basis[:] if stage=="ads" else financial_basis[:] if stage in (
                   "fulfillment_payment","fulfillment_after_sales","after_sales_refunds") else common_basis[:])
        if stage=="traffic" and (a["traffic_definition"] is None or a["traffic_definition"] != b["traffic_definition"]):
            reasons.append("traffic_definition_mismatch_or_missing")
        diff = measure_diff(previous["indicators"][metric],current["indicators"][metric],reasons)
        row = obj["factor_checks"][stage]
        diagnostics.append({"stage":stage,"metric":metric,"measurement":diff,"hypothesis":row["hypothesis"],
                            "hypothesis_status":"REQUIRES_CONTROL" if row["hypothesis"] else "NONE",
                            "countercheck":row["countercheck"],"owner":row["owner"],"due_at":row["due_at"],
                            "source_refs":[previous["source_ref"],current["source_ref"]],
                            "effect_estimated":False,"incremental_effect":None})
    cash = liquidity(obj["cash"]) if site=="PH" else None
    budget = plan_budget(site,obj,current,cash,not ad_basis and not previous["missing_fields"] and not current["missing_fields"])
    available_weeks = all(r["state"]=="READY_OFFLINE" for r in week)
    gap = previous["missing_fields"] or current["missing_fields"] or previous["conflicts"] or current["conflicts"]
    views = [v["state"] for s in (previous,current) for v in s["breakdowns"].values()]
    ready = (budget["state"]=="CONDITIONAL_DRAFT" and available_weeks and not gap
             and bool(views) and all(v=="RECONCILED" for v in views)
             and all(r["measurement"]["state"]=="OBSERVED_CHANGE" for r in diagnostics))
    result = {"state":"READY_OFFLINE" if ready else "PENDING", "review_id":obj["review_id"],
            "previous":previous,"current":current,"financial_comparison":financials,
            "comparability":{"scope":common_basis,"financial":financial_basis,"ad":ad_basis},
            "diagnosis":diagnostics,"weekly_context":week,"upstream":pins,
            "platform_environment":{**obj["environment"],"verified_live":False},
            "next_actions":[dict(type=k,**obj["actions"][k],execution_authorized=False) for k in CONTEXT],
            "proposed_budget":budget,"cash":cash,"incrementality_estimate":None,
            "same_period_movement_used_as_cause":False}
    if obj.get("cod_compensation") is not None:
        result["cod_compensation"] = workflow_cod_compensation.compute(obj["cod_compensation"],bounds,stamp(obj["evaluated_at"]))
    return result


def receipt(r, raw, report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","scope","review_id","input_sha256","upstream")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def display(result):
    lines = [f"# {result['site']} 月度经营复盘 · {result['review_id']}", "",
             "结论：" + result["state"] + "。预算状态 " + result["proposed_budget"]["state"] + "。",
             "本地合成。下面是观察、复核和建议；本站币种的金额按最小单位记录。", "",
             "| 顺序 | 指标 | 观察差值 | 补证责任 | 截止 |", "|---|---|---|---|---|"]
    for stage in result["diagnosis"]:
        values = [stage["stage"],stage["metric"],stage["measurement"]["delta"],stage["owner"],stage["due_at"]]
        lines.append("| "+" | ".join(map(safe_md,values))+" |")
    labels = {"paid_gmv":"支付成交","settled_gmv_after_refunds":"退款后结算",
              "ordered_gmv":"下单成交","signed_gmv":"签收成交","gross_confirmed_collections":"确认实收",
              "net_retained_collections":"扣匹配退款后留存","actual_remittance":"实际汇回","pending_value":"待回款",
              "recognized_refunds":"确认退款","pre_ad_contribution":"投前贡献","ad_spend":"广告支出",
              "post_ad_contribution":"投后贡献"}
    n = lambda x: "待核" if x is None else str(x)
    lines.extend(["", "| 经营指标 | 上月 | 本月 | 同口径变化 |", "|---|---|---|---|"])
    for key,row in result["financial_comparison"].items():
        if key in labels:
            lines.append("| " + " | ".join(map(safe_md,[labels[key],n(row["previous"]),n(row["current"]),n(row["delta"])])) + " |")
    if result["cash"]:
        cash = result["cash"]
        lines.extend(["", f"可动用现金扣承诺后 {n(cash['cash_after_commitments_minor'])}，"
                      f"扣准备金后本轮预算容量 {n(cash['max_new_budget_after_reserve_minor'])}。"])
    if "cod_compensation" in result:
        cod=result["cod_compensation"]
        lines.extend(["", "## 平台 COD 赔付明细（合成）",
                      "按平台结算入账月列示。赔付单列；成交、实际银行到账保持原口径。",
                      f"上月入账 {n(cod['months']['previous']['platform_compensation_posted_minor'])}，"
                      f"本月入账 {n(cod['months']['current']['platform_compensation_posted_minor'])}。"])
    budget = result["proposed_budget"]
    lines.extend(["", "## 本机建议", "",
                  f"策略目标：{safe_md(budget['strategy_goal'])}。拟议总额 {n(budget['proposed_total_minor'])}，"
                  f"控件合成读回 {n(budget['observed_control_minor'])}。未执行。", "",
                  "| 动作 | 负责人 | 截止 | 预期影响与复核 |", "|---|---|---|---|"])
    for row in result["next_actions"]:
        lines.append("| " + " | ".join(map(safe_md,[row["action"],row["owner"],row["due_at"],
                                                     row["expected_impact"]+"；"+row["verify"]])) + " |")
    return "\n".join(lines)+"\n\n"+encode(result).decode()


def run_workflow(project,site,tool,obj,folder,assets,progress,*,linked):
    if tool!=TOOL or assets:
        raise WorkflowError("本机月报仅消费结构化快照和已存周报。")
    r = {**compute(site,obj,linked),"task_id":folder.name,"tool_id":tool,"site":site,
         "scope":SYNTHETIC,"currency":obj["currency"],"input_sha256":sha256(encode(obj)).hexdigest(),
         "execution_authorized":False,"model_called":False,"provider_called":False}
    raw, report = encode(r), display(r).encode()
    paths = {k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:
            f.write(b)
    return r,paths


def verify_saved(task,files):
    r = task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["monthly_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("月报结果或周报引用回执不一致。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"lead","label":"负责人月度归因与预算建议",
             "description":"比较本站合成经营快照，核对现金、分母、周报来源与建议边界。","input_example":{}}]

