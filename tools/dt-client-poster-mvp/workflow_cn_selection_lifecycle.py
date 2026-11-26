"""CN synthetic, revision-bound maintenance of own SKU planning and launch/exit decisions."""
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode,safe_md,term
from workflow_live_rehearsal import amount,check,identify,stamp,task_id
from workflow_offline_planning import SEL as CATEGORY

TOOL="cn-selection-offline-lifecycle"
TOOLS=(TOOL,)
FILES={"json":"selection-lifecycle.json","report":"selection-lifecycle.md","receipt":"selection-lifecycle-receipt.json"}
COSTS=("goods","packing","shipping","platform_fee","returns_reserve")
ACTIONS={"launch":"WAVE_PROPOSAL_ONLY","observe":"CONTINUE_OFFLINE_REVIEW","reprice":"PRICE_CHANGE_REVIEW_ONLY",
         "exit":"EXIT_REVIEW_ONLY"}


class WorkflowError(ValueError):
    pass


def request_key(p):
    return f"{p['case_id']}:{p['version']}"


def _price(item,offer,at):
    if offer is None:
        return {"state":"PENDING_EVIDENCE","buyer_paid_minor":None,"seller_revenue_minor":None,
                "floor_minor":None,"order_cost_minor":None,"contribution_minor":None,"reasons":["no_price_snapshot"],
                "source":None,"evidence_current":False}
    check(offer,"site currency sku_id revision observed_at valid_until evidence_ref buyer_paid_minor subsidy_minor floor_minor costs","独立 SKU 报价")
    if (offer["site"],offer["currency"],offer["sku_id"],offer["revision"])!=("CN","CNY",item["sku_id"],item["revision"]):
        raise WorkflowError("报价不得跨站、币种、SKU 或版本。")
    seen,until=(stamp(offer[k]) for k in ("observed_at","valid_until"))
    if seen>=until:
        raise WorkflowError("报价来源时间先后有误。")
    term(offer["evidence_ref"],"报价及底价出处")
    current=seen<=at<until
    for k in ("buyer_paid_minor","subsidy_minor","floor_minor"):
        amount(offer[k],k,0,10**10)
    check(offer["costs"]," ".join(COSTS),"逐项模拟成本")
    for k,v in offer["costs"].items():
        amount(v,k,0,10**10)
    buyer,subsidy,floor=(offer[k] if current else None for k in ("buyer_paid_minor","subsidy_minor","floor_minor"))
    costs=offer["costs"] if current else dict.fromkeys(COSTS)
    revenue=buyer+subsidy if None not in (buyer,subsidy) else None
    cost_total=sum(costs.values()) if None not in costs.values() else None
    lower=sum(v for v in costs.values() if v is not None)
    profit=revenue-cost_total if None not in (revenue,cost_total) else None
    blockers=[]
    if buyer is not None and floor is not None and buyer<floor:blockers.append("below_fixture_floor")
    if revenue is not None and revenue<lower:blockers.append("known_costs_exceed_revenue")
    unknown=not current or None in (buyer,floor,revenue,profit)
    return {"state":"BLOCKED" if blockers else "PENDING_EVIDENCE" if unknown else "PRICE_RECORDED",
            "buyer_paid_minor":buyer,"seller_revenue_minor":revenue,"floor_minor":floor,"order_cost_minor":cost_total,
            "contribution_minor":profit,"reasons":blockers or (["unknown_or_stale_amount"] if unknown else []),
            "source":{k:offer[k] for k in ("evidence_ref","observed_at","valid_until","sku_id","revision")},
            "evidence_current":current,"known_cost_lower_bound_minor":lower if current and any(v is not None for v in costs.values()) else None}


