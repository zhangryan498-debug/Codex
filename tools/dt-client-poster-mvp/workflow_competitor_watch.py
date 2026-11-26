"""Versioned local monitoring catalog and as-of readings for a single marketplace."""

from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

REGISTER="competitor-watch-registry"
UPDATE="competitor-watch-observation"
VIEW="competitor-watch-as-of"
TOOLS=(REGISTER,UPDATE,VIEW)
FILES={"json":"watch-result.json","report":"watch-report.md","receipt":"watch-receipt.json"}
ZONE={"CN":"Asia/Shanghai","PH":"Asia/Manila"}
FIELDS={
    "shop":{"catalog_items":"count","tracked_products":"count"},
    "product":{"review_total":"count","listed_variants":"count"},
    "sku":{"list_minor":"minor","checkout_minor":"minor","visible_stock":"count"},
    "live_room":{"live_view_count":"count","live_duration_seconds":"seconds"},
    "creator":{"eligible_collaboration_count":"count","observed_video_count":"count"},
}
PARENT={"shop":None,"product":"shop","sku":"product","live_room":"product","creator":"live_room"}
COMMON="site scope currency watch_id"
EXTRA={
    REGISTER:"version previous_task_id effective_at source_ref targets",
    UPDATE:"registry_task_id record_id revision previous_task_id captured_at arrived_at correction_ref readings",
    VIEW:"registry_task_id view_id cutoff_at",
}


class WorkflowError(ValueError):
    pass


def revision(p,name):
    amount(p[name],"版本",1,1000000,False)
    if (p[name]==1)!=(p["previous_task_id"] is None):
        raise WorkflowError("更新必须明确引用上一版任务。")
    if p["previous_task_id"] is not None:
        task_id(p["previous_task_id"],"上一版任务")


def catalog(targets, currency):
    if not isinstance(targets,list) or not 5<=len(targets)<=100:
        raise WorkflowError("监控范围必须登记店、商品、SKU、直播间和达人。")
    rows={}
    for r in targets:
        check(r,"entity entity_id parent_id fields","监控对象")
        e,ident=r["entity"],r["entity_id"]
        if e not in tuple(FIELDS):
            raise WorkflowError("监控对象类型不支持。")
        identify(ident,"监控对象编号")
        if r["parent_id"] is not None:
            identify(r["parent_id"],"上游对象编号")
        names=r["fields"]
        if (not isinstance(names,list) or not names or any(k not in tuple(FIELDS[e]) for k in names)
                or len(set(names))!=len(names) or (e,ident) in rows):
            raise WorkflowError("对象或登记字段重复或超出范围。")
        rows[e,ident]=r
    if {e for e,_ in rows}!=set(FIELDS):
        raise WorkflowError("五类监控对象需明确；例外属于待核范围。")
    leaves={}
    for (kind,ident),row in rows.items():
        parent=PARENT[kind]
        p=row["parent_id"]
        if (parent is None and p is not None) or (parent is not None and (parent,p) not in rows):
            raise WorkflowError("对象与店、商品、SKU、直播间或达人关联不匹配。")
        for field in row["fields"]:
            unit=FIELDS[kind][field]
            leaves[kind,ident,field]=currency+"_minor" if unit=="minor" else unit
    return leaves


def window(cutoff,effective):
    day=cutoff.replace(hour=0,minute=0,second=0,microsecond=0)
    mon=day-timedelta(days=day.weekday())
    first=day.replace(day=1)
    next_month=(first+timedelta(days=32)).replace(day=1)
    return {k:{"start":lo.isoformat(),"end_exclusive":hi.isoformat(),"next_due":hi.isoformat(),
              "pre_registration_seconds":int(max(timedelta(0),min(effective,hi)-lo).total_seconds()),
              "calendar_mode":"local_midnight"}
            for k,(lo,hi) in {"day":(day,day+timedelta(days=1)),"week":(mon,mon+timedelta(days=7)),
                             "month":(first,next_month)}.items()}


