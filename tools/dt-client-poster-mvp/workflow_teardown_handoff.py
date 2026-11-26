"""Site-bound media analysis handoff. Downstream roles own independent local receipts."""
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id
from workflow_teardown_library import TOOL as LIBRARY

SEND = "teardown-offline-four-role-handoff"
DESTINATIONS = {
    "director-teardown-intake": ("video-director", {"hook", "selling_point", "demonstration", "cta", "emotion"},
        "拟原创首镜、分镜及拍摄对照，PH 保留母语复核及本地化前提。", "需另核同 SKU 实物与口播；不可照搬原片或作可发布脚本。"),
    "visual-teardown-intake": ("visual-designer", {"hook", "demonstration", "trust", "emotion"},
        "用原创素材规划封面层级、图文证据槽和可见性。", "需自有图和权利；原帧只溯源，不复制原商品、角度或标识。"),
    "selection-teardown-intake": ("selection", {"selling_point", "demonstration", "trust"},
        "形成待验证的可讲卖点和实物规格问题。", "需核同 SKU、规格、实物、成本和库存；不能据参考画面选定货品。"),
    "ads-teardown-intake": ("ad-buyer", {"hook", "cta", "trust", "emotion"},
        "记录单变量素材假设及需要的对照。", "需自有素材、账户授权、预算和对照；不能断言转化、增量或实际投放。"),
}
TOOLS = (SEND, *DESTINATIONS)
FILES = {"json":"teardown-handoff.json","report":"teardown-handoff.md","receipt":"teardown-handoff-receipt.json"}
BLOCKED_CLAIMS = ("actual_sku_or_physical_property", "authenticity_or_certification", "trademark_or_media_rights",
                  "actual_script_or_citation", "actual_link_or_listing", "causal_effect_or_roi", "price_budget_or_release")
BASE = "site scope currency job_id handoff_id version as_of"


class WorkflowError(ValueError):
    pass


def lookup_key(tool,p):
    if tool==SEND:
        return f"{p['handoff_id']}:{p['version']}"
    return f"{p['handoff_id']}:{p['version']}:{p['operation']}"


def select_ids(ids, label):
    if not isinstance(ids,list) or not 1<=len(ids)<=20 or any(not isinstance(v,str) for v in ids):
        raise WorkflowError(f"{label}须明确要素编号。")
    for v in ids:
        identify(v,label)
    if len(set(ids))!=len(ids):
        raise WorkflowError(f"{label}不可重复。")
    return set(ids)


def validate(site,tool,p):
    send_fields="owner library_id product_id library_task_id library_version deadline purpose element_ids prior_task_id return_task_id"
    target_fields="operation recipient owner source_task_id received_task_id element_ids reason action_due"
    check(p,BASE+" "+(send_fields if tool==SEND else target_fields),"拆解四岗交接")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("拆解交接只用本站、本币、明确合成的原件。")
    for k in ("job_id","handoff_id"):
        identify(p[k],k)
    amount(p["version"],"交接版本",1,10**6,False)
    term(p["owner"],"处理责任人")
    at=stamp(p["as_of"])
    if tool==SEND:
        for k in ("library_id","product_id"):
            identify(p[k],k)
        task_id(p["library_task_id"],"拆解库任务")
        amount(p["library_version"],"要素原件版本",1,10**6,False)
        if p["purpose"] not in ("internal_reference","commercial_reuse_requested"):
            raise WorkflowError("用途须区分本机参考和未授权商业复用。")
        if stamp(p["deadline"])<=at:
            raise WorkflowError("交接时点须早于指定期限。")
        select_ids(p["element_ids"],"来源要素")
        if p["version"]==1:
            if p["prior_task_id"] is not None or p["return_task_id"] is not None:
                raise WorkflowError("首版不应声明历史收件或退回。")
        else:
            task_id(p["prior_task_id"],"同号上一版")
            task_id(p["return_task_id"],"岗位真实退回记录")
    else:
        if p["recipient"]!=ROUTES[site][0]+"-"+DESTINATIONS[tool][0]:
            raise WorkflowError("接收工具必须与本站目标岗位精确匹配。")
        task_id(p["source_task_id"],"当前可取的交接任务")
        if p["operation"]=="receive":
            if any(p[k] is not None for k in ("received_task_id","element_ids","reason","action_due")):
                raise WorkflowError("收件不得预填退回、原因或原收件。")
        elif p["operation"]=="return":
            task_id(p["received_task_id"],"本岗原收件")
            select_ids(p["element_ids"],"精确退回要素")
            term(p["reason"],"退回原因")
            if stamp(p["action_due"])<=at:
                raise WorkflowError("退回需约定下一次核验期限。")
        else:
            raise WorkflowError("只支持独立收件或由收件岗凭原回执退回。")
    return at


