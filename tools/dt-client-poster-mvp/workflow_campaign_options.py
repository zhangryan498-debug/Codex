"""Offline activity choice and COD stock/cash planning over pinned local evidence."""
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_competitor_bulletin import TOOL as BULLETIN
from workflow_campaign_sensitivity import TOOL as PROFIT
from workflow_offline_schedule import CAMPAIGN
from workflow_lead_weekly import encode, ratio, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

CHOICE = "campaign-offline-opportunity"
COD = "ph-campaign-cod-readiness"
STOCK = "ph-selection-stock-cash"
TOOLS = (CHOICE, COD, STOCK)
FILES = {"json": "activity-local.json", "report": "activity-local.md", "receipt": "activity-local-receipt.json"}
TYPES = {"CN": ("domestic_shop", "other_cn_shop"), "PH": ("local_shop", "cross_border_shop")}
MECHANICS = {"CN": ("cn_platform_flash", "cn_merchant_coupon", "cn_theme"),
             "PH": ("ph_flash_sale", "ph_coupon", "ph_affiliate")}
KINDS = {"CN": ("ordinary", "local_holiday"), "PH": ("local_holiday", "ordinary")}
CHECKS = {"platform": ("programme_access", "sku_admitted", "funding_review"),
          "merchant": ("merchant_control", "sku_readiness", "funding_review")}


class WorkflowError(ValueError):
    pass


def window(p):
    return stamp(p["starts_at"]), stamp(p["ends_at"])


def check_choice(site, p):
    task_id(p["competitor_task_id"], "竞品观察回执")
    task_id(p["schedule_task_id"], "活动排期回执")
    identify(p["competitor_observation_id"], "同 SKU 观察编号")
    context = p["competitor_window"]
    check(context, "starts_at ends_at", "历史竞品观察窗口")
    c0,c1 = window(context)
    if not c0 < c1 <= stamp(p["as_of"]):
        raise WorkflowError("竞品观察必须在明确且已结束的回看窗口。")
    if p["shop_type"] not in (*TYPES[site], None):
        raise WorkflowError("店铺类型超出本站合成口径。")
    event=p["event"]
    check(event,"kind name source_ref","活动节点")
    if event["kind"] not in KINDS[site]:
        raise WorkflowError("活动节点类型不匹配。")
    for k in ("name","source_ref"):
        term(event[k],k)
    if not isinstance(p["options"],list) or not 2 <= len(p["options"]) <= 4:
        raise WorkflowError("请比较两至四个活动备选。")
    ids=set(); mechanisms=set(); origins=set(); forecasts=set()
    for opt in p["options"]:
        check(opt,"id origin mechanic mechanism_detail profit_task_id eligibility budget_cap_minor budget_ref","活动备选")
        identify(opt["id"],"玩法编号")
        task_id(opt["profit_task_id"],"各玩法的独立预测")
        if (opt["id"] in ids or opt["profit_task_id"] in forecasts or opt["origin"] not in CHECKS
            or opt["mechanic"] not in MECHANICS[site] or (opt["origin"],opt["mechanic"]) in mechanisms):
            raise WorkflowError("每个备选须使用本站玩法、独立编号和预测。")
        ids.add(opt["id"]); mechanisms.add((opt["origin"],opt["mechanic"])); forecasts.add(opt["profit_task_id"]);origins.add(opt["origin"])
        term(opt["mechanism_detail"],"玩法说明")
        term(opt["budget_ref"],"合成预算出处",True)
        amount(opt["budget_cap_minor"],"活动总建模费用上限")
        k=opt["eligibility"]
        if k is None:
            continue
        check(k,"site currency scope campaign_id option_id origin mechanic sku_id revision shop_types starts_at ends_at observed_at source_ref checks","合成资格输入")
        if (k["campaign_id"],k["option_id"],k["origin"],k["mechanic"])!=(p["campaign_id"],opt["id"],opt["origin"],opt["mechanic"]):
            raise WorkflowError("资格卡与当前活动机制不一致。")
        amount(k["revision"],"资格 SKU 版本",1,10**6,False)
        if (k["site"],k["currency"],k["scope"],k["sku_id"],k["revision"])!=(site,p["currency"],SYNTHETIC,p["sku_id"],p["revision"]):
            raise WorkflowError("资格证据的站点、本币或 SKU 修订不符。")
        k0,k1=window(k)
        if not k0 < k1 or stamp(k["observed_at"]) > stamp(p["as_of"]):
            raise WorkflowError("资格卡时间无效。")
        term(k["source_ref"],"合成资格出处")
        ts=k["shop_types"]
        if not isinstance(ts,list) or any(x not in TYPES[site] for x in ts) or len(ts)!=len(set(ts)):
            raise WorkflowError("资格卡店铺类型不适用。")
        check(k["checks"]," ".join(CHECKS[opt["origin"]]),"资格检查")
        if any(x not in ("cleared_fixture","denied_fixture",None) for x in k["checks"].values()):
            raise WorkflowError("资格项只填写合成相符、不符或待核。")
    if origins != set(CHECKS):
        raise WorkflowError("需有平台与自营两类备选。")


