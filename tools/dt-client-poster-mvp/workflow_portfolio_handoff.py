"""Synthetic portfolio production and independent same-site receiving checks."""
from datetime import datetime, timezone
from hashlib import sha256
import json

import workflow_cn_campaign_offer as cn_offer
import workflow_ph_campaign_offer as ph_offer
from role_tools import ROUTES, SYNTHETIC, RoleToolError
from role_tools_extra import _shop, _load
from workflow_live_rehearsal import check, identify, amount, stamp, task_id, posture


class WorkflowError(ValueError):
    """The local portfolio evidence cannot be accepted."""


PRODUCE="selection-portfolio-handoff"
SHOP="shop-portfolio-intake"
CAMPAIGN="campaign-portfolio-intake"
ADS="ads-portfolio-intake"
TOOLS={PRODUCE:"selection",SHOP:"shop-ops",CAMPAIGN:"campaign-ops",ADS:"ad-buyer"}
FILES={"json":"portfolio.json","report":"portfolio.md","receipt":"portfolio-receipt.json"}
SLOTS=("entry","profit","hero_candidate")
HEADER="site currency scope portfolio_id version evaluated_at"


def common(site,tool,p):
    extra=(" previous_task_id rows activation" if tool==PRODUCE else
           " source_task_id intake_id sku_id revision receiver"+(" campaign_intake_task_id" if tool==ADS else ""))
    check(p,HEADER+extra,"货盘")
    if (p["scope"],p["site"],p["currency"])!=(SYNTHETIC,site,ROUTES[site][1]):
        raise WorkflowError("只接受本站本币的合成货盘。")
    identify(p["portfolio_id"],"货盘编号")
    amount(p["version"],"货盘版本",1,10**6,False)
    stamp(p["evaluated_at"])
    if tool==PRODUCE:
        prev=p["previous_task_id"]
        if (p["version"]==1)!=(prev is None):
            raise WorkflowError("后续货盘须连接前一版本。")
        if prev:task_id(prev,"前序货盘")
        return f"portfolio:{site}:selection:{p['portfolio_id']}:{p['version']}"
    task_id(p["source_task_id"],"货盘任务")
    identify(p["intake_id"],"收件编号");identify(p["sku_id"],"SKU")
    amount(p["revision"],"SKU 修订",1,10**6,False)
    if p["receiver"]!=ROUTES[site][0]+"-"+TOOLS[tool]:
        raise WorkflowError("所填接收岗与本工作台收件工具不符。")
    if tool==ADS and p["campaign_intake_task_id"]:task_id(p["campaign_intake_task_id"],"活动收件任务")
    return f"portfolio:{site}:{tool}:{p['portfolio_id']}:{p['version']}:{p['sku_id']}:{p['intake_id']}"


def bind(site,tool,p,load):
    if tool==PRODUCE:
        if not p["previous_task_id"]:return {},{}
        parent,ref=load(p["previous_task_id"],PRODUCE,site,False)
        if (parent["portfolio_id"]!=p["portfolio_id"] or parent["version"]+1!=p["version"]
                or stamp(parent["evaluated_at"])>=stamp(p["evaluated_at"])):
            raise WorkflowError("前序必须是同站同货盘的连续版本与更早时点。")
        return {"previous":parent},{"previous":ref}
    origin,pin=load(p["source_task_id"],PRODUCE,site,True)
    if (origin["portfolio_id"],origin["version"])!=(p["portfolio_id"],p["version"]):
        raise WorkflowError("货盘编号或提交版本不匹配。")
    rows=[r for r in origin["rows"] if (r["sku_id"],r["revision"])==(p["sku_id"],p["revision"])]
    if len(rows)!=1 or stamp(origin["evaluated_at"])>stamp(p["evaluated_at"]):
        raise WorkflowError("货盘内无同 SKU 同修订，或来源晚于收件。")
    selected=rows[0]
    context={"origin":origin,"row":selected}
    refs={"portfolio":pin}
    if tool==ADS and p["campaign_intake_task_id"]:
        result,ref=load(p["campaign_intake_task_id"],CAMPAIGN,site,False)
        if ((result["source_task_id"],result["sku_id"],result["revision"],result["version"])!=
                (p["source_task_id"],p["sku_id"],p["revision"],p["version"])
                or result["upstream"]["portfolio"]!=pin or stamp(result["evaluated_at"])>stamp(p["evaluated_at"])):
            raise WorkflowError("广告收件的活动价证与当前货盘、SKU 或时点无关。")
        context["campaign"]=result
        refs["campaign"]=ref
    return context,refs


