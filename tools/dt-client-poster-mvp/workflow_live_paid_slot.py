"""Join a local clip and paid slot to an immutable, same-window rehearsal ledger."""

from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, ratio, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL="live-paid-slot-evidence"
FILES={"json":"paid-slot.json","report":"paid-slot.md","receipt":"paid-slot-receipt.json"}


def artifact_names(has_video):
    return {**FILES,"video":"local-paid-slot-clip.mp4"} if has_video else FILES
KEYS=("site","currency","account_id","session_id","sku_id","revision")


class WorkflowError(ValueError):
    pass


def validate(site,p):
    check(p,"site currency scope account_id session_id sku_id revision plan_id as_of control_task_id "
            "material slot budget cpl","直播内投流证据")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("仅支持本站同币种合成资料。")
    for k in ("account_id","session_id","sku_id","plan_id"):
        identify(p[k],k)
    amount(p["revision"],"SKU 修订",1,1000000,False)
    task_id(p["control_task_id"],"费用及边际回执")
    root=tuple(p[k] for k in KEYS)
    for k,suffix in (
            ("material","filename sha256 use_scope source_ref"),
            ("slot","start end source_ref"),
            ("budget","as_of source_ref cap_minor spent_minor committed_minor")):
        row=p[k]
        check(row," ".join(KEYS)+" "+suffix,k)
        if tuple(row[x] for x in KEYS)!=root:
            raise WorkflowError("素材、档期和预算须同账号、场次、SKU、版本和站点。")
        term(row["source_ref"],"合成来源",True)
    identify(p["material"]["filename"],"素材文件名",r"[A-Za-z0-9_-]{1,60}\.mp4")
    identify(p["material"]["sha256"],"素材文件哈希",r"[a-f0-9]{64}")
    if p["material"]["use_scope"] not in ("synthetic_offline_reference",None):
        raise WorkflowError("本轮只使用合成离线素材权利。")
    a,b,at=(stamp(v) for v in (p["slot"]["start"],p["slot"]["end"],p["as_of"]))
    if not a<b<=at or (a.date(),b.date())!=(at.date(),at.date()) or stamp(p["budget"]["as_of"])!=at:
        raise WorkflowError("排品、账本和评估须在同一本地日期及同截点。")
    for k in ("cap_minor","spent_minor","committed_minor"):
        amount(p["budget"][k],k)
    if p["cpl"] is not None:
        if site!="PH":raise WorkflowError("菲律宾 CPL 合成样本不得跨站。")
        c=p["cpl"]
        check(c,"account_id site as_of channel confirmed_rows total_rows source_ref","CPL 合成比例")
        if (c["site"],c["account_id"],stamp(c["as_of"]))!=(site,p["account_id"],at) or c["channel"] not in ("video","live"):
            raise WorkflowError("CPL 须是本站同账号同截点及明确渠道。")
        for k in ("confirmed_rows","total_rows"):
            amount(c[k],k)
        if None not in (c["confirmed_rows"],c["total_rows"]) and c["confirmed_rows"]>c["total_rows"]:
            raise WorkflowError("合成 CPL 分子不得超过同渠道分母。")
        term(c["source_ref"],"CPL 合成样本源",True)
    return at


def bind(site,p,load):
    at=validate(site,p)
    control,pin,original=load(p["control_task_id"],site)
    observed=original["finance_case"]["budget"]
    inc=original["increase"]
    if ((control["site"],control["currency"],control["session_id"],control["sku_id"],control["revision"],
          stamp(control["evaluated_at"])) != (site,p["currency"],p["session_id"],p["sku_id"],p["revision"],at)
            or not inc or tuple(stamp(p["slot"][k]) for k in ("start","end")) != tuple(stamp(inc[k]) for k in ("start","end"))):
        raise WorkflowError("边际证据须对齐同场同 SKU 同段及截点。")
    return {"control":pin},control,observed,inc


def verify_upload(p,media):
    if len(media)>1 or (media and (media[0]["name"],media[0]["sha256"]) !=
                    (p["material"]["filename"],p["material"]["sha256"])):
        raise WorkflowError("提交文件与声明素材名称或 SHA-256 不符。")


