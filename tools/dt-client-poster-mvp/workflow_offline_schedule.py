"""Build and review same-site offline operations calendars from supplied milestones."""
from datetime import datetime, timedelta
from graphlib import CycleError, TopologicalSorter
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import re


class WorkflowError(ValueError):
    pass


CAMPAIGN = "campaign-offline-schedule"
LEAD = "lead-offline-schedule"
TOOLS = {CAMPAIGN, LEAD}
MARKETS = {"CN": ("CNY", "Asia/Shanghai"), "PH": ("PHP", "Asia/Manila")}
ROLES = set("selection ad-buyer video-teardown video-director campaign-ops shop-ops live-ops competitor-analyst visual-designer".split())
CAMPAIGN_ROLES = set("selection ad-buyer video-director visual-designer campaign-ops shop-ops live-ops".split())
STAGES = ("kickoff", "assortment", "creative", "sample_ship", "sample_review", "creator_booking",
          "warmup", "launch", "return", "recap")
OUTPUTS = {"json": "offline-schedule.json", "report": "offline-schedule.md", "receipt": "offline-schedule-receipt.json"}


def keys(obj, expected, label):
    if not isinstance(obj, dict) or set(expected.split()) != obj.keys():
        raise WorkflowError(f"{label}字段不符。")


def word(value, label, limit=240):
    if not isinstance(value, str) or not 0 < len(value.strip()) <= limit or re.search(r"[\x00-\x1f]", value):
        raise WorkflowError(f"{label}缺失或无效。")
    return value


def bounded(value, lo, hi, label):
    if type(value) is not int or not lo <= value <= hi:
        raise WorkflowError(f"{label}超出可接受范围。")
    return value


def maybe_amount(value, label):
    if value is not None:
        bounded(value, 0, 10**12, label)


def date(value):
    word(value, "时间", 40)
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        raise WorkflowError("时间格式无效。") from None
    if result.utcoffset() != timedelta(hours=8) or "T" not in value:
        raise WorkflowError("时间必须明确使用本站 +08:00。")
    return result


def list_rows(value, maximum, label):
    if not isinstance(value, list) or len(value) > maximum:
        raise WorkflowError(f"{label}必须为有界数组。")
    return value


def issue(bucket, code, **details):
    bucket.append({"code": code, **details})


def make_nodes(payload, anchor):
    nodes = {}
    for row in list_rows(payload["tasks"], 60, "任务"):
        keys(row, "id stage role owner priority priority_reason deliverable offset_hours duration_hours depends_on allocations budget_minor", "任务")
        ident = word(row["id"], "任务编号", 52)
        word(row["stage"], "里程碑", 52)
        if ident in nodes or word(row["role"],"岗位",40) not in ROLES:
            raise WorkflowError("任务编号重复或岗位不在本站九岗清单。")
        for field in ("owner", "priority_reason", "deliverable"):
            word(row[field], field)
        bounded(row["priority"], 1, 9, "优先级")
        offset = bounded(row["offset_hours"], -2200, 2200, "相对活动时间")
        hours = bounded(row["duration_hours"], 1, 720, "时长")
        deps = list_rows(row["depends_on"], 30, "上游依赖")
        for dep in deps:
            word(dep, "上游任务", 52)
        if len(deps) != len(set(deps)):
            raise WorkflowError("同一上游依赖不得重复。")
        if not isinstance(row["allocations"], dict) or len(row["allocations"]) > 12:
            raise WorkflowError("资源分配应为资源编号和单位数的对象。")
        for k, v in row["allocations"].items():
            word(k, "资源编号", 52)
            maybe_amount(v, "资源单位数")
        maybe_amount(row["budget_minor"], "任务预算")
        start = anchor + timedelta(hours=offset)
        if start.year > 2099 or start.year < 2000:
            raise WorkflowError("计算后的排期日期超出支持范围。")
        nodes[ident] = {**row, "starts_at": start, "ends_at": start+timedelta(hours=hours)}
    return nodes


def dependencies(nodes, ready_at, blocked, pending):
    graph = {ident: set(node["depends_on"]) & nodes.keys() for ident, node in nodes.items()}
    try:
        topology = TopologicalSorter(graph)
        topology.prepare()
        order = []
        while topology.is_active():
            ready = sorted(topology.get_ready(), key=lambda i: (nodes[i]["priority"], nodes[i]["starts_at"], i))
            order.extend(ready)
            topology.done(*ready)
    except CycleError:
        issue(blocked, "dependency_cycle")
        order = []
    for ident, current in nodes.items():
        if current["starts_at"] < ready_at:
            issue(blocked, "before_preparation_start", task=ident)
        for prior in current["depends_on"]:
            if prior not in nodes:
                issue(pending, "missing_dependency", task=ident, dependency=prior)
            elif nodes[prior]["ends_at"] > current["starts_at"]:
                issue(blocked, "dependency_overlaps", task=ident, dependency=prior)
    return order