def produce(site,p):
    rows=p["rows"]
    if not isinstance(rows,list) or len(rows)!=len(SLOTS):
        raise WorkflowError("须为引流、利润、爆款候选分别提交一条 SKU。")
    actual={}
    for r in rows:
        check(r,"slot sku_id product_id revision rationale evidence_ref image_ids facts_pack campaign_contract ad_brief","货盘 SKU")
        for k in ("sku_id","product_id","evidence_ref"):identify(r[k],k)
        amount(r["revision"],"SKU 修订",1,10**6,False)
        if r["slot"] not in SLOTS or r["slot"] in actual:
            raise WorkflowError("三类货盘定位需逐一对应。")
        if not isinstance(r["rationale"],str) or not 1<=len(r["rationale"].strip())<=160:
            raise WorkflowError("货盘定位需要给出依据。")
        if not isinstance(r["image_ids"],list) or len(r["image_ids"])>12:
            raise WorkflowError("图片编号列表超出约定。")
        for i in r["image_ids"]:identify(i,"图片编号")
        actual[r["slot"]]=r
    if len({r["sku_id"] for r in rows})!=3:
        raise WorkflowError("一个 SKU 不得重复计入多个货盘定位。")
    canonical=[actual[slot] for slot in SLOTS]
    sku_order=[r["sku_id"] for r in canonical]
    plan=p["activation"]
    if site=="CN":
        check(plan,"store_live_order affiliate_live","抖音货盘渠道")
        if plan["store_live_order"]!=sku_order or not isinstance(plan["affiliate_live"],list) or len(plan["affiliate_live"])!=3:
            raise WorkflowError("须分别给店播顺序和三款达播演示安排。")
        for r,id in zip(plan["affiliate_live"],sku_order):
            check(r,"sku_id demo_ref rights_ref","达播资料")
            if r["sku_id"]!=id:raise WorkflowError("达播演示与货盘 SKU 不对应。")
            for f in ("demo_ref","rights_ref"):identify(r[f],f)
    else:
        check(plan,"timezone launch_steps","菲律宾上新")
        if plan["timezone"]!="Asia/Manila" or not isinstance(plan["launch_steps"],list) or len(plan["launch_steps"])!=3:
            raise WorkflowError("菲律宾须分别填写 Manila 预览、试卖及复核三个时点。")
        previous=stamp(p["evaluated_at"])
        for step,stage in zip(plan["launch_steps"],("preview","small_trial","review")):
            check(step,"stage at owner_ref sku_ids","上新节点")
            identify(step["owner_ref"],"节点责任人")
            current=stamp(step["at"])
            if step["stage"]!=stage or step["sku_ids"]!=sku_order or current<=previous:
                raise WorkflowError("上新试点时点与 SKU 顺序不符。")
            previous=current
    return {"state":"READY_OFFLINE","rows":canonical,"activation":plan,
            "hero_market_success_verified":False,"receivers":[ROUTES[site][0]+"-"+TOOLS[k] for k in (SHOP,CAMPAIGN,ADS)]}


def utc_iso(value):
    if not isinstance(value,str) or "T" not in value:
        raise ValueError("事实时间需要带明确时区。")
    dt=datetime.fromisoformat(value.replace("Z","+00:00") if value.endswith("Z") else value)
    if dt.utcoffset() is None:
        raise ValueError("事实时间缺少明确时区。")
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")


def shop_fact_times(facts):
    from copy import deepcopy
    updated=deepcopy(facts)
    for source in [updated,*updated["sources"]]:
        for key in ("as_of","expires_at"):
            if source[key] is not None:source[key]=utc_iso(source[key])
    for row in updated["facts"]:
        if row["kind"]!="coupon":continue
        candidates=([row["value"]] if row["status"]=="known" else [a["value"] for a in row["alternatives"]])
        for term in candidates:
            for k in ("valid_from","valid_until"):
                if term[k] is not None:term[k]=utc_iso(term[k])
    return updated


def receive_shop(project,site,p,r):
    facts=r["facts_pack"]
    if facts is None:
        return {"state":"PENDING","reason":"facts_pack_missing","shop_validated":None}
    if not isinstance(facts,dict) or facts.get("product_id")!=r["product_id"]:
        return {"state":"BLOCK","reason":"facts_product_mismatch","shop_validated":False}
    declared={"sku_id":r["sku_id"],"version":r["revision"],"image_ids":r["image_ids"]}
    try:
        validated=_shop(project,site,{"site":site,"scope":SYNTHETIC,"expect":declared,
                                      "evaluation_time":utc_iso(p["evaluated_at"]),"facts_pack":shop_fact_times(facts)})
    except (RoleToolError,ValueError,KeyError,TypeError) as exc:
        return {"state":"BLOCK","reason":str(exc),"shop_validated":False}
    counts=validated["fact_status_counts"]
    return {"state":"PENDING" if counts["unknown"] or counts["conflicting"] or validated["warnings"] else "READY_OFFLINE",
            "shop_validated":True,"shop_facts":validated,"received_image_ids":r["image_ids"]}


def receive_campaign(project,site,p,r):
    raw=r["campaign_contract"]
    if raw is None:return {"state":"PENDING","reason":"campaign_contract_missing"}
    source=raw.get("source") if isinstance(raw,dict) else None
    if not isinstance(source,dict) or (source.get("sku"),source.get("revision"))!=(r["sku_id"],r["revision"]):
        return {"state":"BLOCK","reason":"campaign_product_or_revision_mismatch"}
    try:answer=(cn_offer._approve if site=="CN" else ph_offer._read_review)(raw,project,p["evaluated_at"])
    except (ValueError,KeyError,TypeError) as exc:
        return {"state":"BLOCK","reason":str(exc)}
    result={"PASS":"READY_OFFLINE","PENDING":"PENDING","REJECT":"BLOCK"}[answer["disposition"]]
    if site=="CN" and answer["scenario"]!="current":result="BLOCK"
    return {"state":result,"campaign_price_disposition":answer["disposition"],"campaign_id":answer["campaign_id"],
            "price_evidence_version":answer["price_evidence_version"],"price_source_sha256":answer["price_source_sha256"],
            "host_calculation":answer["host_calculation"],"blocking_reasons":answer["errors"],"pending_reasons":answer["unknowns"],
            "formal_price_acceptance":False}


