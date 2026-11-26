"""Local, provenance-bound competitor proposal and independent receiving roles."""
from datetime import datetime
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from role_tools_extra import _load
from workflow_live_rehearsal import check, identify, amount, stamp, task_id


class WorkflowError(ValueError):
    pass


PROPOSE="competitor-proposal-handoff"
FEEDBACK="competitor-evidence-feedback"
ROLE_ROUTES={
    "selection-competitor-intake":("selection","selection"),
    "materials-competitor-intake":("materials","video-director"),
    "advertising-competitor-intake":("advertising","ad-buyer"),
}
TOOLS={PROPOSE:"competitor-analyst",FEEDBACK:"competitor-analyst",**{k:v[1] for k,v in ROLE_ROUTES.items()}}
FILES={"json":"competitor-handoff.json","report":"competitor-handoff.md","receipt":"competitor-handoff-receipt.json"}
BASE="site scope currency proposal_id version evaluated_at"
STATES=("RESEARCH_ONLY","PENDING","BLOCKED")
PROPOSALS=("PASS_RESEARCH","PENDING_EVIDENCE","BLOCK_COMPARISON")
RESTRICTIONS={
    "selection":("同规格候选研究","保留样本；请核对同件数、成本与独立库存，再送选品复核。"),
    "materials":("原创素材问题研究","将点位写成待验证问题；请用己方素材核实卖点与权利。"),
    "advertising":("从样本提出测试假设","补本地素材权利、同口径渠道和独立对照，提交广告试验复核。"),
}
EXTRAS={
    PROPOSE:"evidence_task_id previous_task_id feedback_task_id subjects missing_fields hypothesis max_age_seconds",
    FEEDBACK:"source_task_id receiver_task_id receiver_tool feedback_id fact_refs kind counterevidence next_action",
}
UNKNOWN_FIELDS=("matching_sample","own_cost","stock","asset_rights","measurement_basis","approval")


def note(value,label):
    if not isinstance(value,str) or not 4<=len(value.strip())<=500 or any(ord(c)<32 for c in value):
        raise WorkflowError(f"{label}需要明确的单行说明。")


def common(site,tool,p):
    fields=EXTRAS.get(tool,"source_task_id recipient intake_id local_gate gate_ref")
    check(p,BASE+" "+fields,"竞品提案")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("仅接受本站本币、明确标记的合成资料。")
    identify(p["proposal_id"],"提案编号")
    amount(p["version"],"提案版本",1,10**6,False)
    stamp(p["evaluated_at"])
    root=f"c10:{site}:{p['proposal_id']}:{p['version']}"
    if tool==PROPOSE:
        task_id(p["evidence_task_id"],"竞品样本任务")
        previous,feedback=p["previous_task_id"],p["feedback_task_id"]
        if (p["version"]==1 and (previous is not None or feedback is not None)) or (p["version"]>1 and (previous is None or feedback is None)):
            raise WorkflowError("修订须提供同编号的上一版及已保存的回灌。")
        if previous:task_id(previous,"上一版");task_id(feedback,"回灌")
        return root+":producer"
    task_id(p["source_task_id"],"当前提案")
    if tool==FEEDBACK:
        task_id(p["receiver_task_id"],"本岗收件任务")
        identify(p["feedback_id"],"反馈编号")
        if p["receiver_tool"] not in ROLE_ROUTES:raise WorkflowError("须有对应三路收件工具。")
        return root+":feedback:"+p["feedback_id"]
    identify(p["intake_id"],"收件编号")
    if p["recipient"]!=ROUTES[site][0]+"-"+TOOLS[tool]:
        raise WorkflowError("接收者必须为所选本站岗位。")
    return root+":"+tool+":"+p["intake_id"]