def pools(payload, nodes, blocked, pending):
    capacity = {}
    loads = {}
    for row in list_rows(payload["capacities"], 30, "资源"):
        keys(row, "id limit source_ref", "资源")
        ident = word(row["id"], "资源编号", 52)
        word(row["source_ref"], "资源证据")
        maybe_amount(row["limit"], "资源上限")
        if ident in capacity:
            raise WorkflowError("资源编号不得重复。")
        capacity[ident] = row
    for node in nodes.values():
        for res, units in node["allocations"].items():
            loads.setdefault(res, []).append((node["id"], node["starts_at"], node["ends_at"], units))
    used_ids = set(nodes)
    for r in list_rows(payload["reservations"], 60, "既有占用"):
        keys(r, "id site resource starts_at ends_at units source_ref", "既有占用")
        rid = word(r["id"], "既有占用编号", 52)
        word(r["resource"], "资源编号", 52)
        word(r["source_ref"], "既有占用来源")
        if r["site"] != payload["site"] or rid in used_ids:
            raise WorkflowError("既有占用站点不符或编号重复。")
        used_ids.add(rid)
        a, b = date(r["starts_at"]), date(r["ends_at"])
        if a >= b:
            raise WorkflowError("既有占用时间无效。")
        maybe_amount(r["units"], "既有占用单位数")
        loads.setdefault(r["resource"], []).append((rid, a, b, r["units"]))
    checks = {}
    for ident, entries in sorted(loads.items()):
        limit = capacity.get(ident, {}).get("limit")
        if limit is None or any(e[3] is None for e in entries):
            issue(pending, "unknown_capacity_or_units", resource=ident)
        peak = 0
        for moment in sorted({entry[1] for entry in entries}):
            active = [(key, amount) for key, start, end, amount in entries if start <= moment < end]
            known = sum(amount for _, amount in active if amount is not None)
            peak = max(peak, known)
            if limit is not None and known > limit:
                issue(blocked, "resource_overlap", resource=ident, at=moment.isoformat(),
                      booked=[key for key, _ in active], known_units=known, limit=limit)
        checks[ident] = {"limit": limit, "known_peak": peak,
                         "fully_known": limit is not None and all(e[3] is not None for e in entries)}
    for required in ("creative", "live-slot"):
        if required not in checks:
            issue(pending,"missing_required_resource",resource=required)
    budget = payload["budget"]
    keys(budget, "cap_minor committed_minor source_ref", "预算")
    word(budget["source_ref"], "预算来源")
    cap, committed = budget["cap_minor"], budget["committed_minor"]
    amounts = [committed, *(node["budget_minor"] for node in nodes.values())]
    for x in [cap, *amounts]:
        maybe_amount(x, "预算金额")
    total = sum(x for x in amounts if x is not None)
    complete = all(x is not None for x in amounts)
    if cap is None or not complete:
        issue(pending, "budget_unknown")
    if cap is not None and total > cap:
        issue(blocked, "budget_exceeded", known_total_minor=total, cap_minor=cap)
    return {"resources": checks, "budget": {"cap_minor": cap, "known_total_minor": total,
            "total_minor": total if complete else None, "currency": payload["currency"]}}