def receipt(r,raw,report,media):
    return {**{k:r[k] for k in ("task_id","site","tool_id","input_sha256","upstream","plan_id")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "clip_download_sha256":sha256(media).hexdigest() if media is not None else None,"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    pins,source,recorded,inc=linked
    local=assets.get(p["material"]["filename"])
    media=local.read_bytes() if local else None
    if media is not None and sha256(media).hexdigest()!=p["material"]["sha256"]:
        raise WorkflowError("取件素材哈希与提交不符。")
    good_material=bool(media and p["material"]["source_ref"] and p["material"]["use_scope"]=="synthetic_offline_reference")
    supplied={k:p["budget"][k] for k in ("cap_minor","spent_minor","committed_minor")}
    known=p["budget"]["source_ref"] and None not in supplied.values()
    matched=bool(known and supplied==recorded)
    actual_budget_headroom=(supplied["cap_minor"]-supplied["spent_minor"]-supplied["committed_minor"]) if matched else None
    reasons=[]
    if not good_material:reasons.append("material_or_use_scope_unverified")
    if not p["slot"]["source_ref"]:reasons.append("slot_source_unverified")
    if not matched:reasons.append("booking_budget_or_commitment_unverified")
    if source["state"]!="propose_increase" or not inc["matched_basis"] or not inc["mature"]:
        reasons.append("no_supported_increment_in_saved_rehearsal")
    e=p["cpl"]
    cpl=None
    if site=="PH":
        share=ratio(e["confirmed_rows"],e["total_rows"]) if e and e["source_ref"] else None
        cpl={"source":e,"synthetic_qualified_sample_fraction":share,
             "channel":e["channel"] if e else None,"actual_ticket":None,"actual_live_status":None,
             "historical_attribution":None}
    candidate=source["proposed_increment_minor"] if not reasons and actual_budget_headroom is not None else 0
    ready=bool(candidate and 0<candidate<=actual_budget_headroom)
    r={"state":"ALIGNED_OFFLINE" if ready else "HOLD","site":site,"currency":p["currency"],"scope":SYNTHETIC,
       "task_id":folder.name,"tool_id":tool,"plan_id":p["plan_id"],"as_of":p["as_of"],
       "input_sha256":sha256(encode(p)).hexdigest(),"upstream":pins,
       "binding":{k:p[k] for k in KEYS},"slot":p["slot"],
       "clip":{"sha256":p["material"]["sha256"] if media is not None else None,
               "filename":p["material"]["filename"] if media is not None else None,
               "bytes":len(media) if media is not None else None,"local_synthetic_only":True,
               "claim":p["material"]},
       "input_budget":p["budget"],"verified_recorded_booking":recorded,"budget_bound_to_source":matched,
       "booking_headroom_minor":actual_budget_headroom,
       "mature_history_ad_cost_minor":source["financial_observations"]["old_sku_ledger"]["components"]["ad_spend_minor"],
       "historical_attribution_is_current_effect":False,
       "controlled_fixture_lower_profit_minor":source["incremental_profit_lower_minor"],
       "simulated_draft_increment_minor":candidate if ready else 0,"hold_reasons":reasons,
       "cpl_sample":cpl,"actual_paid_business_result":None,"spending_executed":False,
       "platform_permission_verified":False,"execution_authorized":False}
    raw=encode(r)
    report=(f"# {site} 合成投流证据 · {p['plan_id']}\n\n结论：{r['state']}。实际效果未知。\n\n"+raw.decode()).encode()
    content={"json":raw,"report":report,"receipt":encode(receipt(r,raw,report,media))}
    if media is not None:
        content["video"]=media
    files={k:folder/n for k,n in artifact_names(media is not None).items()}
    for k,b in content.items():
        with files[k].open("xb") as f:f.write(b)
    return r,files


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=artifact_names(r["clip"]["sha256"] is not None) or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["paid_slot_pins"]
       or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
       or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"],files.get("video"))):
        raise WorkflowError("投流本机证据、素材包或来源无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"live-ops","label":"直播投流素材、档期和费用对照",
             "description":"只接收本机合成材料，不产生付费动作。","input_example":{},
             "max_media_files":1,"max_media_bytes":2*1024*1024,"media_required":False}]