def receive_ads(project,site,p,r,ctx):
    brief=r["ad_brief"]
    if brief is None:return {"state":"PENDING","reason":"ad_brief_missing","suggested_ad_action":"freeze"}
    check(brief,"asset_id asset_version rights rights_source stock_units stock_stop_units stock_valid_until budget_cap_minor budget_source","广告资料")
    identify(brief["asset_id"],"广告素材")
    amount(brief["asset_version"],"素材版本",1,10**6,False)
    if brief["rights"] not in ("offline_test_only","denied",None):
        raise WorkflowError("素材权利仅登记离线范围、拒绝或未知。")
    for k in ("rights_source","budget_source"):
        if brief[k] is not None:identify(brief[k],k)
    for k in ("stock_units","stock_stop_units","budget_cap_minor"):amount(brief[k],k)
    expiry=stamp(brief["stock_valid_until"]) if brief["stock_valid_until"] else None
    units=brief["stock_units"] if expiry is not None and stamp(p["evaluated_at"])<expiry else None
    campaign=ctx.get("campaign")
    # Recalculate the linked campaign at the later ad intake time, never extend a prior PASS.
    now=receive_campaign(project,site,p,r) if campaign else None
    price_ok=bool(campaign and campaign["state"]=="READY_OFFLINE" and now["state"]=="READY_OFFLINE")
    hard=brief["rights"]=="denied" or bool(campaign and now["state"]=="BLOCK")
    pending=[key for key in ("stock_stop_units","rights","rights_source","budget_cap_minor","budget_source") if brief[key] is None]
    if units is None:pending.append("current_stock_units")
    if not price_ok:pending.append("current_campaign_price")
    reviewed=_load(project,"ad_decision_contract").review(
        {"requested_action":"hold","data_state":"complete" if not pending else "partial",
         "hard_risk":hard,"stock":units,"stop_stock":brief["stock_stop_units"]})
    return {"state":"BLOCK" if reviewed["action"]=="pause" else "PENDING" if pending else "READY_OFFLINE",
            "suggested_ad_action":reviewed["action"],"ad_review":reviewed,"ad_brief":brief,
            "current_stock_units":units,"current_synthetic_price_reviewed":price_ok,"pending_reasons":pending,
            "plan_created":False,"real_spend_authorized":False}


def encode(x):
    return (json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def receipt(a,raw,report):
    return {**{k:a[k] for k in ("task_id","tool_id","site","version","scope","portfolio_id","upstream","input_sha256")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "business_execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked=None):
    if linked is None or assets:raise WorkflowError("货盘缺本机来源绑定。")
    ctx,pins=linked
    if progress:progress("正在按岗位核对合成货盘和来源。")
    base={k:p[k] for k in HEADER.split()}
    if tool==PRODUCE:body=produce(site,p)
    else:
        r=ctx["row"]
        body=(receive_ads(project,site,p,r,ctx) if tool==ADS else
              (receive_shop if tool==SHOP else receive_campaign)(project,site,p,r))
        body.update({k:p[k] for k in ("source_task_id","sku_id","revision","intake_id","receiver")},
                    product_id=r["product_id"],slot=r["slot"],evidence_ref=r["evidence_ref"])
    a={**base,**body,"task_id":folder.name,"tool_id":tool,"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(a)
    report=(f"# {site} · {p['portfolio_id']} v{p['version']} · {TOOLS[tool]}\n\n"
            f"本机合成收件状态 {a['state']}。没有商业审批或执行授权。\n\n"+raw.decode()).encode()
    files={k:folder/v for k,v in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(receipt(a,raw,report))}.items():
        with files[k].open("xb") as f:f.write(b)
    return a,files


def verify_saved(task,blob):
    a=task["result"]
    if (task["local_files"]!=FILES or a!=json.loads(blob["json"]) or
            (a["task_id"],a["site"],a["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
            json.loads(blob["receipt"])!=receipt(a,blob["json"],blob["report"])):
        raise WorkflowError("货盘结果和回执无法一一对应。")


def public_tools():
    labels={PRODUCE:"选品三类货盘离线交接",SHOP:"商城接收本站货盘",CAMPAIGN:"活动接收本站货盘并重算",ADS:"广告接收本站货盘及准入复核"}
    return [{"id":tool,"label":labels[tool],"role_suffix":role,"sites":list(ROUTES),
             "description":"限明确标记的本站合成货盘，逐 SKU 复核并保存回执。",
             "input_example":{}} for tool,role in TOOLS.items()]