def campaign_and_market(site, tool, payload, nodes, anchor, ready_at, blocked, pending):
    stages = {}
    for row in nodes.values():
        stages.setdefault(row["stage"], []).append(row)
    required_roles = ROLES if tool == LEAD else CAMPAIGN_ROLES
    for role in sorted(required_roles - {row["role"] for row in nodes.values()}):
        issue(pending, "role_missing", role=role)
    if tool == CAMPAIGN:
        sku=word(payload["sku"],"同场商品 SKU",80)
        launch=stages.get("launch",[])
        if len(launch)==1 and ("sku:"+sku not in launch[0]["allocations"] or "live-slot" not in launch[0]["allocations"]):
            issue(pending,"launch_resource_not_declared",sku=sku)
        for step in STAGES:
            if len(stages.get(step, [])) != 1:
                issue(pending, "milestone_missing_or_ambiguous", stage=step)
        for before, after in zip(STAGES, STAGES[1:]):
            if len(stages.get(before, [])) == len(stages.get(after, [])) == 1:
                a, b = stages[before][0], stages[after][0]
                if a["ends_at"] > b["starts_at"]:
                    issue(blocked, "milestone_sequence", previous=before, next=after)
                if a["id"] not in b["depends_on"]:
                    issue(pending, "milestone_dependency_missing", previous=before, next=after)
        if len(stages.get("launch", [])) == 1 and stages["launch"][0]["starts_at"] != anchor:
            issue(blocked, "launch_anchor_mismatch")
    rule = payload["review_contract"]
    needed = "source_ref"
    if tool == CAMPAIGN:
        needed += " material_buffer_hours review_min_hours review_max_hours booking_max_hours"
    if site == "PH":
        needed += " localization_buffer_hours holiday_preparation_hours"
    keys(rule, needed, "离线排期约定")
    word(rule["source_ref"], "排期约定来源")
    for name, value in rule.items():
        if name != "source_ref" and value is not None:
            bounded(value, 0, 2200, name)
    if tool == CAMPAIGN:
        rmin, rmax = rule["review_min_hours"], rule["review_max_hours"]
        if rmin is not None and rmax is not None and rmin > rmax:
            raise WorkflowError("审核窗口上下限矛盾。")
        if None in [rule["material_buffer_hours"], rmin, rmax, rule["booking_max_hours"]]:
            issue(pending, "review_contract_incomplete")
        if len(stages.get("creative", [])) == 1 and rule["material_buffer_hours"] is not None:
            if anchor-stages["creative"][0]["ends_at"] < timedelta(hours=rule["material_buffer_hours"]):
                issue(blocked, "material_buffer_short")
        if len(stages.get("sample_review", [])) == 1:
            review = stages["sample_review"][0]
            hours = review["duration_hours"]
            if (rmin is not None and hours < rmin) or (rmax is not None and hours > rmax):
                issue(blocked, "sample_review_duration")
            if len(stages.get("creator_booking", [])) == 1 and rule["booking_max_hours"] is not None:
                if stages["creator_booking"][0]["starts_at"]-review["ends_at"] > timedelta(hours=rule["booking_max_hours"]):
                    issue(blocked, "creator_booking_late")
    if site == "PH":
        local = stages.get("localization", [])
        if len(local) != 1 or rule["localization_buffer_hours"] is None:
            issue(pending, "localization_schedule_unconfirmed")
        elif anchor-local[0]["ends_at"] < timedelta(hours=rule["localization_buffer_hours"]):
            issue(blocked, "localization_buffer_short")
        if payload["event_type"] == "local_holiday":
            hours = rule["holiday_preparation_hours"]
            if hours is None:
                issue(pending, "holiday_lead_time_unconfirmed")
            elif anchor-ready_at < timedelta(hours=hours):
                issue(blocked, "holiday_lead_time_short")



def ph_campaign_readiness(payload, nodes, blocked, pending):
    data = payload["ph_preparation"]
    keys(data, "sku source_ref warehouse_owner funding_owner stock_task_id funding_task_id daily_order_units cover_days stock_on_hand stock_committed cash_available_minor cash_required_minor cash_horizon_days", "菲律宾筹备")
    if data["sku"] != payload["sku"]:
        raise WorkflowError("筹备库存的 SKU 与活动不符。")
    for k in ("sku","source_ref","warehouse_owner","funding_owner","stock_task_id","funding_task_id"):
        word(data[k],k,120)
    for id_key, owner_key, stage in (("stock_task_id","warehouse_owner","stock_ready"),
                                      ("funding_task_id","funding_owner","funds_ready")):
        node = nodes.get(data[id_key])
        if not node:
            issue(pending,"ph_readiness_task_missing",task=data[id_key])
        elif (node["owner"]!=data[owner_key] or node["stage"]!=stage or
              node["ends_at"]>date(payload["event_starts_at"])):
            issue(blocked,"ph_readiness_task_mismatch",task=data[id_key])
    for k in ("daily_order_units","cover_days","stock_on_hand","stock_committed",
              "cash_available_minor","cash_required_minor","cash_horizon_days"):
        maybe_amount(data[k],k)
    if data["cover_days"]==0 or data["cash_horizon_days"]==0:
        raise WorkflowError("库存覆盖和资金观察窗口不能为 0。")
    if any(data[k] is None for k in ("daily_order_units","cover_days","stock_on_hand","stock_committed")):
        need = stock = None
        issue(pending,"ph_stock_unknown",sku=data["sku"])
    else:
        need=data["daily_order_units"]*data["cover_days"]
        stock=data["stock_on_hand"]-data["stock_committed"]
        if stock<need:
            issue(blocked,"ph_stock_short",sku=data["sku"],available=stock,required=need)
    if any(data[k] is None for k in ("cash_available_minor","cash_required_minor","cash_horizon_days")):
        reserve=None
        issue(pending,"ph_cash_unknown")
    else:
        reserve=data["cash_available_minor"]-data["cash_required_minor"]
        if reserve<0:
            issue(blocked,"ph_cash_short",reserve_minor=reserve)
    return {**data,"net_stock_units":stock,"required_order_units":need,"cash_reserve_minor":reserve}


