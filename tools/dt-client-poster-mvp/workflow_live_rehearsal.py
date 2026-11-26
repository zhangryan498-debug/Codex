"""Same-site synthetic inventory, campaign receipt and live control rehearsal."""
from datetime import timedelta
from hashlib import sha256
import json
import re

import workflow_cn_campaign_offer as cn_offer
import workflow_ph_campaign_offer as ph_offer
import workflow_finance_offline as finance
import workflow_live as replay
from role_tools import ROUTES, SYNTHETIC
from role_tools_extra import _load

STOCK = "live-inventory-rehearsal"
BENEFIT = "live-benefit-intake"
CONTROL = "live-control-rehearsal"
RECOVERY = "live-recovery-evidence"
OFFER = "live-noncoupon-campaign-prep"
TOOLS = (STOCK, BENEFIT, CONTROL, RECOVERY, OFFER)
OUTPUTS = {"json":"live-rehearsal.json", "report":"live-rehearsal.md", "receipt":"live-rehearsal-receipt.json"}
COMMON = "site currency scope session_id sku_id revision version previous_task_id evaluated_at"
WINDOW = ("valid_from_inclusive", "valid_until_exclusive")
RISK_STATES = ("clear", "active", "unknown", None)
OBSERVATIONS = ("oral", "caption", "visual", "link")
STATUS = ("safe", "unsafe", "unknown", None)


class WorkflowError(ValueError):
    pass


def check(value, allowed, label):
    if not isinstance(value, dict) or value.keys() != set(allowed.split()):
        raise WorkflowError(f"{label}字段不符。缺失内容需显式填写 null。")


def identify(value, label, pattern=r"[A-Za-z0-9_-]{1,72}"):
    if not isinstance(value,str) or not re.fullmatch(pattern,value):
        raise WorkflowError(f"{label}标识无效。")


def stamp(value):
    try:
        stamp = replay._time(value,"本机证据时点")
    except replay.WorkflowError as exc:
        raise WorkflowError(str(exc)) from None
    if stamp.utcoffset()!=timedelta(hours=8):
        raise WorkflowError("需要明确的本站 +08:00 时间。")
    return stamp


def amount(value, label, lo=0, hi=10**12, nullable=True):
    if value is None and nullable:return
    if type(value) is not int or not lo<=value<=hi:
        raise WorkflowError(f"{label}无效。")


def task_id(value, label):
    identify(value, label, r"[a-f0-9]{32}")


