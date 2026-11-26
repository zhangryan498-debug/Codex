"""Build immutable, same-site competitor bulletins from retained price and monitoring evidence."""

from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_competitor_handoff import FEEDBACK
from workflow_competitor_price_band import TOOL as PRICE
from workflow_lead_weekly import encode, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL = "competitor-periodic-bulletin"
FILES = {"json":"competitor-bulletin.json","report":"competitor-bulletin.md","receipt":"competitor-bulletin-receipt.json"}
CADENCES = ("day","week","month")
WINDOWS = ("previous","current")
SIGHTING_FIELDS = ("id","shop_id","product_id","sku_id","captured_at","source_ref")
PRICE_FIELDS = ("list_minor","conditional_checkout_minor","observed_order_minor")


class WorkflowError(ValueError):
    pass


def midnight(value):
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def month_prior(first):
    return (first-timedelta(days=1)).replace(day=1)


def bounds(cadence, anchor):
    if cadence in ("day","week"):
        length = timedelta(days=1 if cadence=="day" else 7)
        return {"previous":(anchor-2*length,anchor-length),"current":(anchor-length,anchor)}
    if anchor.day==1:
        lo = month_prior(anchor)
        return {"previous":(month_prior(lo),lo),"current":(lo,anchor)}
    lo = anchor.replace(day=1)
    prev = month_prior(lo)
    return {"previous":(prev,min(lo,prev+(anchor-lo))),"current":(lo,anchor)}


def validate(site, p):
    check(p, "site scope currency bulletin_id cadence anchor_at cutoff_at revision previous_task_id "
             "price_sources monitoring_tasks sightings owner next_review_at", "竞品情报")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("只接受所选站点、币种和明确标记的合成证据。")
    if p["cadence"] not in CADENCES:
        raise WorkflowError("请选择日、滚动七日或月度累计。")
    anchor, cutoff = (stamp(p[k]) for k in ("anchor_at","cutoff_at"))
    if anchor!=midnight(anchor) or cutoff<anchor or stamp(p["next_review_at"])<cutoff:
        raise WorkflowError("窗口需由本站零点闭合并给出晚于截点的复核时刻。")
    term(p["owner"],"补证负责人")
    identify(p["bulletin_id"],"报告编号")
    amount(p["revision"],"报告版本",1,1000000,False)
    if (p["revision"]==1)!=(p["previous_task_id"] is None):
        raise WorkflowError("后续版本需要同报的上一版，本期首版不可引用前版。")
    if p["previous_task_id"]:
        task_id(p["previous_task_id"],"上一版任务")
    prices=p["price_sources"]
    if not isinstance(prices,list) or not 1<=len(prices)<=12:
        raise WorkflowError("至少引用一个已保存的价格证据任务。")
    for source in prices:
        check(source,"task_id available_at","归档价格证据")
        task_id(source["task_id"],"价格来源任务")
        stamp(source["available_at"])
    monitors=p["monitoring_tasks"]
    if not isinstance(monitors,list) or len(monitors)>12:
        raise WorkflowError("最多引用十二份监测回执。")
    for ident in monitors:
        task_id(ident,"监测回执")
    rows=p["sightings"]
    if not isinstance(rows,list) or len(rows)>200:
        raise WorkflowError("单份报告最多接收两百条留档观测。")
    for r in rows:
        check(r,"site currency available_at "+" ".join(SIGHTING_FIELDS),"留档观测")
        if (r["site"],r["currency"])!=(site,p["currency"]):
            raise WorkflowError("留档观测不能跨站或跨币。")
        for k in SIGHTING_FIELDS[:4]:
            identify(r[k],k)
        term(r["source_ref"],"观测来源",True)
        if stamp(r["captured_at"])>stamp(r["available_at"]):
            raise WorkflowError("来源到达时间不能早于留档抓取时间。")
    return bounds(p["cadence"],anchor),cutoff


def jsha(value):
    return sha256(encode(value)).hexdigest()


