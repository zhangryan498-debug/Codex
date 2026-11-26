"""CN local playbook library with immutable revisions and target-site review."""

from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

SAVE="cn-campaign-playbook-save"
FIND="cn-campaign-playbook-find"
TOOLS=(SAVE,FIND)
FILES={"json":"campaign-playbook.json","report":"campaign-playbook.md","receipt":"campaign-playbook-receipt.json"}


class WorkflowError(ValueError):
    pass


def validate(site,tool,p):
    more=("playbook_id version previous_task_id effective_at revision_reason campaign_plan_id schedule_task_id "
          "mechanism audience resources steps source_ref" if tool==SAVE else
          "lookup_id playbook_id audience include_history target_site")
    check(p,"site currency scope as_of "+more,"活动玩法")
    if (site,p["site"],p["currency"],p["scope"])!=("CN","CN","CNY",SYNTHETIC):
        raise WorkflowError("仅在原抖音本机活动库操作。")
    now=stamp(p["as_of"])
    identify(p["playbook_id"],"玩法编号")
    term(p["audience"],"明确的合成人群")
    if tool==FIND:
        identify(p["lookup_id"],"查询编号")
        if p["target_site"] not in ("CN","PH") or type(p["include_history"]) is not bool:
            raise WorkflowError("复用评估须声明目标站点与是否查历史。")
        return now
    identify(p["campaign_plan_id"],"原排期编号")
    task_id(p["schedule_task_id"],"原排期回执")
    amount(p["version"],"版本",1,1000000,False)
    if (p["version"]==1)!=(p["previous_task_id"] is None):
        raise WorkflowError("修订须引用同玩法的旧版。")
    if p["previous_task_id"]:
        task_id(p["previous_task_id"],"旧版")
        term(p["revision_reason"],"修订原因")
    elif p["revision_reason"] is not None:
        raise WorkflowError("首版无需修订原因。")
    if stamp(p["effective_at"])>now:
        raise WorkflowError("待生效草案不能充当当前版本。")
    term(p["source_ref"],"合成归档源",True)
    term(p["mechanism"],"活动玩法")
    for key in ("resources","steps"):
        if not isinstance(p[key],list) or not 1<=len(p[key])<=30:
            raise WorkflowError("须登记计划资源及逐条可复用步骤。")
    for r in p["resources"]:
        check(r,"id label version owner proof_ref","资源及版本")
        identify(r["id"],"资源编号")
        term(r["label"],"资源类型")
        term(r["owner"],"责任人")
        amount(r["version"],"资源修订",1,1000000,False)
        term(r["proof_ref"],"资源来源",True)
    if len({r["id"] for r in p["resources"]})!=len(p["resources"]):
        raise WorkflowError("资源编号重复。")
    for r in p["steps"]:
        check(r,"id action owner due_at transfer target_condition source_ref","活动步骤")
        identify(r["id"],"步骤编号")
        for k in ("action","owner","target_condition","source_ref"):
            term(r[k],k,k in ("target_condition","source_ref"))
        if r["transfer"] not in ("CN_ONLY","CONDITIONAL","PENDING"):
            raise WorkflowError("须分开抖音专属、条件转译与待核。")
        stamp(r["due_at"])
    if len({r["id"] for r in p["steps"]})!=len(p["steps"]):
        raise WorkflowError("步骤编号重复。")
    return now


def bind(site,tool,p,load,list_rows):
    now=validate(site,tool,p)
    pins={};ctx={}
    def get(ident,kind):
        ctx[ident],pins[ident]=load(ident,site,kind)
        return ctx[ident]
    if tool==FIND:
        rows=[r for r in list_rows(site,p["playbook_id"],SAVE)
              if stamp(r["effective_at"])<=now and stamp(r["as_of"])<=now]
        for r in rows:get(r["task_id"],SAVE)
        return pins,ctx
    plan=get(p["schedule_task_id"],"campaign-offline-schedule")
    if (plan["plan_id"],plan["currency"],plan["scope"])!=(p["campaign_plan_id"],p["currency"],SYNTHETIC):
        raise WorkflowError("活动不属于该本站排期与币种。")
    if p["previous_task_id"]:
        old=get(p["previous_task_id"],SAVE)
        if ((old["playbook_id"],old["campaign_plan_id"],old["version"]+1)!=
            (p["playbook_id"],p["campaign_plan_id"],p["version"])
            or stamp(old["effective_at"])>=stamp(p["effective_at"]) or stamp(old["as_of"])>now):
            raise WorkflowError("只接收同玩法、同活动的连续版本及上版存档。")
    return pins,ctx


