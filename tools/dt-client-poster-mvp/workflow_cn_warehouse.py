"""China offline lot aging and scoped stock allocations using the recorded WMS snapshot."""
from hashlib import sha256
import json

from role_tools import SYNTHETIC
from workflow_campaign_options import check_purchase, procurement
from workflow_lead_weekly import encode, ratio, term
from workflow_live_rehearsal import STOCK as LIVE_STOCK, amount, check, identify, stamp, task_id

PLAN="cn-selection-warehouse-plan"
INTAKE="cn-stock-allocation-intake"
SHOP="cn-shop-stock-allocation-intake"
TOOLS=(PLAN,INTAKE,SHOP)
FILES={"json":"warehouse-plan.json","report":"warehouse-plan.md","receipt":"warehouse-plan-receipt.json"}
RECIPIENT={"live":"douyin-live-ops","shop":"douyin-shop-ops"}


class WorkflowError(ValueError):
    pass


def validate_proposal(p):
    task_id(p["stock_task_id"],"已有直播库存回执")
    terms=p["warehouse_funding"]
    check(terms,"source_ref balance_minor committed_minor reserve_minor unit_purchase_minor","CNY 备货资金")
    for k in ("balance_minor","committed_minor","reserve_minor","unit_purchase_minor"):
        amount(terms[k],k)
    term(terms["source_ref"],"资金出处",True)
    amount(p["daily_order_units"],"日新增未预占订单",0,10**7)
    term(p["demand_source"],"订单口径来源",True)
    check_purchase(p)
    for row in p["purchase_terms"]["inbounds"] or []:
        if row["warehouse_id"]!=p["warehouse_id"]:
            raise WorkflowError("在途只在当前仓库与 SKU 口径下可计。")
    prior=p["historical_turnover"]
    check(prior,"starts_at ends_at opening_sellable_units added_sellable_units fulfilled_units source_ref","历史售罄分母")
    left,right=(stamp(prior[k]) for k in ("starts_at","ends_at"))
    if not left<right<=stamp(p["as_of"]):
        raise WorkflowError("售罄观察须是已关闭的独立历史段。")
    term(prior["source_ref"],"历史出库出处",True)
    for k in ("opening_sellable_units","added_sellable_units","fulfilled_units"):
        amount(prior[k],k)
    book=p["lot_snapshot"]
    check(book,"captured_at source_ref recount_on_hand rows","库龄按批次盘点")
    if stamp(book["captured_at"])>stamp(p["as_of"]):
        raise WorkflowError("批次盘点晚于当前截点。")
    term(book["source_ref"],"批次盘点出处",True)
    amount(book["recount_on_hand"],"独立复盘账面")
    if not isinstance(book["rows"],list) or len(book["rows"])>30:
        raise WorkflowError("最多 30 个可核对批次。")
    seen=set()
    for r in book["rows"]:
        check(r,"id received_at on_hand reserved safety source_ref","批次库存")
        identify(r["id"],"批次编号")
        if r["id"] in seen:
            raise WorkflowError("同一批次不得重复进入在手。")
        seen.add(r["id"])
        if stamp(r["received_at"])>stamp(book["captured_at"]):
            raise WorkflowError("不得把未来入库放进现存。")
        for k in ("on_hand","reserved","safety"):
            amount(r[k],k)
        term(r["source_ref"],"批次出处",True)
    if any(row["id"] in seen for row in p["purchase_terms"]["inbounds"] or []):
        raise WorkflowError("已入库批次不能再次按在途抵扣。")
    if not isinstance(p["allocations"],list) or not 1<=len(p["allocations"])<=12:
        raise WorkflowError("请列一至十二个本机分货行。")
    for a in p["allocations"]:
        check(a,"id use units source_ref","分货")
        identify(a["id"],"分货号")
        if a["use"] not in RECIPIENT:
            raise WorkflowError("分货接收者不明确。")
        amount(a["units"],"分货件数",1,10**7,False)
        term(a["source_ref"],"合成分货计划出处")
    if not isinstance(p["covered_allocation_ids"],list) or len(p["covered_allocation_ids"])>20:
        raise WorkflowError("列明已由现货快照预占的具名分货。")
    for x in p["covered_allocation_ids"]:
        identify(x,"已在快照预占的分货")
    if len(set(p["covered_allocation_ids"]))!=len(p["covered_allocation_ids"]):
        raise WorkflowError("已预占记录中的分货 ID 不可重复。")
    term(p["covered_allocation_source"],"合成原预占对账单",True)


