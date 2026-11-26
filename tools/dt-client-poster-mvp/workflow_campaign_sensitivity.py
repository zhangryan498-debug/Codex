"""Campaign-level synthetic stress forecasts with independent liquidity and price gates."""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_live_rehearsal import check, identify, amount, stamp, posture, require_bits


class WorkflowError(ValueError):
    pass


TOOL="campaign-profit-stress"
FILES={"json":"campaign-forecast.json","report":"campaign-forecast.md","receipt":"campaign-forecast-receipt.json"}
DEC=Decimal
BASE=DEC(10000)
CORE="orders list_minor merchant_coupon_minor platform_coupon_minor reimbursement_minor floor_minor floor_inclusive stacking_allowed platform_bps creator_bps per_order_outbound_minor other_fixed_minor"
CN=CORE+" ordinary_return_bps refund_only_bps goods_per_order_minor recovery_per_ordinary_minor packing_per_order_minor ad_spend_minor ad_cap_bps explicit_tax_minor"
PH=CORE+" post_sign_refund_bps transaction_bps ad_commission_bps goods_per_signed_minor fx_exposed_goods_per_signed_minor refusal_return_minor cod_per_signed_minor cert_fixed_minor fx_source_ref cohort"
CN_AXES=("orders","ordinary_return_bps","ad_spend_minor","merchant_coupon_minor")
PH_AXES=("post_sign_refund_bps","creator_bps","platform_bps","per_order_outbound_minor","net_price_down_bps","adverse_fx_bps","cod_per_signed_minor")
SW="valid_from_inclusive valid_until_exclusive price_source_ref fee_source_ref"


def op(fn,*values):
    return None if any(v is None for v in values) else fn(*values)


def total(*values):
    return op(lambda *a:sum(a),*values)


def sub(a,b):
    return op(lambda x,y:x-y,a,b)


def mul(a,b):
    return op(lambda x,y:x*y,a,b)


def ratio(a,b):
    return op(lambda x,y:DEC(x)/DEC(y) if y else None,a,b)


def rate(bps):
    return ratio(bps,BASE)


def scale(x,bps):
    return mul(x,rate(bps))


def minor(x):
    return int(DEC(x).quantize(1,rounding=ROUND_HALF_UP)) if x is not None else None


def basis_points(x):
    return format((DEC(x)*BASE).quantize(DEC(".00000001"),rounding=ROUND_HALF_UP),"f") if x is not None else None


def out_values(d):
    return {k:minor(v) for k,v in d.items()}


def window(data,field):
    a,b=(stamp(data[k]) for k in ("valid_from_inclusive","valid_until_exclusive"))
    if b<=a:raise WorkflowError(f"{field}起止时间无效。")
    return a,b


def ph_cohort(data,at):
    check(data,"site source_ref observed_at orders signed refused open","PH 观察样本")
    if data["site"]!="PH":raise WorkflowError("观察样本不是菲律宾站。")
    if data["source_ref"] is not None:identify(data["source_ref"],"观察样本来源")
    if data["observed_at"] and stamp(data["observed_at"])>at:raise WorkflowError("签收样本晚于评估时点。")
    for key in ("orders","signed","refused","open"):amount(data[key],key)
    all_counts=[data[k] for k in ("signed","refused","open")]
    n=data["orders"]
    if n is not None and (sum(v for v in all_counts if v is not None)>n or None not in all_counts and sum(all_counts)!=n):
        raise WorkflowError("签收、拒收、在途与订单量冲突。")
    has_anchor=n not in (None,0) and data["source_ref"] and data["observed_at"]
    closed=has_anchor and data["open"]==0 and None not in all_counts
    signed=ratio(data["signed"],n) if has_anchor else None
    return {"sample":data,"signed_observed_bps":basis_points(signed),
            "mature":bool(closed),"signed_fraction":signed if closed else None}


