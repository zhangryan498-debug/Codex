"""Site-specific topic priority and production scheduling over pinned local activity evidence."""
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_campaign_options import CHOICE
from workflow_competitor_bulletin import TOOL as BULLETIN
from workflow_offline_schedule import CAMPAIGN
from workflow_lead_weekly import encode,term
from workflow_live_rehearsal import amount,check,identify,stamp,task_id

TOOL="director-offline-topic-schedule"
TOOLS=(TOOL,)
FILES={"json":"director-topics.json","report":"director-topics.md","receipt":"director-topics-receipt.json"}
SCORE_KEYS=("product_certainty","demand_certainty","hook_strength","production_ease","competition_headroom","platform_fit")
PURPOSE={"shelf_conversion":"product_card_shelf","live_traffic":"live_room_prewarm","awareness":"discovery_content"}
RESOURCE_TYPES=("director_minutes","editor_minutes","material_uses")


class WorkflowError(ValueError):
    pass


def interval(p):
    return stamp(p["starts_at"]),stamp(p["ends_at"])


def scope(site,p,x):
    return (x["site"],x["currency"],x["sku_id"],x["revision"])==(site,p["currency"],p["sku_id"],p["revision"])


def check_resources(site,p):
    x=p["resources"]
    if not isinstance(x,dict) or len(x)>24:
        raise WorkflowError("使用具名合成人员或素材池。")
    seen=set()
    for key,v in x.items():
        identify(key,"资源编号")
        check(v,"site currency sku_id revision kind resource_identity capacity source_ref starts_at ends_at","合成资源")
        ident=v["resource_identity"]
        identify(ident,"人员或素材的持续编号")
        if ident in seen:
            raise WorkflowError("同一人员或素材身份不得重复声明成额外产能。")
        seen.add(ident)
        amount(v["revision"],"资源 SKU 修订",1,10**6,False)
        if not scope(site,p,v) or v["kind"] not in RESOURCE_TYPES:
            raise WorkflowError("人员/素材资源的站点、SKU 修订或类型不匹配。")
        lo,hi=interval(v)
        if not lo<hi:
            raise WorkflowError("资源覆盖时间不合法。")
        amount(v["capacity"],"合成可用容量",0,1000000)
        if v["kind"]!="material_uses" and v["capacity"] is not None and v["capacity"]*60>(hi-lo).total_seconds():
            raise WorkflowError("单个实名岗位槽的容量不能超过其有效时段。")
        term(v["source_ref"],"具名资源的合成出处",True)


def check_topic(site,p,t):
    check(t,"id title why_now goal route locale content_form production_path priorities sources variants "
          "production required_resources localized_fixture_ref search_terms requires_creator","拍摄命题与可排期差异")
    if type(t["requires_creator"]) is not bool or (site=="CN" and t["requires_creator"]):
        raise WorkflowError("由菲律宾命题明确是否使用达人寄样链。")
    identify(t["id"],"选题编号")
    for k in ("title","why_now","content_form"):
        term(t[k],k)
    if t["goal"] not in PURPOSE or PURPOSE[t["goal"]]!=t["route"]:
        raise WorkflowError("店铺承接、引流和种草用途须分开。")
    if t["locale"] not in (("zh-CN",) if site=="CN" else ("en-PH","fil-PH")):
        raise WorkflowError("选题话术站点语言不匹配。")
    if t["production_path"] not in ("planned_local_montage","planned_image_video"):
        raise WorkflowError("只记录原项目的两种内容准备路线。")
    check(t["priorities"]," ".join(SCORE_KEYS),"六维优先级")
    for k,v in t["priorities"].items():
        amount(v,k,1,5)
    term(t["localized_fixture_ref"],"合成本地化审查",True)
    if not isinstance(t["search_terms"],list) or len(t["search_terms"])>3:
        raise WorkflowError("填零至三个合成搜索承接词。")
    for x in t["search_terms"]:
        term(x,"检索词")
    sources=t["sources"]
    if not isinstance(sources,list) or len(sources)>6:
        raise WorkflowError("使用可查来源。")
    evidence=set()
    for x in sources:
        check(x,"site currency sku_id revision kind ref summary captured_at","选题依据")
        amount(x["revision"],"选题证据 SKU 修订",1,10**6,False)
        if not scope(site,p,x):
            raise WorkflowError("选题依据跨站、跨 SKU 修订或跨币种。")
        if x["kind"] not in ("competitor_benchmark","product_fixture","customer_fixture","creative_fixture"):
            raise WorkflowError("仅用本机指定的合成线索。")
        term(x["ref"],"证据编号")
        term(x["summary"],"为何与此选题有关")
        if stamp(x["captured_at"])>stamp(p["as_of"]) or x["ref"] in evidence:
            raise WorkflowError("同选题避免证据重复或使用截点之后的材料。")
        evidence.add(x["ref"])
    variants=t["variants"]
    if not isinstance(variants,list) or not 2<=len(variants)<=4:
        raise WorkflowError("每题提供两至四个可区分的方案。")
    ids=set()
    for v in variants:
        check(v,"id entry narrative proof cta difference_note asset_ids","结构差异")
        identify(v["id"],"变体编号")
        if v["id"] in ids:
            raise WorkflowError("一个选题内的变体编号须唯一。")
        ids.add(v["id"])
        for k in ("entry","narrative","proof","cta"):
            term(v[k],k)
        term(v["difference_note"],"相对基础版本的结构差异",True)
        if not isinstance(v["asset_ids"],list) or len(v["asset_ids"])>10:
            raise WorkflowError("列各版涉及的获准本机资源。")
        for i in v["asset_ids"]:
            identify(i,"变体引用素材编号")
    slot=t["production"]
    check(slot,"starts_at ends_at due_at owner","制作时间与期限")
    left,right=interval(slot)
    if not left<right or stamp(slot["due_at"])<right:
        raise WorkflowError("制作窗口须完整且不晚于交付。")
    term(slot["owner"],"制作负责人")
    needs=t["required_resources"]
    if not isinstance(needs,dict) or len(needs)>12:
        raise WorkflowError("明确每个池的使用量。")
    for k,v in needs.items():
        identify(k,"资源编号")
        amount(v,"资源需求",1,10**6,False)