def check_cod(p):
    task_id(p["schedule_task_id"],"PH排期")
    task_id(p["forecast_task_id"],"PH活动预测")
    identify(p["forecast_option_id"],"对应预测分支")
    inv=p["inventory"];cash=p["warehouse_funding"];threshold=p["monitor_thresholds"]
    check(inv,"sku_id revision local_warehouse_id location source_ref captured_at usable_units committed_units withheld_units recount_usable_units",
          "本地仓库存")
    amount(inv["revision"],"本地库存修订",1,10**6,False)
    if (inv["sku_id"],inv["revision"])!=(p["sku_id"],p["revision"]):
        raise WorkflowError("盘点 SKU 或修订不符。")
    if stamp(inv["captured_at"])>stamp(p["as_of"]):
        raise WorkflowError("库存晚于当前截点。")
    for k in ("local_warehouse_id","location","source_ref"):
        term(inv[k],k,True)
    for k in ("usable_units","committed_units","withheld_units","recount_usable_units"):
        amount(inv[k],k)
    check(cash,"source_ref available_minor committed_minor reserve_minor unit_purchase_minor","备货资金")
    term(cash["source_ref"],"资金证据",True)
    for k in ("available_minor","committed_minor","reserve_minor","unit_purchase_minor"):
        amount(cash[k],k)
    check(threshold,"source_ref signed_min_bps refused_max_bps","签收/拒收合成触发线")
    term(threshold["source_ref"],"合成触发线出处")
    for k in ("signed_min_bps","refused_max_bps"):
        amount(threshold[k],k,0,10000,False)
    amount(p["stock_max_age_hours"],"本次库存有效小时数",1,720,False)
    amount(p["safety_units"],"库存安全额")


