"""PH synthetic creator fact card, sampling countdown and actual local receiving receipt."""

from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

CARD="ph-selection-creator-card"
INTAKE="ph-live-creator-card-intake"
TOOLS=(CARD,INTAKE)
FILES={"json":"creator-card.json","report":"creator-card.md","receipt":"creator-card-receipt.json"}
STEP=("ship","arrive","review","confirm","book","rehearse","live")
DEPS=("price","shopping_bag","account_scope","materials")


class WorkflowError(ValueError):
    pass


def validate(site,tool,p):
    check(p,"site currency scope job_id sku_id revision as_of "
            + ("locale locale_review_ref facts fact_ids demonstration local_scene materials timed_seconds timed_source "
               "timeline review_record dependencies planning_terms owner" if tool==CARD else "card_task_id recipient"),"达人可讲性")
    if (site,p["site"],p["currency"],p["scope"])!=("PH","PH","PHP",SYNTHETIC):
        raise WorkflowError("仅处理菲律宾本机合成任务。")
    for k in ("job_id","sku_id"):
        identify(p[k],k)
    amount(p["revision"],"规格版",1,1000000,False)
    now=stamp(p["as_of"])
    if tool==INTAKE:
        task_id(p["card_task_id"],"来源卡")
        if p["recipient"]!=ROUTES["PH"][0]+"-live-ops":
            raise WorkflowError("收件人必须是本站直播岗位。")
        return now
    if p["locale"] not in ("en-PH","fil-PH","taglish",None):
        raise WorkflowError("使用明确的菲律宾语言标记或保留待核。")
    for k in ("locale_review_ref","demonstration","local_scene","materials","timed_source","owner"):
        term(p[k],k,k!="owner")
    amount(p["timed_seconds"],"本机示例试讲秒数",1,120)
    if not isinstance(p["facts"],list) or not 1<=len(p["facts"])<=15:
        raise WorkflowError("须登记可追溯或留空的商品事实。")
    seen=set()
    for r in p["facts"]:
        check(r,"id sku_id revision phrase source_ref state","商品事实")
        identify(r["id"],"事实编号")
        if (r["sku_id"],r["revision"])!=(p["sku_id"],p["revision"]) or r["id"] in seen:
            raise WorkflowError("事实须同一 SKU/修订且编号唯一。")
        seen.add(r["id"])
        if r["state"] not in ("synthetic_supported","unknown"):
            raise WorkflowError("事实仅区分合成支持与未知。")
        for k in ("phrase","source_ref"):term(r[k],k,True)
    x=p["fact_ids"]
    if (not isinstance(x,list) or not 1<=len(x)<=3 or
            any(not isinstance(k,str) or k not in seen for k in x) or len(set(x))!=len(x)):
        raise WorkflowError("一句卖点仅可使用已登记的事实编号。")
    check(p["timeline"]," ".join(STEP),"寄样与开播计划")
    for t in p["timeline"].values():
        if t is not None:stamp(t)
    review=p["review_record"]
    check(review,"sku_id revision state received_at reviewed_at source_ref","样品审核")
    if (review["sku_id"],review["revision"])!=(p["sku_id"],p["revision"]):
        raise WorkflowError("样品审核须同 SKU、同规格修订。")
    if review["state"] not in ("approved","rejected","unknown"):
        raise WorkflowError("样品审核状态无效。")
    for k in ("received_at","reviewed_at"):
        if review[k] is not None and stamp(review[k])>now:
            raise WorkflowError("审核实录的时间不能晚于审核截点。")
    term(review["source_ref"],"合成审核实录",True)
    check(p["dependencies"]," ".join(DEPS),"开播前依赖")
    for r in p["dependencies"].values():
        check(r,"sku_id revision state source_ref","开播依赖证据")
        if (r["sku_id"],r["revision"])!=(p["sku_id"],p["revision"]) or r["state"] not in ("ready","blocked","unknown"):
            raise WorkflowError("开播依赖与当前规格不一致。")
        term(r["source_ref"],"依赖来源",True)
    t=p["planning_terms"]
    check(t,"transit_hours review_hours booking_limit_hours rehearsal_buffer_hours source_ref","约定内倒排")
    for k in ("transit_hours","review_hours","booking_limit_hours","rehearsal_buffer_hours"):
        amount(t[k],k,1,1440,False)
    term(t["source_ref"],"合成排期选择")
    return now


def bind(site,tool,p,load):
    validate(site,tool,p);pins={}
    if tool==CARD:return pins,None
    src,pin=load(p["card_task_id"],site,CARD)
    if ((src["sku_id"],src["revision"],src["job_id"])!=(p["sku_id"],p["revision"],p["job_id"])
            or stamp(src["as_of"])>stamp(p["as_of"])):
        raise WorkflowError("不能收到别的 SKU、修订或未来版本。")
    pins[src["task_id"]]=pin
    return pins,src