def validate(site,p):
    check(p,"site currency scope packet_id campaign_id sku_id revision as_of owner opportunity_task_id "
          "schedule_task_id competitor_task_id topics resources ph_dependencies","选题与排期")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("仅处理本站、本币合成排期。")
    for k in ("packet_id","campaign_id","sku_id"):
        identify(p[k],k)
    for k in ("opportunity_task_id","schedule_task_id","competitor_task_id"):
        task_id(p[k],k)
    amount(p["revision"],"下游 SKU 修订",1,10**6,False)
    at=stamp(p["as_of"])
    term(p["owner"],"编导负责人")
    check_resources(site,p)
    if not isinstance(p["topics"],list) or not 1<=len(p["topics"])<=8:
        raise WorkflowError("每批 1 至 8 个选题。")
    ids=set()
    for t in p["topics"]:
        check_topic(site,p,t)
        if t["id"] in ids:
            raise WorkflowError("同选题号不可重复计入。")
        ids.add(t["id"])
    d=p["ph_dependencies"]
    if site=="CN":
        if d is not None:
            raise WorkflowError("抖音不套用菲律宾节庆或寄样规则。")
        return
    if d is None:
        return
    check(d,"festival creator","PH 本地节点与达人依赖")
    e=d["festival"]
    if e is not None:
        check(e,"market region_label starts_at ends_at available_fixture source_ref","合成本地节点")
        if e["market"]!="PH" or not interval(e)[0]<interval(e)[1]:
            raise WorkflowError("节点必须属于菲律宾且有有效窗口。")
        if e["available_fixture"] is not None and type(e["available_fixture"]) is not bool:
            raise WorkflowError("节点可用须为合成证据或待核。")
        term(e["source_ref"],"合成本地日历",True)
        term(e["region_label"],"本地节点")
    c=d["creator"]
    if c is not None:
        check(c,"source_ref sample_shipping_hours review_planned_hours booking_max_hours planning_min_prep_days","PH 寄样倒排条款")
        term(c["source_ref"],"虚构寄样条款",True)
        for k in ("sample_shipping_hours","review_planned_hours","booking_max_hours","planning_min_prep_days"):
            amount(c[k],k,1,24*60)


