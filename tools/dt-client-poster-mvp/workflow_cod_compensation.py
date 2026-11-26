"""Optional evidence for platform-paid COD compensation, separate from revenue and bank cash."""

from collections import defaultdict
from role_tools import SYNTHETIC
from workflow_lead_weekly import term
from workflow_live_rehearsal import amount, check, identify, stamp

FIELDS = "orders settlements source_ref shop_id scope site currency completeness"
ORDER = "order_id claim_id handed_at cancelled_at cancelled_by approved_at buyer_collected_minor handover_ref cancel_ref claim_ref buyer_ref"
POSTING = "settlement_id order_id claim_id posted_at amount_minor source_ref"
PHASES = ("previous","current")


class WorkflowError(ValueError):
    pass


def validate(p, shop):
    check(p,FIELDS,"COD 赔付明细")
    if (p["site"],p["currency"],p["scope"],p["shop_id"])!=("PH","PHP",SYNTHETIC,shop) or shop is None:
        raise WorkflowError("COD 赔付明细必须与菲律宾月报同店、同币种、合成范围一致。")
    term(p["source_ref"],"合成明细档来源")
    check(p["completeness"],"previous current","赔付归档覆盖")
    for v in p["completeness"].values():
        if v is not None:
            term(v,"完整的结算明细取证")
    for name,fields in (("orders",ORDER),("settlements",POSTING)):
        if not isinstance(p[name],list) or len(p[name])>150:
            raise WorkflowError("此月报最多接收 150 条同类合成明细。")
        for r in p[name]:
            check(r,fields,"COD 订单或结算")
            for k in ("order_id","claim_id"):
                identify(r[k],k)
            if name=="orders":
                for k in ("handover_ref","cancel_ref","claim_ref","buyer_ref"):
                    term(r[k],k,True)
                for k in ("handed_at","cancelled_at","approved_at"):
                    if r[k] is not None:
                        stamp(r[k])
                if r["cancelled_by"] not in ("platform","buyer","seller",None):
                    raise WorkflowError("取消主体取证不明。")
                amount(r["buyer_collected_minor"],"买家实收")
            else:
                identify(r["settlement_id"],"平台结算编号")
                stamp(r["posted_at"])
                amount(r["amount_minor"],"结算额")
                term(r["source_ref"],"平台结算明细来源",True)


def period_for(moment,bounds):
    if moment is None:
        return None
    at=stamp(moment)
    return next((k for k in PHASES if bounds[k][0]<=at<bounds[k][1]),None)


def event_state(order,at):
    dates=[order["handed_at"],order["cancelled_at"],order["approved_at"]]
    ready=(all(d is not None and stamp(d)<=at for d in dates)
           and all(order[k] is not None for k in ("handover_ref","cancel_ref","claim_ref")))
    if order["cancelled_by"] not in ("platform",None):
        return "INELIGIBLE_EVENT"
    if not ready or order["cancelled_by"] is None:
        return "PENDING_DOCUMENTS"
    h,c,a=map(stamp,dates)
    return "APPROVAL_RECORDED" if h<c<=a else "EVENT_CHRONOLOGY_CONFLICT"


