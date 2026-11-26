"""Site-specific review of frozen synthetic platform-change evidence."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
import re

from role_tools import ROUTES, SYNTHETIC

TOOL = "lead-environment-change-review"
ZONE = {"CN":"Asia/Shanghai","PH":"Asia/Manila"}
TOPICS = {
    "rule":("lead","shop-ops"),
    "fee":("lead","shop-ops","ad-buyer"),
    "governance":("lead","shop-ops","live-ops"),
    "campaign":("lead","campaign-ops","ad-buyer"),
    "local_holiday":("lead","campaign-ops","shop-ops"),
    "payment":("lead","shop-ops","selection"),
    "customs":("lead","shop-ops","selection"),
}
FIELDS = {"case_id","site","scope","timezone","as_of","changes"}
SOURCE_FIELDS = {"source_id","site","topic","scope_id","published_at","effective_at",
                 "expires_at","evidence","statement","comparison"}
DECISION = {
    "applicable_in_fixture": "对应岗位复核影响与原始依据，形成待审核变更单。",
    "future": "记录生效日期与前置事项，当前仍按已核依据处理。",
    "expired": "留档旧证据，补当前时点的新来源。",
    "conflict": "暂停使用冲突数字，交负责人核对同范围的来源和替代版本。",
    "superseded": "作为历史版本留存。",
    "pending_evidence": "先补足来源、有效期或可比口径，保留未知。",
}


class WorkflowError(ValueError):
    pass


def identifier(value):
    return isinstance(value,str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,83}",value,re.ASCII)


def timestamp(value):
    try:
        if not isinstance(value,str) or len(value)>36 or "T" not in value:
            raise ValueError
        parsed=datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (TypeError,ValueError):
        raise WorkflowError("时间须为带偏移量的 ISO 8601 日期时间。") from None


def _comparison(raw, currency):
    if raw is None:
        return None
    if not isinstance(raw,dict) or set(raw)!={"baseline","candidate","unit","amount_minor","currency"}:
        raise WorkflowError("比较项须有基线、候选、单位、测试金额和币种，未知金额用 null。")
    if raw["unit"] not in ("bps","business_days","calendar_days","count"):
        raise WorkflowError("比较单位仅支持基点、工作日、自然日和数量。")
    for side in ("baseline","candidate"):
        item=raw[side]
        if (not isinstance(item,dict) or set(item)!={"value","basis_id"}
                or not identifier(item["basis_id"])
                or (item["value"] is not None and (type(item["value"]) is not int
                                                   or not 0 <= item["value"] <= 1_000_000))):
            raise WorkflowError("指标值须为适用的非负整数或 null，并提供可比基准编号。")
    amount=raw["amount_minor"]
    if amount is not None and (type(amount) is not int or not 0 <= amount <= 10**12):
        raise WorkflowError("测试金额须为非负整数最小货币单位或 null。")
    if raw["unit"]=="bps":
        if raw["currency"] != currency or any((raw[k]["value"] or 0)>10_000 for k in ("baseline","candidate")):
            raise WorkflowError("费率须使用本站币种和 0–10000 基点。")
    elif amount is not None or raw["currency"] is not None:
        raise WorkflowError("非费率场景的测试金额与币种填 null。")
    return raw


def validate(site,payload):
    if site not in ZONE or not isinstance(payload,dict) or payload.keys()!=FIELDS:
        raise WorkflowError("请使用本站合成规则变化输入。")
    if ((payload["site"],payload["scope"],payload["timezone"])!=(site,SYNTHETIC,ZONE[site])
            or not identifier(payload["case_id"])):
        raise WorkflowError("站点、合成标记、编号或时区不匹配。")
    as_of=timestamp(payload["as_of"])
    if not isinstance(payload["changes"],list) or len(payload["changes"])>32:
        raise WorkflowError("变化记录最多 32 条。")
    ids=set()
    for row in payload["changes"]:
        if not isinstance(row,dict) or row.keys()!=SOURCE_FIELDS:
            raise WorkflowError("来源需给出站点、适用范围、发布时间、生效期、说明与比较项。")
        if row["site"]!=site:
            raise WorkflowError("拒绝使用其他站点来源作为本站规则证据。")
        if (not identifier(row["source_id"]) or row["source_id"] in ids or not identifier(row["scope_id"])
                or not isinstance(row["topic"],str) or row["topic"] not in (TOPICS if site=="PH" else tuple(TOPICS)[:4])):
            raise WorkflowError("来源编号重复或无效，或该主题不适用于所选站点。")
        ids.add(row["source_id"])
        if (row["evidence"] not in ("confirmed_in_fixture","unverified")
                or not isinstance(row["statement"],str) or not 0<len(row["statement"].strip())<=360
                or len(row["statement"])>360 or not row["statement"].isprintable()):
            raise WorkflowError("请填写证据状态及一行合成变化说明。")
        published,effective=timestamp(row["published_at"]),timestamp(row["effective_at"])
        expires=timestamp(row["expires_at"]) if row["expires_at"] is not None else None
        if published>effective or (expires is not None and expires<=effective):
            raise WorkflowError("来源时间须按发布、生效及失效排序。")
        _comparison(row["comparison"],ROUTES[site][1])
    return as_of


def _numbers(raw,state):
    if raw is None:
        return {"status":"unknown","delta":None,"amount_delta_minor":None}
    a,b=raw["baseline"],raw["candidate"]
    if (state not in ("applicable_in_fixture","future") or a["basis_id"]!=b["basis_id"]
            or a["value"] is None or b["value"] is None):
        return {"status":"unknown","delta":None,"amount_delta_minor":None}
    diff=b["value"]-a["value"]
    total=None
    if raw["unit"]=="bps" and raw["amount_minor"] is not None:
        total=int((Decimal(raw["amount_minor"])*diff/10_000).quantize(Decimal(1),rounding=ROUND_HALF_UP))
    return {"status":"conditional_scenario" if state=="future" else "calculated_from_fixture",
            "delta":diff,"unit":raw["unit"],"basis_id":a["basis_id"],"amount_delta_minor":total,
            "currency":raw["currency"],"basis_amount_minor":raw["amount_minor"]}


def _state(row,as_of):
    if row["evidence"]!="confirmed_in_fixture" or timestamp(row["published_at"])>as_of:
        return "pending_evidence"
    if row["expires_at"] is not None and timestamp(row["expires_at"])<=as_of:
        return "expired"
    return "future" if timestamp(row["effective_at"])>as_of else "applicable_in_fixture"


def review(site,payload):
    as_of=validate(site,payload)
    buckets=defaultdict(list)
    changes=[]
    platform,currency=ROUTES[site]
    for item in payload["changes"]:
        row={**item,"state":_state(item,as_of)}
        changes.append(row)
        if row["state"]=="applicable_in_fixture":
            buckets[(row["topic"],row["scope_id"])].append(row)
    for group in buckets.values():
        latest=max(timestamp(r["effective_at"]) for r in group)
        current=[r for r in group if timestamp(r["effective_at"])==latest]
        ambiguous=len({json.dumps([r["statement"],r["comparison"]],sort_keys=True) for r in current})>1
        for row in group:
            if timestamp(row["effective_at"])<latest:
                row["state"]="superseded"
            elif ambiguous:
                row["state"]="conflict"
    for row in changes:
        row["affected_roles"]=[platform+"-"+role for role in TOPICS[row["topic"]]]
        row["scenario"]=_numbers(row["comparison"],row["state"])
        row["review_action"]=DECISION[row["state"]]
        if row["state"]=="applicable_in_fixture" and row["scenario"]["status"]=="unknown":
            row["review_action"]+=" 数量影响仍待同口径证据。"
    topics=tuple(TOPICS) if site=="PH" else tuple(TOPICS)[:4]
    return {"workflow":TOOL,"case_id":payload["case_id"],"site":site,"scope":SYNTHETIC,"currency":currency,
            "as_of_local":as_of.astimezone(timezone(timedelta(hours=8))).isoformat(),"timezone":ZONE[site],
            "changes":changes,"topics_without_sources":[k for k in topics if not any(r["topic"]==k for r in changes)],
            "execution_authorized":False,"provider_called":False,"model_called":False}


def run_workflow(project,site,tool_id,payload,task_dir,assets,progress):
    if tool_id!=TOOL or assets:
        raise WorkflowError("平台变化评估只接收结构化离线资料。")
    result=review(site,payload)
    result["task_id"]=task_dir.name
    if progress:
        progress("正在按站点、时点和可比口径生成待审核评估。")
    report=[f"{result['case_id']} · {site} · {result['as_of_local']} · {result['timezone']}",
            "冻结合成条件，不作为现行规则或执行授权。"]
    for item in result["changes"]:
        report.extend([f"{item['topic']} {item['source_id']} {item['state']} {item['effective_at']}",
                       item["statement"], ", ".join(item["affected_roles"]), item["review_action"],
                       f"假设量 {item['scenario']}"])
    report.append("无来源主题 " + ", ".join(result["topics_without_sources"]))
    folder=task_dir/"environment-review"
    folder.mkdir()
    paths={k:folder/name for k,name in (("json","review.json"),("receipt","receipt.json"),("report","review.md"))}
    for key,data in (("json",json.dumps(result,ensure_ascii=False,indent=2)),("report","\n".join(report))):
        with paths[key].open("x",encoding="utf-8",newline="\n") as stream:
            stream.write(data+"\n")
    receipt={"task_id":task_dir.name,"site":site,"case_id":result["case_id"],"scope":SYNTHETIC,
             "files_sha256":{k:sha256(paths[k].read_bytes()).hexdigest() for k in ("json","report")},
             "source_ids":[row["source_id"] for row in result["changes"]],
             "provider_called":False,"model_called":False,"execution_authorized":False}
    with paths["receipt"].open("x",encoding="utf-8") as stream:
        json.dump(receipt,stream,ensure_ascii=False,indent=2)
    return result,paths


def public_tools():
    return [{"id":TOOL,"label":"平台变化离线评估","role_suffix":"lead","sites":list(ZONE),
             "description":"按站点和适用日期比较冻结合成规则、费率、治理和活动，菲律宾另列节庆、支付和清关。",
             "input_examples":{site:{"case_id":f"{site}-CHANGE-01","site":site,"scope":SYNTHETIC,
                    "timezone":name,"as_of":"2026-11-20T12:00:00+08:00","changes":[]} for site,name in ZONE.items()},
             "max_media_files":0}]