def facts_for(site,data,at):
    check(data,CN if site=="CN" else PH,"费用与业务输入")
    bool_names=("floor_inclusive","stacking_allowed")
    require_bits(data,bool_names)
    for k,v in data.items():
        if k not in (*bool_names,"cohort","fx_source_ref"):
            amount(v,k,1 if k=="orders" else 0,10000 if k.endswith("_bps") else 10**12)
    if site=="CN":
        counts=total(data["ordinary_return_bps"],data["refund_only_bps"])
        if counts is not None and counts>10000:raise WorkflowError("普通退款与仅退款比例合计超过 100%。")
        if data["recovery_per_ordinary_minor"] is not None and data["goods_per_order_minor"] is not None \
                and data["recovery_per_ordinary_minor"]>data["goods_per_order_minor"]:
            raise WorkflowError("普通退货货损回收超过商品成本。")
        rates=[data[k] for k in ("platform_bps","creator_bps")]
        cohort=None
    else:
        if data["fx_source_ref"] is not None:identify(data["fx_source_ref"],"合成汇率来源")
        if data["goods_per_signed_minor"] is not None and data["fx_exposed_goods_per_signed_minor"] is not None \
                and data["fx_exposed_goods_per_signed_minor"]>data["goods_per_signed_minor"]:
            raise WorkflowError("汇率敞口超过可识别的商品成本。")
        rates=[data[k] for k in ("platform_bps","creator_bps","transaction_bps","ad_commission_bps")]
        cohort=ph_cohort(data["cohort"],at)
    if sum(x for x in rates if x is not None)>10000:raise WorkflowError("同口径费用合计可能超过 100%。")
    return cohort


def unit_price(a):
    return sub(a["list_minor"],total(a["merchant_coupon_minor"],a["platform_coupon_minor"]))


def price_checks(a,price):
    blocked=[];pending=[]
    if price is not None and price<0:
        blocked.append("negative_paid_price")
    coupons=[a["merchant_coupon_minor"],a["platform_coupon_minor"]]
    if all(x is not None and x>0 for x in coupons):
        if a["stacking_allowed"] is False:blocked.append("coupon_stacking_denied")
        elif a["stacking_allowed"] is None:pending.append("coupon_stacking_unknown")
    if a["platform_coupon_minor"] is not None and a["reimbursement_minor"] is not None and a["reimbursement_minor"]>a["platform_coupon_minor"]:
        blocked.append("reimbursement_exceeds_coupon")
    floor=a["floor_minor"]
    if price is not None and floor is not None:
        if price<floor or price==floor and a["floor_inclusive"] is False:blocked.append("buyer_price_below_floor")
        elif price==floor and a["floor_inclusive"] is None:pending.append("floor_equality_unknown")
    else:pending.append("price_or_floor_missing")
    return blocked,pending


def cn_ledger(a,unit):
    n=a["orders"]
    ordinary=scale(n,a["ordinary_return_bps"]);only=scale(n,a["refund_only_bps"])
    returns=total(ordinary,only)
    gross=mul(unit,n)
    paid_refunds=mul(unit,returns)
    subsidy=mul(a["reimbursement_minor"],n)
    clawback=mul(a["reimbursement_minor"],returns)
    retained=sub(total(sub(gross,paid_refunds),subsidy),clawback)
    platform=scale(retained,a["platform_bps"]);creator=scale(retained,a["creator_bps"])
    settle=sub(retained,total(platform,creator))
    goods_gross=mul(a["goods_per_order_minor"],n)
    recovery=mul(a["recovery_per_ordinary_minor"],ordinary)
    goods_net=sub(goods_gross,recovery)
    pack=mul(a["packing_per_order_minor"],n);ship=mul(a["per_order_outbound_minor"],n)
    all_costs=total(goods_net,pack,ship,a["ad_spend_minor"],a["other_fixed_minor"],a["explicit_tax_minor"])
    cap=scale(retained,a["ad_cap_bps"])
    numbers={"orders":n,"gross_paid_minor":gross,"ordinary_refund_minor":mul(unit,ordinary),
            "refund_only_minor":mul(unit,only),"platform_reimbursement_minor":subsidy,"reimbursement_clawback_minor":clawback,
            "retained_revenue_minor":retained,"platform_fee_minor":platform,"creator_fee_minor":creator,
            "forecast_settlement_minor":settle,"goods_before_recovery_minor":goods_gross,
            "ordinary_goods_recovery_minor":recovery,"goods_after_recovery_minor":goods_net,
            "packing_minor":pack,"outbound_minor":ship,"explicit_tax_minor":a["explicit_tax_minor"],
            "ad_spend_minor":a["ad_spend_minor"],"ad_spend_cap_minor":cap,"other_fixed_minor":a["other_fixed_minor"],
            "forecast_expenses_minor":all_costs,"forecast_profit_minor":sub(settle,all_costs)}
    return numbers


