"""Versioned, provenance-bound synthetic teardown element libraries."""
from calendar import monthrange
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode,term
from workflow_live_rehearsal import amount,check,identify,stamp,task_id
from workflow_media_library import DIRECTOR, available
from workflow_teardown import TOOL_ID as TEARDOWN

TOOL="teardown-offline-element-library"
TOOLS=(TOOL,)
FILES={"json":"teardown-library.json","report":"teardown-library.md","receipt":"teardown-library-receipt.json"}
CATEGORIES=("hook","selling_point","demonstration","trust","cta","emotion")
LABELS=dict(zip(CATEGORIES,("钩子","卖点","演示","信任","促单","情绪")))
LOCALIZATION=("direct_with_review","replace_language","replace_culture","structure_only_reshoot")


class WorkflowError(ValueError):
    pass


def three_months(t):
    offset=t.month+2
    year,month=t.year+offset//12,offset%12+1
    return t.replace(year=year,month=month,day=min(t.day,monthrange(year,month)[1]))


def validate(site,p):
    kind=p.get("mode") if isinstance(p,dict) else None
    fields={
        "save":"version previous_task_id update_reason clip_task_id registry_task_id media_sha256 media_version rule entries",
        "find":"lookup_id include_history",
    }.get(kind)
    if fields is None:
        raise WorkflowError("使用分站登记或本机查询。")
    check(p,"mode site currency scope library_id product_id as_of owner "+fields,"拆解要素库")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("仅处理本站、本币的离线合成素材和记录。")
    for k in ("library_id","product_id"):
        identify(p[k],k)
    at=stamp(p["as_of"])
    term(p["owner"],"库维护")
    if kind=="find":
        identify(p["lookup_id"],"查询编号")
        if type(p["include_history"]) is not bool:
            raise WorkflowError("明确是否附历史回执。")
        return
    amount(p["version"],"库版本",1,10**6,False)
    amount(p["media_version"],"引用素材版本",1,10**6,False)
    task_id(p["clip_task_id"],"原拆解回执")
    if p["registry_task_id"] is not None:
        task_id(p["registry_task_id"],"本地素材权利版本")
    identify(p["media_sha256"],"合成原片摘要",r"[a-f0-9]{64}")
    if p["version"]==1:
        if p["previous_task_id"] is not None:
            raise WorkflowError("初版不接受前驱。")
    else:
        task_id(p["previous_task_id"],"上一原版")
    term(p["update_reason"],"登记/变更理由")
    limit=p["rule"]
    check(limit,"account_id metric window_days unit source_ref","可比测试口径")
    identify(limit["account_id"],"本机合成账号")
    if (limit["metric"],limit["unit"],limit["window_days"])!=("views_after_same_7d","views",7):
        raise WorkflowError("本机只对同账号七天观看量作可比模板验证。")
    term(limit["source_ref"],"本轮合成口径")
    if not isinstance(p["entries"],list) or not 1<=len(p["entries"])<=18:
        raise WorkflowError("登记 1 至 18 个具名要素。")
    seen=set()
    for e in p["entries"]:
        check(e,"id category title method mechanism applicable_product_id applicable_site reproducibility forbidden failure_conditions "
              "source localization validation","六类要素与证据")
        ident=e["id"]
        identify(ident,"持续要素编号")
        if ident in seen or e["category"] not in CATEGORIES:
            raise WorkflowError("要素编号唯一，使用原六类。")
        seen.add(ident)
        if (e["applicable_product_id"],e["applicable_site"])!=(p["product_id"],site):
            raise WorkflowError("不可把其他站点、商品或材料带入本站库。")
        for k in ("title","method","mechanism","forbidden","failure_conditions"):
            term(e[k],k,True)
        if e["reproducibility"] not in ("low","medium","high",None):
            raise WorkflowError("复制度缺证时写未知。")
        src=e["source"]
        if src is not None:
            check(src,"basis channel from_ms to_ms frame_id annotation_ref","原片时间码与合成说明")
            if (src["basis"] not in ("planned_fixture","visually_observed_fixture")
                or src["channel"] not in ("offline_design","verified_frame")):
                raise WorkflowError("这里只接受画面引用或合成设计，没有音轨或口播验证。")
            for k in ("from_ms","to_ms"):
                amount(src[k],k,0,3600000)
            if src["frame_id"] is not None:
                identify(src["frame_id"],"原抽帧编号")
            term(src["annotation_ref"],"影像/设计说明",True)
        lo=e["localization"]
        if site=="CN":
            if lo is not None:
                raise WorkflowError("抖音档案不直接复用菲律宾在地结论。")
        elif lo is not None:
            check(lo,"target_site locale difficulty adaptation locale_review_ref","菲律宾本地化证据")
            if lo["target_site"]!="PH" or lo["locale"] not in ("en-PH","fil-PH") or lo["difficulty"] not in (*LOCALIZATION,None):
                raise WorkflowError("只登记菲律宾站的本地化方案。")
            term(lo["adaptation"],"本地化做法",True)
            term(lo["locale_review_ref"],"合成语言确认",True)
        attempts=e["validation"]
        if not isinstance(attempts,list) or len(attempts)>30:
            raise WorkflowError("使用最多 30 条已冻结的合成验证记录。")
        for row in attempts:
            check(row,"application_id site account_id metric unit from_at to_at recorded_at views baseline_median "
                  "state source_ref","合成应用与可比中位数")
            for k in ("application_id","account_id"):
                identify(row[k],k)
            if row["site"] not in ROUTES or row["state"] not in ("verified_fixture","open_fixture"):
                raise WorkflowError("注明应用的原站点和成熟状态。")
            start,end,recorded=(stamp(row[k]) for k in ("from_at","to_at","recorded_at"))
            if not start<end<=recorded<=at:
                raise WorkflowError("应用、截止与归档时点顺序错误。")
            for k in ("views","baseline_median"):
                amount(row[k],k)
            term(row["source_ref"],"合成回执出处",True)


