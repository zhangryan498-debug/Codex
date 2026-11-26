"""Offline pre-release checks and four distinct director handoff receipts."""
import json
from hashlib import sha256

from role_tools import ROUTES,SYNTHETIC
from workflow_director_topic import TOOL as TOPIC
from workflow_lead_weekly import encode,term
from workflow_live_rehearsal import amount,check,identify,stamp,task_id

PREFLIGHT="director-offline-preflight"
ROLES={
    "visual-director-intake":("visual-designer",("materials","ai_label"),"拟封面及字幕层级，待独立核视觉和素材权利。"),
    "shop-director-intake":("shop-ops",("price","materials","compliance"),"核同款商品页、承接与发布商品硬关联。"),
    "ad-director-intake":("ad-buyer",("price","efficacy","compliance","ai_label"),"仅设计同条件单变量测试，缺权益、预算或授权不投放。"),
    "live-director-intake":("live-ops",("efficacy","price","materials","compliance"),"对照同款货盘准备核事实话本，缺资格不承诺。"),
}
TOOLS=(PREFLIGHT,*ROLES)
FILES={"json":"director-preflight.json","report":"director-preflight.md","receipt":"director-preflight-receipt.json"}
FIELDS=("efficacy","price","compliance","materials")
CONTEXT=("grantor","grantee","region","placement")
METHODS={"action_closeup":"用自制机位和新道具，从手部动作的开始拍到完成，再用另一个自拍角度展示结果。",
         "before_after":"用同一自有机位分别记录整理前后，保持构图和照明一致，并避免使用研究原音画。"}
COMMON="site scope currency job_id packet_id version as_of"


class WorkflowError(ValueError):
    pass


def request_key(p):
    return f"{p['packet_id']}:{p['version']}"


def _sha(s):
    identify(s,"SHA256",r"[a-f0-9]{64}")


def _time_scope(row,at):
    start,end=(stamp(row[k]) for k in ("from_at","until"))
    if start>=end:
        raise WorkflowError("合成依据的生效和到期顺序不符。")
    return start<=at<end


def _subject(scope,kind):
    if scope is None:
        return None
    check(scope,"asset_id sha256","独立媒体编号和哈希")
    identify(scope["asset_id"],kind)
    _sha(scope["sha256"])
    return scope


def compute_rights(p,at,canonical_video_id):
    check(p["use"]," ".join(CONTEXT),"素材使用范围")
    for key in CONTEXT:
        identify(p["use"][key],key)
    assets={"video":_subject(p["video"],"视频"),"audio":_subject(p["audio"],"音轨")}
    if not assets["video"]:
        raise WorkflowError("须显式提供视频分析对象。音轨未知可留空。")
    rows=p["rights"]
    if not isinstance(rows,list) or len(rows)>30:
        raise WorkflowError("合成许可列表最多 30 条。")
    ids=set()
    for row in rows:
        check(row,"id kind subject grantor grantee region placement from_at until status source_ref","独立许可")
        identify(row["id"],"许可编号")
        if row["id"] in ids or row["kind"] not in assets or row["status"] not in ("allowed_fixture","denied_fixture"):
            raise WorkflowError("许可编号须唯一，并明确视频或音轨及合成授权状态。")
        ids.add(row["id"])
        if _subject(row["subject"],row["kind"]) is None:
            raise WorkflowError("许可须有独立媒体主体。")
        for key in CONTEXT:
            identify(row[key],key)
        term(row["source_ref"],"合成许可出处")
        _time_scope(row,at)
    out={}
    for kind,asset in assets.items():
        canonical=(kind!="video" or asset["asset_id"]==canonical_video_id)
        matched=[r for r in rows if canonical and r["kind"]==kind and r["subject"]==asset and
                 all(r[k]==p["use"][k] for k in CONTEXT) and _time_scope(r,at)]
        state="BLOCKED" if any(r["status"]=="denied_fixture" for r in matched) else (
             "MATCHED_SYNTHETIC_SCOPE" if matched else "PENDING_EVIDENCE")
        out[kind]={"state":state,"matched_fixture_ids":[r["id"] for r in matched],"asset":asset,
                   "region":p["use"]["region"],"placement":p["use"]["placement"],"grantee":p["use"]["grantee"],
                   "actual_rights_verified":False,
                   "identity_basis":"local_original_task_and_sha256" if kind=="video" and canonical
                        else "unbound_video_alias" if kind=="video" else "caller_supplied_synthetic_audio_metadata"}
    states={v["state"] for v in out.values()}
    return {**out,"full_pack_state":"BLOCKED" if "BLOCKED" in states else (
        "PENDING_EVIDENCE" if "PENDING_EVIDENCE" in states else "MATCHED_SYNTHETIC_SCOPE"),
        "published_or_copied":False,"inheritable":False}