def bind(site,tool,p,load,research):
    ctx,pins={},{}
    def fetch(k,id,kind,latest=True):
        ctx[k],pins[k]=load(id,kind,site,latest)
        return ctx[k]
    if tool==PROPOSE:
        ctx["reviews"],pins["reviews"]=research(p["evidence_task_id"],site)
        if p["previous_task_id"]:
            old=fetch("prior",p["previous_task_id"],PROPOSE,False)
            fb=fetch("feedback",p["feedback_task_id"],FEEDBACK,False)
            if (old["proposal_id"]!=p["proposal_id"] or old["version"]+1!=p["version"]
                or fb["source_task_id"]!=old["task_id"] or stamp(old["evaluated_at"])>=stamp(p["evaluated_at"])
                or stamp(fb["evaluated_at"])>=stamp(p["evaluated_at"])
                or old["upstream"]["reviews"]==pins["reviews"]):
                raise WorkflowError("修订须沿用提案身份，指向原回灌，并引用另一份可核本机证据。")
        return ctx,pins
    prior=fetch("proposal",p["source_task_id"],PROPOSE)
    if (prior["proposal_id"],prior["version"])!=(p["proposal_id"],p["version"]) or stamp(prior["evaluated_at"])>stamp(p["evaluated_at"]):
        raise WorkflowError("当前提案身份或时点不符。")
    if tool==FEEDBACK:
        received=fetch("recipient",p["receiver_task_id"],p["receiver_tool"],False)
        if (received["source_task_id"]!=prior["task_id"] or received["upstream"]["proposal"]!=pins["proposal"]
                or stamp(received["evaluated_at"])>stamp(p["evaluated_at"])):
            raise WorkflowError("回灌须绑定同案、同版本的实际岗位收件。")
    return ctx,pins


def produce(policy,p,c):
    if not isinstance(p["subjects"],list) or len(p["subjects"])!=2:
        raise WorkflowError("须有两个明确的商品和 SKU。")
    report=c["reviews"]
    ids=set();facts=[];triggers=[];missing=[]
    t=stamp(p["evaluated_at"])
    amount(p["max_age_seconds"],"证据使用期限",1,10**7,False)
    note(p["hypothesis"],"推测")
    supplied=p["missing_fields"]
    if (not isinstance(supplied,list) or len(supplied)>len(UNKNOWN_FIELDS)
            or any(not isinstance(v,str) or v not in UNKNOWN_FIELDS for v in supplied) or len(set(supplied))!=len(supplied)):
        raise WorkflowError("缺字段须按所列范围逐项登记。")
    for row in p["subjects"]:
        check(row,"product_id sku_id","对照对象")
        for k in row:identify(row[k],k)
        product=next((a for a in report["products"] if a["product_id"]==row["product_id"]),None)
        sku=next((a for a in (product or {}).get("skus",[]) if a["sku_id"]==row["sku_id"]),None)
        if not sku or row["product_id"] in ids:raise WorkflowError("对照须指定证据中两个不同商品的精确 SKU。")
        ids.add(row["product_id"])
        src=product["source"]
        captured=datetime.fromisoformat(src["captured_utc"].replace("Z","+00:00"))
        age=(t-captured).total_seconds()
        if age<0:raise WorkflowError("来源晚于评估时点。")
        current=age<=p["max_age_seconds"]
        if not current:triggers.append("snapshot_expired")
        def fact(label,v):
            return {**row,"fact_id":row["sku_id"]+"-"+label,"field":label,"value":v,
                    "grade":"source_observation" if v is not None else "unknown",
                    "source_ref":src["reference"],"observed_at":src["captured_utc"],
                    "sample_window":[src["window_from"],src["window_to"]],
                    "allowed_use":"current_research_only" if current and v is not None else "historical_only" if v is not None else "none"}
        facts.extend((fact("pack_count",sku["unit_count"]),
                      fact("sample_reviews",product["review_count_by_known_sku"][sku["sku_id"]] if src["coverage"]=="sampled" else None)))
        if sku["unit_count"] is None or src["coverage"]!="sampled":missing.append("aligned_samples_missing")
    packs=[x["value"] for x in facts if x["field"]=="pack_count"]
    if None not in packs and packs[0]!=packs[1]:triggers.append("pack_count_conflict")
    if report["comparison"]["same_sampling_window_and_method"] is not True:missing.append("sample_window_or_method_mismatch")
    rank=2 if triggers else 1 if missing or supplied else 0
    routes={k:{"status":STATES[rank],"allowed_claims":[f["fact_id"] for f in facts if f["value"] is not None],
               "next_action":v[1]} for k,v in RESTRICTIONS.items()}
    a={"state":PROPOSALS[rank],"disposition":PROPOSALS[rank],"handoffs":routes,"facts":facts,
       "observed_conflicts":sorted(set(triggers)),"unknowns":sorted(set(missing+supplied)),
       "hypothesis":{"text":p["hypothesis"],"grade":"unverified_hypothesis"},
       "next_action":"补并核对同件数同抽样窗口的证据，按新版本复核后交接。" if rank else "送三岗离线研究并记录后续证据。",
       "evidence_clock":"caller-selected synthetic horizon"}
    if policy.producer_state_errors(a):raise WorkflowError("交接状态与当前提案矛盾。")
    return a