def bind(site,tool,p,load,list_ready):
    check(p,COMMON+" "+EXTRA[tool],"竞品监控")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("监控只接收选中站点原币合成范围。")
    name=p["watch_id"]
    identify(name,"监控簿编号")
    pins={}
    ctx={}
    def read(i,kind):
        ctx[i],pins[i]=load(i,site,kind)
        if ctx[i]["watch_id"]!=name:
            raise WorkflowError("引用必须来自同一站点与监控簿。")
        return ctx[i]
    if tool==REGISTER:
        revision(p,"version")
        term(p["source_ref"],"监控任务依据")
        catalog(p["targets"],p["currency"])
        start=stamp(p["effective_at"])
        if p["previous_task_id"]:
            old=read(p["previous_task_id"],REGISTER)
            if old["version"]+1!=p["version"] or stamp(old["effective_at"])>=start:
                raise WorkflowError("登记修订要求连续版本和晚于前版的生效时刻。")
        return "watch:reg:"+site+":"+name+":"+str(p["version"]),pins,ctx
    task_id(p["registry_task_id"],"监控范围任务")
    reg=read(p["registry_task_id"],REGISTER)
    if tool==UPDATE:
        revision(p,"revision")
        identify(p["record_id"],"留档观测编号")
        captured, arrived = stamp(p["captured_at"]),stamp(p["arrived_at"])
        if not stamp(reg["effective_at"])<=captured<=arrived:
            raise WorkflowError("观测需在监控生效后，抓取也不得晚于资料到达。")
        eligible=[row for row in list_ready(site,name,REGISTER) if stamp(row["effective_at"])<=captured]
        if not eligible or max(eligible,key=lambda r:r["version"])["task_id"]!=reg["task_id"]:
            raise WorkflowError("观测必须绑定抓取时点生效的范围版本。")
        if p["revision"]>1:
            old=read(p["previous_task_id"],UPDATE)
            if ((old["record_id"],old["registry_task_id"],old["captured_at"],old["revision"]+1)!=
                (p["record_id"],p["registry_task_id"],p["captured_at"],p["revision"]) or stamp(old["arrived_at"])>=arrived):
                raise WorkflowError("观测修订须连续并保留抓取时间、对象范围和原编号。")
            term(p["correction_ref"],"更正说明")
        elif p["correction_ref"] is not None:
            raise WorkflowError("初版观测没有修订来源。")
        leaves=catalog(reg["targets"],p["currency"])
        if not isinstance(p["readings"],list) or not 1<=len(p["readings"])<=len(leaves):
            raise WorkflowError("需提供范围内至少一组原始读数。")
        known=set()
        for r in p["readings"]:
            check(r,"entity entity_id field value unit source_ref","观测字段")
            for k in ("entity","entity_id","field"):
                identify(r[k],k)
            leaf=tuple(r[k] for k in ("entity","entity_id","field"))
            if leaf not in leaves or leaf in known or r["unit"]!=leaves[leaf]:
                raise WorkflowError("观察对象、字段或计量单位不属于登记范围，或读数重复。")
            known.add(leaf)
            amount(r["value"],r["field"])
            term(r["source_ref"],"原字段来源",r["value"] is None)
        return "watch:obs:"+site+":"+name+":"+p["record_id"]+":"+str(p["revision"]),pins,ctx
    cutoff=stamp(p["cutoff_at"])
    if cutoff<stamp(reg["effective_at"]):
        raise WorkflowError("取件截点不能早于登记生效。")
    identify(p["view_id"],"截点视图编号")
    # The current view pulls saved scoped events; an old cutoff sees only events received by then.
    for row in list_ready(site,name,UPDATE):
        if (row["registry_task_id"]==reg["task_id"] and
                stamp(row["captured_at"])<cutoff and stamp(row["arrived_at"])<=cutoff):
            read(row["task_id"],UPDATE)
    return "watch:view:"+site+":"+name+":"+p["view_id"],pins,ctx


def observation(p,reg):
    return {"state":"SAVED_OFFLINE","registry_task_id":reg["task_id"],"registry_version":reg["version"],
            **{k:p[k] for k in ("record_id","revision","previous_task_id","captured_at","arrived_at","correction_ref","readings")}}


