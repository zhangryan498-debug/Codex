"""Evidence-bounded creator cooperation, channel and commission analysis."""

from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, ratio, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp

TOOL="competitor-creator-cohorts"
FILES={"json":"creator-cohorts.json","report":"creator-cohorts.md","receipt":"creator-cohorts-receipt.json"}
CHANNELS=("product_card","shoppable_video","live")
COMPONENTS=("creator_base","creator_ads","platform")
BASES=("settled_goods_minor","advertising_attributed_settled_minor")
TIERS=("tail","middle","head","unknown")
PROGRAMS={"CN":("domestic",None),"PH":("local","cross_border",None)}
ORIGINS={"CN":("domestic",None),"PH":("local","cross_border",None)}
SCHEMAS={
 "creators":"creator_id followers origin observed_at source_ref",
 "cooperations":"cooperation_id creator_id shop_id sku_id seller_program relationship captured_at source_ref",
 "transactions":"order_id cooperation_id channel transaction_at net_settled_minor currency source_ref",
 "costs":"fee_id order_id component basis_kind basis_ref basis_minor cost_minor currency source_ref",
 "rate_bands":"rate_id seller_program tier component basis_kind valid_from valid_until min_bps max_bps source_ref",
}


class WorkflowError(ValueError):
    pass


def ids(obj,keys):
    for k in keys:
        identify(obj[k],k)


def choice(obj,key,allowed):
    if obj[key] not in allowed:
        raise WorkflowError("无效的来源、状态、渠道或费用口径："+key)


def validate(site,p):
    check(p,"site scope currency study_id evaluated_at period sample tiering "+" ".join(SCHEMAS),"达人样本")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("仅接受已选站点、本站原币和明确标注的合成样本。")
    ids(p,("study_id",))
    check(p["period"],"start end","已闭合观察时间窗")
    start,end,cutoff=(stamp(v) for v in (p["period"]["start"],p["period"]["end"],p["evaluated_at"]))
    if not start<end<=cutoff or end-start>timedelta(days=90):
        raise WorkflowError("观察窗须已结束、采用 +08:00 且不长于 90 天。")
    s=p["sample"]
    check(s,"ref expected_settled_orders by_shop_expected_orders","样本范围")
    term(s["ref"],"样本来源",True)
    amount(s["expected_settled_orders"],"同窗应有订单样本数")
    counts=s["by_shop_expected_orders"]
    if not isinstance(counts,dict) or len(counts)>20:
        raise WorkflowError("按同窗店铺声明覆盖分母。")
    for shop,count in counts.items():
        identify(shop,"样本店铺")
        amount(count,"店铺预期订单样本")
    if (s["expected_settled_orders"] is not None and counts and None not in counts.values()
            and sum(counts.values())!=s["expected_settled_orders"]):
        raise WorkflowError("站点与店铺明示样本分母未对账。")
    t=p["tiering"]
    check(t,"middle_from_followers head_from_followers source_ref","合成分层阈值")
    for k in ("middle_from_followers","head_from_followers"):
        amount(t[k],k,1,10**9,False)
    term(t["source_ref"],"分层来源")
    if t["middle_from_followers"]>=t["head_from_followers"]:
        raise WorkflowError("中腰与头部边界必须严格递增。")
    for k,fields in SCHEMAS.items():
        rows=p[k]
        if not isinstance(rows,list) or len(rows)>300:
            raise WorkflowError("各类合成明细上限 300 行。")
        for r in rows:
            check(r,fields,"达人或费用证据")
            term(r["source_ref"],"记录来源",True)
    for r in p["creators"]:
        ids(r,("creator_id",))
        amount(r["followers"],"观察粉丝数")
        choice(r,"origin",ORIGINS[site])
        stamp(r["observed_at"])
    for r in p["cooperations"]:
        ids(r,("cooperation_id","creator_id","shop_id","sku_id"))
        choice(r,"seller_program",PROGRAMS[site])
        choice(r,"relationship",("active","invited","cancelled",None))
        stamp(r["captured_at"])
    for r in p["transactions"]:
        ids(r,("order_id","cooperation_id"))
        choice(r,"currency",(p["currency"],None))
        choice(r,"channel",CHANNELS)
        amount(r["net_settled_minor"],"样本退款后结算额")
        stamp(r["transaction_at"])
    for r in p["costs"]:
        ids(r,("fee_id","order_id"))
        choice(r,"currency",(p["currency"],None))
        choice(r,"component",COMPONENTS)
        choice(r,"basis_kind",BASES)
        term(r["basis_ref"],"费用分母来源",True)
        for k in ("basis_minor","cost_minor"):
            amount(r[k],k)
    for r in p["rate_bands"]:
        ids(r,("rate_id",))
        choice(r,"seller_program",tuple(x for x in PROGRAMS[site] if x is not None))
        choice(r,"tier",TIERS)
        choice(r,"component",COMPONENTS)
        choice(r,"basis_kind",BASES)
        for k in ("min_bps","max_bps"):
            amount(r[k],k,0,10000)
        if stamp(r["valid_from"])>=stamp(r["valid_until"]) or (
                None not in (r["min_bps"],r["max_bps"]) and r["min_bps"]>r["max_bps"]):
            raise WorkflowError("佣金带或有效区间矛盾。")
    return start,end,cutoff