def summarize(tool,p,ctx):
    if tool==SAVE:
        plan=ctx[p["schedule_task_id"]]
        pending=not p["source_ref"] or any(not x["proof_ref"] for x in p["resources"]) or any(
            not x["source_ref"] or (x["transfer"]=="CONDITIONAL" and not x["target_condition"]) for x in p["steps"])
        pending=pending or plan["state"]!="READY_OFFLINE"
        keys=("playbook_id","version","previous_task_id","effective_at","revision_reason","campaign_plan_id",
              "mechanism","audience","source_ref","resources","steps")
        return {**{k:p[k] for k in keys},"state":"PENDING_EVIDENCE" if pending else "LIBRARY_RECORDED",
                "schedule_scope":"local_plan_not_actual_performance","schedule_state":plan["state"]}
    rows=sorted(ctx.values(),key=lambda r:r["version"],reverse=True)
    current=rows[0] if rows and rows[0]["audience"]==p["audience"] else None
    dispositions={}
    if current:
        for s in current["steps"]:
            if p["target_site"]=="CN":
                outcome=("LOCAL_REFERENCE" if current["state"]=="LIBRARY_RECORDED" else "UNVERIFIED")
            elif s["transfer"]=="CN_ONLY":
                outcome="DO_NOT_TRANSFER"
            elif s["transfer"]=="CONDITIONAL" and s["source_ref"] and s["target_condition"] and current["state"]=="LIBRARY_RECORDED":
                outcome="TARGET_EVIDENCE_REQUIRED"
            else:
                outcome="UNVERIFIED"
            dispositions[s["id"]]={"state":outcome,"condition":s["target_condition"],"source_ref":s["source_ref"]}
    return {"playbook_id":p["playbook_id"],"state":"CURRENT_REFERENCE" if current else "NO_SCOPED_CURRENT_VERSION",
            "target_site":p["target_site"],"current_task_id":current["task_id"] if current else None,
            "current_version":current["version"] if current else None,"current_library_state":current["state"] if current else None,
            "history":[{"task_id":r["task_id"],"version":r["version"],"state":r["state"]} for r in rows] if p["include_history"] else [],
            "step_transfer_review":dispositions,"rules_verification_from_real_platform":False}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","input_sha256","upstream","playbook_id")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:raise WorkflowError("仅接收本机合成结构记录。")
    pins,ctx=linked
    r={**summarize(tool,p,ctx),"task_id":folder.name,"tool_id":tool,"site":site,"scope":SYNTHETIC,"currency":p["currency"],
       "as_of":p["as_of"],"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "dispatched":False,"actual_campaign_outcomes":None,"execution_authorized":False,"model_called":False}
    b=encode(r);report=(f"# 抖音活动玩法 · {r['playbook_id']}\n\n结论：{r['state']}。本机合成归档。\n\n"+b.decode()).encode()
    files={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":b,"report":report,"receipt":encode(receipt(r,b,report))}.items():
        with files[k].open("xb") as f:f.write(v)
    return r,files


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
        or r["task_id"]!=task["id"] or r["tool_id"]!=task["source"]["tool_id"]
        or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("活动玩法或历史回执不一致。")


def public_tools():
    return [{"id":tool,"sites":["CN"],"role_suffix":"campaign-ops","label":title,
        "description":"同站合成玩法、版本及跨站条件本机留痕。","input_example":{}}
        for tool,title in ((SAVE,"保存活动玩法与活动 SOP"),(FIND,"查询活动玩法及跨站适用性"))]