def ph_ledger(a,unit,cohort,fx):
    n=a["orders"];signed=mul(n,cohort["signed_fraction"]);refused=sub(n,signed)
    gross=mul(unit,signed);refund=scale(gross,a["post_sign_refund_bps"])
    subsidy=mul(a["reimbursement_minor"],signed);clawback=scale(subsidy,a["post_sign_refund_bps"])
    fees={name+"_minor":scale(gross,a[field]) for name,field in
          (("platform_fee","platform_bps"),("transaction_fee","transaction_bps"),("creator_fee","creator_bps"),
           ("advertising_commission","ad_commission_bps"))}
    retained=sub(total(sub(gross,refund),subsidy),clawback)
    settle=sub(retained,total(*fees.values()))
    raw_goods=mul(a["goods_per_signed_minor"],signed)
    exposed=mul(a["fx_exposed_goods_per_signed_minor"],signed)
    fx_goods=raw_goods if fx==1 else op(lambda g,x,f:g-x+x/f,raw_goods,exposed,fx)
    ship=mul(n,a["per_order_outbound_minor"]);reverse=mul(refused,a["refusal_return_minor"])
    cod=mul(signed,a["cod_per_signed_minor"])
    costs=total(fx_goods,ship,reverse,cod,a["cert_fixed_minor"],a["other_fixed_minor"])
    profit=sub(settle,costs)
    return ({"orders":n,"signed_projected_orders":signed,"refused_projected_orders":refused,"gross_placed_minor":mul(n,unit),
            "signed_gross_minor":gross,"post_sign_refund_minor":refund,"platform_reimbursement_minor":subsidy,
            "reimbursement_clawback_minor":clawback,"retained_revenue_minor":retained,"forecast_settlement_minor":settle,
            "goods_after_fx_minor":fx_goods,"outbound_minor":ship,"refusal_return_minor":reverse,"cod_minor":cod,
            "certificate_fixed_minor":a["cert_fixed_minor"],"other_fixed_minor":a["other_fixed_minor"],**fees,
            "forecast_expenses_minor":costs,"forecast_profit_minor":profit},
            {"signed":signed,"gross":gross,"subsidy":subsidy,"net_subsidy":sub(subsidy,clawback),"total_fees":total(*fees.values())})


def ph_break_even(a,model,parts):
    n=parts["signed"];expense=model["forecast_expenses_minor"]
    profit=model["forecast_profit_minor"]
    refund_limit=total(rate(a["post_sign_refund_bps"]),ratio(profit,total(parts["gross"],parts["subsidy"])))
    creator_limit=total(rate(a["creator_bps"]),ratio(profit,parts["gross"]))
    combined=total(*(a[k] for k in ("post_sign_refund_bps","platform_bps","creator_bps","transaction_bps","ad_commission_bps")))
    share=sub(1,rate(combined))
    limit=ratio(sub(expense,parts["net_subsidy"]),mul(n,share)) if share is None or share>0 else None
    return {"max_post_sign_refund_bps":basis_points(refund_limit),"max_creator_bps":basis_points(creator_limit),
            "minimum_paid_unit_price_minor":int(max(DEC(0),limit).to_integral_value(rounding=ROUND_CEILING)) if limit is not None else None,
            "thresholds_conditional_on_supplied_forecast":True}


def cash_setup(value,expected_settle,start,end):
    check(value,"source_ref opening_minor working_capital_minor minimum_reserve_minor payouts","预计现金表")
    if value["source_ref"] is not None:identify(value["source_ref"],"现金预算来源")
    for k in ("opening_minor","working_capital_minor","minimum_reserve_minor"):amount(value[k],k)
    rows=value["payouts"]
    if not isinstance(rows,list) or len(rows)>20:raise WorkflowError("预计回款批次应为不超过 20 条的清单。")
    seen=set();past=None;known=True;typed=[]
    for r in rows:
        check(r,"id at forecast_minor","预计回款批次")
        identify(r["id"],"批次 ID");amount(r["forecast_minor"],"预计回款金额")
        t=stamp(r["at"])
        if r["id"] in seen or t<end or past is not None and t<=past:
            raise WorkflowError("回款批次重复、倒序或早于本期结束。")
        seen.add(r["id"]);past=t
        if r["forecast_minor"] is None:known=False
        typed.append({**r,"days_from_start":(t-start).total_seconds()/86400})
    known_total=sum(r["forecast_minor"] for r in rows) if known else None
    mistake=known_total is not None and expected_settle is not None and known_total!=minor(expected_settle)
    usable=known_total is not None and expected_settle is not None and bool(value["source_ref"]) and not mistake
    return {"input":value,"forecast_rows":typed,"provided_total_minor":known_total,"baseline_amount_matches":usable,
            "contradictory_amounts":mistake}