def evaluate(site, tool, payload):
    required = "site scope currency timezone plan_id version owner event_name event_type goal source_ref preparation_starts_at event_starts_at review_contract tasks capacities reservations budget"
    if tool == CAMPAIGN:
        required += " sku"
        if site == "PH":
            required += " ph_preparation"
    keys(payload, required, "排期")
    if ((payload["site"], payload["scope"], payload["currency"], payload["timezone"]) !=
            (site, "synthetic_offline", *MARKETS[site]) or
            payload["event_type"] not in ("ordinary", "local_holiday")):
        raise WorkflowError("站点、币种、时区或模拟标识不符。")
    for k in ("plan_id", "owner", "event_name", "goal", "source_ref"):
        word(payload[k], k)
    bounded(payload["version"], 1, 9999, "版本")
    anchor, ready = date(payload["event_starts_at"]), date(payload["preparation_starts_at"])
    if ready > anchor or not 2001 <= anchor.year <= 2098:
        raise WorkflowError("筹备时间或活动时间无效。")
    nodes = make_nodes(payload, anchor)
    blocked, pending = [], []
    order = dependencies(nodes, ready, blocked, pending)
    resources = pools(payload, nodes, blocked, pending)
    campaign_and_market(site, tool, payload, nodes, anchor, ready, blocked, pending)
    ph_preparation = ph_campaign_readiness(payload,nodes,blocked,pending) if site=="PH" and tool==CAMPAIGN else None
    ordered = sorted(nodes.values(), key=lambda row: (row["starts_at"],row["priority"],row["id"]))
    return {"state": "CONFLICT" if blocked else "PENDING" if pending else "READY_OFFLINE",
            "timezone": payload["timezone"], "event_starts_at": anchor.isoformat(),
            "preparation_starts_at": ready.isoformat(), "event_name": payload["event_name"],
            "event_type": payload["event_type"], "goal": payload["goal"], "owner": payload["owner"],
            "plan_id": payload["plan_id"], "version": payload["version"], "source_ref": payload["source_ref"], "sku": payload.get("sku"),
            "applied_synthetic_constraints":payload["review_contract"], "execution_priority_order": order,
            "calendar": [{**r,"starts_at":r["starts_at"].isoformat(),"ends_at":r["ends_at"].isoformat()} for r in ordered],
            "reserved": payload["reservations"],"capacity_sources":payload["capacities"],
            "budget_source":payload["budget"]["source_ref"],"resource_review":resources,"ph_preparation":ph_preparation,"conflicts":blocked,"pending":pending}


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)+"\n").encode("utf-8")


def digest(data):
    return sha256(data).hexdigest()


def make_report(result):
    def safe(value):
        return escape(str(value)).replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;").replace(chr(96), "&#96;")
    lines = [f'# {safe(result["event_name"])} · 模拟排期 v{result["version"]}', "",
             f'{result["site"]} · {result["timezone"]} · {result["state"]}', "",
             f'活动锚点 {result["event_starts_at"]} · 负责人 {safe(result["owner"])} · 目标 {safe(result["goal"])}', "",
             "|任务|岗位|责任人|开始|结束|依赖|优先级／依据|交付物|",
             "|---|---|---|---|---|---|---|---|"]
    for r in result["calendar"]:
        values = [r["id"],r["role"],r["owner"],r["starts_at"],r["ends_at"],
                  "、".join(r["depends_on"]),f'{r["priority"]} {r["priority_reason"]}',r["deliverable"]]
        lines.append("|"+"|".join(map(safe,values))+"|")
    budget = result["resource_review"]["budget"]
    lines += ["", "## 冲突与待核", "",
              f'预算最小单位 {result["currency"]}，已知金额 {budget["known_total_minor"]}，总额 {budget["total_minor"]}，上限 {budget["cap_minor"]}。',
              *[f'- {safe(x["code"])} · {safe(x.get("resource") or x.get("task") or x.get("stage") or "")}'
                for x in result["conflicts"]+result["pending"]], "",
              "仅核对合成排期和资源证据。未预订档期、派单、寄样、启动活动或变更预算。", ""]
    ph = result["ph_preparation"]
    if ph:
        lines += [f'菲律宾筹备 · {safe(ph["sku"])} · 预计下单所需件数 {ph["required_order_units"]} · 净库存 {ph["net_stock_units"]} · 资金差额 {ph["cash_reserve_minor"]} · 来源 {safe(ph["source_ref"])}', ""]
    return "\n".join(lines).encode("utf-8")