def request_key(p):
    return (f"{p['library_id']}:v{p['version']}" if p["mode"]=="save"
            else f"{p['library_id']}:lookup:{p['lookup_id']}")


def bind(site,tool,p,fetch,registry,source,frame,history_ids):
    validate(site,p)
    ids=history_ids(site,p["library_id"])
    links={};pins={}
    if p["mode"]=="find":
        at=stamp(p["as_of"])
        wrong_product=False
        snapshots=[]
        for ident in ids:
            x,pin=fetch(ident,site,TOOL)
            if x["product_id"]!=p["product_id"]:
                wrong_product=True
                continue
            if stamp(x["as_of"])<=at:
                snapshots.append(x)
                pins[ident]=pin
        if wrong_product:
            raise WorkflowError("该库已绑定另一个商品。")
        return pins,sorted(snapshots,key=lambda x:x["version"],reverse=True)
    prior=None
    if p["previous_task_id"]:
        prior,pins["previous"]=fetch(p["previous_task_id"],site,TOOL)
        if ((prior["mode"],prior["library_id"],prior["product_id"],prior["version"]+1)!=
            ("save",p["library_id"],p["product_id"],p["version"]) or stamp(prior["as_of"])>=stamp(p["as_of"])):
            raise WorkflowError("下版须钉住同一库、商品、上一版和更晚时间。")
        if prior["trial_rule"]!=p["rule"]:
            raise WorkflowError("同库既有验证口径不可在更新后替换。")
        current={x["id"]:x for x in p["entries"]}
        for old in prior["elements"]:
            if old["id"] not in current or old["category"]!=current[old["id"]]["category"]:
                raise WorkflowError("历史编号须显式保留，类别不可漂移。")
            new={encode(r) for r in current[old["id"]]["validation"]}
            if any(encode(r) not in new for r in old["validation"]):
                raise WorkflowError("版本不能移除原应用明细，冲突应保留。")
    e,pins["teardown"]=source(p["clip_task_id"],TEARDOWN,site)
    if not e["synthetic_material"] or e["source_sha256"]!=p["media_sha256"]:
        raise WorkflowError("原拆解的站点、合成材料或文件摘要不符合。")
    frame_ids={row["id"] for row in e["frames"]}
    selected={row["source"]["frame_id"] for row in p["entries"] if row["source"] and row["source"]["frame_id"] in frame_ids}
    for fid in sorted(selected):
        _,pins["frame:"+fid],_=frame(site,p["clip_task_id"],fid)
    r=None
    if p["registry_task_id"]:
        r,pins["registry"]=registry(site,p["registry_task_id"],DIRECTOR)
        if (r["site"],r["product_id"],r["version"],r["source"]["source_task_id"],r["source"]["media_sha256"])!=(
            site,p["product_id"],p["media_version"],p["clip_task_id"],p["media_sha256"]
        ) or any(r["upstream"]["source"][k]!=v for k,v in pins["teardown"].items()):
            raise WorkflowError("登记权利不能跨素材、商品、版本、原回执或站点。")
        if stamp(r["as_of"])>stamp(p["as_of"]):
            raise WorkflowError("登记权利晚于归档时点，不能回填。")
    links.update(teardown=e,registry=r,prior=prior)
    return pins,links