def cash_rows(setup,settlement,expense,baseline):
    raw=setup["input"]
    known=setup["baseline_amount_matches"]
    before=sub(raw["opening_minor"],total(minor(expense),raw["working_capital_minor"]))
    first=setup["forecast_rows"][0] if setup["forecast_rows"] else None
    opening_known=before if raw["source_ref"] is not None else None
    forecast=minor(settlement)
    available=raw["minimum_reserve_minor"]
    gap=op(lambda x,y:max(0,y-x),opening_known,available)
    detail=[]
    if known and forecast is not None and forecast>=0:
        rows=setup["forecast_rows"];b=setup["provided_total_minor"]
        if rows and (b>0 or forecast==0):
            running=0
            for i,r in enumerate(rows):
                # Cumulative allocation prevents independent rounding from overshooting.
                earned=sum(row["forecast_minor"] for row in rows[:i+1])
                to_date=forecast if i==len(rows)-1 else minor(DEC(forecast)*earned/b) if b else 0
                detail.append({**r,"forecast_minor":to_date-running,"cash_treatment":"forecast_not_collected"})
                running=to_date
    matched=known and (bool(detail) or forecast==0)
    closing=total(opening_known,forecast) if matched else None
    return {"cash_before_first_receipt_minor":opening_known,"cash_gap_to_reserve_minor":gap,
            "cash_closing_forecast_minor":closing,"first_forecast_payout_at":first["at"] if first and matched else None,
            "first_forecast_payout_lag_days":first["days_from_start"] if first and matched else None,
            "payouts":detail,"schedule_conflict":setup["contradictory_amounts"] if baseline else False,
            "payout_evidence_complete":matched,"actual_cash_received_minor":None}