def summarize(p):
    gaps=[];bad=[]
    use={r["id"]:r for r in p["facts"]}
    used=[use[i] for i in p["fact_ids"]]
    words=[f["phrase"] for f in used if f["state"]=="synthetic_supported" and f["phrase"] and f["source_ref"]]
    if len(words)!=len(used):gaps.append("unsupported_claims")
    if p["locale"] is None or (p["locale"] in ("taglish","fil-PH") and not p["locale_review_ref"]):
        gaps.append("locale_review_required")
    if not p["demonstration"] or not p["local_scene"] or not p["materials"]:
        gaps.append("demonstration_or_materials_unknown")
    if not p["timed_source"] or p["timed_seconds"] is None:gaps.append("delivery_duration_unknown")
    elif p["timed_seconds"]>15:bad.append("exceeds_synthetic_15s_brief")
    t=p["planning_terms"]
    clocks={k:stamp(v) if v else None for k,v in p["timeline"].items()}
    sr=p["review_record"]
    if sr["state"]=="rejected":bad.append("sample_rejected")
    elif sr["state"]!="approved" or not sr["source_ref"] or not sr["received_at"] or not sr["reviewed_at"]:
        gaps.append("sample_review_unknown")
    elif (stamp(sr["received_at"]),stamp(sr["reviewed_at"]))!=(clocks["arrive"],clocks["review"]):
        bad.append("sample_receipt_or_review_time_mismatch")
    if None in clocks.values():gaps.append("plan_steps_missing")
    else:
        elapsed=[clocks[a]<=clocks[b] for a,b in zip(STEP,STEP[1:])]
        if not all(elapsed) or clocks["arrive"]-clocks["ship"]<timedelta(hours=t["transit_hours"]) or (
            clocks["review"]-clocks["arrive"]<timedelta(hours=t["review_hours"])):
            bad.append("sampling_order_or_lead_time_conflict")
        if clocks["book"]-clocks["confirm"]>timedelta(hours=t["booking_limit_hours"]):
            bad.append("booking_after_confirm_limit")
        if clocks["live"]-clocks["rehearse"]<timedelta(hours=t["rehearsal_buffer_hours"]):
            bad.append("rehearsal_buffer_not_met")
    for k,v in p["dependencies"].items():
        if v["state"]=="blocked" and v["source_ref"]:bad.append(k+"_blocked")
        elif v["state"]!="ready" or not v["source_ref"]:gaps.append(k+"_unknown")
    planned_arrive=clocks["review"]-timedelta(hours=t["review_hours"]) if clocks["review"] else None
    latest={"arrive":planned_arrive,"ship":planned_arrive-timedelta(hours=t["transit_hours"]) if planned_arrive else None,
            "book":clocks["confirm"]+timedelta(hours=t["booking_limit_hours"]) if clocks["confirm"] else None,
            "rehearse":clocks["live"]-timedelta(hours=t["rehearsal_buffer_hours"]) if clocks["live"] else None}
    return {"state":"BLOCKED" if bad else "PENDING" if gaps else "CARD_FOR_LOCAL_PLAN",
            "selling_line":" ".join(words) if len(words)==len(used) else None,
            "facts":p["facts"],"script_fact_ids":p["fact_ids"],"demonstration":p["demonstration"],
            "local_scene":p["local_scene"],"materials":p["materials"],"locale":p["locale"],"locale_review_ref":p["locale_review_ref"],
            "timeline":p["timeline"],"sample_review":sr,"planning_terms":t,"dependencies":p["dependencies"],
            "latest_target_times":{k:v.isoformat() if v else None for k,v in latest.items()},
            "owner":p["owner"],"blocking_reasons":bad,"pending_reasons":gaps}


def make_receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","input_sha256","upstream","revision","sku_id")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:raise WorkflowError("只接收结构化合成事实。")
    pins,source=linked
    x=summarize(p) if tool==CARD else {"state":"ACCEPTED_FOR_LOCAL_PLAN" if source["state"]=="CARD_FOR_LOCAL_PLAN" else "RETURNED_OR_PENDING",
        "card_task_id":source["task_id"],"source_card_state":source["state"],"received_selling_line":source["selling_line"],
        "card_schedule":source["timeline"],"responsible_role":p["recipient"],"source_dependencies":source["dependencies"]}
    r={**x,"task_id":folder.name,"tool_id":tool,"site":site,"scope":SYNTHETIC,"currency":p["currency"],
        "job_id":p["job_id"],"sku_id":p["sku_id"],"revision":p["revision"],"as_of":p["as_of"],"upstream":pins,
        "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,"shipping_started":False,
        "creator_contacted":False,"live_started":False,"model_called":False}
    b=encode(r);report=(f"# PH 达人可讲性·{p['job_id']}\n\n结论：{r['state']}。合成安排，仅本机流转。\n\n"+b.decode()).encode()
    files={k:folder/v for k,v in FILES.items()}
    for k,v in {"json":b,"report":report,"receipt":encode(make_receipt(r,b,report))}.items():
        with files[k].open("xb") as f:f.write(v)
    return r,files


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
        or r["task_id"]!=task["id"] or r["tool_id"]!=task["source"]["tool_id"]
        or json.loads(files["receipt"])!=make_receipt(r,files["json"],files["report"])):
        raise WorkflowError("达人可讲性卡或取件回执不一致。")


def public_tools():
    return [{"id":tool,"sites":["PH"],"role_suffix":role,"label":label,"description":"本机合成试讲、倒排或签收。",
             "input_example":{}} for tool,role,label in ((CARD,"selection","PH 达人可讲性卡与寄样倒排"),
             (INTAKE,"live-ops","PH 直播接收可讲性卡"))]