def bind(site,tool,p,fetch):
    shared="site currency scope packet_id campaign_id sku_id revision starts_at ends_at as_of owner review_due "
    check(p,shared+{
        CHOICE:"schedule_task_id competitor_task_id competitor_window competitor_observation_id shop_type event options",
        COD:"schedule_task_id forecast_task_id forecast_option_id inventory warehouse_funding monitor_thresholds stock_max_age_hours safety_units",
        STOCK:"cod_task_id purchase_terms",
    }[tool],"活动本机方案")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC) or (tool!=CHOICE and site!="PH"):
        raise WorkflowError("只接受本站、本币合成输入。")
    for k in ("packet_id","campaign_id","sku_id"):
        identify(p[k],k)
    amount(p["revision"],"同 SKU 修订",1,10**6,False)
    st,en=window(p);at=stamp(p["as_of"])
    if not at<st<en or stamp(p["review_due"])<at:
        raise WorkflowError("只处理未启动的合成演练。")
    term(p["owner"],"责任人")
    pins={}; links={}
    def read(key, ident, route):
        r,pin=fetch(ident,site,route)
        if (r["site"],r["currency"],r["scope"])!=(site,p["currency"],SYNTHETIC):
            raise WorkflowError("上游不满足本站合成范围。")
        pins[key]=pin;links[key]=r
        return r
    def forecast(k,ident,branch):
        r=read(k,ident,PROFIT)
        w=r.get("forecast_window")
        if (r["campaign_id"],r["sku_id"],r["revision"])!=(f"{p['campaign_id']}-{branch}",p["sku_id"],p["revision"]) or not w or window(w)!=(st,en) or stamp(w["evaluated_at"])>at:
            raise WorkflowError("预测须按同活动分支、SKU 修订、起止时间绑定。")
    if tool==STOCK:
        task_id(p["cod_task_id"],"活动岗 COD 方案")
        check_purchase(p)
        r=read("cod",p["cod_task_id"],COD)
        if any(r[k]!=p[k] for k in ("campaign_id","sku_id","revision")) or window(r)!=(st,en) or stamp(r["as_of"])>at:
            raise WorkflowError("选品只接收本活动、本 SKU、本窗口的活动岗回执。")
        if (at-stamp(r["inventory_source"]["captured_at"])).total_seconds()>3600*r["stock_max_age_hours"]:
            raise WorkflowError("收件时库存证据已过合成有效期。")
    else:
        if tool==CHOICE:
            check_choice(site,p)
        else:
            check_cod(p)
        s=read("schedule",p["schedule_task_id"],CAMPAIGN)
        if (s["plan_id"],s["sku"],s["version"],stamp(s["event_starts_at"]))!=(p["campaign_id"],p["sku_id"],p["revision"],st):
            raise WorkflowError("排期与活动、SKU 版本及开始时间不一致。")
        if tool==COD:
            forecast("forecast",p["forecast_task_id"],p["forecast_option_id"])
        else:
            if (s["event_name"],s["event_type"])!=(p["event"]["name"],p["event"]["kind"]):
                raise WorkflowError("节点名称和类型必须吻合已冻结排期。")
            r=read("competitor",p["competitor_task_id"],BULLETIN)
            if stamp(r["cutoff_at"])>at:
                raise WorkflowError("竞品回执晚于当前截点。")
            observed=[x for x in r["observations"]["current"] if x["observation_id"]==p["competitor_observation_id"]]
            if any(x["identity"][2]!=p["sku_id"] for x in observed):
                raise WorkflowError("竞品观察 SKU 与方案不符。")
            for option in p["options"]:
                forecast("profit:"+option["id"],option["profit_task_id"],option["id"])
    return pins,links


def request_key(p):
    return p["packet_id"]


def assess_choice(p, links):
    history=links["competitor"]
    c0,c1=window(p["competitor_window"])
    observations=history["observations"]["current"]
    candidates=[r for r in observations if r["observation_id"]==p["competitor_observation_id"]]
    matched=[r for r in candidates if c0<=stamp(r["captured_at"])<c1 and r["source_ref"] and r["state"]=="READY_OFFLINE"]
    context=matched[0] if len(matched)==1 else None
    start,end=window(p)
    outcomes=[]
    for option in p["options"]:
        ledger=links["profit:"+option["id"]]["baseline"]
        proof=option["eligibility"]
        cap=option["budget_cap_minor"] if option["budget_ref"] else None
        expenses=ledger["ledger"]["forecast_expenses_minor"]
        profit=ledger["ledger"]["forecast_profit_minor"]
        blocked=list(ledger["blocking_reasons"])
        pending=list(ledger["pending_reasons"])
        valid_proof=proof is not None and window(proof)[0]<=start and end<=window(proof)[1]
        if not valid_proof:
            pending.append("eligibility_missing_or_out_of_window")
        else:
            if "denied_fixture" in proof["checks"].values() or (p["shop_type"] is not None and p["shop_type"] not in proof["shop_types"]):
                blocked.append("fixture_shop_type_or_check_denied")
            if p["shop_type"] is None or None in proof["checks"].values():
                pending.append("shop_type_or_check_unknown")
        if expenses is None or cap is None:
            pending.append("budget_basis_unknown")
        elif expenses>cap:
            blocked.append("modeled_expense_cap_exceeded")
        if not context:
            pending.append("matched_competitor_history_unknown")
        if links["schedule"]["state"]!="READY_OFFLINE":
            pending.append("schedule_not_ready")
        outcomes.append({"id":option["id"],"origin":option["origin"],"mechanic":option["mechanic"],
                         "mechanism_detail":option["mechanism_detail"],"qualification_test_input":proof,
                         "forecast_task_id":option["profit_task_id"],"profit_minor":profit,"modeled_expenses_minor":expenses,
                         "expense_cap_minor":cap,"expense_cap_source":option["budget_ref"],
                         "budget_headroom_minor":cap-expenses if cap is not None and expenses is not None else None,
                         "blocked_reasons":blocked,"pending_reasons":pending,
                         "state":"BLOCKED_FIXTURE" if blocked else "PENDING_EVIDENCE" if pending else "REVIEWABLE_FIXTURE"})
    ready=[x for x in outcomes if x["state"]=="REVIEWABLE_FIXTURE"]
    best=max((x["profit_minor"] for x in ready),default=None)
    return {"state":"OPTIONS_RECORDED","options":outcomes,"historical_context":context,
            "history_used_as_profit_or_eligibility":False,"shop_type":p["shop_type"],"event":p["event"],
            "recommended_option_ids":sorted(x["id"] for x in ready if x["profit_minor"]==best),
            "allocation_scope":"forecast_expenses_only; schedule reservations tracked separately",
            "actual_platform_qualification":None,"timezone":links["schedule"]["timezone"]}