def bind(site,tool,p,fetch):
    validate(site,p)
    pins={};links={}
    for key,typ in (("opportunity",CHOICE),("schedule",CAMPAIGN),("competitor",BULLETIN)):
        r,pin=fetch(p[key+"_task_id"],site,typ)
        if (r["site"],r["currency"],r["scope"])!=(site,p["currency"],SYNTHETIC):
            raise WorkflowError("上游必须同站、本币及合成。")
        links[key]=r;pins[key]=pin
    o,s,c=(links[k] for k in ("opportunity","schedule","competitor"))
    identity=(p["campaign_id"],p["sku_id"],p["revision"])
    if ((o["campaign_id"],o["sku_id"],o["revision"])!=identity or
        (s["plan_id"],s["sku"],s["version"])!=identity or stamp(o["starts_at"])!=stamp(s["event_starts_at"]) or
        any(o["upstream"][k]!=pins[k] for k in ("schedule","competitor"))):
        raise WorkflowError("上游活动、SKU 修订与原竞品和排期回执必须一致。")
    if max(stamp(o["as_of"]),stamp(c["cutoff_at"]))>stamp(p["as_of"]):
        raise WorkflowError("不能使用截点之后的活动和竞品回执。")
    observations={x["observation_id"]:x for x in c["observations"]["current"]}
    for t in p["topics"]:
        for evidence in t["sources"]:
            if evidence["kind"]=="competitor_benchmark" and evidence["ref"] in observations and (
                observations[evidence["ref"]]["identity"][2]!=p["sku_id"] or
                stamp(observations[evidence["ref"]]["captured_at"])!=stamp(evidence["captured_at"])
            ):
                raise WorkflowError("竞品证据的原 SKU 或时间不匹配。")
    return pins,links


def request_key(p):
    return p["packet_id"]


def ph_timing(p,s,calendar):
    if p["site"]!="PH":
        return None
    d=p["ph_dependencies"] or {}
    e=d.get("festival");q=d.get("creator")
    event=stamp(s["event_starts_at"])
    issues=[]
    region_state="PENDING_FESTIVAL"
    if e and e["source_ref"] and e["available_fixture"] is not None:
        region_state=("FESTIVAL_FIXTURE_AVAILABLE" if e["available_fixture"] and interval(e)[0]<=event<interval(e)[1]
                      else "FESTIVAL_FIXTURE_UNAVAILABLE")
    if s["event_type"]=="local_holiday" and region_state!="FESTIVAL_FIXTURE_AVAILABLE":
        issues.append(region_state)
    required=("sample_ship","sample_review","creator_booking","localization")
    if not q or not q["source_ref"] or any(q[k] is None for k in (
        "sample_shipping_hours","review_planned_hours","booking_max_hours","planning_min_prep_days"
    )) or any(k not in calendar for k in required):
        issues.append("PENDING_CREATOR_DEPENDENCIES")
        return {"festival_state":region_state,"festival_input":e,"creator_terms":q,"issues":issues,"creator_timeline":None}
    ship=calendar["sample_ship"];review=calendar["sample_review"];book=calendar["creator_booking"]
    local=calendar["localization"]
    ship_start,arrive=interval(ship);review_start,review_done=interval(review);book_start,book_done=interval(book)
    latest_prep=event-timedelta(days=q["planning_min_prep_days"])
    latest_ship=book_start-timedelta(hours=q["sample_shipping_hours"]+q["review_planned_hours"])
    if (arrive-ship_start<timedelta(hours=q["sample_shipping_hours"]) or arrive>review_start
        or review_done-review_start!=timedelta(hours=q["review_planned_hours"]) or review_done>book_start
        or book_start>review_done+timedelta(hours=q["booking_max_hours"]) or book_done>event
        or stamp(s["preparation_starts_at"])>latest_prep or stamp(local["ends_at"])>event):
        issues.append("CREATOR_OR_LOCALIZATION_BACKPLAN_CONFLICT")
    return {"festival_state":region_state,"festival_input":e,"creator_terms":q,"issues":issues,
            "creator_timeline":{"sample_dispatch_at":ship["starts_at"],"sample_arrival_at":ship["ends_at"],
                "review_starts_at":review["starts_at"],"review_complete_at":review["ends_at"],
                "booking_at":book["starts_at"],"localization_ready_at":local["ends_at"],
                "latest_dispatch_for_review_and_booking":latest_ship.isoformat(),"latest_preparation_start":latest_prep.isoformat(),
                "booking_after_review_hours":(book_start-review_done).total_seconds()/3600,
                "planned_live_start":s["event_starts_at"],"shipment_or_booking_executed":False}}


