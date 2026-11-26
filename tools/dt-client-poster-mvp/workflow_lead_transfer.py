"""Review the conditions for transferring an operating method between the two sites."""
from datetime import timedelta, timezone
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_environment import ZONE, identifier, timestamp

TOOL = "lead-cross-site-method-review"
PORTABLE = {"evidence_trace", "versioned_review", "handoff"}
LOCAL_ONLY = {"payment", "refund", "fee", "warehouse"}
PUBLIC_KEYS = {"scope","site","source_site","case_id","as_of","entries"}
ENTRY_KEYS = {"id","mechanism","original_logic","source_record","target_record","prerequisites"}


class WorkflowError(ValueError):
    pass


def _text(s,limit):
    return isinstance(s,str) and 0<len(s.strip())<=limit and len(s)<=limit and s.isprintable()


def _record(data,site):
    if data is None:
        return
    if (not isinstance(data,dict) or set(data)!={"site","reference","checked_at","state","condition"}
            or data["site"]!=site or not identifier(data["reference"])
            or data["state"] not in ("provided","uncertain","conflicting") or not _text(data["condition"],280)):
        raise WorkflowError("来源或目标对照必须标明匹配站点、证据编号、核对时间、状态和条件。")
    try:
        timestamp(data["checked_at"])
    except ValueError as error:
        raise WorkflowError(str(error)) from None


def validate(site,payload):
    if (not isinstance(payload,dict) or set(payload)!=PUBLIC_KEYS or payload["site"]!=site
            or site not in ROUTES or not isinstance(payload["source_site"], str) or payload["source_site"] not in ROUTES
            or payload["source_site"]==site or payload["scope"]!=SYNTHETIC or not identifier(payload["case_id"])):
        raise WorkflowError("请选择目标站点和另一来源站点，只接受有编号的合成任务。")
    try:
        as_of=timestamp(payload["as_of"])
    except ValueError as error:
        raise WorkflowError(str(error)) from None
    entries=payload["entries"]
    if not isinstance(entries,list) or not 1<=len(entries)<=25:
        raise WorkflowError("转译案例须有 1–25 条待比较方法。")
    ids=set()
    for row in entries:
        if (not isinstance(row,dict) or set(row)!=ENTRY_KEYS or not identifier(row["id"]) or row["id"] in ids
                or not isinstance(row["mechanism"],str) or row["mechanism"] not in PORTABLE|LOCAL_ONLY
                or not _text(row["original_logic"],380)):
            raise WorkflowError("待比较方法的编号、机制或原始逻辑不正确。")
        ids.add(row["id"])
        _record(row["source_record"],payload["source_site"])
        _record(row["target_record"],site)
        ps=row["prerequisites"]
        if not isinstance(ps,list) or len(ps)>12:
            raise WorkflowError("可迁移前提须为列表，最多 12 条。")
        for item in ps:
            if (not isinstance(item,dict) or set(item)!={"condition","state"} or not _text(item["condition"],200)
                    or item["state"] not in ("matched","mismatch","unknown")):
                raise WorkflowError("每条可迁移前提须写明条件及匹配、冲突或未知状态。")
    return as_of


def _valid(record,instant):
    return record is not None and record["state"]=="provided" and timestamp(record["checked_at"])<=instant