def normalized_validations(rows,rule,site,created):
    by_id={};dup=0
    for x in rows:
        key=x["application_id"]
        group=by_id.setdefault(key,{})
        raw=encode(x)
        dup+=raw in group
        group[raw]=x
    checked=[];tail=0;last=None;confirmed=0;pending=0
    ordered=sorted(by_id.items(),key=lambda kv:(min(stamp(v["to_at"]) for v in kv[1].values()),kv[0]))
    for key,group in ordered:
        sample=list(group.values());r=sample[0]
        if len(sample)>1:
            state="CONFLICT"
        elif (r["site"],r["account_id"],r["metric"],r["unit"])!=(site,rule["account_id"],rule["metric"],rule["unit"]) or (
            stamp(r["to_at"])-stamp(r["from_at"])!=timedelta(days=rule["window_days"]) or stamp(r["from_at"])<created
        ):
            state="OUTSIDE_COMPARABLE_FIXTURE"
        elif r["state"]!="verified_fixture" or not r["source_ref"] or None in (r["views"],r["baseline_median"]):
            state="PENDING_VERIFICATION"
        else:
            state="NOT_ABOVE_BASELINE" if r["views"]<=r["baseline_median"] else "ABOVE_BASELINE"
        if state in ("CONFLICT","PENDING_VERIFICATION"):
            pending+=1
            tail=0
        elif state!="OUTSIDE_COMPARABLE_FIXTURE":
            confirmed+=1
            last=max(last,stamp(r["recorded_at"])) if last else stamp(r["recorded_at"])
            tail=tail+1 if state=="NOT_ABOVE_BASELINE" else 0
        checked.append({"application_id":key,"state":state,"raw":sample})
    return {"comparable_verified_count":confirmed,"pending_count":pending,"trailing_not_above":tail,
            "duplicates_ignored":dup,"last_verified_at":last.isoformat() if last else None,"audit":checked}


def life(e,at):
    h=e["trial_audit"]
    since=stamp(h["last_verified_at"] or e["first_registered_at"])
    boundary=three_months(since)
    if at>=boundary:
        state="ARCHIVED_NO_VERIFICATION"
    elif h["trailing_not_above"]>=5:
        state="DOWNGRADED_FIVE_NOT_ABOVE"
    elif h["pending_count"]:
        state="VERIFICATION_PENDING"
    else:
        state="ACTIVE_RECORD"
    return {"state":state,"archive_at":boundary.isoformat(),"evaluated_at":at.isoformat(),
            "history_retained":True}


def evidence_for(site,p,row,clip,rights):
    missing=[k for k in ("title","method","mechanism","forbidden","failure_conditions","reproducibility") if not row[k]]
    if not rights or available(rights,p["as_of"],"director_reference")!="allowed_offline_reference":
        missing.append("rights")
    s=row["source"];frm=None;clip_ok=False
    if s is None:
        missing.append("source")
    elif s["from_ms"] is None or s["to_ms"] is None or not (
        0<=s["from_ms"]<s["to_ms"]<=round(clip["duration_seconds"]*1000)
    ):
        missing.append("source_timecode")
    else:
        cand=[f for f in clip["frames"] if f["id"]==s["frame_id"]]
        frm=cand[0] if len(cand)==1 and s["from_ms"]<=cand[0]["actual_seconds"]*1000<s["to_ms"] else None
        clip_ok=bool(frm)
        if not (frm and s["annotation_ref"] and (s["basis"],s["channel"]) in (
            ("planned_fixture","offline_design"),("visually_observed_fixture","verified_frame")
        )):
            missing.append("source_channel_annotation_or_frame")
    if site=="PH":
        loc=row["localization"]
        if not loc or not all(loc[k] for k in ("difficulty","adaptation","locale_review_ref")):
            missing.append("localization")
    return missing,{"original_task_id":p["clip_task_id"],"original_sha256":p["media_sha256"],"media_version":p["media_version"],
                    "verified_frame":frm,"timecode_bound_to_frame":clip_ok,
                    "visual_record_is_fixture_only":True,"spoken_claim_verified":False,"physical_product_verified":False}