def dedup(rows,fields):
    groups=defaultdict(list)
    for r in rows:
        groups[tuple(r[k] for k in fields)].append(r)
    accepted=[];conflicts=[];repeated=0
    for key,data in groups.items():
        distinct={encode(r) for r in data}
        repeated+=len(data)-len(distinct)
        if len(distinct)==1:
            accepted.append(data[0])
        else:
            conflicts.append({"key":list(key),"evidence":data})
    return accepted,repeated,conflicts


def tier(r,t,cutoff):
    if r is None or r["source_ref"] is None or r["followers"] is None or stamp(r["observed_at"])>cutoff:
        return "unknown"
    f=r["followers"]
    return "head" if f>=t["head_from_followers"] else "middle" if f>=t["middle_from_followers"] else "tail"


def coverage(known,denominator):
    return {"known":known,"expected":denominator,"known_fraction":ratio(known,denominator)}


def grouping(coops,creator_rows,p,start,end,cutoff):
    kept={}
    for r in coops:
        if r["relationship"]=="active" and r["source_ref"] is not None and start<=stamp(r["captured_at"])<end:
            identity=creator_rows.get(r["creator_id"])
            origin=identity["origin"] if identity and identity["source_ref"] and stamp(identity["observed_at"])<=cutoff else None
            kept[r["cooperation_id"]]={**r,"tier":tier(identity,p["tiering"],cutoff),"creator_origin":origin}
    return kept


def scope_rows(data,start,end):
    in_scope=[];excluded=[]
    for row in data:
        (in_scope if start<=stamp(row["transaction_at"])<end else excluded).append(row)
    return in_scope,excluded


def measures(p,orders,partnerships):
    by_channel={k:[] for k in CHANNELS}
    accepted={}
    pending=[]
    for r in orders:
        coop=partnerships.get(r["cooperation_id"])
        if coop is None or r["source_ref"] is None:
            pending.append({**r,"reason":"cooperation_or_transaction_source_unverified"})
            continue
        eligible={**r,"creator_id":coop["creator_id"],"tier":coop["tier"],"creator_origin":coop["creator_origin"],
                  "seller_program":coop["seller_program"]}
        by_channel[r["channel"]].append(eligible)
        accepted[r["order_id"]]=eligible
        if r["currency"] is None or r["net_settled_minor"] is None:
            pending.append({**r,"reason":"currency_or_amount_unknown"})
    good=[r for r in accepted.values() if r["currency"] is not None and r["net_settled_minor"] is not None]
    total=sum(r["net_settled_minor"] for r in good) if good else None
    denominators=coverage(len(good),p["sample"]["expected_settled_orders"] if p["sample"]["ref"] else None)
    matrix={}
    for key,data in by_channel.items():
        accepted_cost=[r for r in data if r["currency"] is not None and r["net_settled_minor"] is not None]
        subtotal=sum(r["net_settled_minor"] for r in accepted_cost) if accepted_cost else None
        matrix[key]={"evidenced_settled_orders":len(data),"known_amount_orders":len(accepted_cost),
                     "known_amount_subtotal_minor":subtotal,"share_of_known_sample_amount":ratio(subtotal,total),
                     "share_of_evidenced_orders":ratio(len(data),len(accepted))}
    leaders=defaultdict(int)
    for r in good:
        leaders[r["creator_id"]]+=r["net_settled_minor"]
    concentration=[{"creator_id":i,"known_amount_minor":v,"share_of_known_sample_amount":ratio(v,total)}
                   for i,v in sorted(leaders.items(),key=lambda x:(-x[1],x[0]))]
    def split(field,labels):
        return {name:{"effective_cooperations":sum(r[field]==value for r in partnerships.values()),
                      "unique_effective_creators":len({r["creator_id"] for r in partnerships.values() if r[field]==value}),
                      "known_amount_minor":sum(r["net_settled_minor"] for r in good if r[field]==value)
                            if any(r[field]==value for r in good) else None}
                for name,value in labels}
    tiers=split("tier",[(k,k) for k in TIERS])
    sites=[(v or "unknown",v) for v in PROGRAMS[p["site"]]]
    return accepted,pending,{"totals":{"known_amount_minor":total,"valid_order_ids":len(accepted),
             "known_amount_coverage":denominators},"channels":matrix,"creator_concentration":concentration,
             "top_1_share_of_known_amount":concentration[0]["share_of_known_sample_amount"] if concentration else None,
             "tiers":tiers,"creator_origin":split("creator_origin",sites),"seller_program":split("seller_program",sites),
             "scope":"observed_synthetic_orders_only"}