def consume(policy,tool,p,c):
    origin=c["proposal"]
    route=ROLE_ROUTES[tool][0]
    gate=p["local_gate"]
    if gate not in ("ready","unknown","conflicting"):
        raise WorkflowError("本岗只填明确研究、未知或冲突。")
    if p["gate_ref"] is not None:identify(p["gate_ref"],"本岗局部依据")
    if gate=="conflicting" and p["gate_ref"] is None:raise WorkflowError("确定冲突必须有本岗来源。")
    rank=max(STATES.index(origin["handoffs"][route]["status"]),{"ready":0,"unknown":1,"conflicting":2}[gate],
             1 if p["gate_ref"] is None else 0)
    allowed=origin["handoffs"][route]["allowed_claims"]
    r={"state":STATES[rank],"own_review_state":STATES[rank],"route":route,
       "upstream_disposition":origin["disposition"],"reference_ids":[f["fact_id"] for f in origin["facts"]],
       "allowed_facts":[f for f in origin["facts"] if f["fact_id"] in allowed],
       "unknowns":origin["unknowns"],"hypothesis":origin["hypothesis"],
       "scope_of_use":RESTRICTIONS[route][0],"next_action":RESTRICTIONS[route][1],
       "source_task_id":p["source_task_id"],"recipient":p["recipient"],
       "copy_action_authorized":False,"price_or_budget_authorized":False}
    if policy.consumer_state_errors(origin,route,r):raise WorkflowError("本岗决定放宽了上游状态。")
    return r


def feedback(p,c):
    r=c["recipient"]
    ids=p["fact_refs"]
    if p["kind"] not in ("return_for_evidence","monitor") or not isinstance(ids,list) or not ids:
        raise WorkflowError("回灌须指明退回或监控，以及精确事实编号。")
    for k in ids:identify(k,"事实编号",r"[A-Za-z0-9_-]{1,100}")
    if len(set(ids))!=len(ids) or any(k not in r["reference_ids"] for k in ids):
        raise WorkflowError("事实引用不属于该接收任务。")
    note(p["counterevidence"],"反证或待监控证据")
    note(p["next_action"],"后续动作")
    return {"state":"RETURNED_FOR_REVIEW" if p["kind"]=="return_for_evidence" else "MONITOR_LOGGED",
            "source_task_id":p["source_task_id"],"receiver_task_id":p["receiver_task_id"],
            "fact_refs":ids,"counterevidence":p["counterevidence"],"next_action":p["next_action"],
            "source_state_preserved":r["own_review_state"],"unblocked_source":False}


def blob(x):
    return (json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def receipt(result,raw,report):
    return {**{k:result[k] for k in ("task_id","tool_id","site","proposal_id","version","upstream","input_sha256")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "business_execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked=None):
    if assets or linked is None:raise WorkflowError("竞品交接须使用已经绑定的本机证据。")
    c,pins=linked
    policy=_load(project,"research_handoff_state")
    body=produce(policy,p,c) if tool==PROPOSE else feedback(p,c) if tool==FEEDBACK else consume(policy,tool,p,c)
    a={**body,**{k:p[k] for k in BASE.split()},"task_id":folder.name,"tool_id":tool,
       "upstream":pins,"input_sha256":sha256(blob(p)).hexdigest(),"release_status":"not_authorized",
       "model_called":False,"provider_called":False,"execution_authorized":False}
    raw=blob(a)
    report=(f"# {site} · {p['proposal_id']} v{p['version']} · {a['state']}\n\n"
            "本机合成证据交接，真实业务未审批。\n\n"+raw.decode("utf-8")).encode("utf-8")
    files={k:folder/name for k,name in FILES.items()}
    for k,x in {"json":raw,"report":report,"receipt":blob(receipt(a,raw,report))}.items():
        with files[k].open("xb") as f:f.write(x)
    return a,files


def verify_saved(task,b):
    a=task["result"]
    if (task["local_files"]!=FILES or a!=json.loads(b["json"]) or
            (a["task_id"],a["site"],a["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(b["receipt"])!=receipt(a,b["json"],b["report"])):
        raise WorkflowError("竞品交接结果、编号或回执不一致。")


def public_tools():
    names={PROPOSE:"竞品研究提案",FEEDBACK:"竞品证据回灌",**{k:v for k,v in
      zip(ROLE_ROUTES,("选品接收竞品","编导素材接收竞品","广告接收竞品"))}}
    return [{"id":id,"role_suffix":r,"sites":list(ROUTES),"label":names[id],
             "description":"只在本机核对本站合成竞品证据和版本。","input_example":{}}
            for id,r in TOOLS.items()]
