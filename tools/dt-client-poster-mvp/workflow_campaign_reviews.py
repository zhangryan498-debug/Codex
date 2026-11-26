"""Same-site synthetic campaign designs and retrospectives with distinct local receipts."""

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, ratio, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

PH_DESIGN="ph-campaign-creator-roi-design"
CN_REVIEW="cn-campaign-six-stage-review"
PH_REVIEW="ph-campaign-six-stage-review"
PH_INTAKE="ph-ad-campaign-design-intake"
CN_INTAKE="cn-lead-campaign-review-intake"
SOURCES={PH_INTAKE:PH_DESIGN,CN_INTAKE:CN_REVIEW}
TOOLS=(PH_DESIGN,CN_REVIEW,PH_INTAKE,CN_INTAKE,PH_REVIEW)
SITES={PH_DESIGN:"PH",PH_INTAKE:"PH",CN_REVIEW:"CN",CN_INTAKE:"CN",PH_REVIEW:"PH"}
FILES={"json":"campaign-review.json","report":"campaign-review.md","receipt":"campaign-review-receipt.json"}
TIERS=("head","middle","tail")
LEVELS=("assortment","price","creative","creator","ads","fulfillment_service")
CN_COSTS=("goods","platform","merchant_subsidy","creator","creative","advertising","fulfillment_service")
PH_LEVELS = ("site_assortment", "fulfillment_payment", "content_localization",
             "affiliate", "advertising", "after_sales_refunds")
PH_COSTS = (*CN_COSTS, "payment_cod", "returns_refusals", "certification_localization")
PH_KPIS = ("placed_orders", "signed_orders", "refused_orders", "returned_signed_orders", "cod_orders",
           "placed_gmv_minor", "signed_gmv_minor", "refunds_minor", "settled_minor")
PH_CASH_FIELDS = ("received_minor", "receivable_minor")
PH_SETTLEMENT_BASIS = "signed_after_refunds_and_declared_withholdings"
PH_REVIEW_REFERENCES = ("runtime-agents/tiktok-campaign-ops/agent.md",
                        ".agents/skills/tiktok-campaign-ops-playbook/SKILL.md",
                        "templates/活动运营/活动复盘表.md")
PH_NUMBERS=("placed_orders","signed_orders","refunded_signed_orders","gross_signed_minor","refund_minor","settlement_minor",
            "ads_gross_minor","ads_refunds_minor","direct_cost_minor","sample_fixed_minor","media_spend_minor")


class WorkflowError(ValueError):
    pass


def maybe_sum(items):
    vals=list(items)
    return sum(vals) if None not in vals else None


def sub(a,b):
    return a-b if a is not None and b is not None else None


def line_fee(basis,bps):
    if basis is None or bps is None:
        return None
    return int((Decimal(basis)*bps/10000).quantize(Decimal("1"),rounding=ROUND_HALF_UP))


def optional_ref(value,label):
    term(value,label,True)