def evidence_ready(t,known):
    src=[]
    for x in t["sources"]:
        match=known.get(x["ref"]) if x["kind"]=="competitor_benchmark" else None
        okay=x["kind"]!="competitor_benchmark" or bool(match and match["source_ref"] and match["state"]=="READY_OFFLINE")
        src.append({**x,"linked_bulletin":match if okay else None,"usable_for_fixture":okay})
    return src


def preliminary(site,p,t,known,cal,timing):
    sources=evidence_ready(t,known);rules=t["priorities"];needs=t["required_resources"]
    content=[]
    if (not sources or not all(x["usable_for_fixture"] for x in sources)
        or not any(x["kind"]=="product_fixture" for x in sources)):
        content.append("SOURCE_EVIDENCE_PENDING")
    if any(x is None for x in rules.values()):
        content.append("PRIORITY_EVIDENCE_PENDING")
    if rules["product_certainty"] is not None and rules["product_certainty"]<3:
        content.append("PRODUCT_CERTAINTY_BELOW_PROJECT_FLOOR")
    if site=="CN" and t["goal"]=="shelf_conversion" and len({x.strip() for x in t["search_terms"]})!=3:
        content.append("SHELF_SEARCH_TERMS_PENDING")
    vv=t["variants"]
    seen={tuple(v[k].strip() for k in ("entry","narrative","proof")) for v in vv}
    if len(seen)!=len(vv) or any(not v["difference_note"] for v in vv[1:]):
        content.append("VARIANT_STRUCTURE_OR_DIFFERENCE_PENDING")
    asset_ids={v for x in vv for v in x["asset_ids"]}
    resources=p["resources"]
    if (not asset_ids or any(not v["asset_ids"] for v in vv) or
        any(i not in resources or i not in needs or resources[i]["kind"]!="material_uses" for i in asset_ids)):
        content.append("VERSIONED_ASSET_EVIDENCE_PENDING")
    if site=="PH" and not t["localized_fixture_ref"]:
        content.append("LOCALIZATION_FIXTURE_REVIEW_PENDING")
    time=[]
    target=cal.get("warmup" if t["goal"]=="live_traffic" else "asset_final_review")
    if target:
        deadline=target["starts_at"] if t["goal"]=="live_traffic" else target["ends_at"]
        if stamp(t["production"]["due_at"])>stamp(deadline):
            time.append("AFTER_PINNED_CHANNEL_DEADLINE")
    else:
        deadline=None
        time.append("SCHEDULE_MILESTONE_PENDING")
    if timing:
        time.extend(x for x in timing["issues"] if x.startswith("FESTIVAL_") or x=="PENDING_FESTIVAL" or t["requires_creator"])
        localized=cal.get("localization")
        if localized is None:
            time.append("LOCALIZATION_MILESTONE_PENDING")
        elif stamp(t["production"]["starts_at"])<stamp(localized["ends_at"]):
            time.append("BEFORE_LOCALIZATION_READY")
    return content,time,sources,deadline