def _item(site,row,instant):
    mechanism=row["mechanism"]
    missing=[key for key in ("source_record","target_record") if not _valid(row[key],instant)]
    blocked=[p["condition"] for p in row["prerequisites"] if p["state"]=="mismatch"]
    waiting=[p["condition"] for p in row["prerequisites"] if p["state"]=="unknown"]
    if mechanism in LOCAL_ONLY:
        decision="do_not_transfer_platform_terms"
        follow="只保留经验问题；目标站点分别核对本地机制和口径，不套用来源站点的支付、退款、费率或仓配条款。"
    elif blocked:
        decision="not_applicable"
        follow="记录不满足的前提，调整目标站流程后重新评估。"
    elif missing or waiting or not row["prerequisites"]:
        decision="pending_evidence"
        follow="核对缺失、冲突或晚于评估时点的来源，补齐目标站前提后再审核。"
    else:
        decision="eligible_for_offline_trial"
        follow="仅按给定前提在目标站做离线对照；保留站点独立、版本证据与人工复核。"
    owners=("lead","shop-ops","selection") if mechanism in LOCAL_ONLY else ("lead","shop-ops")
    return {**row,"decision":decision,"unverified_records":missing,"unmet_prerequisites":blocked,
            "unknown_prerequisites":waiting,"target_roles":[ROUTES[site][0]+"-"+x for x in owners],
            "review_owner":ROUTES[site][0]+"-lead",
            "review_due_at_local":(instant + timedelta(days=1)).astimezone(timezone(timedelta(hours=8))).isoformat(),
            "review_due_basis":"proposed_next_day_offline_checkpoint",
            "review_action":follow}


def run_workflow(project,site,tool_id,payload,task_dir,assets,progress):
    if tool_id!=TOOL or assets:
        raise WorkflowError("此评估只使用本站结构化离线材料。")
    now=validate(site,payload)
    result={"workflow":TOOL,"task_id":task_dir.name,"case_id":payload["case_id"],
            "source_site":payload["source_site"],"site":site,"target_currency":ROUTES[site][1],"timezone":ZONE[site],
            "as_of_local":now.astimezone(timezone(timedelta(hours=8))).isoformat(),"scope":SYNTHETIC,
            "evaluations":[_item(site,row,now) for row in payload["entries"]],
            "execution_authorized":False,"model_called":False,"provider_called":False}
    if progress:
        progress("正在核对跨站机制、前提与复核责任。")
    folder=task_dir/"method-review"
    folder.mkdir()
    paths={k:folder/name for k,name in (("json","result.json"),("receipt","receipt.json"),("report","review.md"))}
    lines=[f"{result['case_id']} · {payload['source_site']} → {site}", "合成离线对照，仅作人工复核。"]
    for row in result["evaluations"]:
        lines.extend([f"{row['id']} {row['mechanism']} {row['decision']}",row["original_logic"],
                      row["review_action"],", ".join(row["target_roles"]), f"{row['review_owner']} {row['review_due_at_local']}"])
    with paths["report"].open("x",encoding="utf-8",newline="\n") as f:f.write("\n".join(lines)+"\n")
    with paths["json"].open("x",encoding="utf-8",newline="\n") as f:json.dump(result,f,ensure_ascii=False,indent=2)
    receipt={"scope":SYNTHETIC,"task_id":task_dir.name,"site":site,"source_site":result["source_site"],
             "case_id":result["case_id"],"files_sha256":{k:sha256(paths[k].read_bytes()).hexdigest() for k in ("json","report")},
             "execution_authorized":False,"model_called":False,"provider_called":False}
    with paths["receipt"].open("x",encoding="utf-8",newline="\n") as f:json.dump(receipt,f,ensure_ascii=False,indent=2)
    return result,paths


def public_tools():
    inputs={}
    for site in ROUTES:
        other="CN" if site=="PH" else "PH"
        inputs[site]={"scope":SYNTHETIC,"site":site,"source_site":other,"case_id":f"{site}-METHOD-01",
                      "as_of":"2026-11-20T12:00:00+08:00",
                      "entries":[{"id":"method-1","mechanism":"versioned_review",
                                 "original_logic":"对照版本与原回执的合成示例。","source_record":None,
                                 "target_record":None,"prerequisites":[]}]}
    return [{"id":TOOL,"role_suffix":"lead","sites":list(ROUTES),"label":"跨平台经验转译（离线）",
             "description":"按原逻辑、目标站证据和迁移前提逐条比较，只给离线试行或复核动作。",
             "input_examples":inputs,"max_media_files":0}]