def receipt_for(result, raw, report):
    return {"task_id":result["task_id"],"tool_id":result["tool_id"],"site":result["site"],
            "input_sha256":result["input_sha256"],"result_sha256":digest(raw),"report_sha256":digest(report),
            "scope":result["scope"],"business_execution_authorized":False}


def run_workflow(project, site, tool, payload, task_dir, assets, progress):
    if site not in MARKETS or tool not in TOOLS or assets:
        raise WorkflowError("排期仅支持已启用站点的结构化本机输入。")
    progress("正在倒排依赖、核对里程碑、档期与预算冲突。")
    result = {**evaluate(site,tool,payload),"task_id":Path(task_dir).name,"site":site,"tool_id":tool,
              "scope":"synthetic_offline","input_sha256":digest(json_bytes(payload)),"currency":payload["currency"],
              "business_execution_authorized":False,"dispatched":False,"provider_called":False,"model_called":False}
    raw, report = json_bytes(result), make_report(result)
    blobs = {"json":raw,"report":report,"receipt":json_bytes(receipt_for(result,raw,report))}
    files = {k:Path(task_dir)/filename for k, filename in OUTPUTS.items()}
    for kind, path in files.items():
        with path.open("xb") as file:
            file.write(blobs[kind])
    return result, files


def verify_artifacts(task, files):
    result = task["result"]
    if (task["local_files"]!=OUTPUTS or result!=json.loads(files["json"]) or result["task_id"]!=task["id"]
            or result["site"]!=task["site"] or result["tool_id"]!=task["source"]["tool_id"]
            or json.loads(files["receipt"])!=receipt_for(result,files["json"],files["report"])):
        raise WorkflowError("离线排期产物身份或哈希无法核验。")


def example(site,tool):
    currency,zone=MARKETS[site]
    contract={"source_ref":"SYN-RULE"}
    if tool==CAMPAIGN:
        contract.update(material_buffer_hours=72, review_min_hours=48,
                        review_max_hours=120, booking_max_hours=72)
    if site=="PH":
        contract.update(localization_buffer_hours=72,holiday_preparation_hours=None)
    task={"id":"SYN-FIRST","stage":"kickoff","role":"campaign-ops","owner":"合成责任人",
          "priority":1,"priority_reason":"先确认范围","deliverable":"活动范围说明",
          "offset_hours":-96,"duration_hours":1,"depends_on":[],"allocations":{},"budget_minor":0}
    report={"site":site,"scope":"synthetic_offline","currency":currency,"timezone":zone,
             "plan_id":"SYN-SCHEDULE","version":1,"owner":"合成负责人","event_name":"示例活动",
             "event_type":"ordinary","goal":"演示倒排","source_ref":"SYN-EXAMPLE",
             "preparation_starts_at":"2026-12-01T10:00:00+08:00","event_starts_at":"2026-12-10T10:00:00+08:00",
             "review_contract":contract,"tasks":[task],"capacities":[],"reservations":[],
             "budget":{"source_ref":"SYN-BUDGET","cap_minor":None,"committed_minor":None}}
    if tool==CAMPAIGN:
        report["sku"]="SYN-SKU"
    if site=="PH" and tool==CAMPAIGN:
        report["ph_preparation"]={
            "sku":"SYN-SKU","source_ref":"SYN-FULFILLMENT","warehouse_owner":"合成仓配",
            "funding_owner":"合成财务","stock_task_id":"SYN-STOCK","funding_task_id":"SYN-FUND",
            **dict.fromkeys(("daily_order_units","cover_days","stock_on_hand","stock_committed",
                             "cash_available_minor","cash_required_minor","cash_horizon_days"))}
    return report


def public_tools():
    return [{"id":tool,"sites":list(MARKETS),"role_suffix":role,"label":label,
             "description":"提供同站活动锚点、相对工期、依赖、容量、既有档期及预算来源，生成模拟排期。",
             "input_example":example("CN",tool),"input_examples":{site:example(site,tool) for site in MARKETS}}
            for tool,role,label in ((CAMPAIGN,"campaign-ops","活动倒排与跨岗冲突检查"),
                                    (LEAD,"lead","九岗排期与资源审查"))]