def common(site,tool,payload):
    tail={
        STOCK:"snapshot fulfillment",
        BENEFIT:"mechanism campaign_task_id campaign_id coupon_refs price_version terms inventory_task_id",
        CONTROL:"request stock_task_id benefit_task_id observations restart finance_case increase",
        RECOVERY:"paused_task_id journal remedies valid_until",
        OFFER:"mechanism offer_id contract",
    }[tool]
    if tool==BENEFIT and (payload.get("mechanism")!="coupon" and (payload.get("campaign_task_id") or "offer_id" in payload or "offer_version" in payload)):
        tail+=" offer_id offer_version"
    check(payload,COMMON+" "+tail,"离线直播")
    if (payload["site"],payload["currency"],payload["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("只接受本站原币种的合成演练。")
    for k in ("session_id","sku_id"):
        identify(payload[k],k)
    for k in ("revision","version"):
        amount(payload[k],k,1,10**6,False)
    stamp(payload["evaluated_at"])
    prev=payload["previous_task_id"]
    if payload["version"]==1 and prev is not None or payload["version"]>1 and prev is None:
        raise WorkflowError("首版无需前序，后续版本必须绑定前一份本机回执。")
    if prev is not None:task_id(prev,"前序本机任务")
    if tool==BENEFIT:
        validate_coupon_refs(payload["coupon_refs"])
        if "offer_id" in payload:
            identify(payload["offer_id"],"非券权益编号")
            amount(payload["offer_version"],"非券条款版本",1,10**6,False)
        if payload["mechanism"]!="coupon" and (payload["campaign_id"] is not None or payload["price_version"] is not None or payload["coupon_refs"]):
            raise WorkflowError("非券权益须另用活动回执，不能用券或券价版本。")
    if tool==OFFER:
        identify(payload["offer_id"],"非券权益编号")
    if tool==BENEFIT:
        for k in ("inventory_task_id","campaign_task_id"):
            if payload[k] is not None:task_id(payload[k],k)
    if tool==RECOVERY:
        task_id(payload["paused_task_id"],"暂停检查点")
        stamp(payload["valid_until"])
    if tool==CONTROL:
        for k in ("stock_task_id","benefit_task_id"):
            if payload[k] is not None:task_id(payload[k],k)
    return payload["session_id"] if tool==CONTROL else payload["session_id"]+":"+payload["sku_id"]


def snapshot_at(site, subject, snap, at):
    check(snap,"site session_id sku_id revision source_ref captured_at order_basis on_hand reserved safety_stock ordered_units refunds_units stop_below max_age_seconds","库存")
    if [(snap[k]) for k in ("site","session_id","sku_id","revision")] != [site,subject["session_id"],subject["sku_id"],subject["revision"]]:
        raise WorkflowError("库存证据的站点、场次、SKU 或修订不符。")
    if snap["source_ref"] is not None:identify(snap["source_ref"],"库存来源")
    if snap["order_basis"] not in ("snapshot_reflects_orders",None):
        raise WorkflowError("只接受已反映订单移动的在手仓库快照；禁止逐窗再次扣销量。")
    for k in ("on_hand","reserved","safety_stock","ordered_units","refunds_units","stop_below","max_age_seconds"):
        amount(snap[k],k)
    age=(stamp(at)-stamp(snap["captured_at"])).total_seconds() if snap["captured_at"] is not None else None
    fresh=(None if age is None or snap["max_age_seconds"] is None else 0<=age<=snap["max_age_seconds"])
    values=[snap[k] for k in ("on_hand","reserved","safety_stock")]
    raw=values[0]-values[1]-values[2] if None not in values else None
    usable=raw if fresh and snap["order_basis"] and snap["source_ref"] else None
    blocked=[]; missing=[]
    if usable is None:missing.append("stock_source_basis_or_freshness_unverified")
    if snap["stop_below"] is None:missing.append("stock_cutoff_unknown")
    if usable is not None and (usable<0 or snap["stop_below"] is not None and usable<snap["stop_below"]):
        blocked.append("inventory_below_stop")
    return {"snapshot":snap,"age_seconds":age,"fresh":fresh,"reported_net_units":raw,"available_units":usable,
            "order_units_subtracted_again":0,"refund_units_added_without_putaway":0,"formula":"on_hand-reserved-safety_stock",
            "blocked":blocked,"missing":missing}


def fulfillment(site, subject, data):
    fields="warehouse_id source_ref shipping_state"
    if site=="PH":fields+=" cashflow_state cod pending_order_value_minor"
    check(data,fields,"仓配")
    for k in ("warehouse_id","source_ref"):
        if data[k] is not None:identify(data[k],k)
    controls={"fulfillment_state":data["shipping_state"]}
    if site=="PH":controls["cashflow_state"]=data["cashflow_state"]
    if any(x not in STATUS for x in controls.values()):raise WorkflowError("仓配或现金流状态无效。")
    blocked=[k+"_unsafe" for k,x in controls.items() if x=="unsafe"]
    pending=[k+"_unknown" for k,x in controls.items() if x in (None,"unknown")]
    if not data["source_ref"] or not data["warehouse_id"]:pending.append("warehouse_source_unknown")
    cod=None
    if site=="PH":
        amount(data["pending_order_value_minor"],"待收订单额")
        if data["cod"] is None:
            pending.append("cod_evidence_missing")
        else:
            c=data["cod"]
            try:
                cod=finance.cod(c)
            except finance.WorkflowError as exc:
                raise WorkflowError(str(exc)) from None
            parts=[c[k] for k in ("signed","refused","open")]
            if c["cohort_orders"] is not None and sum(x for x in parts if x is not None)>c["cohort_orders"]:
                raise WorkflowError("已知签收、拒收和未结数量之和已超过同批订单。")
            if any(c[k] is None for k in finance.COD_FIELDS):
                pending.append("cod_evidence_incomplete")
    return {"original":data,"controls":controls,"cod":cod,"pending_orders_counted_as_collected":False,
            "blocked":blocked,"missing":pending}


def posture(blocked,missing,ready="READY_OFFLINE"):
    return "BLOCK" if blocked else "PENDING" if missing else ready


def inventory(site,p):
    stock=snapshot_at(site,p,p["snapshot"],p["evaluated_at"])
    ledger=fulfillment(site,p,p["fulfillment"])
    blocked=stock["blocked"]+ledger["blocked"]
    pending=stock["missing"]+ledger["missing"]
    return {"state":posture(blocked,pending),"inventory":stock,"fulfillment":ledger,
            "blocking_reasons":blocked,"pending_reasons":pending,
            "next_review":"由仓配核对在手、预留、安全储备和截止时点。菲律宾另核同批签收、拒收及已汇回金额。"}


def parent_identity(row,p,*,match_sku=True):
    keys=["site","session_id"]+(["sku_id","revision"] if match_sku else [])
    if any(row[k]!=p[k] for k in keys):
        raise WorkflowError("上游任务与本站场次、SKU 或产品修订不符。")


def bind(project,site,tool,p,load,quote):
    chain=common(site,tool,p)
    ctx,pins={},{}
    def fetch(key,ident,actual_tool,latest=True):
        ctx[key],pins[key]=load(ident,actual_tool,site,latest)
        if stamp(ctx[key]["evaluated_at"])>stamp(p["evaluated_at"]):
            raise WorkflowError("不能引用晚于当前评估时点的回执。")
        return ctx[key]
    if p["previous_task_id"]:
        prior=fetch("previous",p["previous_task_id"],tool,False)
        if prior["chain"]!=chain or prior["version"]+1!=p["version"] or stamp(prior["evaluated_at"])>=stamp(p["evaluated_at"]):
            raise WorkflowError("前序必须是同链上一版本，且评估时点严格后移。")
    if tool==RECOVERY:
        paused=fetch("paused",p["paused_task_id"],CONTROL)
        if (paused["active_sku"],paused["active_revision"],paused["session_id"],paused["session_state"]) != (
                p["sku_id"],p["revision"],p["session_id"],"PAUSED_REHEARSAL"):
            raise WorkflowError("解除须引用同场、同现品版本、仍为暂停的工作台检查点。")
    if tool in (BENEFIT,CONTROL):
        inv=p["inventory_task_id"] if tool==BENEFIT else p["stock_task_id"]
        if inv:parent_identity(fetch("stock",inv,STOCK),p)
    if tool==BENEFIT and p["campaign_task_id"]:
        if p["mechanism"]=="coupon":
            pins["campaign"], raw, prior=quote(site,p["campaign_task_id"])
            ctx["campaign"]=priced_quote(project,site,p,raw,prior)
        else:
            parent_identity(fetch("prepared_offer",p["campaign_task_id"],OFFER),p)
    if tool==CONTROL and p["benefit_task_id"]:
        accepted=fetch("benefit",p["benefit_task_id"],BENEFIT)
        parent_identity(accepted,p)
        if accepted.get("source_campaign_task_id") and accepted.get("mechanism")=="coupon":
            pins["campaign"],raw,prior=quote(site,accepted["source_campaign_task_id"])
            if pins["campaign"]!=accepted["upstream"].get("campaign"):
                raise WorkflowError("活动价审原回执已变化。")
            ctx["current_quote"]=priced_quote(project,site,{**accepted,"evaluated_at":p["evaluated_at"]},raw,prior)
    if tool==CONTROL and isinstance(p["restart"],dict) and "proof_task_id" in p["restart"]:
        check(p["restart"],"proof_task_id","恢复请求")
        ident=p["restart"]["proof_task_id"]
        task_id(ident,"解除回执")
        parent_identity(fetch("restoration",ident,RECOVERY),p)
    return ctx,pins


def priced_quote(project,site,p,raw,prior):
    if (prior.get("scope"),prior.get("release_status"),prior.get("site"),prior.get("sku"),prior.get("revision")) != (
        SYNTHETIC,"not_authorized",site,p["sku_id"],p["revision"]):
        raise WorkflowError("活动价审来源的站点、SKU、产品修订或合成标记不匹配。")
    module=cn_offer if site=="CN" else ph_offer
    fn=module._approve if site=="CN" else module._read_review
    redone=fn(raw,project,p["evaluated_at"])
    refs=redone["coupon_refs"] if site=="CN" else [{"coupon_id":redone["coupon_id"],"version":redone["coupon_version"]}]
    equal=lambda a,b:sorted((r["coupon_id"],r["version"]) for r in a)==sorted((r["coupon_id"],r["version"]) for r in b)
    matches=(p["campaign_id"]==redone["campaign_id"] and p["price_version"]==redone["price_evidence_version"]
             and equal(p["coupon_refs"],refs))
    windows=[raw["price_evidence"],*(raw["coupons"] if site=="CN" else [raw["coupon"]])]
    if site=="CN":
        windows.append(raw["contract"])
        if raw["contract"]["stacking"] is not None:windows.append(raw["contract"]["stacking"])
    effective=[w.get(WINDOW[1]) for w in windows]
    till=min(stamp(v) for v in effective).isoformat() if all(effective) else None
    ok=redone["disposition"]=="PASS" and prior["disposition"]=="PASS" and matches and (
       site!="CN" or redone["scenario"]=="current")
    return {"passed":ok,"binding_matched":matches,"campaign_disposition":redone["disposition"],
            "prior_disposition":prior["disposition"],"coupon_refs":refs,"price_version":redone["price_evidence_version"],
            "source_price_sha256":redone["price_source_sha256"],"valid_until_exclusive":till,
            "price_minor":redone["host_calculation"]["numbers"]["buyer_paid_cents"] if ok else None,
            "floor_state":redone["host_calculation"]["floor_status"],"errors":redone["errors"],"unknowns":redone["unknowns"]}


def terms_at(terms, at):
    check(terms,"audience_key observed_audience_key eligible source_ref units valid_from_inclusive valid_until_exclusive","权益条件")
    for k in ("audience_key","observed_audience_key","source_ref"):
        if terms[k] is not None:identify(terms[k],k)
    if terms["eligible"] not in (True,False,None) or type(terms["eligible"]) is int:
        raise WorkflowError("权益资格使用 true、false 或 null。")
    amount(terms["units"],"所需库存",lo=1)
    a,b=(stamp(terms[k]) if terms[k] is not None else None for k in WINDOW)
    t=stamp(at)
    outside=(a is not None and t<a or b is not None and t>=b or a is not None and b is not None and a>=b)
    deny=[]; unknown=[]
    if outside:deny.append("benefit_window_invalid_or_expired")
    if terms["eligible"] is False or terms["audience_key"] is not None and terms["observed_audience_key"] is not None and terms["audience_key"]!=terms["observed_audience_key"]:
        deny.append("audience_ineligible_or_mismatch")
    if any(x is None for x in terms.values()):unknown.append("benefit_terms_incomplete")
    return deny,unknown


def validate_coupon_refs(refs):
    if not isinstance(refs,list) or len(refs)>2:raise WorkflowError("券版本须为明确的少量列表。")
    for r in refs:
        check(r,"coupon_id version","券版本")
        identify(r["coupon_id"],"券编号")
        amount(r["version"],"券版本",1,10**6,False)
    if len({r["coupon_id"] for r in refs})!=len(refs):raise WorkflowError("券编号重复。")


def prepare_noncoupon(site,p,at):
    """Recompute a campaign-owned draft using its declared synthetic limits."""
    kinds=("flash_sale","lucky_bag","draw") if site=="CN" else ("flash_sale",)
    kind=p["mechanism"]
    if kind not in kinds:raise WorkflowError("该站点没有此合成权益类型。")
    contract=p["contract"]
    check(contract,"audience_key eligible source_ref valid_from_inclusive valid_until_exclusive cap consumed requested units_per_allocation details","非券活动合同")
    for name in ("audience_key","source_ref"):
        if contract[name] is not None:identify(contract[name],name)
    require_bits(contract,("eligible",))
    for name in ("cap","consumed","requested","units_per_allocation"):
        amount(contract[name],name,lo=1 if name in ("requested","units_per_allocation") else 0)
    start,end=(stamp(contract[n]) if contract[n] is not None else None for n in WINDOW)
    moment=stamp(at)
    deny=[];pending=[]
    if start is not None and moment<start or end is not None and moment>=end or start is not None and end is not None and start>=end:
        deny.append("campaign_mechanism_interval_invalid_or_expired")
    if contract["eligible"] is False:deny.append("mechanism_eligibility_denied")
    unknown=[k for k,v in contract.items() if v is None]
    if unknown:pending.append("campaign_mechanism_conditions_missing")
    cap,consumed,requested,per=(contract[n] for n in ("cap","consumed","requested","units_per_allocation"))
    remaining=cap-consumed if cap is not None and consumed is not None else None
    needed=requested*per if requested is not None and per is not None else None
    if remaining is not None and (remaining<0 or requested is not None and requested>remaining):
        deny.append("mechanism_allocation_cap_exceeded")
    details=contract["details"]
    price=None
    entry=None
    if kind=="flash_sale":
        check(details,"price_minor floor_minor floor_inclusive floor_source_ref","秒杀价证")
        amount(details["price_minor"],"秒杀价")
        amount(details["floor_minor"],"底价")
        require_bits(details,("floor_inclusive",))
        if details["floor_source_ref"] is not None:identify(details["floor_source_ref"],"底价来源")
        if None in details.values():pending.append("flash_price_floor_evidence_missing")
        if details["price_minor"] is not None and details["floor_minor"] is not None:
            price=details["price_minor"]
            if price<details["floor_minor"] or price==details["floor_minor"] and details["floor_inclusive"] is False:
                deny.append("flash_price_below_declared_floor")
    else:
        extra=" entry_count entry_close draw_at" if kind=="draw" else ""
        check(details,"free_entry_charge_minor no_purchase_required"+extra,"免费权益")
        entry=details["free_entry_charge_minor"]
        amount(entry,"参与费用")
        require_bits(details,("no_purchase_required",))
        if entry is None or details["no_purchase_required"] is None:
            pending.append("free_entry_terms_missing")
        if entry not in (0,None) or details["no_purchase_required"] is False:
            deny.append("free_entry_terms_conflict")
        if kind=="draw":
            amount(details["entry_count"],"报名数")
            a=stamp(details["entry_close"]) if details["entry_close"] else None
            b=stamp(details["draw_at"]) if details["draw_at"] else None
            if a is None or b is None or details["entry_count"] is None:
                pending.append("draw_schedule_or_entry_count_missing")
            if a is not None and b is not None and (b<a or start is not None and a<start or end is not None and b>=end):
                deny.append("draw_schedule_invalid")
            if requested is not None and details["entry_count"] is not None and requested>details["entry_count"]:
                deny.append("draw_requested_awards_exceed_entries")
    return {"state":posture(deny,pending),"blocking_reasons":deny,"pending_reasons":pending,
            "mechanism":kind,"offer_id":p["offer_id"],"terms_version":p["version"],"contract":contract,
            "required_units":needed,"remaining_allocations":remaining,"sale_price_minor":price,
            "participant_charge_minor":entry,"retail_quote_usable_for_control":False,
            "can_issue_benefit":False,"campaign_role":ROUTES[site][0]+"-campaign-ops"}


def consume_noncoupon(site,p,prepared,blocked,pending):
    if prepared is None:
        pending.append("no_applicable_typed_campaign_receipt_for_mechanism")
        return None
    eligibility=prepare_noncoupon(site,prepared,p["evaluated_at"])
    terms=p["terms"]
    contract=prepared["contract"]
    if (prepared["offer_id"],prepared["version"],prepared["mechanism"]) != (p["offer_id"],p["offer_version"],p["mechanism"]):
        blocked.append("mechanism_or_terms_version_mismatch")
    if terms["audience_key"] is not None and contract["audience_key"] is not None and terms["audience_key"]!=contract["audience_key"]:
        blocked.append("campaign_audience_mismatch")
    expected=eligibility["required_units"]
    if expected is not None and terms["units"] is not None and expected!=terms["units"]:
        blocked.append("physical_quantity_does_not_match_campaign_allocation")
    for key in WINDOW:
        if terms[key] is None or contract[key] is None:continue
        if (key==WINDOW[0] and stamp(terms[key])<stamp(contract[key]) or
            key==WINDOW[1] and stamp(terms[key])>stamp(contract[key])):
            blocked.append("live_window_outside_campaign_window")
            break
    if eligibility["state"]=="BLOCK" or prepared["state"]=="BLOCK":
        blocked.extend(["campaign_mechanism_rejected",*eligibility["blocking_reasons"]])
    elif eligibility["state"]!="READY_OFFLINE" or prepared["state"]!="READY_OFFLINE":
        pending.append("campaign_mechanism_unproven")
    return {k:eligibility[k] for k in ("state","offer_id","terms_version","required_units","remaining_allocations",
            "sale_price_minor","participant_charge_minor","retail_quote_usable_for_control")}


def benefits(site,p,ctx):
    allowed=("coupon","flash_sale","lucky_bag","draw") if site=="CN" else ("coupon","flash_sale")
    if p["mechanism"] not in allowed:raise WorkflowError("权益机制不在本站离线接单范围。")
    if p["campaign_id"] is not None:identify(p["campaign_id"],"活动编号")
    amount(p["price_version"],"活动价证版本",1,10**6)
    refs=p["coupon_refs"]
    blocked,pending=terms_at(p["terms"],p["evaluated_at"])
    q=ctx.get("campaign")
    mechanism=None
    if p["mechanism"]!="coupon":
        mechanism=consume_noncoupon(site,p,ctx.get("prepared_offer"),blocked,pending)
    elif q and (not q["binding_matched"] or q["campaign_disposition"]=="REJECT" or q["prior_disposition"]=="REJECT"):
        blocked.append("campaign_price_or_coupon_binding_rejected")
    elif not q or not q["passed"]:
        pending.append("campaign_price_unverified")
    current=None
    if inv:=ctx.get("stock"):
        current=snapshot_at(site,p,inv["inventory"]["snapshot"],p["evaluated_at"])
        blocked+=current["blocked"]+inv["fulfillment"]["blocked"]
        pending+=current["missing"]+inv["fulfillment"]["missing"]
        needed=p["terms"]["units"]
        if current["available_units"] is not None and needed is not None and current["available_units"]<needed:
            blocked.append("benefit_required_units_unavailable")
    else:pending.append("live_inventory_receipt_missing")
    return {"state":posture(blocked,pending),"blocking_reasons":blocked,"pending_reasons":pending,
            "mechanism":p["mechanism"],"campaign_id":p["campaign_id"],"price_version":p["price_version"],
             "coupon_refs":refs,"terms":p["terms"],"quote":q,"stock_at_intake":current,
            "prepared_offer_receipt":mechanism,
            "source_campaign_task_id":p["campaign_task_id"],"can_issue_benefit":False,
            "intake_role":ROUTES[site][0]+"-live-ops"}


def require_bits(value, names):
    if any(value.get(k) is not None and type(value.get(k)) is not bool for k in names):
        raise WorkflowError("演练安全检查值必须是 true、false 或 null。")


def observation(site,p):
    value=p["observations"]
    keys="site session_id sku_id revision observed_at max_age_seconds source_ref risk_state connected oral caption visual link incident_ref surfaces"
    check(value,keys,"场中观察")
    parent_identity(value,p)
    ages=(stamp(p["evaluated_at"])-stamp(value["observed_at"])).total_seconds() if value["observed_at"] else None
    amount(value["max_age_seconds"],"观察有效秒数")
    if value["risk_state"] not in RISK_STATES:raise WorkflowError("风险状态无效。")
    require_bits(value,("connected",*OBSERVATIONS))
    for k in ("source_ref","incident_ref"):
        if value[k] is not None:identify(value[k],k)
    fresh=ages is not None and value["max_age_seconds"] is not None and 0<=ages<=value["max_age_seconds"] and bool(value["source_ref"])
    if value["surfaces"] is not None:
        check(value["surfaces"]," ".join(OBSERVATIONS),"四路画面和口播")
        for obs in value["surfaces"].values():
            if obs is None:continue
            check(obs,"sku_id revision source_ref price_minor","单路记录")
            identify(obs["sku_id"],"画面实际 SKU")
            identify(obs["source_ref"],"单路记录来源")
            amount(obs["revision"],"画面修订",1,10**6,False)
            amount(obs["price_minor"],"该路显示金额")
    return value,fresh


def surface_checks(value,p,quote):
    errors,unknowns=[],[]
    for kind in OBSERVATIONS:
        surface=(value["surfaces"] or {}).get(kind)
        if not surface:
            unknowns.append("surface_evidence_missing:"+kind)
            continue
        if (surface["sku_id"],surface["revision"])!=(p["sku_id"],p["revision"]):
            errors.append("surface_sku_or_revision_mismatch:"+kind)
        if kind=="oral" and surface["price_minor"] is None:
            unknowns.append("oral_price_unknown")
        if quote and quote["passed"] and surface["price_minor"] is not None and surface["price_minor"]!=quote["price_minor"]:
            errors.append("surface_price_mismatch:"+kind)
    return errors,unknowns


def restoration(p,ctx,pins):
    paused=ctx["paused"]
    at=stamp(p["evaluated_at"])
    log=p["journal"]
    remedies=p["remedies"]
    check(log,"anchor_version anchor_result_sha256 events","演练事件日志")
    amount(log["anchor_version"],"日志锚点",1,10**6,False)
    identify(log["anchor_result_sha256"],"暂停结果指纹",r"[a-f0-9]{64}")
    if not isinstance(log["events"],list) or not 1<=len(log["events"])<=30:
        raise WorkflowError("演练日志需要 1–30 条显式事件。")
    if not isinstance(remedies,list) or len(remedies)>32:
        raise WorkflowError("演练解除清单最多记录 32 个事件。")
    errors=[];pending=[]
    anchor=paused["version"]
    if (log["anchor_version"],log["anchor_result_sha256"])!=(anchor,pins["paused"]["result_sha256"]):
        errors.append("paused_checkpoint_anchor_mismatch")
    valid_prior=stamp(paused["evaluated_at"])
    previous=valid_prior
    ids=set()
    required_incidents=paused["open_incident_refs"]
    seen=set()
    for i,e in enumerate(log["events"],anchor+1):
        check(e,"id site session_id seq kind at source_ref covered_through_seq","演练日志事件")
        for key in ("id","source_ref"):identify(e[key],key)
        amount(e["seq"],"事件游标",1,10**7,False)
        if e["covered_through_seq"] is not None:amount(e["covered_through_seq"],"事件覆盖游标",1,10**7,False)
        if e["kind"] not in ("disconnect","reconnect","observe","resync"):
            raise WorkflowError("合成日志事件类型无效。")
        now=stamp(e["at"])
        if (e["site"],e["session_id"])!=(p["site"],p["session_id"]) or e["id"] in ids or e["seq"]!=i or not previous<now<=at:
            errors.append("journal_identity_order_or_gap")
        ids.add(e["id"])
        previous=now
        if e["kind"]=="resync" and (e["covered_through_seq"]!=e["seq"]-1 or e is not log["events"][-1]):
            errors.append("resync_cursor_unverified")
        if e["kind"]!="resync" and e["covered_through_seq"] is not None:
            errors.append("unexpected_resync_coverage")
    if log["events"][-1]["kind"]!="resync":
        pending.append("resync_event_missing")
    until=stamp(p["valid_until"])
    if until<=at:
        errors.append("restoration_receipt_expired")
    for record in remedies:
        check(record,"incident_ref site session_id sku_id revision reviewer removal_ref lift_ref action removed_at lifted_at","单个解除记录")
        for key in ("incident_ref","reviewer","removal_ref","lift_ref"):identify(record[key],key)
        before,after=stamp(record["removed_at"]),stamp(record["lifted_at"])
        if (record["site"],record["session_id"],record["sku_id"],record["revision"])!=(p["site"],p["session_id"],p["sku_id"],p["revision"]) \
                or not valid_prior<before<=after<=at or record["action"]!="approved_for_rehearsal" \
                or record["incident_ref"] not in required_incidents or record["incident_ref"] in seen:
            errors.append("incident_remedy_identity_or_approval_mismatch")
        seen.add(record["incident_ref"])
    if set(required_incidents)-seen:
        pending.append("outstanding_incident_remedy_missing")
    return {"state":posture(errors,pending),"blocking_reasons":errors,"pending_reasons":pending,
            "paused_task_id":p["paused_task_id"],"paused_checkpoint_version":anchor,
            "resolved_incident_refs":sorted(seen) if not errors and not pending else [],
            "last_resync_seq":log["events"][-1]["seq"] if log["events"][-1]["kind"]=="resync" and not errors else None,
            "valid_until":p["valid_until"],"synthetic_journal":log,"synthetic_remedies":remedies,
            "platform_lift_verified":False,"earlier_transport_history_verified":False}


def control(project,site,p,ctx):
    request=p["request"]
    check(request,"action resume_at switch_ref switch_candidate_cleared increment_minor","场中动作")
    mode=request["action"]
    if mode not in ("continue","pause","switch","resume","increase","close"):raise WorkflowError("动作不在本机彩排范围。")
    require_bits(request,("switch_candidate_cleared",))
    for k in ("resume_at","increment_minor"):amount(request[k],k)
    if request["switch_ref"] is not None:identify(request["switch_ref"],"替代款演练方案")
    old=ctx.get("previous")
    prior_state=old["session_state"] if old else "ACTIVE_REHEARSAL"
    active=old["active_sku"] if old else p["sku_id"]
    active_revision=old["active_revision"] if old else p["revision"]
    if (mode=="switch" and (old is None or p["sku_id"]==active)) or (mode!="switch" and p["sku_id"]!=active):
        raise WorkflowError("只有已保存前序的切品可请求不同 SKU。")
    if mode!="switch" and p["revision"]!=active_revision:
        raise WorkflowError("继续、暂停或恢复不得更换当前商品修订。")
    observation_data,fresh=observation(site,p)
    restart=ctx.get("restoration")
    error=[]; missing=[]
    hazard=observation_data["incident_ref"]
    prior_incidents=old["open_incident_refs"] if old else []
    current_inv=ctx.get("stock")
    available=None
    if mode in ("pause","close"):
        decision="pause" if mode=="pause" else "close"
    else:
        if prior_state=="CLOSED_REHEARSAL":error.append("closed_session_terminal")
        if observation_data["risk_state"]=="active" or observation_data["connected"] is False or any(observation_data[x] is False for x in OBSERVATIONS):
            error.append("known_safety_or_content_problem")
        if not fresh or observation_data["risk_state"]!="clear" or observation_data["connected"] is not True or any(observation_data[x] is not True for x in OBSERVATIONS):
            missing.append("current_content_link_risk_or_connection_unverified")
        if prior_state=="PAUSED_REHEARSAL":
            if mode!="resume":
                error.append("paused_session_requires_explicit_resume")
            if (restart is None or restart["state"]!="READY_OFFLINE"
                    or restart["paused_task_id"]!=p["previous_task_id"]
                    or restart["paused_checkpoint_version"]!=old["version"]
                    or restart["resolved_incident_refs"]!=prior_incidents
                    or stamp(p["evaluated_at"])>=stamp(restart["valid_until"]) or hazard is not None):
                error.append("restart_binding_or_resync_not_confirmed")
        elif mode=="resume":
            error.append("resume_requires_paused_checkpoint")
        if mode=="switch" and (request["switch_candidate_cleared"] is not True or not request["switch_ref"]):
            error.append("target_switch_plan_unproven")
        if current_inv:
            sv=snapshot_at(site,p,current_inv["inventory"]["snapshot"],p["evaluated_at"])
            available=sv["available_units"]
            error+=sv["blocked"]+current_inv["fulfillment"]["blocked"]
            missing+=sv["missing"]+current_inv["fulfillment"]["missing"]
            if prior_state=="PAUSED_REHEARSAL" and (available is None or request["resume_at"] is None
                    or sv["snapshot"]["stop_below"] is None or request["resume_at"]<=sv["snapshot"]["stop_below"]
                    or available<request["resume_at"]):
                error.append("stock_resume_hysteresis_not_met")
        else:missing.append("current_inventory_missing")
        quote=ctx.get("current_quote")
        received=ctx.get("benefit")
        errors,unproven=surface_checks(observation_data,p,quote)
        error+=errors
        missing+=unproven
        if not quote or not quote["passed"] or not received or received["state"]!="READY_OFFLINE":
            error.append("current_bound_price_unproven")
        elif any(terms_at(received["terms"],p["evaluated_at"])):
            error.append("benefit_window_or_audience_no_longer_valid")
        elif available is not None and available<received["terms"]["units"]:
            error.append("current_stock_below_benefit_units")
        if p["finance_case"] is None:missing.append("same_sku_financial_window_missing")
        decision="hold"
    # Pause and close remain reachable without a financial observation or price receipt.
    detail=None
    if decision=="hold" and not error and not missing:
        detail=finance_decision(project,site,p,active,prior_state,current_inv)
        decision=detail["action"]
        if mode=="resume" and decision=="propose_continue":decision="propose_resume"
    if prior_state=="CLOSED_REHEARSAL":
        decision="closed"
    elif error:
        decision="pause"
    elif missing and decision=="hold":
        decision="hold"
    switched=decision=="propose_switch"
    state=("CLOSED_REHEARSAL" if decision in ("close","closed") else "PAUSED_REHEARSAL" if decision=="pause"
           else "ACTIVE_REHEARSAL" if decision in ("propose_switch","propose_resume","propose_continue","propose_increase") else prior_state)
    known_b=bool(detail and detail["current_windows_complete"])
    finance_scope=None
    if detail:
        finance_scope={"sku_id":active,"same_basis_windows_complete":known_b,"old_sku_ledger":detail["cumulative"]["mature"] if known_b else None}
    incidents=sorted(set(prior_incidents+([hazard] if hazard is not None else [])))
    if decision=="propose_resume":incidents=[]
    return {"state":decision,"session_state":state,"active_sku":p["sku_id"] if switched else active,
            "active_revision":p["revision"] if switched else active_revision,
            "new_sku_baseline":({"sku_id":p["sku_id"],"state":"not_observed","roi":None,"retained_minor":None} if switched else None),
             "open_incident_ref":incidents[0] if incidents else None,"open_incident_refs":incidents,
             "restoration_evidence_scope":"separately checked same-site synthetic journal and incident ledger when supplied; independent platform unverified",
            "blocking_reasons":error+(detail["blocking_reasons"] if detail else []),"pending_reasons":missing,
            "available_units":available,"financial_observations":finance_scope,"observations_fresh":fresh,
            "proposed_increment_minor":detail["proposed_increment_minor"] if detail and decision=="propose_increase" else 0,
            "increment_cap_minor":detail.get("increment_cap_minor") if detail else None,
            "incremental_profit_lower_minor":detail.get("incremental_profit_lower_minor") if detail else None,
            "simulated_transport_command":None,"rehearsal_action_only":True}


def finance_decision(project,site,p,active,prior_state,inv):
    mode=p["request"]["action"]
    wire=p["finance_case"]
    if not isinstance(wire,dict) or (wire.get("site"),wire.get("session_id"),wire.get("sku_id"),wire.get("evaluation_time")) != (
            site,p["session_id"],active,p["evaluated_at"]):
        raise WorkflowError("财务窗口须绑定当前 SKU 和评估时点；不能用目标 SKU 或其他场次代替。")
    try:
        data=replay._input(site,wire)
    except replay.WorkflowError as exc:
        raise WorkflowError(str(exc)) from None
    if "inventory" in wire or "ph_controls" in wire:raise WorkflowError("库存与菲律宾履约只能取封存回执，不接收内联替代。")
    data.update(target_sku=p["sku_id"],hard_risk=False,
                requested_action="continue" if mode=="resume" else mode,restart_checks_verified=mode=="resume")
    raw=inv["inventory"]["snapshot"]
    data["inventory"]={**{k:(raw["captured_at"] if k=="snapshot_time" else raw[k]) for k in replay.INVENTORY},
                       "paused":prior_state=="PAUSED_REHEARSAL","resume_at":p["request"]["resume_at"]}
    data["budget"]["requested_increment_minor"]=p["request"]["increment_minor"]
    data["switch"]={"preapproved":mode=="switch","identity_verified":mode=="switch","pricing_revision_verified":mode=="switch"}
    if site=="PH":data["ph_controls"]={**inv["fulfillment"]["controls"],"site":site}
    trial=p["increase"]
    if trial is not None:
        check(trial,"source_ref source_kind sku_id session_id start end mature matched_basis approved budget_constraint_evidence control_limits_verified cooldown_satisfied min_incremental_profit_minor absolute_increment_cap_minor balance_fraction_bps risk_spend_cap_minor incremental_pre_ad_contribution_lower_minor","受控增量演练")
        identify(trial["source_ref"],"合成增量证据来源")
        data["increase_evidence"]=trial
    m=_load(project,"live_window_review_v6" if site=="CN" else "live_window_ph_v3")
    eval_case=m.review_cn if site=="CN" else m.review_ph
    checked=eval_case(data,lambda _:{"proven":True,"reason":"current_site_synthetic_campaign_and_live_intake_only"})
    if checked["execution_authorized"] is not False or checked["release_status"]!="not_authorized":
        raise WorkflowError("离线计算器超出彩排权限。")
    return checked


def encode(data):
    return (json.dumps(data,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def sha(data):
    return sha256(data).hexdigest()


def receipt(result,raw,report):
    return {**{k:result[k] for k in ("task_id","site","tool_id","scope","session_id","sku_id","revision","version",
                "input_sha256","upstream","execution_authorized")},"result_sha256":sha(raw),"report_sha256":sha(report)}


def output_names(attempt):
    return OUTPUTS if attempt is None else {key:f"attempt-{attempt}/{name}" for key,name in OUTPUTS.items()}


def run_workflow(project,site,tool,p,task_dir,assets,progress,*,linked=None,attempt=None):
    if linked is None or assets:raise WorkflowError("须有已经绑定的本机演练证据。")
    chain=common(site,tool,p)
    ctx,pins=linked
    if progress:progress("正在核对离线库存、权益或场中动作并保存凭据。")
    if tool==STOCK:body=inventory(site,p)
    elif tool==BENEFIT:body=benefits(site,p,ctx)
    elif tool==RECOVERY:body=restoration(p,ctx,pins)
    elif tool==OFFER:body=prepare_noncoupon(site,p,p["evaluated_at"])
    else:body=control(project,site,p,ctx)
    result={**body,**{k:p[k] for k in COMMON.split()},"chain":chain,"task_id":task_dir.name,"tool_id":tool,
            "upstream":pins,"input_sha256":sha(encode(p)),"execution_authorized":False,
            "release_status":"not_authorized","model_called":False,"provider_called":False,
            "platform_control_performed":False}
    raw=encode(result)
    lines=[f"# {site} · {tool} · {p['session_id']} · {p['sku_id']} v{p['version']}", "",
           f"本机彩排状态 {result['state']}。已保存证据，不执行活动、安排行为、上架、开播、修改预算或恢复真实直播。",
           "",*(f"- {x}" for x in result["blocking_reasons"]+result["pending_reasons"]),""]
    report=("\n".join(lines)+"\n"+raw.decode("utf-8")).encode("utf-8")
    if attempt is not None:
        result["attempt"]=attempt
        raw=encode(result)
        report=("\n".join(lines)+"\n"+raw.decode("utf-8")).encode("utf-8")
        (task_dir/f"attempt-{attempt}").mkdir()
    files={k:task_dir/v for k,v in output_names(attempt).items()}
    for k,b in (("json",raw),("report",report),("receipt",encode(receipt(result,raw,report)))):
        with files[k].open("xb") as f:f.write(b)
    return result,files


def verify_artifacts(task,files):
    result=task["result"]
    if (task["local_files"]!=output_names(task.get("attempt")) or result.get("attempt")!=task.get("attempt")
            or result!=json.loads(files["json"]) or result["task_id"]!=task["id"]
            or result["site"]!=task["site"] or result["tool_id"]!=task["source"]["tool_id"]
            or json.loads(files["receipt"])!=receipt(result,files["json"],files["report"])):
        raise WorkflowError("直播演练产物和本机回执不一致。")


def public_tools():
    return [{"id":tool,"role_suffix":"campaign-ops" if tool==OFFER else "live-ops","sites":list(ROUTES),"label":label,
             "description":"本站合成演练，使用同场次和 SKU 的本机回执，限制实际平台执行。",
             "input_example":{}}
            for tool,label in ((STOCK,"直播仓配与库存离线核对"),(BENEFIT,"直播权益离线收件"),
                               (CONTROL,"直播异常切品及恢复彩排"),(RECOVERY,"直播演练重同步及解除证据"),(OFFER,"活动非券权益离线准备回执"))]