def assess_cod(p, links):
    f=links["forecast"];snap=links["schedule"]["ph_preparation"];inv=p["inventory"];fund=p["warehouse_funding"]
    led=f["baseline"]["ledger"];cf=f["baseline"]["cash"];ch=f["ph_cohort"]
    orders=led["orders"]
    daily,days=(snap.get(k) if snap else None for k in ("daily_order_units","cover_days"))
    required=max(orders,daily*days)+p["safety_units"] if None not in (orders,daily,days,p["safety_units"]) else None
    usable,committed,withheld,recount=(inv[k] for k in ("usable_units","committed_units","withheld_units","recount_usable_units"))
    fresh=(stamp(p["as_of"])-stamp(inv["captured_at"])).total_seconds()<=p["stock_max_age_hours"]*3600
    conflict=bool(usable is not None and ((recount is not None and usable!=recount)
                  or (None not in (committed,withheld) and committed+withheld>usable)))
    sourced=bool(fresh and inv["source_ref"] and inv["local_warehouse_id"] and inv["location"])
    free=usable-committed-withheld if sourced and not conflict and None not in (usable,committed,withheld) else None
    replenish=max(required-free,0) if free is not None and required is not None else None
    paid_cost=(0 if replenish==0 else replenish*fund["unit_purchase_minor"]) if replenish is not None and (
        replenish==0 or fund["unit_purchase_minor"] is not None) else None
    available=(fund["available_minor"]-fund["committed_minor"]-fund["reserve_minor"]
               if fund["source_ref"] and None not in (fund["available_minor"],fund["committed_minor"],fund["reserve_minor"]) else None)
    funding_gap=max(0,paid_cost-available) if None not in (available,paid_cost) else None
    sample=ch["sample"] if ch and ch["mature"] else None
    signed=ratio(sample["signed"],sample["orders"]) if sample else None
    refused=ratio(sample["refused"],sample["orders"]) if sample else None
    tr=p["monitor_thresholds"]
    actions=[]
    if replenish is None:
        actions.append("RECOUNT_LOCAL_INVENTORY")
    elif replenish:
        actions.append("REVIEW_STOCK_SHORTAGE_AND_HOLD")
    if funding_gap is None or cf["cash_gap_to_reserve_minor"] is None:
        actions.append("REVIEW_UNPROVEN_CASH")
    elif funding_gap or cf["cash_gap_to_reserve_minor"]:
        actions.append("HOLD_FOR_CASH_GAP")
    if sample is None or signed is None:
        actions.append("AWAIT_CLOSED_COD_COHORT")
    elif (sample["signed"]*10000<tr["signed_min_bps"]*sample["orders"]
          or sample["refused"]*10000>tr["refused_max_bps"]*sample["orders"]):
        actions.append("REVIEW_LOW_SIGNED_OR_HIGH_REFUSAL")
    if f["state"]!="READY_OFFLINE" or links["schedule"]["state"]!="READY_OFFLINE":
        actions.append("REVIEW_UPSTREAM_PREDICTION_OR_SCHEDULE")
    return {"state":"HOLD_OR_RECHECK" if actions else "READY_FOR_LOCAL_PLAN_REVIEW",
            "inventory_source":inv,"stock_max_age_hours":p["stock_max_age_hours"],"warehouse_funding_source":fund,
            "stock":{"forecast_placed_orders":orders,"daily_order_units":daily,"cover_days":days,
                     "safety_units":p["safety_units"],"required_units":required,"available_units":free,
                     "conflict":conflict,"fresh":fresh,"recommended_replenishment_units":replenish},
            "cash":{"stock_allocation_after_commitments_and_reserve_minor":available,"replenishment_cost_minor":paid_cost,
                    "stock_funding_gap_minor":funding_gap,"forecast_campaign_cash_gap_minor":cf["cash_gap_to_reserve_minor"],
                    "forecast_first_payout_at":cf["first_forecast_payout_at"],
                    "forecast_first_payout_lag_days":cf["first_forecast_payout_lag_days"],
                    "payout_already_spendable":False,"actual_bank_receipt_minor":None},
            "cod_monitor":{"sample":sample,"signed_rate":signed,"refused_rate":refused,"test_trigger_terms":tr},
            "trigger_actions":actions}


