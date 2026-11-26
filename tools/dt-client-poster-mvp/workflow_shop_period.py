"""Offline store reviews derived from exact local funnel and captured synthetic ledger scopes."""
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_channel_alignment import SHOP as FUNNEL
from workflow_lead_weekly import encode, ratio, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL="shop-offline-period-review"
TOOLS=(TOOL,)
FILES={"json":"shop-period.json","report":"shop-period.md","receipt":"shop-period-receipt.json"}
FIELDS=("settled_gross_minor","refund_minor","platform_fee_minor","goods_cost_minor","commission_minor","ad_spend_minor")
IDENTITY=("shop_id","account_id","product_id","sku_id","revision")
WINDOW=("starts_at","ends_at")
FLAGS=("execution_authorized","model_called","provider_called")


class WorkflowError(ValueError):
    pass


def request_key(p):
    return f"{p['review_id']}:{p['version']}"


def observe(p):
    check(p,"site scope currency job_id review_id version period starts_at ends_at as_of owner groups prior_task_id revision_reason","商城周期复盘")
    site=p["site"]
    if site not in ROUTES or (p["scope"],p["currency"])!=(SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("仅处理本站原币及明确标注的离线合成快照。")
    for k in ("job_id","review_id"):
        identify(p[k],k)
    term(p["owner"],"商城复盘责任人")
    amount(p["version"],"复盘版本",1,10**6,False)
    if p["version"]==1:
        if p["prior_task_id"] is not None or p["revision_reason"] is not None:
            raise WorkflowError("首版不应带上一轮回执。")
    else:
        task_id(p["prior_task_id"],"同号上一版")
        term(p["revision_reason"],"修订理由")
    start,end,at=(stamp(p[k]) for k in (*WINDOW,"as_of"))
    if not(start<end<=at) or any(v.hour or v.minute or v.second or v.microsecond for v in (start,end)):
        raise WorkflowError("须为本站完整自然日的已结束窗口。")
    if p["period"]=="week" and end-start==timedelta(days=7):
        return start,end,at
    if (p["period"]=="month" and start.day==end.day==1 and start.year*12+start.month+1==end.year*12+end.month):
        return start,end,at
    raise WorkflowError("周期仅支持七自然日和自然月。")


def _latest_part(snapshot,p,group,end,at):
    if snapshot is None:
        return None
    check(snapshot,"site scope currency shop_id account_id product_id sku_id revision starts_at ends_at capture_at expires_at source_ref values","独立合成经营证据")
    dims=("site","scope","currency",*IDENTITY,*WINDOW)
    expected={**p,**group}
    if any(snapshot[k]!=expected[k] for k in dims):
        raise WorkflowError("合成原证据的站点、账号、店铺、SKU、版本或窗口不同。")
    capture,expires=(stamp(snapshot[k]) for k in ("capture_at","expires_at"))
    if capture>=expires:
        raise WorkflowError("原证据采集和失效时间倒序。")
    term(snapshot["source_ref"],"合成原件出处")
    return snapshot["values"] if end<=capture<=at<expires else None


def _action(task,p,at):
    if task is None:
        return None
    check(task,"id owner field due action","改进任务")
    identify(task["id"],"改进任务号")
    term(task["owner"],"改进负责人")
    term(task["action"],"改进与后续读数")
    if task["field"] not in ("traffic","cost","health","returns") or stamp(task["due"])<=at:
        raise WorkflowError("本机改进任务需绑定本期字段和未来核验截止时间。")
    if (p["site"]=="CN" and task["field"] not in ("traffic","cost")) or (p["site"]=="PH" and task["field"]=="cost"):
        raise WorkflowError("改进字段必须属于该站点的复盘表。")
    return {**task,"state":"PLANNED_OFFLINE","actual_assignment_or_execution":False}


def bind(site,p,fetch,history):
    start,end,at=observe(p)
    if p["site"]!=site:
        raise WorkflowError("提交站点与周期合成数据不符。")
    if not isinstance(p["groups"],list) or not 1<=len(p["groups"])<=24:
        raise WorkflowError("必须提供 1 至 24 个独立店铺商品条目。")
    if p["version"]<max((r["version"] for r in history(site,p["review_id"],TOOL)),default=0):
        raise WorkflowError("已有同号新版，旧版仅可查看。")
    pins={}
    previous=None
    if p["prior_task_id"] is not None:
        previous,pins["previous"]=fetch(p["prior_task_id"],site,TOOL)
        if ((previous["review_id"],previous["version"]+1,previous["period"],previous["starts_at"],previous["ends_at"])!=
            (p["review_id"],p["version"],p["period"],*map(p.__getitem__,WINDOW))
            or not stamp(previous["as_of"])<at):
            raise WorkflowError("新版本须保留同站、同报表和同周期。")
    scopes=set()
    shop_accounts={}
    entries=[]
    for group in p["groups"]:
        check(group," ".join(IDENTITY)+" funnel_task_id snapshot action","商城独立范围")
        for k in IDENTITY[:-1]:
            identify(group[k],k)
        amount(group["revision"],"商品快照版本",1,10**6,False)
        key=(group["shop_id"],group["account_id"],group["sku_id"])
        if key in scopes or shop_accounts.setdefault(group["shop_id"],group["account_id"])!=group["account_id"]:
            raise WorkflowError("重复店铺与 SKU，或同一店铺使用不同账号，先核对应关系。")
        scopes.add(key)
        funnel=None
        ident=group["funnel_task_id"]
        if ident:
            task_id(ident,"原商城漏斗回执")
            funnel,pins["funnel:"+":".join(key)]=fetch(ident,site,FUNNEL)
            x=funnel["target"]
            if ((x["shop_id"],x["product_id"],x["sku_id"],x["currency"],x.get("account_id"),x.get("listing_revision"))!=
                (group["shop_id"],group["product_id"],group["sku_id"],p["currency"],group["account_id"],group["revision"])
                or (stamp(x["start"]),stamp(x["end"]))!=(start,end)
                or not end<=stamp(funnel["cutoff"])<=at):
                raise WorkflowError("本地漏斗必须为同店、同 SKU、同币和同统计窗口。")
        raw=_latest_part(group["snapshot"],p,group,end,at)
        computed=_cn(raw) if site=="CN" else _ph(raw,funnel,end,at,group["snapshot"])
        entries.append((group,funnel,computed,_action(group["action"],p,at)))
    if previous:
        prior_set={tuple(r[k] for k in IDENTITY) for r in previous["scopes"]}
        if prior_set!={tuple(x[k] for k in IDENTITY) for x in p["groups"]}:
            raise WorkflowError("同号修订保留原店铺、账号、商品及快照身份。")
    return pins,(entries,previous)


def _cn(raw):
    if raw is None:
        return {"state":"PENDING_EVIDENCE","values":{k:None for k in FIELDS},"settled_net_minor":None,"contribution_minor":None}
    check(raw,"coverage "+" ".join(FIELDS),"结算与成本分项")
    if raw["coverage"] not in ("complete","partial"):
        raise WorkflowError("模拟结算覆盖须明确。")
    values={k:raw[k] for k in FIELDS}
    for k,v in values.items():
        amount(v,k,0,10**12)
    gross,refund,*costs=values.values()
    net=gross-refund if raw["coverage"]=="complete" and None not in (gross,refund) else None
    contribution=net-sum(costs) if net is not None and None not in costs else None
    return {"state":"RECONCILED_FIXTURE" if contribution is not None else "PENDING_EVIDENCE","values":values,
            "settled_net_minor":net,"contribution_minor":contribution,"derived_from_buyer_gmv":False,"scope":"settled_source_components"}


def _ph(raw,funnel,period_end,at,snapshot):
    pending={"state":"PENDING_EVIDENCE","health":None,"refunds":None,"live_affiliate_visibility":None}
    if raw is None:
        return pending
    check(raw,"health returns","菲律宾承接健康与售后")
    health,returns=(raw[k] for k in ("health","returns"))
    if health is not None:
        check(health,"shop_health_score listing_rating_x100 sold_count certification_state","店铺健康和商品观察")
        for k,limit in (("shop_health_score",100),("listing_rating_x100",500),("sold_count",10**9)):
            amount(health[k],k,0,limit)
        if health["certification_state"] not in ("recorded_fixture","not_required_fixture",None):
            raise WorkflowError("认证只记录模拟状态或未知。")
    ready=False
    refund=None
    if returns is not None:
        check(returns,"eligible_paid_orders refunded_orders cod_in_transit mature_at followup_cutoff","退款终态与 COD 在途")
        counts=[returns[k] for k in ("eligible_paid_orders","refunded_orders","cod_in_transit")]
        for number in counts:
            amount(number,"售后单数",0,10**9)
        eligible,refunded,transit=counts
        matured,cutoff=(stamp(returns[k]) for k in ("mature_at","followup_cutoff"))
        paid_row=funnel["reconciled"]["paid_orders"] if funnel else {}
        paid=paid_row.get("value") if paid_row.get("status")=="matched" else None
        if not period_end<=cutoff<=stamp(snapshot["capture_at"])<=at or matured<period_end:
            raise WorkflowError("退款窗须在周期结束之后，且不晚于原快照和报告的截点。")
        control=funnel["reconciled"]["refund_orders"] if funnel else {}
        comparable=bool(funnel and cutoff==stamp(funnel["cutoff"]) and eligible is not None and eligible==paid)
        control_status="DIFFERENT_CUTOFF_OR_SUBSET"
        if comparable:
            control_status="CONTROL_MISSING" if control.get("status")!="matched" else (
                "MATCHED" if control["value"]==refunded else "CONFLICT")
        if None in counts or paid is None:
            status="PENDING_EVIDENCE"
        elif refunded>eligible or eligible>paid or transit>paid or control_status=="CONFLICT":
            status="SOURCE_CONFLICT"
        elif cutoff<matured or transit:
            status="PENDING_MATURITY"
        elif eligible==0:
            status="ZERO_DENOMINATOR"
        elif control_status=="CONTROL_MISSING":
            status="PENDING_EVIDENCE"
        else:
            status="OBSERVED_MATURE_FIXTURE"
            ready=True
        refund={**returns,"state":status,"rate":ratio(refunded,eligible) if ready else None,
                "same_scope_funnel_refund_control":control_status,"causal_attribution":None}
    return {**pending,"state":"RECONCILED_FIXTURE" if ready and health and None not in health.values() else "PENDING_EVIDENCE",
            "health":health,"refunds":refund,"actual_account_health_verified":False}


def assemble(site,p,context):
    packs,previous=context
    traffic,costs,health,actions,scopes=[],[],[],[],[]
    for group,funnel,derived,action in packs:
        who={k:group[k] for k in IDENTITY}
        scopes.append(who)
        matched=bool(funnel and funnel["state"]=="ALIGNED_FIXTURE")
        traffic.append({**who,"state":"ALIGNED_FIXTURE" if matched else "PENDING_EVIDENCE",
                        "source_task_id":group["funnel_task_id"],"source_cutoff":funnel["cutoff"] if funnel else None,
                        "metrics":funnel["reconciled"] if funnel else None,"views":funnel["views"] if funnel else None,
                        "multiple_views_summed":False})
        observation={**who,**derived,"source_snapshot":group["snapshot"]}
        (costs if site=="CN" else health).append(observation)
        actions.append({**who,**(action or {"state":"PENDING_ACTION","owner":p["owner"]}),
                         "source_task_id":group["funnel_task_id"],
                         "snapshot_ref":group["snapshot"]["source_ref"] if group["snapshot"] else None})
    issues=any(t["state"]!="ALIGNED_FIXTURE" or d["state"]!="RECONCILED_FIXTURE" or a["state"]!="PLANNED_OFFLINE"
               for t,d,a in zip(traffic,costs if site=="CN" else health,actions))
    return {"state":"PENDING_EVIDENCE" if issues else "REVIEW_RECORDED","timezone":"Asia/Shanghai" if site=="CN" else "Asia/Manila",
            "scopes":scopes,"traffic_table":traffic,"settlement_cost_table":costs,"health_returns_table":health,
            "improvement_actions":actions,"cross_store_totals":None,"actual_business_verified":False,
            "unverified_affiliate_visibility":None,"prior_snapshot_task_id":previous["task_id"] if previous else None,
            "version_chain_preserved":True}


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","tool_id","site","review_id","version","upstream","input_sha256","state")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("复盘只接收本机已核回执与结构化合成快照。")
    pins,context=linked
    r={**assemble(site,p,context),**{k:p[k] for k in ("site","scope","currency","job_id","review_id","version","period",
          "starts_at","ends_at","as_of","revision_reason")},"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),**{k:False for k in FLAGS}}
    raw=encode(r)
    def table(title,columns,rows):
        short=lambda v:safe_md(str(v) if v is not None else "待核")
        return [f"## {title}","","| "+" | ".join(columns)+" |","|"+"|".join("---" for k in columns)+"|",
                *["| "+" | ".join(short(x) for x in line)+" |" for line in rows],""]
    lines=[f"# {site} · {r['review_id']} v{r['version']} · {r['state']}","",
           f"{r['starts_at']} 至 {r['ends_at']}，取数截点 {r['as_of']}。以下为本机合成复盘。",""]
    lines+=table("分店流量",("店铺","账号","SKU","状态","来源任务","可追时点","曝光","支付单"),
        [(x["shop_id"],x["account_id"],x["sku_id"],x["state"],x["source_task_id"],x["source_cutoff"],
          *((x["metrics"] or {}).get(k,{}).get("value") for k in ("impressions","paid_orders"))) for x in r["traffic_table"]])
    if site=="CN":
        lines+=table("独立结算与成本（分）",("店铺","账号","净结算","贡献","成本明细","来源","观察时点"),
            [(x["shop_id"],x["account_id"],x["settled_net_minor"],x["contribution_minor"],x["values"],
             (x["source_snapshot"] or {}).get("source_ref"),(x["source_snapshot"] or {}).get("capture_at")) for x in r["settlement_cost_table"]])
    else:
        lines+=table("菲律宾健康和售后",("店铺","SKU","观察值","退款状态","退款率及分母","COD 在途","来源","观察时点"),
            [(x["shop_id"],x["sku_id"],x["health"],(x["refunds"] or {}).get("state"),
             (x["refunds"] or {}).get("rate"),(x["refunds"] or {}).get("cod_in_transit"),
             (x["source_snapshot"] or {}).get("source_ref"),(x["source_snapshot"] or {}).get("capture_at")) for x in r["health_returns_table"]])
    lines+=table("下一步改进",("店铺","负责人","状态","期限","本机动作"),
        [(x["shop_id"],x["owner"],x["state"],x.get("due"),x.get("action")) for x in r["improvement_actions"]])
    report=("\n".join(lines)+"\n").encode("utf8")
    paths={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(v)
    return r,paths


def verify_saved(task, files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("商城周期产物或收件回执无法核验。")


def public_tools():
    return [{"id":TOOL,"role_suffix":"shop-ops","sites":list(ROUTES),"label":"分店商城经营复盘",
             "description":"本机合成统计。抖音分列流量及成本，菲律宾保留五源、健康和成熟退款。","input_example":{}}]