def bind(site,tool,p,fetch):
    t="stock_task_id lot_snapshot daily_order_units demand_source historical_turnover warehouse_funding purchase_terms allocations covered_allocation_ids covered_allocation_source"
    i="plan_task_id allocation_id use recipient units"
    check(p,"site currency scope packet_id sku_id revision session_id warehouse_id as_of owner "+(t if tool==PLAN else i),"库存本机闭环")
    if (site,p["site"],p["currency"],p["scope"])!=("CN","CN","CNY",SYNTHETIC):
        raise WorkflowError("本工具只接受抖音中国站合成库存。")
    for k in ("packet_id","sku_id","session_id","warehouse_id"):
        identify(p[k],k)
    amount(p["revision"],"商品修订",1,10**6,False)
    at=stamp(p["as_of"])
    term(p["owner"],"责任人")
    if tool==PLAN:
        validate_proposal(p)
        r,proof=fetch(p["stock_task_id"],site,LIVE_STOCK)
        inv=r["inventory"]["snapshot"]
        if r["fulfillment"]["original"]["warehouse_id"]!=p["warehouse_id"] or stamp(r["evaluated_at"])>at:
            raise WorkflowError("库存回执仓库或截点不一致。")
        if p["covered_allocation_ids"] and (not p["covered_allocation_source"] or p["covered_allocation_source"]!=inv["source_ref"]):
            raise WorkflowError("声称已预占须标明对应的合成原预占对账。")
        if stamp(inv["captured_at"])!=stamp(p["lot_snapshot"]["captured_at"]):
            raise WorkflowError("逐批盘点须和已验证库存快照处于同一截点。")
    else:
        task_id(p["plan_task_id"],"选品分货计划")
        identify(p["allocation_id"],"待签收分货号")
        amount(p["units"],"签收数量",1,10**7,False)
        r,proof=fetch(p["plan_task_id"],site,PLAN)
        if r["warehouse_id"]!=p["warehouse_id"] or stamp(r["as_of"])>at:
            raise WorkflowError("分货仓库或时间不一致。")
        x=r["allocation"]["unique_lines"].get(p["allocation_id"])
        if (not x or (x["units"],x["use"])!=(p["units"],p["use"])
            or p["use"]!=("live" if tool==INTAKE else "shop") or p["recipient"]!=RECIPIENT.get(p["use"])):
            raise WorkflowError("分货号、数量、用途或收件岗位不匹配。")
    if any(r[k]!=p[k] for k in ("site","currency","scope","sku_id","revision","session_id")):
        raise WorkflowError("不得跨站、跨 SKU 版本或场次读取。")
    return {"source":proof},r


def request_key(p):
    return p["packet_id"] if "allocation_id" not in p else f'{p["plan_task_id"]}:{p["allocation_id"]}'


def assess(p,r):
    inv=r["inventory"]
    snapshot=inv["snapshot"]
    book=p["lot_snapshot"]
    age=(stamp(p["as_of"])-stamp(snapshot["captured_at"])).total_seconds()
    fresh=age<=snapshot["max_age_seconds"] and r["state"]=="READY_OFFLINE"
    qty_mismatch=False
    bands={"0_to_30_days":0,"31_to_90_days":0,"over_90_days":0}
    lot_rows=[]
    total=dict(on_hand=0,reserved=0,safety=0)
    valid=bool(book["source_ref"] and book["rows"])
    for row in book["rows"]:
        age_days=(stamp(book["captured_at"])-stamp(row["received_at"])).days
        good=bool(row["source_ref"] and None not in (row["on_hand"],row["reserved"],row["safety"]))
        net=row["on_hand"]-row["reserved"]-row["safety"] if good else None
        qty_mismatch|=net is not None and net<0
        valid&=good
        band="0_to_30_days" if age_days<=30 else "31_to_90_days" if age_days<=90 else "over_90_days"
        if good:
            for k in total:
                total[k]+=row[k]
            bands[band]+=net
        lot_rows.append({**row,"age_days":age_days,"age_band":band,"sellable_units":net})
    if valid:
        qty_mismatch|=(total["on_hand"],total["reserved"],total["safety"])!=(snapshot["on_hand"],snapshot["reserved"],snapshot["safety_stock"])
        qty_mismatch|=book["recount_on_hand"] is not None and book["recount_on_hand"]!=total["on_hand"]
    free=inv["available_units"] if fresh and valid and not qty_mismatch else None
    allocations={};conflicts=set();duplicates=0
    for a in p["allocations"]:
        i=a["id"]
        if i not in allocations:
            allocations[i]=a
        elif allocations[i]==a:
            duplicates+=1
        else:
            conflicts.add(i)
    existing=set(p["covered_allocation_ids"])
    covered=sum(x["units"] for k,x in allocations.items() if k in existing)
    # Prior reservations are covered by the original snapshot, cross-check
    # them before excluding them from the incremental deduction.
    if covered and (snapshot["reserved"] is None or covered>snapshot["reserved"]):
        conflicts.update(existing.intersection(allocations))
    new=sum(x["units"] for k,x in allocations.items() if k not in existing) if not conflicts else None
    remaining=free-new if None not in (free,new) else None
    t=p["historical_turnover"]
    a,b,n=(t[k] for k in ("opening_sellable_units","added_sellable_units","fulfilled_units"))
    total_supply=a+b if t["source_ref"] and None not in (a,b) else None
    overturn=n is not None and total_supply is not None and n>total_supply
    turnover=ratio(n,total_supply) if not overturn else None
    f=p["warehouse_funding"]
    cash=f["balance_minor"]-f["committed_minor"]-f["reserve_minor"] if f["source_ref"] and None not in (
        f["balance_minor"],f["committed_minor"],f["reserve_minor"]) else None
    buy=procurement(p,{"warehouse_funding_source":f,"stock":{"available_units":remaining,
        "daily_order_units":p["daily_order_units"] if p["demand_source"] else None},
        "inventory_source":{"local_warehouse_id":p["warehouse_id"]},
        "cash":{"stock_allocation_after_commitments_and_reserve_minor":cash}})
    gates=[]
    if free is None:
        gates.append("WAREHOUSE_STALE_CONFLICT_OR_UNKNOWN")
    if conflicts:
        gates.append("ALLOCATION_ID_CONFLICT")
    if remaining is not None and remaining<0:
        gates.append("ALLOCATION_EXCEEDS_SELLABLE")
    if turnover is None:
        gates.append("TURNOVER_SOURCE_PENDING")
    if buy["budget_gap_minor"] is None:
        gates.append("PURCHASE_EVIDENCE_PENDING")
    elif buy["budget_gap_minor"]>0:
        gates.append("PURCHASE_BUDGET_GAP")
    if buy["first_shortage_before_new_receipt_day"] is not None:
        gates.append("STOCKOUT_BEFORE_NEW_SUPPLY")
    return {"state":"ALLOCATION_READY_FOR_LOCAL_REVIEW" if not gates else "HOLD_STOCK_OR_EVIDENCE",
        "triggers":gates,"lot_snapshot":book,"warehouse_snapshot":snapshot,
        "warehouse_age_seconds":age,"warehouse_snapshot_fresh":fresh,
        "lot_rows":lot_rows,"aging_band_sellable_units":bands if free is not None else None,
        "lot_count_conflict":qty_mismatch,"available_before_new_allocation_units":free,
        "remaining_after_new_allocation_units":remaining,"stockout_at_snapshot":free==0 if free is not None else None,
        "historical_turnover":{"source":t,"observed_sell_through":turnover,"denominator_units":total_supply,"conflict":overturn},
        "allocation":{"unique_lines":allocations,"deducted_incrementally_units":new,"already_covered_units":covered,
                      "duplicate_rows_ignored":duplicates,"conflict_ids":sorted(conflicts),
                      "ordered_units_subtracted_again":0,"refund_units_restocked_without_putaway":0},
        "usable_warehouse_cash_minor":cash,"procurement":buy}