def check_purchase(p):
    terms=p["purchase_terms"]
    check(terms,"source_ref lead_days safety_days minimum_units lot_units fixed_if_order_minor inbounds","补货与到仓假设")
    term(terms["source_ref"],"补货合同",True)
    for k,lo,hi in (("lead_days",1,45),("safety_days",0,30),("minimum_units",1,10**6),("lot_units",1,10**6),
                    ("fixed_if_order_minor",0,10**12)):
        amount(terms[k],k,lo,hi)
    rows=terms["inbounds"]
    if rows is None:
        return
    if not isinstance(rows,list) or len(rows)>20:
        raise WorkflowError("在途只使用已列明的承诺，未知请写 null。")
    seen=set()
    for v in rows:
        check(v,"id sku_id revision warehouse_id usable_day units source_ref confirmed","在途批次")
        for k in ("id","sku_id","warehouse_id"):
            identify(v[k],k)
        amount(v["revision"],"到货 SKU 版本",1,10**6,False)
        amount(v["usable_day"],"已知可售日",1,75)
        amount(v["units"],"净可售在途",0,10**7)
        term(v["source_ref"],"在途出处",True)
        if v["id"] in seen or (v["sku_id"],v["revision"])!=(p["sku_id"],p["revision"]) or type(v["confirmed"]) is not bool:
            raise WorkflowError("在途编号不得重复，SKU 修订或确认状态须明确。")
        seen.add(v["id"])