def _fact_types(kind,value):
    if value is None:
        raise WorkflowError("已提出的声明需填实际审稿文字或金额，未知依据应留空。")
    if kind=="price":
        amount(value,"同币买家价（分或分币）",0,10**10,False)
    else:
        term(value,"审稿文本")


def _checks(p,at):
    check(p["claims"]," ".join(FIELDS),"功效、价格、原料和合规")
    check(p["facts"]," ".join(FIELDS),"各审稿事实的冻结来源")
    out={}
    for kind in FIELDS:
        item,register=p["claims"][kind],p["facts"][kind]
        if item is None:
            out[kind]={"state":"NOT_ASSERTED","claim":None,"evidence":None}
            continue
        _fact_types(kind,item)
        state="PENDING_EVIDENCE"
        if register is not None:
            check(register,"site sku_id revision currency value source_ref from_at until","冻结的审稿事实")
            if (register["site"],register["sku_id"],register["revision"],register["currency"])!=(
                p["site"],p["sku_id"],p["subject_revision"],p["currency"]):
                raise WorkflowError("审稿事实须来自本站同一 SKU、修订及原币。")
            _fact_types(kind,register["value"])
            term(register["source_ref"],"审稿事实出处")
            if _time_scope(register,at):
                state="SUPPORTED_FIXTURE_ONLY" if register["value"]==item else "BLOCKED"
        out[kind]={"state":state,"claim":item,"evidence":register,"assertible_as_actual":False}
    ai=p["ai"]
    check(ai,"used label source_ref","AI 使用、依据与标注")
    if ai["used"] is not None and type(ai["used"]) is not bool:
        raise WorkflowError("AI 使用须为是、否或未知。")
    for k in ("label","source_ref"):
        if ai[k] is not None:term(ai[k],k)
    state=("PENDING_EVIDENCE" if ai["used"] is None or ai["source_ref"] is None else "NOT_APPLICABLE"
           if ai["used"] is False else "REVIEW_LABEL_FIXTURE" if ai["label"] else "BLOCKED")
    out["ai_label"]={"state":state,"record":ai,"actual_platform_label_verified":False}
    return out


def _header(site,p):
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("只使用本站本币合成材料与本机回执。")
    for k in ("job_id","packet_id"):
        identify(p[k],k)
    amount(p["version"],"编导包版本",1,10**6,False)
    term(p["owner"],"责任人")
    return stamp(p["as_of"])