def bind(site,p,load):
    period,cutoff=validate(site,p)
    pins={}
    prices={}
    for entry in p["price_sources"]:
        ident=entry["task_id"]
        arrival=entry["available_at"]
        if ident in prices:
            if prices[ident][1]!=arrival:
                raise WorkflowError("同一价格任务的到达时点冲突，须保留原引用后补证。")
            continue
        result,pins[ident]=load(ident,site,PRICE)
        observations=result["observations"]
        if any(stamp(q["observed_at"])>stamp(arrival) for q in observations):
            raise WorkflowError("归档价格来源到达早于已有抓取时点。")
        prices[ident]=(result,arrival)
    monitoring={}
    for ident in dict.fromkeys(p["monitoring_tasks"]):
        monitoring[ident],pins[ident]=load(ident,site,FEEDBACK)
    old=None
    if p["previous_task_id"]:
        ident=p["previous_task_id"]
        old,pins[ident]=load(ident,site,TOOL)
        if ((old["bulletin_id"],old["cadence"],old["anchor_at"],old["revision"]+1) !=
                (p["bulletin_id"],p["cadence"],p["anchor_at"],p["revision"]) or
                stamp(old["cutoff_at"])>=cutoff):
            raise WorkflowError("上一版必须是同报告、同区间、连续版本与更早截点。")
        if (not set(old["price_source_task_ids"])<=set(prices) or
                not set(old["monitoring_source_task_ids"])<=set(monitoring) or
                not set(old["sighting_fingerprints"])<={jsha(r) for r in p["sightings"]}):
            raise WorkflowError("修订须保留历史来源与已提交的原始观测。")
        for ident,arrival in old["price_arrival_by_task"].items():
            if prices[ident][1]!=arrival:
                raise WorkflowError("修订应保留原价格来源到达时间。")
    return period,cutoff,pins,prices,monitoring,old


def capture_records(prices,sightings):
    records=[]
    for result,arrival in prices.values():
        for x in result["observations"]:
            records.append({"observation_id":x["id"],"identity":x["identity"],"source_kind":"c04_conditional_quote",
                "source_ref":x["source_ref"],"captured_at":x["observed_at"],"available_at":arrival,
                "source_task_id":result["task_id"],"state":x["state"],"comparison":x["comparison_fields"],
                **{k:x[k] for k in PRICE_FIELDS},"payment":x["paid_evidence"]})
    for r in sightings:
        records.append({"observation_id":r["id"],"identity":[r["shop_id"],r["product_id"],r["sku_id"]],
            "captured_at":r["captured_at"],"available_at":r["available_at"],"source_ref":r["source_ref"],
            "source_kind":"synthetic_listing_sighting","source_task_id":None,
            "state":"OBSERVED" if r["source_ref"] else "PENDING",
            "comparison":None,**{k:None for k in PRICE_FIELDS},"payment":None})
    return records


def signature(r):
    return jsha({k:v for k,v in r.items() if k not in ("available_at","source_task_id","observation_id")})


def unique(records, cutoff, anchor):
    accepted=[]
    future=[]
    late=[]
    group=defaultdict(list)
    for r in records:
        if stamp(r["captured_at"])>=anchor:
            future.append(r)
        elif stamp(r["available_at"])>cutoff:
            late.append(r)
        else:
            group[(r["source_kind"],r["observation_id"])].append(r)
    duplicate=0
    conflicts=[]
    by_capture=defaultdict(list)
    for rows in group.values():
        if len({signature(r) for r in rows})!=1:
            conflicts.append({"kind":"same_id_different_payload","rows":rows})
            continue
        duplicate+=len(rows)-1
        r=min(rows,key=lambda x:stamp(x["available_at"]))
        key=(r["source_kind"],tuple(r["identity"]),r["captured_at"],tuple(r["comparison"] or ()))
        by_capture[key].append(r)
    for rows in by_capture.values():
        if len({signature(r) for r in rows})!=1:
            conflicts.append({"kind":"same_capture_different_payload","rows":rows})
            continue
        duplicate+=len(rows)-1
        accepted.append(min(rows,key=lambda r:(stamp(r["available_at"]),r["observation_id"])))
    return accepted,late,future,conflicts,duplicate


def in_window(stamp_value,window):
    return window[0]<=stamp(stamp_value)<window[1]


def coverage(rows,window):
    dates=sorted({stamp(r["captured_at"]).date().isoformat() for r in rows if r["source_ref"]})
    lo,hi=window
    expected=[(lo+timedelta(days=i)).date().isoformat() for i in range((hi-lo).days)]
    return {"start":lo.isoformat(),"end_exclusive":hi.isoformat(),"observation_count":len(rows),
            "verified_capture_dates":dates,"missing_capture_dates":[d for d in expected if d not in dates],
            "complete_market_coverage":False}