def _demand(item,p,at):
    rows=item["windows"]
    if not isinstance(rows,list) or len(rows)>2:
        raise WorkflowError("仅支持两个同口径的已关闭观察窗。")
    last=None
    seen=set()
    summary=[]
    rules=p["fixture_thresholds"]
    for r in rows:
        check(r,"id sku_id revision start end capture_at source_ref opening added sold basis","同 SKU 销动观察")
        if (r["sku_id"],r["revision"])!=(item["sku_id"],item["revision"]) or r["basis"]!="FIXTURE-SOLD-FROM-AVAILABLE":
            raise WorkflowError("观察必须使用同一 SKU 版本和固定库存分母。")
        identify(r["id"],"窗口编号")
        start,end,capture=(stamp(r[k]) for k in ("start","end","capture_at"))
        if r["id"] in seen or end-start!=timedelta(days=7) or last and start<last or not end<=capture<=at:
            raise WorkflowError("观察窗须同长度、已关闭、按时序且不重叠。")
        seen.add(r["id"]);last=end
        term(r["source_ref"],"观察出处")
        opening,added,sold=(r[k] for k in ("opening","added","sold"))
        for n in (opening,added,sold):
            amount(n,"已知件数",0,10**9)
        stock=opening+added if None not in (opening,added) else None
        eligible=stock is not None and sold is not None and stock>=rules["min_available"] and sold<=stock
        state=("UNKNOWN_OR_INSUFFICIENT" if not eligible else
               "FAST" if sold*10000>=stock*rules["fast_at_least_bp"] else
               "SLOW" if sold*10000<=stock*rules["slow_at_most_bp"] else "MID")
        summary.append({"window_id":r["id"],"available":stock,"sold":sold,
                        "sellthrough":round(sold/stock,6) if eligible else None,"state":state,
                        "source":{k:r[k] for k in ("source_ref","start","end","capture_at","basis","sku_id","revision")}})
    unique={r["state"] for r in summary}
    complete=len(summary)==2 and "UNKNOWN_OR_INSUFFICIENT" not in unique
    state=(next(iter(unique)) if len(unique)==1 else "MIXED") if complete else "PENDING_EVIDENCE"
    return {"state":state,"observations":summary,"combined_opening_stock_used":False,
            "evidence_status":"TWO_ALIGNED_FIXTURE_WINDOWS" if complete else "PENDING_EVIDENCE",
            "last_observation_end":summary[-1]["source"]["end"] if summary else None,
            "actual_store_activity_verified":False}


def _variant(item,p,at):
    check(item,"sku_id revision spec_label units catalog_source offer wave windows requested_action","本机 SKU 和生命周期")
    identify(item["sku_id"],"内部合成 SKU");amount(item["revision"],"规格版本",1,10**6,False)
    term(item["spec_label"],"规格标签");amount(item["units"],"规格件数",1,10000,False)
    term(item["catalog_source"],"己方合成商品卡出处")
    if item["requested_action"] not in ACTIONS:
        raise WorkflowError("动作限上新、观察、改价或退出复核。")
    wave=item["wave"]
    if wave is not None:
        check(wave,"wave_id ordinal opens_at review_at source_ref","波段及复核安排")
        identify(wave["wave_id"],"波次编号");amount(wave["ordinal"],"上新波次",1,24,False)
        term(wave["source_ref"],"合成排期来源")
        if stamp(wave["opens_at"])>=stamp(wave["review_at"]):
            raise WorkflowError("波段复核必须晚于预定开始。")
    price,demand=_price(item,item["offer"],at),_demand(item,p,at)
    action=item["requested_action"]
    price_source=price["source"]
    covers_wave=bool(wave and price_source and stamp(price_source["observed_at"])<=stamp(wave["opens_at"])<
                     stamp(price_source["valid_until"]))
    reasons=[]
    if action=="launch" and not covers_wave:
        reasons.append("price_evidence_does_not_cover_planned_wave")
    if action=="exit" and demand["state"]!="SLOW":
        reasons.append("slow_lifecycle_not_supported")
    if price["state"]=="BLOCKED":
        decision="BLOCKED"
    elif price["state"]!="PRICE_RECORDED" or reasons:
        decision="PENDING_EVIDENCE"
    else:
        decision=ACTIONS[action]
    timing="WAVE_NOT_SUPPLIED" if wave is None else (
        "SCHEDULED_NOT_EXECUTED" if at<stamp(wave["opens_at"]) else "PLANNED_TIME_REACHED_ACTUAL_UNKNOWN")
    return {"sku_id":item["sku_id"],"revision":item["revision"],"spec_label":item["spec_label"],"units":item["units"],
            "price":price,"demand":demand,"wave":wave,"wave_state":timing,"requested_action":action,"decision":decision,
            "price_evidence_covers_wave":covers_wave,"action_reasons":reasons,
            "actual_first_sale_at":None,"actual_price_or_listing_changed":False,"actual_exit":False,
            "catalog_source":item["catalog_source"]}