def bind(site,tool,p,fetch,media,history):
    producer="owner deadline sku_id subject_revision topic_task_id topic_id clip_task_id video audio use rights claims facts ai method_id publication_hint previous_task_id"
    receiver="owner recipient source_task_id"
    check(p,COMMON+" "+(producer if tool==PREFLIGHT else receiver),"编导前置自检和本机接收")
    at=_header(site,p)
    latest=max((r["version"] for r in history(site,p["packet_id"],PREFLIGHT)),default=0)
    if tool!=PREFLIGHT:
        if p["recipient"]!=ROUTES[site][0]+"-"+ROLES[tool][0]:
            raise WorkflowError("接收入口与本站岗位并不匹配。")
        task_id(p["source_task_id"],"前置自检原任务")
        source,pin=fetch(p["source_task_id"],site,PREFLIGHT)
        if ((source["packet_id"],source["version"],latest)!=(p["packet_id"],p["version"],p["version"])
                or not stamp(source["as_of"])<=at<stamp(source["deadline"])):
            raise WorkflowError("收件必须绑定当前同号、同站和有效期。")
        return {"preflight":pin},source
    if p["version"]<latest or p["version"]>latest+1 or stamp(p["deadline"])<=at:
        raise WorkflowError("旧版只可取回，提交连续修订且有效期须晚于审稿。")
    identify(p["sku_id"],"SKU");identify(p["topic_id"],"方案主题")
    amount(p["subject_revision"],"商品事实修订",1,10**6,False)
    task_id(p["topic_task_id"],"已存本站方案")
    task_id(p["clip_task_id"],"原始合成素材记录")
    source,pin=fetch(p["topic_task_id"],site,TOPIC)
    topic=next((x for x in source["topics"] if x["id"]==p["topic_id"]),None)
    if ((source["sku_id"],source["revision"])!=(p["sku_id"],p["subject_revision"])
            or not topic or not topic["input_ready"] or stamp(source["as_of"])>at):
        raise WorkflowError("须有同站、同 SKU、同版且已就绪的本机选题。")
    original,clip=media(p["clip_task_id"],site)
    video=_subject(p["video"],"视频")
    if not video or not original["synthetic_material"] or clip["media_sha256"]!=video["sha256"]:
        raise WorkflowError("审阅的视频须与原拆解身份和字节一致。")
    canonical_id="CLIP-"+clip["task_id"]
    rights=compute_rights(p,at,canonical_id)
    if p["method_id"] not in METHODS:
        raise WorkflowError("只接收独立通用手法编号；不随包附带研究影音或文字。")
    if p["publication_hint"] is not None:
        identify(p["publication_hint"],"尚未证实的商品 ID")
    check_data=_checks(p,at)
    pins={"topic":pin,"clip":clip}
    if p["version"]==1 and p["previous_task_id"] is not None:
        raise WorkflowError("首版不能声明历史修订。")
    if p["version"]>1:
        task_id(p["previous_task_id"],"同号上一版")
        previous,pins["previous"]=fetch(p["previous_task_id"],site,PREFLIGHT)
        if ((previous["packet_id"],previous["version"]+1,previous["sku_id"],previous["subject_revision"])!=
            (p["packet_id"],p["version"],p["sku_id"],p["subject_revision"]) or stamp(previous["as_of"])>=at):
            raise WorkflowError("修订须沿用原编号、同商品及此前已保存版本。")
    return pins,(rights,check_data,topic,canonical_id)


def _summary(rights,checks):
    states=[c["state"] for c in checks.values()]+[rights["full_pack_state"]]
    return "BLOCKED" if "BLOCKED" in states else "PENDING_EVIDENCE" if "PENDING_EVIDENCE" in states else "SYNTHETIC_CHECK_COMPLETE"