def novelty(rows,current,conflicts):
    by_product=defaultdict(list)
    by_sku=defaultdict(list)
    for r in rows:
        if r["source_ref"]:
            by_product[tuple(r["identity"][:2])].append(r)
            by_sku[tuple(r["identity"])].append(r)
    suspect={tuple(r["identity"][:2]) for g in conflicts for r in g["rows"]}
    out=[]
    for key,rs in by_sku.items():
        first=min(rs,key=lambda r:(stamp(r["captured_at"]),r["observation_id"]))
        if not in_window(first["captured_at"],current):
            continue
        preceding=min(by_product[key[:2]],key=lambda r:(stamp(r["captured_at"]),r["identity"],r["observation_id"]))
        status=("PENDING_CONFLICT" if key[:2] in suspect else
                "FIRST_OBSERVED_PRODUCT" if first["identity"]==preceding["identity"] else "FIRST_OBSERVED_SKU_OF_KNOWN_PRODUCT")
        out.append({"identity":list(key),"state":status,"first_sku_capture":first["captured_at"],
                    "first_product_capture":preceding["captured_at"],"first_product_observation_id":preceding["observation_id"],
                    "observation_id":first["observation_id"],
                    "source_ref":first["source_ref"],"source_task_id":first["source_task_id"],
                    "actual_launch_at":None,"confidence":"retained_local_observation_only"})
    return sorted(out,key=lambda n:(n["first_sku_capture"],n["identity"]))


def quote_pairs(selected, period):
    buckets=defaultdict(lambda:{"previous":[],"current":[]})
    for w,rows in selected.items():
        for r in rows:
            if (r["source_kind"]=="c04_conditional_quote" and r["comparison"] is not None
                    and None not in r["comparison"] and r["state"]=="READY_OFFLINE"
                    and r["conditional_checkout_minor"] is not None and r["source_ref"]):
                buckets[tuple(r["identity"])+tuple(r["comparison"])][w].append(r)
    output=[]
    for pair in buckets.values():
        before={}
        after={}
        for name,target in (("previous",before),("current",after)):
            for r in pair[name]:
                dt=stamp(r["captured_at"])
                target[dt-period[name][0]]=r
        matching=sorted(set(before).intersection(after))
        if not matching:
            continue
        matched=matching[-1]
        a,b=before[matched],after[matched]
        x,y=a["conditional_checkout_minor"],b["conditional_checkout_minor"]
        output.append({"identity":a["identity"],"comparison":a["comparison"],"previous":x,"current":y,
                       "delta_minor":y-x,"measurement":"conditional_checkout_minor",
                       "source_captures":[{"observation_id":r["observation_id"],"captured_at":r["captured_at"],
                                           "source_task_id":r["source_task_id"],"source_ref":r["source_ref"]} for r in (a,b)],
                       "confidence":"same_sku_terms_and_relative_time_in_retained_synthetic_sample",
                       "long_term_trend_confirmed":False})
    return sorted(output,key=lambda x:(x["identity"],x["comparison"]))


def monitor_rows(monitors, current, cutoff):
    out=[]
    for row in monitors.values():
        t=row["evaluated_at"]
        if in_window(t,current) and stamp(t)<=cutoff:
            out.append({k:row[k] for k in ("task_id","fact_refs","evaluated_at","counterevidence","next_action","version","state",
                                         "source_state_preserved","unblocked_source","release_status")})
    return out