def receive(p,r):
    okay=r["state"]=="ALLOCATION_READY_FOR_LOCAL_REVIEW"
    return {"state":"ALLOCATION_RECEIVED_FOR_LOCAL_REVIEW" if okay else "RETURNED_STOCK_OR_EVIDENCE",
            "recipient":p["recipient"],"allocation_id":p["allocation_id"],
            "received_units_for_review":p["units"] if okay else None,"source_plan_task_id":p["plan_task_id"],
            "source_state":r["state"],"source_triggers":r["triggers"],"second_inventory_deduction_units":0}


def receipt(r,b):
    return {**{k:r[k] for k in ("task_id","site","input_sha256","upstream")},
            "result_sha256":sha256(b["json"]).hexdigest(),"report_sha256":sha256(b["report"]).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("只接收同站本机合成快照及分货回执。")
    pins,source=linked
    out=assess(p,source) if tool==PLAN else receive(p,source)
    keys=("site","currency","scope","packet_id","sku_id","revision","session_id","warehouse_id","as_of","owner")
    r={**{k:p[k] for k in keys},**out,"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,"actual_inventory_modified":False,
       "purchase_execution_authorized":False,"pricing_execution_authorized":False,"release_status":"not_authorized",
       "model_called":False}
    lines=[f"# 抖音本机库存与分货 · {p['packet_id']}","","合成仓库情景，供选品与直播或商城接收岗核查。",
           f"状态：{r['state']}。仓库 {p['warehouse_id']} · {p['sku_id']}。"]
    if tool==PLAN:
        lines.extend([f"可售 {r['available_before_new_allocation_units']} · 新分货后 {r['remaining_after_new_allocation_units']}。",
                      f"库龄（可售件）：{r['aging_band_sellable_units']} · 需补处理：{', '.join(r['triggers']) or '无'}。"])
    else:
        lines.append(f"收件 {p['allocation_id']} · {p['recipient']} · 不再扣减库存。")
    b={"json":encode(r),"report":("\n".join(lines)+"\n").encode("utf8")}
    b["receipt"]=encode(receipt(r,b))
    paths={k:folder/n for k,n in FILES.items()}
    for k,f in paths.items():
        with f.open("xb") as stream:
            stream.write(b[k])
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or
        r["upstream"]!=task["source"]["role_packet_pins"] or
        (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
        json.loads(files["receipt"])!=receipt(r,files)):
        raise WorkflowError("库龄或分货收件回执无法核验。")


def public_tools():
    return [{"id":t,"sites":["CN"],"role_suffix":role,"label":label,
             "description":"合成仓库库存、补货和接收。","input_example":{}} for t,role,label in (
                 (PLAN,"selection","抖音库龄与分货备货"),(INTAKE,"live-ops","抖音直播仓库分货收件"),
                 (SHOP,"shop-ops","抖音商城仓库分货收件"))]