def bind(site,p,fetch,history):
    check(p,"site scope currency job_id case_id version own_product_id as_of owner candidate_task_id category_id variants fixture_thresholds previous_task_id change_reason","抖音选品维护")
    if (site,p["site"],p["currency"],p["scope"])!=("CN","CN","CNY",SYNTHETIC):
        raise WorkflowError("只使用抖音本站、本币及明确合成的商品变更。")
    for k in ("job_id","case_id","own_product_id","category_id"):identify(p[k],k)
    amount(p["version"],"货品维护版本",1,10**6,False);term(p["owner"],"维护责任人")
    at=stamp(p["as_of"])
    r=p["fixture_thresholds"]
    check(r,"min_available fast_at_least_bp slow_at_most_bp source_ref","合成畅滞销阈值")
    for k in ("min_available","fast_at_least_bp","slow_at_most_bp"):amount(r[k],k,1,10000,False)
    if r["slow_at_most_bp"]>=r["fast_at_least_bp"]:
        raise WorkflowError("畅销和滞销阈值必须可明确区分。")
    term(r["source_ref"],"合成规则来源")
    if not isinstance(p["variants"],list) or not 1<=len(p["variants"])<=24:
        raise WorkflowError("须有 1 至 24 项合成规格。")
    rows=[_variant(v,p,at) for v in p["variants"]]
    if len({r["sku_id"] for r in rows})!=len(rows):
        raise WorkflowError("同一商品版本中 SKU 不得重复。")
    waves=sorted((v["wave"] for v in rows if v["wave"]),key=lambda w:w["ordinal"])
    for a,b in zip(waves,waves[1:]):
        if a["ordinal"]==b["ordinal"] and a!=b or a["ordinal"]!=b["ordinal"] and stamp(a["opens_at"])>=stamp(b["opens_at"]):
            raise WorkflowError("同波次定义须一致，后续波次不得抢占先前时间。")
    task_id(p["candidate_task_id"],"本机类目研究来源")
    market,pin=fetch(p["candidate_task_id"],site,CATEGORY)
    if market["category_id"]!=p["category_id"] or stamp(market["as_of"])>at:
        raise WorkflowError("类目研究须同类和不晚于复盘；不能从竞品推己方规格。")
    latest=max((v["version"] for v in history(site,p["case_id"],TOOL)),default=0)
    if p["version"]<latest or p["version"]>latest+1:
        raise WorkflowError("旧版本只可回读，维护须连续修订。")
    pins={"category_research":pin}
    previous=None
    changes=[]
    if p["version"]==1:
        if p["previous_task_id"] is not None or p["change_reason"] is not None:
            raise WorkflowError("首版无变更来源。")
    else:
        task_id(p["previous_task_id"],"原同号商品记录");term(p["change_reason"],"变更理由")
        previous,pins["previous"]=fetch(p["previous_task_id"],site,TOOL)
        if ((previous["case_id"],previous["own_product_id"],previous["version"]+1)!=(p["case_id"],p["own_product_id"],p["version"])
                or stamp(previous["as_of"])>=at):
            raise WorkflowError("须匹配同商品、同维护号及先前已保存版本。")
        old={v["sku_id"]:v for v in previous["variants"]}
        if not set(old)<= {v["sku_id"] for v in rows}:
            raise WorkflowError("原规格须保留，通过退出复核留痕。")
        for v in rows:
            other=old.get(v["sku_id"])
            if other is None:
                changes.append({"sku_id":v["sku_id"],"changed":["new_variant"]});continue
            compared=("spec_label","units","revision","catalog_source","price","wave","demand","requested_action","decision")
            changed=[k for k in compared if v[k]!=other[k]]
            if (v["revision"] not in (other["revision"],other["revision"]+1)
                    or {"spec_label","units"}&set(changed) and v["revision"]!=other["revision"]+1):
                raise WorkflowError("规格变更需有同 SKU 的下一修订。")
            changes.append({"sku_id":v["sku_id"],"changed":changed})
    return pins,(rows,changes,previous)


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","tool_id","site","case_id","version","upstream","input_sha256","state")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:raise WorkflowError("选品维护只使用结构化的本机合成证据。")
    pins,(rows,changes,previous)=linked
    state="REVIEW_RECORDED" if all(r["decision"] not in ("BLOCKED","PENDING_EVIDENCE") for r in rows) else "PARTIAL_OR_HELD"
    r={**{k:p[k] for k in ("site","scope","currency","job_id","case_id","version","own_product_id","owner","as_of","fixture_thresholds","change_reason")},
       "task_id":folder.name,"tool_id":tool,"state":state,"variants":rows,"changes":changes,
       "previous_task_id":previous["task_id"] if previous else None,"category_research_used_as_own_fact":False,
       "actual_sales_verified":False,"execution_authorized":False,"model_called":False,"provider_called":False,
       "upstream":pins,"input_sha256":sha256(encode(p)).hexdigest()}
    raw=encode(r)
    lines=[f"# {site} · {p['case_id']} v{p['version']} · {state}","",
      "合成规格、价格与生命周期条件。实际改价、上架和退出未授权。","",
      "| SKU | 规格 | 定价检查 | 畅滞销 | 波段 | 建议 |","|---|---|---|---|---|---|",
      *["| "+" | ".join(safe_md(str(x[k])) for k in ("sku_id","spec_label"))+
        f" | {x['price']['state']} | {x['demand']['state']} | {x['wave_state']} | {x['decision']} |" for x in rows]]
    report=("\n".join(lines)+"\n").encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(v)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or json.loads(files["json"])!=r or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("货盘维护原件或回执无法核验。")


def public_tools():
    return [{"id":TOOL,"role_suffix":"selection","sites":["CN"],"label":"抖音 SKU 定价、上新和生命周期",
             "description":"同 SKU 追溯合成定价、波次和畅滞销状态。","input_example":{}}]