def validate(tool,site,p):
    tail={
        PH_DESIGN:"window cohort_task_id forecast_task_id tiers volume_plan approvals owner due_at",
        CN_REVIEW:"window schedule_task_id settlement costs layer_signals platform_context forecast owner due_at",
        PH_REVIEW:"window schedule_task_id settlement cash costs layer_signals platform_context playbook owner due_at",
        PH_INTAKE:"source_task_id recipient",
        CN_INTAKE:"source_task_id recipient",
    }[tool]
    check(p,"site currency scope package_id campaign_id sku_id revision as_of "+tail,"活动岗位包")
    if (site,p["site"],p["currency"],p["scope"])!=(SITES[tool],site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("仅处理所选站点、本币和明确标注的合成资料。")
    for k in ("package_id","campaign_id","sku_id"):
        identify(p[k],k)
    amount(p["revision"],"SKU 修订",1,1000000,False)
    at=stamp(p["as_of"])
    if tool in SOURCES:
        task_id(p["source_task_id"],"待签收报告")
        want="tiktok-ad-buyer" if tool==PH_INTAKE else "douyin-lead"
        if p["recipient"]!=want:
            raise WorkflowError("收件岗位与任务、站点不符。")
        return
    check(p["window"],"start end","观察时间窗")
    begin,end=(stamp(p["window"][k]) for k in ("start","end"))
    if not begin<end<=at or stamp(p["due_at"])<at:
        raise WorkflowError("仅接受闭合的合成样本窗，复核截止不得早于评估。")
    term(p["owner"],"负责岗位")
    if tool==PH_DESIGN:
        task_id(p["cohort_task_id"],"达人来源")
        task_id(p["forecast_task_id"],"活动测算来源")
        check(p["tiers"]," ".join(TIERS),"达人分层")
        for row in p["tiers"].values():
            check(row," ".join(PH_NUMBERS)+" base_bps ads_bps commission_basis billing_terms_ref evidence_ref settled_as_of",
                  "分层计划、结算及费用")
            for k in PH_NUMBERS:amount(row[k],k)
            for k in ("base_bps","ads_bps"):amount(row[k],k,0,10000)
            if row["commission_basis"] not in ("retained_base_plus_ad_addon","retained_base_only",None):
                raise WorkflowError("计费合同基数不明，须留待核。")
            for k in ("billing_terms_ref","evidence_ref"):optional_ref(row[k],k)
            if row["settled_as_of"] is not None and not end<=stamp(row["settled_as_of"])<=at:
                raise WorkflowError("结算证据应于窗后及截止前。")
        v=p["volume_plan"]
        check(v,"start end tiers","预期带量")
        if not at<stamp(v["start"])<stamp(v["end"]):
            raise WorkflowError("计划带量须为与历史观测窗明确分开的未来合成场次。")
        check(v["tiers"]," ".join(TIERS),"预期分层")
        for row in v["tiers"].values():
            check(row,"expected_placed expected_signed assumption_ref","预计下单和签收")
            placed,signed=(row[k] for k in ("expected_placed","expected_signed"))
            amount(placed,"预计下单")
            amount(signed,"预计签收")
            optional_ref(row["assumption_ref"],"计划假设")
            if None not in (placed,signed) and signed>placed:
                raise WorkflowError("预计签收不能超过预计下单。")
        check(p["approvals"],"commercial finance source_ref","合成审批状态")
        for k in ("commercial","finance"):
            if p["approvals"][k] not in ("pending","approved_fixture","rejected_fixture"):
                raise WorkflowError("标记审批待核或合成同意/拒绝。")
        optional_ref(p["approvals"]["source_ref"],"审批出处")
    elif tool==PH_REVIEW:
        validate_ph_review(p,site,begin,end,at)
    else:
        task_id(p["schedule_task_id"],"活动排期来源")
        r=p["settlement"]
        check(r,"gross_paid_minor refunds_minor settled_minor paid_orders target_orders maturity ref observed_at",
              "活动支付、退款与结算")
        for k in ("gross_paid_minor","refunds_minor","settled_minor","paid_orders","target_orders"):amount(r[k],k)
        if r["maturity"] not in ("mature","immature",None):
            raise WorkflowError("结算成熟度无效。")
        optional_ref(r["ref"],"结算出处")
        if r["observed_at"] is not None and not end<=stamp(r["observed_at"])<=at:
            raise WorkflowError("只能使用窗后至截止间的复盘账。")
        if not isinstance(p["costs"],list) or len(p["costs"])>40:
            raise WorkflowError("只支持少量具名成本行。")
        for c in p["costs"]:
            check(c,"cost_id category minor source_ref treatment","活动成本")
            identify(c["cost_id"],"成本编号")
            if c["category"] not in CN_COSTS:raise WorkflowError("退款和已计价款不得再列费用。")
            amount(c["minor"],"费用")
            optional_ref(c["source_ref"],"成本出处")
            if c["treatment"] not in ("separate_expense","already_in_paid",None):
                raise WorkflowError("费用须声明是否已反映于支付金额。")
            if c["category"]!="merchant_subsidy" and c["treatment"]=="already_in_paid":
                raise WorkflowError("已从支付额扣除的优惠须单列为商家补贴。")
        f=p["forecast"]
        check(f,"contribution_minor ref comparison_basis","预测比较")
        amount(f["contribution_minor"],"预测贡献",-10**12,10**12)
        optional_ref(f["ref"],"预测出处")
        if f["comparison_basis"] not in ("same_synthetic_scope","not_comparable",None):
            raise WorkflowError("预测可比性只能使用明确的合成口径。")
        validate_review_observations(p,site,begin,end,at,LEVELS)

def validate_review_observations(p,site,begin,end,at,levels):
    pc=p["platform_context"]
    check(pc,"state ref as_of description","独立平台变化记录")
    if pc["state"] not in ("bounded_synthetic_change","unknown"):
        raise WorkflowError("平台变化只登记合成观察或待核。")
    for k in ("ref","description"):optional_ref(pc[k],k)
    if pc["as_of"] is not None and not begin<=stamp(pc["as_of"])<=at:
        raise WorkflowError("平台观察不在本复盘证据范围。")
    check(p["layer_signals"]," ".join(levels),"六层归因证据")
    for signal in p["layer_signals"].values():
        check(signal,"metric previous current previous_basis current_basis hypothesis next_check owner",
             "六序观察")
        for k in ("metric","next_check","owner"):term(signal[k],k)
        for k in ("previous_basis","current_basis","hypothesis"):optional_ref(signal[k],k)
        for slot in ("previous","current"):
            row=signal[slot]
            check(row,"numerator denominator ref window site sku_id","比较观测")
            if (row["site"],row["sku_id"])!=(site,p["sku_id"]):
                raise WorkflowError("六序观测必须为本活动站点和 SKU。")
            check(row["window"],"start end","比较时窗")
            left,right=(stamp(row["window"][k]) for k in ("start","end"))
            if not left<right or (slot=="previous" and right>begin) or (
                    slot=="current" and not begin<=left<right<=end):
                raise WorkflowError("六序观测超出当前活动窗或基线重叠。")
            for k in ("numerator","denominator"):amount(row[k],k)
            optional_ref(row["ref"],"观测来源")


def validate_ph_review(p,site,begin,end,at):
    task_id(p["schedule_task_id"],"活动排期来源")
    s = p["settlement"]
    check(s, " ".join((*PH_KPIS, "basis", "maturity", "ref", "observed_at")), "菲律宾签收与结算")
    for key in PH_KPIS:
        amount(s[key], key)
    if s["basis"] not in (None, PH_SETTLEMENT_BASIS) or s["maturity"] not in (None,"mature","immature"):
        raise WorkflowError("未知结算口径或成熟状态须留待核。")
    validate_snapshot(s, "ref", end, at)
    cash = p["cash"]
    check(cash, " ".join((*PH_CASH_FIELDS,"ref","observed_at")), "实收和应收记录")
    for key in PH_CASH_FIELDS:
        amount(cash[key], key)
    validate_snapshot(cash, "ref", end, at)
    costs = p["costs"]
    if not isinstance(costs,list) or len(costs)>40:
        raise WorkflowError("只支持少量具名活动费用。")
    for row in costs:
        check(row,"cost_id category minor source_ref treatment","菲律宾活动费用")
        identify(row["cost_id"],"费用编号")
        amount(row["minor"],"费用")
        optional_ref(row["source_ref"],"费用出处")
        if row["category"] not in PH_COSTS:
            raise WorkflowError("只能登记既定费用类别，退款金额单列。")
        if row["treatment"] not in ("separate_expense","already_in_settlement","already_in_signed_gmv",None):
            raise WorkflowError("费用须声明分开支出、结算已扣、签收额已反映或待核。")
        if row["treatment"]=="already_in_signed_gmv" and row["category"]!="merchant_subsidy":
            raise WorkflowError("签收金额已反映仅适用于商家补贴。")
    candidate = p["playbook"]
    check(candidate,"mechanism ref","站点玩法候选")
    for key in ("mechanism","ref"):
        optional_ref(candidate[key],key)
    if (candidate["mechanism"] is None) != (candidate["ref"] is None):
        raise WorkflowError("玩法候选需同时提供机制说明及来源，没有资料时两项均待核。")
    validate_review_observations(p,site,begin,end,at,PH_LEVELS)


def validate_snapshot(snapshot, reference, earliest, cutoff):
    optional_ref(snapshot[reference],"来源")
    observed = snapshot["observed_at"]
    if observed is not None and not earliest<=stamp(observed)<=cutoff:
        raise WorkflowError("只能使用活动结束后且在复盘截点以内的合成快照。")


def bind(tool,site,p,load):
    validate(tool,site,p)
    linked={};pins={}
    def fetch(k,ident,type_id):
        linked[k],pins[k]=load(ident,site,type_id)
        x=linked[k]
        if (x["currency"],x["scope"])!=(p["currency"],SYNTHETIC):
            raise WorkflowError("归档引用跨币种或超出合成范围。")
        return x
    if tool==PH_DESIGN:
        fetch("historical_cohorts",p["cohort_task_id"],"competitor-creator-cohorts")
        proof=fetch("forecast_context",p["forecast_task_id"],"campaign-profit-stress")
        if tuple(proof[k] for k in ("campaign_id","sku_id","revision"))!=tuple(p[k] for k in ("campaign_id","sku_id","revision")):
            raise WorkflowError("压力测算引用须匹配活动、SKU 与修订。")
    elif tool in (CN_REVIEW,PH_REVIEW):
        x=fetch("schedule_context",p["schedule_task_id"],"campaign-offline-schedule")
        if (x["plan_id"],x["sku"],x["version"],stamp(x["event_starts_at"]))!=(
                p["campaign_id"],p["sku_id"],p["revision"],stamp(p["window"]["start"])):
            raise WorkflowError("活动、SKU、版本与开始时点必须对应原排期。")
    else:
        x=fetch("producer",p["source_task_id"],SOURCES[tool])
        if (tuple(x[k] for k in ("package_id","campaign_id","sku_id","revision"))!=
            tuple(p[k] for k in ("package_id","campaign_id","sku_id","revision")) or stamp(x["as_of"])>stamp(p["as_of"])):
            raise WorkflowError("只接受同包、活动、同 SKU 版本及截点内的正式来源。")
    return pins,linked


def design_row(r):
    findings=[]
    signed,placed,refused_count=(r[k] for k in ("signed_orders","placed_orders","refunded_signed_orders"))
    if (None not in (signed,placed) and signed>placed) or (None not in (refused_count,signed) and refused_count>signed):
        findings.append("order_counts_conflict")
    retained=sub(r["gross_signed_minor"],r["refund_minor"])
    ads_retained=sub(r["ads_gross_minor"],r["ads_refunds_minor"])
    if (retained is not None and retained<0) or (ads_retained is not None and ads_retained<0) or (
            None not in (retained,ads_retained) and ads_retained>retained) or (
            None not in (r["ads_gross_minor"],r["gross_signed_minor"]) and r["ads_gross_minor"]>r["gross_signed_minor"]) or (
            None not in (r["ads_refunds_minor"],r["refund_minor"]) and r["ads_refunds_minor"]>r["refund_minor"]):
        findings.append("settlement_or_ad_subset_conflict")
    support=bool(r["evidence_ref"] and r["settled_as_of"])
    settled=r["settlement_minor"] if support and not findings and r["settlement_minor"]==retained else None
    if support and None not in (r["settlement_minor"],retained) and r["settlement_minor"]!=retained:
        findings.append("settlement_does_not_reconcile")
    mode=r["commission_basis"]
    if mode=="retained_base_only" and any(r[k] not in (0,None) for k in ("ads_gross_minor","ads_refunds_minor","ads_bps","media_spend_minor")):
        findings.append("no_ads_contract_conflicts_with_ads_inputs")
    b=settled if r["billing_terms_ref"] and mode and not findings else None
    a=ads_retained if b is not None else None
    base,ads=line_fee(b,r["base_bps"]),line_fee(a,r["ads_bps"])
    program=maybe_sum((base,ads,r["sample_fixed_minor"],r["media_spend_minor"])) if support else None
    contrib=sub(settled,r["direct_cost_minor"])
    net=sub(contrib,program)
    return {"source":r,"flags":findings,"placed_orders":placed if support and not findings else None,
            "signed_orders":signed if support and not findings else None,
            "retained_orders":sub(signed,refused_count) if support and not findings else None,
            "signed_minor":r["gross_signed_minor"] if support else None,"refund_minor":r["refund_minor"] if support else None,
            "settlement_minor":settled,"program_cost_minor":program,"before_program_contribution_minor":contrib,
            "net_contribution_minor":net,"program_net_roi":ratio(net,program),"recognized_sales_over_program_cost":ratio(settled,program),
            "base_commission":{"basis_minor":b,"fee_minor":base,"basis":"settled_goods_after_matching_refunds"},
            "advertising_commission":{"basis_minor":a,"fee_minor":ads,"basis":"matched_ad_subset_after_matching_refunds"},
            "ad_subset_added_to_revenue":False,"commissions_added_once":True}


def design(p):
    rows={tier:design_row(p["tiers"][tier]) for tier in TIERS}
    fields=("placed_orders","signed_orders","retained_orders","signed_minor","refund_minor","settlement_minor",
            "program_cost_minor","before_program_contribution_minor","net_contribution_minor")
    totals={k:maybe_sum(r[k] for r in rows.values()) for k in fields}
    totals["base_commission_minor"]=maybe_sum(r["base_commission"]["fee_minor"] for r in rows.values())
    totals["advertising_commission_minor"]=maybe_sum(r["advertising_commission"]["fee_minor"] for r in rows.values())
    totals["program_net_roi"]=ratio(totals["net_contribution_minor"],totals["program_cost_minor"])
    totals["recognized_sales_over_program_cost"]=ratio(totals["settlement_minor"],totals["program_cost_minor"])
    def gates():
        a=p["approvals"]
        if a["source_ref"] and "rejected_fixture" in (a["commercial"],a["finance"]):return "BLOCKED_FIXTURE"
        if a["source_ref"] and a["commercial"]==a["finance"]=="approved_fixture":return "APPROVED_FIXTURE_ONLY"
        return "PENDING_APPROVAL"
    approval=gates()
    estimates=p["volume_plan"]["tiers"]
    expected={k:maybe_sum(v[k] if v["assumption_ref"] else None for v in estimates.values())
              for k in ("expected_placed","expected_signed")}
    refs=[r["source"]["evidence_ref"] for r in rows.values()]
    repeated_evidence=len([x for x in refs if x])!=len({x for x in refs if x})
    if repeated_evidence:
        totals={k:None for k in totals}
    ready=(totals["program_net_roi"] is not None and None not in (*expected.values(),totals["retained_orders"],
            totals["placed_orders"],totals["signed_orders"]) and not any(r["flags"] for r in rows.values()))
    return {"state":"BLOCKED_DESIGN" if approval=="BLOCKED_FIXTURE" else "DESIGN_REVIEWABLE" if ready else "PENDING_DATA",
            "tiers":rows,"totals":totals,"approval_state":approval,"approval_evidence":p["approvals"],
            "volume_plan":{"status":"PENDING_VOLUME" if None in expected.values() else "SYNTHETIC_FORECAST",
                           "assumptions":p["volume_plan"],"totals":expected},
            "historical_evidence_reused_between_tiers":repeated_evidence,
            "historical_cohort_and_forecast_sources_are_context_only":True,
            "current_commission_policy_verified":False,"forecast_reference_added_to_cost":False,
            "actual_creator_commitment":None,"causal_lift":None}


def review_costs(rows, categories, reflected):
    by_id=defaultdict(list)
    for row in rows:by_id[row["cost_id"]].append(row)
    accepted=[];disputed=[];duplicates=0
    for ident,data in by_id.items():
        if len({encode(x) for x in data})!=1:
            disputed.append({"cost_id":ident,"evidence":data})
        else:
            accepted.append(data[0]);duplicates+=len(data)-1
    sums={}
    for k in categories:
        items=[r for r in accepted if r["category"]==k]
        known=items and all(r["source_ref"] and r["minor"] is not None and r["treatment"] is not None for r in items)
        conflict=any(any(r["category"]==k for r in x["evidence"]) for x in disputed)
        sums[k]=sum(r["minor"] for r in items if r["treatment"]=="separate_expense") if known and not conflict else None
    return {"categories":sums,"known_partial_subtotal_minor":sum(x["minor"] for x in accepted if x["source_ref"]
                and x["minor"] is not None and x["treatment"]=="separate_expense"),
            "already_reflected_rows":[r for r in accepted if r["treatment"] in reflected],
            "unique_total_minor":maybe_sum(sums.values()),"duplicate_rows_skipped":duplicates,"conflicts":disputed,
            "missing_or_conflicting_categories":[k for k,v in sums.items() if v is None],"accepted_rows":accepted}


def ordered_review_observations(p, order):
    levels=[]
    for stage in order:
        e=p["layer_signals"][stage]
        a,b=(ratio(e[t]["numerator"],e[t]["denominator"]) if e[t]["ref"] else None for t in ("previous","current"))
        windows=[e[k]["window"] for k in ("previous","current")]
        spans=[stamp(w["end"])-stamp(w["start"]) for w in windows]
        matched=bool(a and b and spans[0]==spans[1] and e["previous_basis"] and e["previous_basis"]==e["current_basis"])
        delta=ratio(b["numerator"]*a["denominator"]-a["numerator"]*b["denominator"],a["denominator"]*b["denominator"]) if matched else None
        levels.append({"stage":stage,"previous_sample":a,"current_sample":b,
                       "matched_change":delta,"state":"OBSERVED_DIFFERENCE" if matched else "PENDING_COMPARABILITY",
                       "cited_observations":e,"causal_increment":None,"hypothesis_verified":False,
                       "next_check":e["next_check"],"owner":e["owner"],"due_at":p["due_at"]})
    return levels


def review(p):
    s=p["settlement"];c=review_costs(p["costs"],CN_COSTS,{"already_in_paid"})
    retained=sub(s["gross_paid_minor"],s["refunds_minor"])
    coherent=(retained is not None and retained>=0 and retained==s["settled_minor"])
    mature=s["maturity"]=="mature" and s["ref"] is not None and s["observed_at"] is not None and coherent
    settlement=s["settled_minor"] if mature else None
    g=sub(settlement,c["unique_total_minor"])
    ads=c["categories"]["advertising"]
    levels=ordered_review_observations(p,LEVELS)
    pc=p["platform_context"]
    confirmed=bool(pc["state"]=="bounded_synthetic_change" and pc["ref"] and pc["as_of"] and pc["description"])
    creative_and_ads=maybe_sum((ads,c["categories"]["creative"]))
    f=p["forecast"]
    forecast=f["contribution_minor"] if f["ref"] and f["comparison_basis"]=="same_synthetic_scope" else None
    totals={"gross_paid_minor":s["gross_paid_minor"],"refunds_minor":s["refunds_minor"],"settled_minor":settlement,
            "reported_settlement_minor":s["settled_minor"],"net_contribution_minor":g,
            "gross_roas":ratio(s["gross_paid_minor"],ads) if s["ref"] else None,
            "settled_roas":ratio(settlement,ads),"net_contribution_over_ad_spend":ratio(g,ads),
            "order_target_completion":ratio(s["paid_orders"],s["target_orders"]) if s["ref"] else None,
            "net_contribution_over_ads_and_creative":ratio(g,creative_and_ads),
            "known_expenses_only_residual_minor":sub(settlement,c["known_partial_subtotal_minor"]),
            "refund_subtracted_again_after_settlement":False}
    pending=[k for k in ("gross_roas","settled_roas","net_contribution_over_ad_spend","order_target_completion")
             if totals[k] is None]
    pending_stages=[level["stage"] for level in levels if level["state"]=="PENDING_COMPARABILITY"]
    state="REVIEW_RECORDED" if g is not None and not pending and not pending_stages else "PENDING_DATA"
    return {"state":state,"totals":totals,"expense_ledger":c,"ratio_pending":pending,"pending_stages":pending_stages,
            "forecast_comparison":{"source":f,"contribution_delta_minor":sub(g,forecast),"causal":False},
            "settlement_source":s,"settlement_mature_and_reconciled":mature,
            "ordered_stages":levels,"platform_side":{"reported":pc,"observation":"SYNTHETIC_SEPARATE" if confirmed else "UNKNOWN",
                                                     "causal_link":None},
            "own_execution_side":"six_stage_scoped_evidence","causal_increment":None}


def ph_review(p):
    settlement = p["settlement"]
    cash = p["cash"]
    ledger = review_costs(p["costs"],PH_COSTS,{"already_in_settlement","already_in_signed_gmv"})
    # Only positively identified withholdings can explain the difference from retained signed GMV.
    withheld = [row for row in ledger["already_reflected_rows"] if row["treatment"]=="already_in_settlement"]
    could_change_withholding = any(row["treatment"] in (None,"already_in_settlement")
                                   for conflict in ledger["conflicts"] for row in conflict["evidence"])
    unknown_treatment = any(row["treatment"] is None for row in ledger["accepted_rows"])
    withheld_total = None
    if not (could_change_withholding or unknown_treatment) and all(row["source_ref"] for row in withheld):
        withheld_total = maybe_sum(row["minor"] for row in withheld)
    scoped = bool(settlement["ref"] and settlement["observed_at"])
    observed = {key:settlement[key] if scoped else None for key in PH_KPIS}
    placed = observed["placed_orders"]
    signed = observed["signed_orders"]
    refused = observed["refused_orders"]
    returned = observed["returned_signed_orders"]
    cod = observed["cod_orders"]
    orders_conflict = any(
        left is not None and right is not None and left>right
        for left,right in ((signed,placed),(refused,sub(placed,signed)),(returned,signed),(cod,placed))
    )
    retained = sub(observed["signed_gmv_minor"],observed["refunds_minor"])
    computed_settlement = sub(retained,withheld_total)
    settled = observed["settled_minor"]
    gmv_conflict = (observed["signed_gmv_minor"] is not None and observed["placed_gmv_minor"] is not None
                    and observed["signed_gmv_minor"]>observed["placed_gmv_minor"])
    reconciled = (scoped and not orders_conflict and not gmv_conflict and settlement["maturity"]=="mature"
                  and all(value is not None for value in (placed,signed,refused,returned))
                  and settlement["basis"]==PH_SETTLEMENT_BASIS and computed_settlement is not None
                  and computed_settlement>=0 and settled==computed_settlement)
    activities = ordered_review_observations(p,PH_LEVELS)
    platform = p["platform_context"]
    platform_reported = bool(platform["state"]=="bounded_synthetic_change" and platform["ref"]
                             and platform["as_of"] and platform["description"])
    cash_observed = bool(cash["ref"] and cash["observed_at"])
    settled_net = settled if reconciled else None
    contribution = sub(settled_net,ledger["unique_total_minor"])
    pending_checks = [item["stage"] for item in activities if item["state"]=="PENDING_COMPARABILITY"]
    ratios = {"signature_rate":ratio(signed,placed),"refusal_rate":ratio(refused,placed),
              "return_rate_on_signed_orders":ratio(returned,signed),"cod_share":ratio(cod,placed)}
    if orders_conflict:
        ratios = dict.fromkeys(ratios)
    totals = {**observed, **ratios, "settled_minor":settled_net,
              "signed_after_refunds_minor":retained if not (gmv_conflict or retained is None or retained<0) else None,
              "reported_settlement_minor":settled,
              "withheld_cost_minor":withheld_total if reconciled else None,
              "separate_expenses_minor":ledger["unique_total_minor"],
              "synthetic_net_contribution_minor":contribution,
              "synthetic_net_contribution_rate":ratio(contribution,settled_net),
              "cash_received_minor":cash["received_minor"] if cash_observed else None,
              "cash_receivable_minor":cash["receivable_minor"] if cash_observed else None}
    pending_finance = [key for key in ("settled_minor","synthetic_net_contribution_minor","cash_received_minor",
                                      "cash_receivable_minor","signature_rate","refusal_rate",
                                      "return_rate_on_signed_orders","cod_share") if totals[key] is None]
    return {"state":"PENDING_DATA" if pending_finance else "SYNTHETIC_REVIEW_RECORDED",
            "attribution_state":"PENDING_EVIDENCE" if pending_checks or not platform_reported
                                else "SYNTHETIC_DIFFERENCES_ONLY",
            "totals":totals,"settlement_reconciled":reconciled,"order_count_conflict":orders_conflict,
            "gmv_conflict":gmv_conflict,"settlement_source":settlement,"cash_source":cash,
            "expense_ledger":ledger,"pending_finance_fields":pending_finance,"ordered_stages":activities,
            "pending_stages":pending_checks,"own_execution_side":"same_site_synthetic_observations",
            "platform_side":{"reported":platform,"observation":"SYNTHETIC_SEPARATE" if platform_reported else "UNKNOWN",
                             "causal_link":None},
            "causal_increment":None,"actual_causality_verified":False,
            "real_business_net_profit_minor":None,
            "playbook_candidate":{"site":"PH","state":"LOCAL_DRAFT_AWAITING_REVIEW",
                                  "candidate":p["playbook"],"campaign_id":p["campaign_id"],
                                  "sku_id":p["sku_id"],"revision":p["revision"],
                                  "published":False,"adopted":False},
            "cash_is_bank_receipt":False}


def ph_report(result, inputs):
    t = result["totals"]
    metric_labels = (
        ("placed_orders","下单"),("signed_orders","签收"),("refused_orders","拒收"),
        ("returned_signed_orders","签收后退货"),("placed_gmv_minor","下单 GMV"),
        ("signed_gmv_minor","签收 GMV"),("refunds_minor","退款"),("settled_minor","已核对结算"),
        ("cash_received_minor","实收记录"),("cash_receivable_minor","应收记录"),
        ("synthetic_net_contribution_minor","合成净贡献"))
    render = lambda value: "待核" if value is None else str(value)
    stage_names = ("站点与货盘","履约与支付","内容与本地化","达人分销","投放效率","售后与退款")
    lines = [f"# 菲律宾活动复盘 · {result['package_id']}",
             "", "本机合成复盘。金额为 PHP 最小货币单位。", "",
             f"- 复盘状态 {result['state']}",
             f"- 归因状态 {result['attribution_state']}，因果结论待核",
             f"- 平台变化 {result['platform_side']['observation']}，与自身执行分列",
             f"- 复核负责人 {safe_md(inputs['owner'])}，期限 {inputs['due_at']}", "",
             "| 指标 | 合成数值 |","|---|---:|"]
    lines.extend(f"| {label} | {render(t[key])} |" for key,label in metric_labels)
    lines.extend(["", "## 六层核查", "", "| 顺序 | 观察状态 | 下一步 | 责任人 |", "|---|---|---|---|"])
    for label,item in zip(stage_names,result["ordered_stages"],strict=True):
        lines.append(f"| {label} | {item['state']} | {safe_md(item['next_check'])} | {safe_md(item['owner'])} |")
    lines.extend(["", "站点玩法库候选已保存于本机复盘任务，待复核与采纳。", ""])
    return "\n".join(lines).encode("utf8")


def receipt(r,b,m):
    return {**{k:r[k] for k in ("task_id","tool_id","site","input_sha256","upstream","package_id")},
            "result_sha256":sha256(b).hexdigest(),"report_sha256":sha256(m).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:raise WorkflowError("只使用本机归档的合成结构化输入。")
    pins,source=linked
    if tool==PH_DESIGN:
        x=design(p)
    elif tool==CN_REVIEW:
        x=review(p)
    elif tool==PH_REVIEW:
        x=ph_review(p)
    else:
        row=source["producer"]
        x={"state":"SIGNED_FOR_LOCAL_REVIEW" if row["state"] in ("REVIEW_RECORDED","DESIGN_REVIEWABLE") else "RETURNED_FOR_EVIDENCE",
           "producer_task_id":row["task_id"],"producer_state":row["state"],"recipient":p["recipient"],
           "received_totals":row["totals"],"approval_state_from_source":row.get("approval_state"),
           "received_volume_plan":row.get("volume_plan"),"pending_stages_from_source":row.get("pending_stages")}
    r={**x,**{k:p[k] for k in ("site","currency","scope","package_id","campaign_id","sku_id","revision","as_of")},
       "task_id":folder.name,"tool_id":tool,"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "execution_authorized":False,"spending_executed":False,"campaign_launched":False,"model_called":False}
    if tool not in SOURCES:
        r["next_review"]={k:p[k] for k in ("owner","due_at")}
    b=encode(r);m=(f"# {site} 活动合成包 · {p['package_id']}\n\n结论：{r['state']}。只供本地核算和收件。\n\n"+b.decode()).encode()
    if tool==PH_REVIEW:
        r["reference_sha256"] = {name:sha256((project/name).read_bytes()).hexdigest() for name in PH_REVIEW_REFERENCES}
        b,m=encode(r),ph_report(r,p)
    files={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":b,"report":m,"receipt":encode(receipt(r,b,m))}.items():
        with files[k].open("xb") as f:f.write(v)
    return r,files


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["campaign_review_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("活动结果或交接回执与本机记录不符。")


def ph_review_example():
    window = {"start":"2026-12-10T10:00:00+08:00","end":"2026-12-12T10:00:00+08:00"}
    prior = {"start":"2026-12-08T10:00:00+08:00","end":window["start"]}
    def empty_observation(slot):
        return {"site":"PH","sku_id":"SYN-SKU","window":slot,"numerator":None,"denominator":None,"ref":None}
    return {"site":"PH","currency":"PHP","scope":SYNTHETIC,"package_id":"SYN-PH-REVIEW",
            "campaign_id":"SYN-SCHEDULE","sku_id":"SYN-SKU","revision":1,
            "as_of":"2026-12-13T10:00:00+08:00","due_at":"2026-12-15T10:00:00+08:00","owner":"活动运营",
            "window":window,"schedule_task_id":"填写本机菲律宾活动排期任务编号",
            "settlement":dict.fromkeys((*PH_KPIS,"basis","maturity","ref","observed_at")),
            "cash":dict.fromkeys((*PH_CASH_FIELDS,"ref","observed_at")),"costs":[],
            "layer_signals":{stage:{"metric":"填写同口径指标","previous":empty_observation(prior),
                "current":empty_observation(window),"previous_basis":None,"current_basis":None,"hypothesis":None,
                "next_check":"补充同口径证据","owner":"活动运营"} for stage in PH_LEVELS},
            "platform_context":{"state":"unknown","ref":None,"as_of":None,"description":None},
            "playbook":{"mechanism":None,"ref":None}}


def public_tools():
    roles=((PH_DESIGN,"campaign-ops","分层达人佣金与 ROI 设计"),(CN_REVIEW,"campaign-ops","活动六序复盘与净贡献"),
           (PH_INTAKE,"ad-buyer","菲律宾广告接收达人设计"),(CN_INTAKE,"lead","抖音负责人接收活动复盘"))
    return [{"id":tool,"sites":[SITES[tool]],"role_suffix":role,"label":label,"description":"本机合成计算与收件。",
             "input_example":{}} for tool,role,label in roles] + [{
        "id":PH_REVIEW,"sites":["PH"],"role_suffix":"campaign-ops","label":"菲律宾活动复盘",
        "description":"提交同活动、SKU 与版本的本机排期和合成签收、结算、费用、回款及六层证据，保存可取回的复盘与玩法候选。",
        "execution_scope":"使用 PHP 最小货币单位。没有来源的金额或归因保留待核。",
        "input_example":ph_review_example(),
    }]