def fees(rows,accepted):
    keyed=defaultdict(list)
    pending=[]
    for r in rows:
        tr=accepted.get(r["order_id"])
        if None in (r["source_ref"],r["basis_ref"],r["basis_minor"],r["cost_minor"],r["currency"]) or (
                tr is None or tr["currency"] is None or tr["seller_program"] is None):
            pending.append({**r,"reason":"cost_basis_currency_or_link_unverified"})
            continue
        if r["basis_kind"]=="settled_goods_minor" and r["basis_minor"]!=tr["net_settled_minor"]:
            pending.append({**r,"reason":"basis_not_reconciled_to_financial_order"})
            continue
        keyed[tr["seller_program"],r["component"],r["basis_kind"]].append(r)
    out=[]
    for (program,component,basis),group in sorted(keyed.items()):
        n=sum(r["cost_minor"] for r in group);d=sum(r["basis_minor"] for r in group)
        out.append({"seller_program":program,"component":component,"basis_kind":basis,"known_cost_minor":n,
                    "known_denominator_minor":d,"row_count":len(group),"ratio":ratio(n,d),"evidence":group})
    return out,pending


def compute(site,p):
    start,end,cutoff=validate(site,p)
    roster,dr,cr=dedup(p["creators"],("creator_id",))
    raw_coop,dc,cc=dedup(p["cooperations"],("cooperation_id",))
    valid=grouping(raw_coop,{r["creator_id"]:r for r in roster},p,start,end,cutoff)
    t0,dt,ct=dedup(p["transactions"],("order_id",))
    transactions,excluded=scope_rows(t0,start,end)
    accepted,pending,summary=measures(p,transactions,valid)
    fee_rows,df,cf=dedup(p["costs"],("fee_id",))
    # One contractual fee component and accounting basis per order; avoid overlap across nominal fee IDs.
    groups=defaultdict(list)
    for r in fee_rows:
        groups[r["order_id"],r["component"],r["basis_kind"]].append(r)
    fee_rows=[]
    for k,items in groups.items():
        if len(items)>1:
            cf.append({"key":list(k),"evidence":items})
        else:
            fee_rows.append(items[0])
    costs,pend_fee=fees(fee_rows,accepted)
    rates,db,cb=dedup(p["rate_bands"],("rate_id",))
    rate_groups=defaultdict(list)
    available=[]
    for r in rates:
        ok=stamp(r["valid_from"])<=cutoff<stamp(r["valid_until"]) and None not in (
            r["source_ref"],r["min_bps"],r["max_bps"])
        if ok:
            rate_groups[r["seller_program"],r["tier"],r["component"],r["basis_kind"]].append(r)
        available.append({**r,"state":"SUPPORTED_SYNTHETIC_BAND" if ok else "UNVERIFIED_OR_OUTSIDE_WINDOW",
                          "platform_fee_verified":False})
    contradictory={k for k,items in rate_groups.items() if len({(x["min_bps"],x["max_bps"]) for x in items})>1}
    for r in available:
        if (r["seller_program"],r["tier"],r["component"],r["basis_kind"]) in contradictory and r["state"]=="SUPPORTED_SYNTHETIC_BAND":
            r["state"]="BAND_CONFLICT"
    duplicates={"creator_rows":dr,"cooperation_rows":dc,"transaction_rows":dt,"cost_rows":df,"rate_band_rows":db}
    conflicts={"creators":cr,"cooperations":cc,"transactions":ct,"costs":cf,"rate_bands":cb}
    exp=summary["totals"]["known_amount_coverage"]["expected"]
    known=summary["totals"]["known_amount_coverage"]["known"]
    sample_match=exp is not None and exp==known and bool(known)
    program_gaps=sorted({r["seller_program"] for r in valid.values() if r["seller_program"] is not None}
                        - {r["seller_program"] for r in available if r["state"]=="SUPPORTED_SYNTHETIC_BAND"})
    shop_summaries={}
    for shop in sorted({r["shop_id"] for r in valid.values()} | set(p["sample"]["by_shop_expected_orders"])):
        relations={k:r for k,r in valid.items() if r["shop_id"]==shop}
        shop_orders=[r for r in transactions if r["cooperation_id"] in relations]
        per_shop={**p,"sample":{"ref":p["sample"]["ref"],"expected_settled_orders":p["sample"]["by_shop_expected_orders"].get(shop)}}
        shop_accepted,_,metrics=measures(per_shop,shop_orders,relations)
        shop_cost,_=fees([r for r in fee_rows if r["order_id"] in shop_accepted],shop_accepted)
        shop_summaries[shop]={**metrics,"cost_groups":shop_cost,"effective_cooperations":len(relations)}
    shops_match=bool(shop_summaries) and all(v["totals"]["known_amount_coverage"]["known"]==
                                           v["totals"]["known_amount_coverage"]["expected"] for v in shop_summaries.values())
    ready=(bool(valid) and sample_match and shops_match and not pending and not pend_fee
           and not any(conflicts.values()) and not contradictory and not program_gaps
           and all(r["seller_program"] is not None and r["tier"]!="unknown" for r in valid.values()))
    return {**summary,"state":"READY_OFFLINE" if ready else "PENDING",
            "sample":p["sample"],"shops":shop_summaries,"rate_program_gaps":program_gaps,
            "sampling_gap":None if sample_match and shops_match else "check_sample_denominator_and_receipts",
            "period":p["period"],"evaluated_at":p["evaluated_at"],"tiering":p["tiering"],
            "valid_cooperation_count":len(valid),"effective_creator_count":len({x["creator_id"] for x in valid.values()}),
            "effective_cooperations":list(valid.values()),
            "other_cooperation_records":[r for r in raw_coop if r["cooperation_id"] not in valid],
            "settled_order_sample":list(accepted.values()),"pending_transactions":pending,"out_of_window_transactions":excluded,
            "cost_groups":costs,"pending_costs":pend_fee,"rate_bands":available,"duplicates_skipped":duplicates,"conflicts":conflicts,
            "payer_commission_components_added_as_common_rate":False,"market_full_coverage":False,
            "actual_platform_rates_verified":False,"actual_creator_performance_verified":False,
            "next_action":{"owner":ROUTES[site][0]+"-competitor-analyst","action":"按本站合作关系、渠道及佣金分母复核原取样，留待证实；不联系达人。"}}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","scope","study_id","input_sha256")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress):
    if assets or tool!=TOOL:
        raise WorkflowError("仅接受已标注的本机合成达人证据。")
    r={**compute(site,p),"task_id":folder.name,"site":site,"currency":p["currency"],"scope":SYNTHETIC,"tool_id":tool,
       "study_id":p["study_id"],"input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "provider_called":False,"model_called":False}
    raw=encode(r)
    lines=[f"# {site} 达人群组与合作 · {r['study_id']}","",f"结论：{r['state']}。"
           f"有证合作 {r['valid_cooperation_count']} 份，去重达人 {r['effective_creator_count']} 人。",
           "本地合成样本，各币种金额按最小单位记录。份额按明确的样本分母，费率带须按来源及有效期复核。","",
           "| 渠道 | 有证订单 | 金额已知订单 | 有证子样本金额 |","|---|---|---|---|"]
    for k,v in r["channels"].items():
        lines.append("| "+" | ".join(map(safe_md,[k,v["evidenced_settled_orders"],v["known_amount_orders"],v["known_amount_subtotal_minor"]]))+" |")
    body=("\n".join(lines)+"\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":body,"receipt":encode(receipt(r,raw,body))}.items():
        with paths[k].open("xb") as f:f.write(b)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or
        (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
        json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("达人分析或来源回执无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"competitor-analyst","label":"达人分层、合作和佣金证据",
             "description":"只接受本地合成对照，核查分层、去重、渠道和分母。","input_example":{}}]