def compute(p,bounds,at):
    validate(p,p["shop_id"])
    orders=defaultdict(list)
    postings=defaultdict(list)
    for r in p["orders"]:
        key=(r["order_id"],r["claim_id"])
        if r not in orders[key]:
            orders[key].append(r)
    for r in p["settlements"]:
        postings[r["settlement_id"]].append(r)
    months={k:{"buyer_receipts":[],"approved_claim_ids":[],"verified_postings":[],
               "gross_merchandise_value_included":False,"bank_arrival_minor":None,
               "archive_complete_ref":p["completeness"][k],"unresolved_settlement_ids":[]} for k in PHASES}
    order_status=[]
    for (order_id,claim),items in sorted(orders.items()):
        order=items[0]
        state=event_state(order,at) if len(items)==1 else "ORDER_EVIDENCE_CONFLICT"
        order_status.append({"order_id":order_id,"claim_id":claim,"state":state,"evidence":items})
        if state=="ORDER_EVIDENCE_CONFLICT":
            continue
        cancel_period=period_for(order["cancelled_at"],bounds)
        if cancel_period and stamp(order["cancelled_at"])<=at:
            value=order["buyer_collected_minor"] if order["buyer_ref"] is not None else None
            months[cancel_period]["buyer_receipts"].append({"order_id":order_id,"minor":value,"source_ref":order["buyer_ref"]})
        approval_period=period_for(order["approved_at"],bounds)
        if approval_period and state=="APPROVAL_RECORDED":
            months[approval_period]["approved_claim_ids"].append(claim)
    state_by_order={(r["order_id"],r["claim_id"]):r["state"] for r in order_status}
    duplicates=0
    conflicts=[]
    pending=[]
    ignored_after_cutoff=[]
    for key,items in sorted(postings.items()):
        arrived=[r for r in items if stamp(r["posted_at"])<=at]
        ignored_after_cutoff.extend(r for r in items if stamp(r["posted_at"])>at)
        if not arrived:
            continue
        payloads=defaultdict(list)
        for row in arrived:
            identity=tuple(row[k] for k in ("order_id","claim_id","posted_at","amount_minor"))
            payloads[identity].append(row)
        duplicates+=sum(len(rows)-1 for rows in payloads.values())
        if len(payloads)>1:
            conflicts.append({"settlement_id":key,"evidence":arrived,"state":"SETTLEMENT_ID_CONFLICT"})
            for period in {period_for(x["posted_at"],bounds) for x in arrived}-{None}:
                months[period]["unresolved_settlement_ids"].append(key)
            continue
        r=next((entry for entry in arrived if entry["source_ref"] is not None),arrived[0])
        month=period_for(r["posted_at"],bounds)
        order_key=(r["order_id"],r["claim_id"])
        order_state=state_by_order.get(order_key)
        linked=order_state=="APPROVAL_RECORDED"
        ordering_ok=linked and stamp(orders[order_key][0]["approved_at"])<=stamp(r["posted_at"])
        if not linked or not ordering_ok or r["source_ref"] is None or r["amount_minor"] is None:
            pending.append({"settlement_id":key,"evidence":arrived,"order_state":order_state,
                            "reason":"POSTING_PRECEDES_APPROVAL" if linked and not ordering_ok else "POSTING_OR_ORDER_UNVERIFIED"})
            if month:
                months[month]["unresolved_settlement_ids"].append(key)
        elif month:
            months[month]["verified_postings"].append(r)
    for month in months.values():
        by_order=defaultdict(set)
        for r in month["buyer_receipts"]:
            by_order[r["order_id"]].add(r["minor"])
        known_buyer=bool(by_order) and all(len(values)==1 and None not in values for values in by_order.values())
        month["buyer_cash_for_observed_cancelled_orders_minor"]=(
            sum(next(iter(values)) for values in by_order.values()) if known_buyer else None)
        known=sum(r["amount_minor"] for r in month["verified_postings"])
        month["verified_postings_subtotal_minor"]=known
        month["platform_compensation_posted_minor"]=(known if month["archive_complete_ref"] is not None
                                                       and not month["unresolved_settlement_ids"] else None)
    return {"site":"PH","currency":"PHP","scope":SYNTHETIC,"source_ref":p["source_ref"],
            "months":months,"order_lifecycle":order_status,"duplicate_settlement_rows_skipped":duplicates,
            "settlement_conflicts":conflicts,"pending_settlements":pending,"ignored_after_cutoff":ignored_after_cutoff,
            "counts_as_gmv":False,"included_in_actual_bank_remittance":False,
            "status":"PENDING" if conflicts or pending or any(r["state"] not in ("APPROVAL_RECORDED","INELIGIBLE_EVENT") for r in order_status) or any(v["platform_compensation_posted_minor"] is None for v in months.values())
                     else "RECONCILED_OFFLINE"}