def compose(site,tool,p,c):
    if tool!=PREFLIGHT:
        need=ROLES[tool][1]
        action=ROLES[tool][2]
        moment=stamp(p["as_of"])
        canonical_id="CLIP-"+c["upstream"]["clip"]["task_id"]
        active_scope=compute_rights(c["rights_request"],moment,canonical_id)
        checks=_checks(c["checks_request"],moment)
        current=_summary(active_scope,checks)
        states=("SYNTHETIC_CHECK_COMPLETE","PENDING_EVIDENCE","BLOCKED")
        state=max((c["state"],current),key=states.index)
        needed=set(c["requires_evidence"])
        needed.update(k for k,v in checks.items() if v["state"] in ("BLOCKED","PENDING_EVIDENCE"))
        if active_scope["full_pack_state"]!="MATCHED_SYNTHETIC_SCOPE":
            needed.add("independent_video_and_audio_licensing")
        return {"state":state,"upstream_state":c["state"],"recipient":p["recipient"],"source_task_id":p["source_task_id"],
          "facts":{k:checks[k] for k in need},"rights":active_scope,"technique":c["technique"],
          "next_action":action,"requires_evidence":sorted(needed),"actual_publish_product_id":None,
          "authority":"offline_assignment_receipt","deadline":c["deadline"],
          "sku_id":c["sku_id"],"topic_id":c["topic_id"],"subject_revision":c["subject_revision"],
          "own_product_identity_confirmed":False,"blocked_action_ids":c["blocked_action_ids"],
          "source_topic_plan":c.get("source_topic_plan"),"research_clip_may_establish_own_product":False}
    rights,checks,topic,canonical_id=c
    projection={k:topic[k] for k in ("title","route","locale","goal","content_form","search_terms")}
    projection["topic_variants"]=[{k:v[k] for k in ("id","entry","narrative","cta")} for v in topic["variants"]]
    projection["search_term_state"]="DRAFT_ONLY" if projection["search_terms"] else "NO_VERIFIED_TERMS_OR_HASHTAGS"
    projection["actual_search_popularity"]=None
    pending=[k for k,v in checks.items() if v["state"] in ("PENDING_EVIDENCE","BLOCKED")]
    if rights["full_pack_state"]!="MATCHED_SYNTHETIC_SCOPE":pending.append("independent_video_and_audio_licensing")
    pending+=["own_listing_link"]+(["native_review","cod_and_local_policy_unverified"] if site=="PH" else [])
    return {"state":_summary(rights,checks),"rights":rights,"checks":checks,"topic_id":p["topic_id"],
     "sku_id":p["sku_id"],"subject_revision":p["subject_revision"],"deadline":p["deadline"],
     "requires_evidence":pending,"blocked_action_ids":["actual_publish","transfer_media","claim_proven_efficacy","grant_budget"],
     "requires_independent_native_review":site=="PH","actual_publish_product_id":None,
     "unverified_listing_hint":p["publication_hint"],"predecessor_task_id":p["previous_task_id"],
     "canonical_reference_video_id":canonical_id,"source_topic_plan":projection,
     "research_clip_may_establish_own_product":False,"actual_audio_file_identity_verified":False,
     "technique":{"text":METHODS[p["method_id"]],"state":"INDEPENDENT_TEXT_TECHNIQUE","asset_attachments":[],
                 "original_audio_copied":False,"original_video_copied":False},
     "rights_request":{k:p[k] for k in ("use","video","audio","rights")},
     "checks_request":{k:p[k] for k in ("claims","facts","ai","site","sku_id","subject_revision","currency")}}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","tool_id","site","packet_id","version","upstream","input_sha256","state")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("不得在审稿交接中再次附带原片、原音或待授权素材。")
    pins,context=linked
    r={**compose(site,tool,p,context),**{k:p[k] for k in (*COMMON.split(),"owner")},
       "task_id":folder.name,"tool_id":tool,"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(r)
    report=(f"# {site} · {r['packet_id']} v{r['version']} · {r['state']}\n\n"
            "本机模拟自检和收件。真实权利、发布、投放及实物未经验证。\n\n"+raw.decode()).encode()
    paths={k:folder/v for k,v in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(v)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or json.loads(files["json"])!=r or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("编导审稿、收件或来源回执无法核验。")


def public_tools():
    labels=("编导合规自检","视觉接收编导","商城接收编导","广告接收编导","直播接收编导")
    roles=("video-director",*(x[0] for x in ROLES.values()))
    return [{"id":tool,"role_suffix":role,"sites":list(ROUTES),"label":label,
             "description":"本站合成版权、商品声明、AI 标注和独立收件。","input_example":{}}
            for tool,role,label in zip(TOOLS,roles,labels,strict=True)]