def compute(p, linked):
    period,cutoff,pins,prices,monitoring,old=linked
    anchor=period["current"][1]
    raw=capture_records(prices,p["sightings"])
    accepted,late,future,conflicts,duplicate=unique(raw,cutoff,anchor)
    selected={w:sorted((r for r in accepted if in_window(r["captured_at"],period[w])),
                       key=lambda r:(r["captured_at"],r["identity"],r["observation_id"])) for w in WINDOWS}
    rows=selected["current"]
    days={w:coverage(selected[w],period[w]) for w in WINDOWS}
    new=novelty(accepted,period["current"],conflicts)
    quotes=quote_pairs(selected,period)
    mon=monitor_rows(monitoring,period["current"],cutoff)
    blocking_price_tasks=[i for i,(r,t) in prices.items() if stamp(t)<=cutoff and r["quarantined_conflicting_observations"]]
    state=("PARTIAL_COVERAGE" if rows else "NO_OBSERVATIONS")
    action=[]
    if quotes:
        action.append({"kind":"price","owner":p["owner"],"due_at":p["next_review_at"],
                       "verify":"复取同店同 SKU、同规格、券资格和配送条件，核到手价与实际成交分层。",
                       "observation_ids":[x["observation_id"] for r in quotes for x in r["source_captures"]]})
    if new:
        action.append({"kind":"new_listing","owner":p["owner"],"due_at":p["next_review_at"],
                       "verify":"补原商品页和历史快照，区分首次看到、已有商品新变体和平台实际首发。",
                       "observation_ids":[x["observation_id"] for x in new]})
    for evidence in mon:
        action.append({"kind":"monitor_followup","owner":p["owner"],"due_at":p["next_review_at"],
                       "verify":evidence["next_action"],"source_task_id":evidence["task_id"],
                       "release_status":evidence["release_status"]})
    if not quotes or days["current"]["missing_capture_dates"] or conflicts or blocking_price_tasks:
        action.append({"kind":"sample_gap","owner":p["owner"],"due_at":p["next_review_at"],
                       "verify":"补缺日留档或同规格对照，复核冲突来源；未取到按缺测保留。",
                       "missing_capture_dates":days["current"]["missing_capture_dates"]})
    prev_admitted=set(old["admitted_observation_keys"]) if old else set()
    admitted={r["source_kind"]+":"+r["observation_id"] for r in accepted}
    return {"state":state,"cadence":p["cadence"],"bulletin_id":p["bulletin_id"],"revision":p["revision"],
            "anchor_at":p["anchor_at"],"cutoff_at":p["cutoff_at"],"coverage":days,"observations":selected,
            "new_listing_observations":new,"same_sku_quote_pairs":quotes,"monitoring_context":mon,
            "late_observations":late,"out_of_window_future_observations":future,"conflicts":conflicts,
            "source_price_conflict_task_ids":blocking_price_tasks,"duplicate_observations_skipped":duplicate,
            "admitted_observation_keys":sorted(admitted),"sighting_fingerprints":sorted({jsha(r) for r in p["sightings"]}),
            "newly_admitted_after_revision":sorted(admitted-prev_admitted) if old else [],
            "withdrawn_after_revision":sorted(prev_admitted-admitted) if old else [],
            "previous_version_task":old["task_id"] if old else None,
            "price_source_task_ids":list(prices),"price_arrival_by_task":{k:v[1] for k,v in prices.items()},
            "monitoring_source_task_ids":list(monitoring),"upstream":pins,"next_actions":action,
            "actual_launch_inferred":False,"market_sales_total":None,"fresh_market_refresh_performed":False}


def render(r):
    period={"day":"日报","week":"滚动七日周报","month":"月度累计"}[r["cadence"]]
    current=r["coverage"]["current"]
    lines=[f"# {r['site']} 竞品{period} · {r['bulletin_id']} v{r['revision']}","",
           f"结论：本期留档 {current['observation_count']} 条，"
           f" 首次观测 {len(r['new_listing_observations'])} 条，同条件价格对照 {len(r['same_sku_quote_pairs'])} 组。",
           f"统计窗 {current['start']} 至 {current['end_exclusive']}（不含）；取证截点 {r['cutoff_at']}；币种 {r['currency']}。",
           "本地合成样本。首次观测的实际首发未知；缺测保留，未刷新平台数据。","",
           "| 后续核验 | 负责人 | 期限 |","|---|---|---|"]
    for a in r["next_actions"]:
        lines.append("| "+" | ".join(map(safe_md,(a["verify"],a["owner"],a["due_at"])))+" |")
    return "\n".join(lines)+"\n\n"+encode(r).decode()


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","scope","input_sha256","upstream","revision","cadence")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets or tool!=TOOL:
        raise WorkflowError("仅消费本机冻结的本站证据。")
    r={**compute(p,linked),"task_id":folder.name,"site":site,"currency":p["currency"],"tool_id":tool,"scope":SYNTHETIC,
       "input_sha256":jsha(p),"execution_authorized":False,"provider_called":False,"model_called":False}
    raw,report=encode(r),render(r).encode()
    files={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(receipt(r,raw,report))}.items():
        with files[k].open("xb") as sink:sink.write(b)
    return r,files


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or
        (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
        r["upstream"]!=task["source"]["bulletin_pins"] or
        json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("竞品报告或来源回执无法核对。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"competitor-analyst","label":"竞品日周月情报与首次观测",
             "description":"按本站时间、同 SKU、留档首见和证据截点独立生成。","input_example":{}}]