def archive_save(site,p,links):
    clip=links["teardown"];record=links["registry"];prior=links["prior"]
    current=stamp(p["as_of"])
    prev={r["id"]:r for r in prior["elements"]} if prior else {}
    rows=[]
    for src in p["entries"]:
        first=prev[src["id"]]["first_registered_at"] if src["id"] in prev else p["as_of"]
        tr=normalized_validations(src["validation"],p["rule"],site,stamp(first))
        missing,ev=evidence_for(site,p,src,clip,record)
        row={**src,"first_registered_at":first,"trial_audit":tr,"provenance":ev,
             "metadata_state":"REFERENCE_METADATA_READY" if not missing else "PENDING_EVIDENCE",
             "missing_fields":missing,"automatic_cross_site_reuse":False,
             "asset_reuse_or_publication_authorized":False,"viral_effect_verified":False}
        row["lifecycle"]=life(row,current)
        row["next_evidence_request"]=(None if not missing else
            "补原片/抽帧时间、合成标注或权利用途和有效期；菲律宾另补本地化依据。")
        rows.append(row)
    rights_state=available(record,p["as_of"],"director_reference") if record else "rights_pending"
    return {"state":"ELEMENT_VERSION_RECORDED","version":p["version"],"previous_task_id":p["previous_task_id"],
            "update_reason":p["update_reason"],"original_duration_ms":round(clip["duration_seconds"]*1000),
            "rights_state":rights_state,"media_registration":record,
            "registered_rights_scope":"recorded_reference_only","elements":rows,
            "categories_present":[c for c in CATEGORIES if any(r["category"]==c for r in rows)],
            "complete_categories":[c for c in CATEGORIES if any(r["category"]==c and not r["missing_fields"] for r in rows)],
            "trial_rule":p["rule"]}


def query(site,p,history):
    if not history:
        return {"state":"NO_VERSION_AT_CUTOFF","latest_version":None,"current_task_id":None,
                "history":[],"elements":[],"rights_state":"rights_pending"}
    src=history[0];r=src["media_registration"]
    rights=available(r,p["as_of"],"director_reference") if r else "rights_pending"
    rows=[]
    cutoff=stamp(p["as_of"])
    for row in src["elements"]:
        missing=[x for x in row["missing_fields"] if x!="rights"]
        if rights!="allowed_offline_reference":
            missing.append("rights")
        rows.append({**row,"missing_fields":missing,
                     "metadata_state":"PENDING_EVIDENCE" if missing else "REFERENCE_METADATA_READY",
                     "next_evidence_request":("补到期权利及适用范围。" if rights!="allowed_offline_reference" else row.get("next_evidence_request")),
                     "lifecycle":life(row,cutoff)})
    return {"state":"LIBRARY_QUERY_RECORDED","latest_version":src["version"],"current_task_id":src["task_id"],
            "elements":rows,"rights_state":rights,
            "history":[{k:x[k] for k in ("version","task_id","as_of","previous_task_id")} for x in history] if p["include_history"] else [],
            "expired_rights_never_restored_by_history":True}


def receipt(r,b):
    return {**{k:r[k] for k in ("task_id","site","input_sha256","upstream")},
            "result_sha256":sha256(b["json"]).hexdigest(),"report_sha256":sha256(b["report"]).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("使用本机已核的原拆解与素材登记回执。")
    pins,ls=linked
    val=archive_save(site,p,ls) if p["mode"]=="save" else query(site,p,ls)
    keys=("site","currency","scope","mode","library_id","product_id","as_of","owner")
    r={**{k:p[k] for k in keys},**val,"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,"actual_external_reuse_or_publication":False,
       "model_called":False}
    lines=[f"# {site} 拆解要素库 · {p['library_id']}","","合成素材及模板验证，只供本机参考。",
           f"状态：{r['state']}。权利记录：{r['rights_state']}。"]
    for t in r["elements"]:
        lines.append(f"- {t['id']} · {LABELS[t['category']]} · {t['title'] or '待补名称'} · "
                     f"{t['metadata_state']} · {t['lifecycle']['state']}。")
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
        raise WorkflowError("拆解要素版本或来源回执无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":["CN","PH"],"role_suffix":"video-teardown","label":"拆解六类要素与版本库",
             "description":"复用本站已核本机拆解、素材权利与合成验证来源，保留历史。","input_example":{}}]