def prepare_source(site,p,at,source,lookup,frame):
    if ((source["scope"],source["mode"],source["library_id"],source["product_id"],source["version"])!=
            (SYNTHETIC,"save",p["library_id"],p["product_id"],p["library_version"])
            or lookup["current_task_id"]!=p["library_task_id"]):
        raise WorkflowError("须绑定截至该时点的本站同商品当前拆解归档。")
    rights=source.get("media_registration") or {}
    registered=rights.get("rights") or {}
    state=lookup["rights_state"]
    deadline=registered.get("expires_at")
    expires=min(stamp(p["deadline"]),stamp(deadline)) if deadline else None
    admitted=(state=="allowed_offline_reference" and p["purpose"]=="internal_reference" and expires is not None and at<expires)
    by_id={r["id"]:r for r in lookup["elements"]}
    if not set(p["element_ids"])<=by_id.keys():
        raise WorkflowError("存在未在本版拆解归档的要素编号。")
    positions={}
    facts=[]
    for eid in p["element_ids"]:
        row=by_id[eid]
        provenance=row["provenance"]
        scan=provenance.get("verified_frame")
        obs=row["source"]
        frame_pin=None
        if admitted and scan and obs and provenance.get("timecode_bound_to_frame"):
            public,pin,_blobs=frame(site,provenance["original_task_id"],scan["id"])
            frame_pin={**public,**pin}
            if (frame_pin["frame_sha256"]!=scan["sha256"] or
                    frame_pin["media_sha256"]!=provenance["original_sha256"] or
                    not obs["from_ms"]<=1000*frame_pin["actual_seconds"]<obs["to_ms"]):
                raise WorkflowError("素材摘要、帧时间码或原任务与登记不一致。")
            positions[eid]=frame_pin
        missing=list(row["missing_fields"])
        lifecycle=row["lifecycle"]["state"]
        if lifecycle!="ACTIVE_RECORD":
            missing.append("element_lifecycle_"+lifecycle.lower())
        if not frame_pin:
            missing.append("readable_approved_reference_frame")
        cls=("observed_synthetic_frame" if obs and obs["basis"]=="visually_observed_fixture"
             else "unverified_structure_hypothesis")
        proof={"source_task_id":provenance["original_task_id"],"sha256":provenance["original_sha256"],
               "media_version":provenance["media_version"],"interval_ms":([obs["from_ms"],obs["to_ms"]] if obs else None),
               "frame":frame_pin,"claim_is_proven":False,"physical_fact_is_proven":False}
        facts.append({"id":eid,"category":row["category"],"grade":cls,"title":row["title"],"provenance":proof,
                      "idea":row["method"] if admitted and frame_pin and not missing else None,
                      "reference_available":bool(admitted and frame_pin and not missing),
                      "role_scope":row["forbidden"],"source_gaps":sorted(set(missing)),"lifecycle":lifecycle,
                      "localization":row.get("localization"),"library_trial_summary":row["trial_audit"]})
    return facts,positions,{"state":state,"registry_version":rights.get("version"),
            "reference":registered.get("reference"),"archive_only":not admitted,
            "authorized_internal_reference":admitted,"public_or_commercial_use_authorized":False,
            "ends_at":expires.isoformat() if expires else None}


def bind(site, tool, p, fetch, library_lookup, frame, history):
    at=validate(site,tool,p)
    latest=max((x["version"] for x in history(site,p["handoff_id"],SEND)),default=0)
    if tool==SEND:
        if p["version"]<latest or p["version"]>latest+1:
            raise WorkflowError("交接只允许当前或连续下一版，旧版只可查看。")
        original,library_pin=fetch(p["library_task_id"],site,LIBRARY)
        lookup,history_pins=library_lookup(site,p)
        facts,positions,rights=prepare_source(site,p,at,original,lookup,frame)
        pins={"library":library_pin,"history":history_pins,"reference_frames":positions}
        prior=None
        if p["prior_task_id"]:
            prior,pins["prior"]=fetch(p["prior_task_id"],site,SEND)
            raw=p["return_task_id"]
            returns=[(r,t) for t in DESTINATIONS for r in history(site,p["handoff_id"],t)
                     if r["task_id"]==raw and r.get("operation")=="return"]
            if len(returns)!=1:
                raise WorkflowError("修订须使用同号真退回任务。")
            r,tool_id=returns[0]
            _,f_pin=fetch(raw,site,tool_id)
            pins["return"]=f_pin
            if ((prior["handoff_id"],prior["version"]+1,prior["library_id"],prior["product_id"],prior["owner"],prior["purpose"])!=
                (p["handoff_id"],p["version"],p["library_id"],p["product_id"],p["owner"],p["purpose"])
                or prior["task_id"]!=r["source_task_id"] or not stamp(prior["as_of"])<=stamp(r["as_of"])<at
                or p["library_version"]<=prior["library_version"] or not set(r["returned_elements"])<=set(p["element_ids"])):
                raise WorkflowError("修订须沿用原身份、责任人、退回要素和更新的可核版本。")
        return pins,(facts,rights,prior)
    source,source_pin=fetch(p["source_task_id"],site,SEND)
    if (source["handoff_id"],source["version"],latest)!=(p["handoff_id"],p["version"],p["version"]):
        raise WorkflowError("禁止使用旧版或其他交接编号。")
    if not stamp(source["as_of"])<=at<stamp(source["deadline"]):
        raise WorkflowError("收件或退回晚于交接期限或早于发件。")
    rights=source["rights"]
    pins={"handoff":source_pin}
    if not rights["authorized_internal_reference"] or at>=stamp(rights["ends_at"]):
        raise WorkflowError("该资料无当前本机参考权利或权利已到期。")
    view={**p,"library_id":source["library_id"],"product_id":source["product_id"]}
    lookup,pins["library_history"]=library_lookup(site,view)
    if (lookup["current_task_id"]!=source["library_task_id"] or
            lookup["rights_state"]!="allowed_offline_reference"):
        raise WorkflowError("已出现更新的拆解归档，须先复核本交接编号。")
    if p["operation"]=="return":
        original,pins["received"]=fetch(p["received_task_id"],site,tool)
        refs={x["id"] for x in original.get("facts",[])}
        if (original["operation"]!="receive" or original["source_task_id"]!=source["task_id"]
                or original["upstream"]["handoff"]!=source_pin or (original["recipient"],original["owner"])!=(p["recipient"],p["owner"])
                or not stamp(original["as_of"])<=at or not select_ids(p["element_ids"],"退回要素")<=refs):
            raise WorkflowError("退回必须来自该岗位自己的本版真实收件及其证据。")
    return pins,source