def current_view(p,ctx,reg):
    typed=catalog(reg["targets"],p["currency"])
    cutoff=stamp(p["cutoff_at"])
    records=defaultdict(list)
    for r in ctx.values():
        if r["tool_id"]==UPDATE:
            records[r["record_id"]].append(r)
    latest={}
    historical={}
    for key,rows in records.items():
        chain=sorted(rows,key=lambda x:x["revision"])
        current=chain[-1]
        latest[key]=current
        historical[key]=[{k:r[k] for k in ("task_id","revision","arrived_at","captured_at","correction_ref","previous_task_id")}
                         for r in chain]
    live=defaultdict(list)
    for r in latest.values():
        for reading in r["readings"]:
            k=(reading["entity"],reading["entity_id"],reading["field"])
            live[k].append((stamp(r["captured_at"]),r,reading))
    fields=[]
    for key,unit in typed.items():
        rows=live[key]
        last_capture=max((t for t,_,_ in rows),default=None)
        matched=[item for item in rows if item[0]==last_capture]
        vals={reading["value"] for _,_,reading in matched}
        value=next(iter(vals)) if len(vals)==1 else None
        fields.append({"entity":key[0],"entity_id":key[1],"field":key[2],"unit":unit,"value":value,
                       "state":"CONFLICT" if len(vals)>1 else "OBSERVED" if value is not None else "UNKNOWN",
                       "sources":[{"task_id":r["task_id"],"record_id":r["record_id"],"revision":r["revision"],
                                   "captured_at":r["captured_at"],"arrived_at":r["arrived_at"],
                                   "reported_value":reading["value"],"source_ref":reading["source_ref"]}
                                  for _,r,reading in matched]})
    count=sum(x["state"]=="OBSERVED" for x in fields)
    return {"state":"COMPLETE_EVIDENCE" if count==len(fields) else "PARTIAL_EVIDENCE","view_id":p["view_id"],
            "cutoff_at":p["cutoff_at"],"registry_task_id":reg["task_id"],"registry_version":reg["version"],
            "cadences":window(cutoff,stamp(reg["effective_at"])),"fields":fields,"field_count":len(fields),
            "known_fields":count,"unknown_or_conflicting_fields":len(fields)-count,
            "distinct_observation_ids":len(records),"observation_history":historical,
            "market_full_coverage":False,"scheduled_fetches_started":False}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","scope","watch_id","input_sha256","upstream")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets or tool not in TOOLS:
        raise WorkflowError("监控簿只接受已登记的本机结构化资料。")
    key,pins,ctx=linked
    if tool==REGISTER:
        out={"state":"REGISTERED_OFFLINE",**{k:p[k] for k in ("version","previous_task_id","effective_at","source_ref","targets")},
             "cadences":window(stamp(p["effective_at"]),stamp(p["effective_at"]))}
    else:
        reg=ctx[p["registry_task_id"]]
        out=observation(p,reg) if tool==UPDATE else current_view(p,ctx,reg)
    r={**out,"site":site,"currency":p["currency"],"scope":SYNTHETIC,"timezone":ZONE[site],"tool_id":tool,
       "task_id":folder.name,"watch_id":p["watch_id"],"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "backend_key":key,"execution_authorized":False,"model_called":False,"provider_called":False}
    raw=encode(r)
    title={REGISTER:"监控范围登记",UPDATE:"监控观测入库",VIEW:"监控截至视图"}[tool]
    lines=[f"# {site} {title} · {p['watch_id']}","",f"结论：{r['state']}。本站合成留档，本币 {r['currency']}。"]
    if tool==VIEW:
        lines.extend(["",f"截至 {r['cutoff_at']}，已知 {r['known_fields']}/{r['field_count']}，缺证或冲突另列。","",
                      "| 对象 | 字段 | 值 | 状态 |","|---|---|---|---|"])
        for item in r["fields"]:
            lines.append("| "+" | ".join(map(safe_md,[item[k] for k in ("entity_id","field","value","state")]))+" |")
    report=("\n".join(lines)+"\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(b)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["watch_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("监控来源或报告与本机回执不一致。")


def public_tools():
    labels={REGISTER:"登记竞品监控范围与排期",UPDATE:"竞品观测更新和修订",VIEW:"竞品监控簿截至视图"}
    return [{"id":tool,"sites":list(ROUTES),"role_suffix":"competitor-analyst","label":labels[tool],
             "description":"合成资料按本站、对象、口径与版本保存。","input_example":{}} for tool in TOOLS]