def schedule(site,p,links):
    s=links["schedule"]
    by_stage={}
    for r in s["calendar"]:
        by_stage.setdefault(r["stage"],[]).append(r)
    cal={key:rows[0] for key,rows in by_stage.items() if len(rows)==1}
    known={r["observation_id"]:r for r in links["competitor"]["observations"]["current"]}
    timing=ph_timing(p,s,cal)
    topics=sorted(p["topics"],key=lambda t:(
        any(v is None for v in t["priorities"].values()),-sum(v or 0 for v in t["priorities"].values()),t["id"]
    ))
    used={k:0 for k in p["resources"]};crew_use={}
    rows=[]
    for t in topics:
        content,reasons,proof,deadline=preliminary(site,p,t,known,cal,timing)
        a,b=interval(t["production"])
        if (a<stamp(s["preparation_starts_at"]) or a<=stamp(p["as_of"])
            or stamp(t["production"]["due_at"])>stamp(s["event_starts_at"])):
            reasons.append("OUTSIDE_PREPARATION_WINDOW")
        if s["state"]!="READY_OFFLINE":
            reasons.append("UPSTREAM_SCHEDULE_PENDING")
        needs=t["required_resources"]; asset={x for v in t["variants"] for x in v["asset_ids"]}
        pools=p["resources"]
        if set(RESOURCE_TYPES)!={pools[i]["kind"] for i in needs if i in pools}:
            reasons.append("REQUIRED_RESOURCE_TYPES_PENDING")
        if any(i in pools and pools[i]["kind"]=="material_uses" and i not in asset for i in needs):
            reasons.append("UNASSIGNED_MATERIAL_USES")
        gaps={}
        for i,n in needs.items():
            z=pools.get(i)
            if not z or not z["source_ref"] or z["capacity"] is None:
                gaps[i]={"state":"UNKNOWN_RESOURCE"}
                continue
            x,y=interval(z);after=used[i]+n
            crew=z["kind"]!="material_uses"
            over=after>z["capacity"]
            conflict=crew and any(a<end and start<b for start,end in crew_use.get(i,[]))
            if a<x or y<b or (crew and n*60>(b-a).total_seconds()) or over or conflict:
                gaps[i]={"state":"RESOURCE_LIMIT_OR_CONFLICT","before":used[i],"requested":n,"available":z["capacity"],
                         "excess_units":max(after-z["capacity"],0),"overlap":bool(conflict)}
        if gaps:
            reasons.append("RESOURCE_NOT_AVAILABLE")
        okay=not content and not reasons
        if okay:
            for i,n in needs.items():
                used[i]+=n
                if pools[i]["kind"]!="material_uses":
                    crew_use.setdefault(i,[]).append((a,b))
        rows.append({**t,"state":"PROPOSED_SLOT_FIXTURE" if okay else "REPLAN_OR_GATHER_EVIDENCE",
                     "score":sum(t["priorities"].values()) if all(x is not None for x in t["priorities"].values()) else None,
                     "input_ready":not content,"reasons":content+reasons,
                     "sourced_inputs":proof,"channel_deadline":deadline,"resource_issues":gaps,
                     "already_shot":False,"already_published":False})
    return {"state":"TOPICS_RECORDED","timezone":s["timezone"],"event_name":s["event_name"],"topics":rows,
            "production_use":used,"resource_sources":p["resources"],"ph_planning":timing,
            "ranking_policy":"complete fixture scores, higher equals higher priority; ID breaks ties; no allocation for held proposals",
            "coverage":{"topics":len(rows),"content_input_ready":sum(x["input_ready"] for x in rows),
                        "slot_proposed":sum(x["state"]=="PROPOSED_SLOT_FIXTURE" for x in rows)},
            "benchmark_used_as_own_asset_or_terms":False,"actual_marketing_result":None}


def receipt(r,b):
    return {**{k:r[k] for k in ("task_id","site","input_sha256","upstream")},
            "result_sha256":sha256(b["json"]).hexdigest(),"report_sha256":sha256(b["report"]).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("本机方案只核历史回执和合成资源记录。")
    pins,links=linked
    keys=("site","currency","scope","packet_id","campaign_id","sku_id","revision","as_of","owner")
    r={**{k:p[k] for k in keys},**schedule(site,p,links),"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "actual_production_or_booking":False,"model_called":False}
    lines=[f"# {site} 编导选题与排期 · {p['packet_id']}","","合成来源和产能，只供本机评审。",
           f"节点：{r['event_name']} · {r['timezone']}。"]
    for t in r["topics"]:
        lines.append(f"- {t['id']} · {t['title']} · {t['route']} · {t['state']} · "
                     f"期限 {t['production']['due_at']} · {', '.join(t['reasons']) or '无待处理'}")
    if r["ph_planning"] and r["ph_planning"]["creator_timeline"]:
        c=r["ph_planning"]["creator_timeline"]
        lines.append(f"- PH 寄样计划 {c['sample_dispatch_at']} · 审核结束 {c['review_complete_at']} · 拟预约 {c['booking_at']}。")
    b={"json":encode(r),"report":("\n".join(lines)+"\n").encode("utf8")}
    b["receipt"]=encode(receipt(r,b))
    paths={k:folder/v for k,v in FILES.items()}
    for k,f in paths.items():
        with f.open("xb") as stream:
            stream.write(b[k])
    return r,paths


def verify_saved(task,b):
    r=task["result"]
    if (task["local_files"]!=FILES or json.loads(b["json"])!=r or
        r["upstream"]!=task["source"]["role_packet_pins"] or
        (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
        json.loads(b["receipt"])!=receipt(r,b)):
        raise WorkflowError("选题与排期的回执或来源无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":["CN","PH"],"role_suffix":"video-director",
             "label":"选题、变体与制作排期","description":"按同站 SKU 原回执和合成素材、人员产能安排本机命题。",
             "input_example":{}}]