def procurement(p,r):
    t=p["purchase_terms"]
    unit=r["warehouse_funding_source"]["unit_purchase_minor"]
    free=r["stock"]["available_units"]
    daily=r["stock"]["daily_order_units"]
    lead,safety=(t[x] for x in ("lead_days","safety_days"))
    horizon=lead+safety if None not in (lead,safety) else None
    inbounds=t["inbounds"]
    stock_ref=r["inventory_source"]["local_warehouse_id"]
    known=t["source_ref"] and inbounds is not None and all(
        v["confirmed"] and v["source_ref"] and None not in (v["usable_day"],v["units"]) and v["warehouse_id"]==stock_ref
        for v in inbounds)
    arrivals={}
    if known:
        for v in inbounds:
            day=v["usable_day"]
            arrivals[day]=arrivals.get(day,0)+v["units"]
    target=daily*horizon if None not in (daily,horizon) else None
    timeline=[]
    if known and None not in (target,free):
        pos=free
        for day in range(1,horizon+1):
            before=pos-daily
            pos=before+arrivals.get(day,0)
            timeline.append({"day":day,"before_arrival_units":before,"after_existing_arrival_units":pos})
    gap=max(0,-timeline[-1]["after_existing_arrival_units"]) if timeline else None
    early=[q for q in timeline if q["day"]<=lead and q["before_arrival_units"]<0] if lead else []
    moq,step,fixed=(t[x] for x in ("minimum_units","lot_units","fixed_if_order_minor"))
    if gap==0:
        qty=0
    elif gap is not None and None not in (moq,step):
        qty=((max(gap,moq)+step-1)//step)*step
    else:
        qty=None
    known_product_cost=qty*unit if None not in (qty,unit) else None
    total=0 if qty==0 else known_product_cost+fixed if None not in (known_product_cost,fixed) else None
    avail=r["cash"]["stock_allocation_after_commitments_and_reserve_minor"]
    affordable=None
    if None not in (total,avail):
        if qty==0 or total<=avail:
            affordable=qty
        elif avail>=fixed and unit>0:
            partial=(avail-fixed)//(unit*step)*step
            affordable=partial if partial>=moq else 0
        else:
            affordable=0
    return {"terms":t,"horizon_days":horizon,"target_units":target,"known_inbound_scope":bool(known),
            "target_gap_units":gap,"requested_lot_units":qty,"known_product_cost_minor":known_product_cost,
            "complete_purchase_cost_minor":total,"budget_gap_minor":max(0,total-avail) if None not in (total,avail) else None,
            "budgeted_units_up_to_target":affordable,"gap_after_budgeted_order_units":max(0,gap-affordable)
                    if None not in (gap,affordable) else None,
            "first_shortage_before_new_receipt_day":early[0]["day"] if early else None,
            "timeline":timeline,"forecast_payout_added_to_cash":False}


def assess_stock(p,r):
    buy=procurement(p,r)
    held=(r["state"]!="READY_FOR_LOCAL_PLAN_REVIEW" or buy["complete_purchase_cost_minor"] is None
          or buy["budget_gap_minor"] is None or buy["budget_gap_minor"]>0
          or buy["first_shortage_before_new_receipt_day"] is not None)
    return {"state":"STOCK_PLAN_ON_HOLD" if held else "STOCK_PLAN_RECORDED",
            "cod_source_state":r["state"],"stock":r["stock"],"cash":r["cash"],"cod_monitor":r["cod_monitor"],
            "procurement":buy,"trigger_actions_from_campaign":r["trigger_actions"],
            "source_warehouse":r["inventory_source"]["local_warehouse_id"],
            "ordering_authorized":False,"purchase_execution_authorized":False,
            "pricing_execution_authorized":False,"release_status":"not_authorized"}



def receipt(r,b):
    return {**{k:r[k] for k in ("task_id","site","input_sha256","upstream")},
            "result_sha256":sha256(b["json"]).hexdigest(),"report_sha256":sha256(b["report"]).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("本机方案仅接收合成结构化资料。")
    pins,links=linked
    value=assess_choice(p,links) if tool==CHOICE else assess_cod(p,links) if tool==COD else assess_stock(p,links["cod"])
    keys=("site","currency","scope","packet_id","campaign_id","sku_id","revision","starts_at","ends_at","as_of","owner","review_due")
    r={**{k:p[k] for k in keys},**value,"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "actual_shop_or_campaign_action":False,"model_called":False}
    lines=[f"# {site} 本机方案 · {p['packet_id']}","","合成资格、成本与资金仅用于本机复核。",
           f"状态：{r['state']}。负责人：{p['owner']}，复核截止：{p['review_due']}。"]
    if tool==CHOICE:
        lines.append(f"节点：{p['event']['name']} · {value['timezone']} · 建议进入本机评审：{', '.join(value['recommended_option_ids']) or '待核'}")
        for x in value["options"]:
            lines.append(f"- {x['id']} · {x['mechanic']} · {x['state']} · 贡献、费用、上限（最小货币单位） "
                         f"{x['profit_minor']} / {x['modeled_expenses_minor']} / {x['expense_cap_minor']}")
    else:
        lines.append(f"预计下单 {r['stock']['forecast_placed_orders']}，需要 {r['stock']['required_units']} 件，"
                     f"可售 {r['stock']['available_units']} 件。待处理：{', '.join(r.get('trigger_actions',r.get('trigger_actions_from_campaign',[]))) or '无'}。")
    b={"json":encode(r),"report":("\n".join(lines)+"\n").encode("utf8")}
    b["receipt"]=encode(receipt(r,b))
    paths={k:folder/name for k,name in FILES.items()}
    for k,path in paths.items():
        with path.open("xb") as stream:
            stream.write(b[k])
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"])
        or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=receipt(r,files)):
        raise WorkflowError("本机方案或交接回执不一致。")


def public_tools():
    specs=((CHOICE,["CN","PH"],"campaign-ops","平台与自营活动选型"),
           (COD,["PH"],"campaign-ops","COD 履约与周转现金预案"),
           (STOCK,["PH"],"selection","库存备货与现金流收件"))
    return [{"id":t,"sites":sites,"role_suffix":role,"label":label,
             "description":"本机合成预案与来源回读。","input_example":{}} for t,sites,role,label in specs]