def compose(tool,p,context):
    if tool==SEND:
        facts,rights,prior=context
        routes={}
        for target,(role,categories,allowed,needed) in DESTINATIONS.items():
            selected=[r for r in facts if r["category"] in categories]
            ready=any(x["reference_available"] for x in selected)
            routes[target]={"recipient":ROUTES[p["site"]][0]+"-"+role,"facts":selected,
                            "allowed_internal_use":allowed if ready else None,
                            "evidence_needed":needed,"prohibited":list(BLOCKED_CLAIMS),
                            "status":"REFERENCE_PACKET_READY" if ready else "PENDING_EVIDENCE"}
        return {"state":"SENT_FOR_LOCAL_REVIEW" if rights["authorized_internal_reference"] and all(
                            x["status"]=="REFERENCE_PACKET_READY" for x in routes.values()) else "HELD_REFERENCE_ONLY",
                "library_id":p["library_id"],"library_task_id":p["library_task_id"],"product_id":p["product_id"],"sku_id":None,
                "library_version":p["library_version"],"deadline":p["deadline"],"rights":rights,"purpose":p["purpose"],
                "routes":routes,"prior_task_id":p["prior_task_id"],"return_task_id":p["return_task_id"],
                "returned_issue":"RECIPIENT_RECHECK_REQUIRED" if prior else None}
    source=context
    package=source["routes"][tool]
    result={"source_task_id":source["task_id"],"recipient":p["recipient"],"product_id":source["product_id"],
            "sku_id":None,"library_version":source["library_version"],"rights":source["rights"],"operation":p["operation"]}
    if p["operation"]=="return":
        return {**result,"state":"RETURNED_FOR_EVIDENCE","reason":p["reason"],"returned_elements":p["element_ids"],
                "received_task_id":p["received_task_id"],"action_due":p["action_due"],"unblocked":False}
    return {**result,**package,"state":"RECEIVED_REFERENCE_ONLY" if source["state"]=="SENT_FOR_LOCAL_REVIEW" and
            package["status"]=="REFERENCE_PACKET_READY" else "RECEIVED_PENDING_EVIDENCE"}


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","tool_id","site","handoff_id","version","upstream","input_sha256","state")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "business_execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("这里仅引用本机已保存的拆解原件。")
    pins,context=linked
    r={**compose(tool,p,context),**{k:p[k] for k in BASE.split()},"owner":p["owner"],
       "task_id":folder.name,"tool_id":tool,"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "provider_called":False,"model_called":False,"execution_authorized":False,"production_asset_copied":False}
    raw=encode(r)
    heading=f"# {site} · {p['handoff_id']} v{p['version']} · {r['state']}\n\n"
    report=(heading+"内部合成证据。权限仅按原登记，实物、商业复用、上架和投放仍未验证。\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,data in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(data)
    return r,paths


def verify_saved(task, files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("拆解交接原件与岗位回执不符。")


def public_tools():
    labels=("拆解四岗发件","编导收件与退回","美工收件与退回","选品收件与退回","广告收件与退回")
    roles=("video-teardown",*(r[0] for r in DESTINATIONS.values()))
    return [{"id":tool,"role_suffix":role,"sites":list(ROUTES),"label":"拆解 · "+label,
             "description":"本机引用本站拆解原件、要素和权利。","input_example":{}}
            for tool,role,label in zip(TOOLS,roles,labels,strict=True)]