def calculate(site,p):
    check(p,"site currency scope campaign_id sku_id revision starts_at ends_at evaluated_at evidence facts stresses cash","活动压力")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("活动预测限定本站本币及合成合同。")
    identify(p["campaign_id"],"活动编号");identify(p["sku_id"],"商品 SKU")
    amount(p["revision"],"SKU 修订",1,10**6,False)
    start,end,at=(stamp(p[k]) for k in ("starts_at","ends_at","evaluated_at"))
    if start>=end:raise WorkflowError("活动日期无效。")
    check(p["evidence"],SW,"模拟费率/报价")
    since,until=window(p["evidence"],"证据")
    for k in ("price_source_ref","fee_source_ref"):
        if p["evidence"][k] is not None:identify(p["evidence"][k],k)
    global_block=["synthetic_terms_outside_evaluation_or_campaign_window"] if at<since or at>=until or start<since or end>until else []
    global_pending=["source_of_price_or_fee_unknown"] if not p["evidence"]["price_source_ref"] or not p["evidence"]["fee_source_ref"] else []
    a=p["facts"];cohort=facts_for(site,a,at)
    axes=CN_AXES if site=="CN" else PH_AXES
    check(p["stresses"]," ".join(axes),"独立压力值")
    for k,v in p["stresses"].items():
        amount(v,k,1 if k=="orders" else 0,9999 if k=="adverse_fx_bps" else 10000 if k.endswith("_bps") else 10**12)
    unit=unit_price(a)
    if site=="CN":
        baseline=cn_ledger(a,unit);breakeven=None
    else:
        baseline,parts=ph_ledger(a,unit,cohort,DEC(1))
        breakeven=ph_break_even(a,baseline,parts)
    cash=cash_setup(p["cash"],baseline["forecast_settlement_minor"],start,end)

    def row(facts,numbers,price,is_baseline):
        b,q=price_checks(facts,price)
        b.extend(global_block);q.extend(global_pending)
        if site=="CN":
            ad,cap=numbers["ad_spend_minor"],numbers["ad_spend_cap_minor"]
            if ad is None or cap is None:q.append("ad_cost_or_declared_cap_unknown")
            elif ad>cap:b.append("declared_ad_cost_cap_exceeded")
        if numbers["forecast_profit_minor"] is None:q.append("forecast_component_unknown")
        elif numbers["forecast_profit_minor"]<0:b.append("forecast_loss")
        liquidity=cash_rows(cash,numbers["forecast_settlement_minor"],numbers["forecast_expenses_minor"],is_baseline)
        if liquidity["schedule_conflict"]:b.append("forecast_payouts_do_not_reconcile")
        if not liquidity["payout_evidence_complete"] or liquidity["cash_gap_to_reserve_minor"] is None:q.append("cash_schedule_or_opening_unproven")
        if liquidity["cash_gap_to_reserve_minor"] is not None and liquidity["cash_gap_to_reserve_minor"]>0:b.append("forecast_cash_gap")
        return {"state":posture(b,q),"blocking_reasons":b,"pending_reasons":q,"buyer_paid_unit_minor":minor(price),
                "ledger":out_values(numbers),"cash":liquidity}
    initial=row(a,baseline,unit,True)
    stress=[]
    for axis in axes:
        value=p["stresses"][axis]
        modified={**a}
        if axis in a:modified[axis]=value
        price=unit_price(modified)
        if axis=="net_price_down_bps":
            price=scale(unit,sub(BASE,value))
        fx=sub(DEC(1),rate(value)) if axis=="adverse_fx_bps" and a.get("fx_source_ref") else None if axis=="adverse_fx_bps" else DEC(1)
        adjusted=facts_for(site,modified,at)
        if site=="CN":n=cn_ledger(modified,price)
        else:n,_=ph_ledger(modified,price,adjusted,fx)
        stress.append({"changed_axis":axis,"input_value":value,**row(modified,n,price,False)})
    return {"state":initial["state"],"baseline":initial,"pressure_tests":stress,"ph_break_even":breakeven,
            "ph_cohort":({k:v for k,v in cohort.items() if k!="signed_fraction"} if cohort else None),
             "cash_assumption":"modeled costs and working capital debited at kickoff; forecast settlements only",
            "quantity_display":"forecast quantities rounded half up for display only; money calculations retain precision",
            "price_or_policy_verified_for_platform":False,"cash_actual_collected":False,
            "source_refs":p["evidence"]}


def encode(x):
    return (json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def receipt(a,raw,report):
    return {**{k:a[k] for k in ("task_id","site","scope","tool_id","input_sha256","campaign_id")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "business_execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress):
    if assets or tool!=TOOL or site not in ROUTES:raise WorkflowError("仅接受本站合成活动预测数据。")
    if progress:progress("正在按本站合成成本、推广上限和资金回款做压力复算。")
    with localcontext() as ctx:
        ctx.prec=46
        values=calculate(site,p)
    result={**values,"task_id":folder.name,"tool_id":tool,"site":site,"scope":SYNTHETIC,
            "currency":p["currency"],"campaign_id":p["campaign_id"],"sku_id":p["sku_id"],"revision":p["revision"],
            "forecast_window":{k:p[k] for k in ("starts_at","ends_at","evaluated_at")},
            "execution_authorized":False,"model_called":False,"provider_called":False,"input_sha256":sha256(encode(p)).hexdigest()}
    raw=encode(result)
    report=(f"# {site} · {p['campaign_id']} · {result['state']}\n\n仅限合成活动预测，未核实际平台价格与费率。\n\n"
            +raw.decode("utf-8")).encode()
    f={k:folder/v for k,v in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(receipt(result,raw,report))}.items():
        with f[k].open("xb") as s:s.write(b)
    return result,f


def verify_saved(task,files):
    a=task["result"]
    if (task["local_files"]!=FILES or a!=json.loads(files["json"]) or
            (a["task_id"],a["site"],a["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
            json.loads(files["receipt"])!=receipt(a,files["json"],files["report"])):
        raise WorkflowError("活动压力结果与本机回执不一致。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"campaign-ops","label":"活动利润、现金与压力复核",
             "description":"按本站合成费用、优惠、订单和回款计划计算活动压力，金额为最小货币单位。",
             "input_example":{}}]
