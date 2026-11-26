"""Model-free review of versioned teardown evidence and synthetic CN product cards."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from hashlib import sha256
import json
from struct import unpack_from
from zoneinfo import ZoneInfo

from role_tools import ROUTES, SYNTHETIC
from workflow_cn_sales_plans import SEARCH
from workflow_director_review import mp4_boxes
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id
from workflow_teardown_library import TOOL as LIBRARY

TEARDOWN = "teardown-offline-period-review"
SHOP = "cn-shop-offline-card-health"
TOOLS = (TEARDOWN, SHOP)
FILES = {"json": "offline-review.json", "report": "offline-review.md", "receipt": "offline-review-receipt.json"}
CHANNELS = {"CN": {"recommendation", "search", "product_card"}, "PH": {"recommendation", "search", "affiliate"}}
TIMEZONES = {"CN": "Asia/Shanghai", "PH": "Asia/Manila"}
RED_LINES = ("description_match", "claims_supported", "media_rights", "price_consistent")
NUMBER_FIELDS = ("quality_score", "sales", "positive_reviews", "total_reviews", "detailed_10_chars_and_image")
CARD_METHOD = "CN_SHOP_PLAYBOOK_OFFLINE_CHECKLIST"
METHOD_PATH = ".agents/skills/douyin-shop-ops-playbook/SKILL.md"


class WorkflowError(ValueError):
    pass


def number(value, key, upper=10**9):
    return amount(value, key, 0, upper, True)


def sha(value):
    return sha256(value).hexdigest()


def absolute_time(value):
    try:
        instant=datetime.fromisoformat(value.replace("Z","+00:00"))
        if instant.utcoffset() is not None:
            return instant
    except (AttributeError,ValueError):
        pass
    raise WorkflowError("时间戳须保留原始 UTC 偏移。")


def header(p, site):
    if (p["site"], p["scope"], p["currency"]) != (site, SYNTHETIC, ROUTES[site][1]):
        raise WorkflowError("只接受明确标注的本站和本币合成输入。")
    identify(p["job_id"], "验收任务号")
    term(p["owner"], "负责人")
    return absolute_time(p["as_of"])


def _day_window(p, as_of):
    start, end = stamp(p["starts_at"]), stamp(p["ends_at"])
    local_start, local_end = (d.astimezone(ZoneInfo(TIMEZONES[p["site"]])) for d in (start, end))
    if not (start < end <= as_of < stamp(p["feedback_due"])) or any(t.time() != time.min for t in (local_start, local_end)):
        raise WorkflowError("周月窗口须为完整本站自然日，已结束并有晚于复盘的反馈期限。")
    first, last = local_start.date(), local_end.date()
    if p["period"] == "week" and last-first == timedelta(days=7):
        return start, end
    if (p["period"] == "month" and first.day == last.day == 1 and
            last.year*12+last.month == first.year*12+first.month+1):
        return start, end
    raise WorkflowError("仅支持七自然日复盘和连续自然月复盘。")


def _observation(o, p, elements):
    check(o, "evidence_id site product_id version element_id channel arm account_id basis measurement_started_at measurement_ended_at recorded_at source_ref views clicks cod", "原始同口径观测")
    if (o["site"], o["product_id"], o["version"]) != (p["site"], p["product_id"], p["version"]):
        raise WorkflowError("观测记录的站点、商品或要素版本不匹配。")
    if o["element_id"] not in elements or o["channel"] not in CHANNELS[p["site"]] or o["arm"] not in ("reference", "variant"):
        raise WorkflowError("观测记录须绑定本库当前要素和支持的渠道/对照组。")
    identify(o["evidence_id"], "观测编号")
    term(o["source_ref"], "合成观测出处")
    identify(o["account_id"], "冻结匿名账号")
    term(o["basis"], "计量口径与观察龄")
    seen = stamp(o["recorded_at"])
    measured_from,measured_until=(stamp(o[k]) for k in ("measurement_started_at","measurement_ended_at"))
    if measured_until-measured_from!=timedelta(days=7) or measured_until>seen:
        raise WorkflowError("当前离线复盘仅比较已完成七日观测龄的同口径证据。")
    for key in ("views", "clicks"):
        number(o[key], key)
    if o["cod"] is not None:
        check(o["cod"], "cohort_id assigned signed final_failed in_transit mature maturity_rule opened_at", "COD 合成队列")
        cod=o["cod"]
        if p["site"] != "PH" or type(cod["mature"]) is not bool or cod["maturity_rule"] != "SYN_DAY_7":
            raise WorkflowError("COD 只接受 PH 同一冻结模拟口径。")
        identify(cod["cohort_id"], "COD 队列编号")
        cohort_age = seen - stamp(cod["opened_at"])
        if cohort_age < timedelta(0) or cod["mature"] != (cohort_age >= timedelta(days=7)):
            raise WorkflowError("COD 队列须按冻结测试的七日窗独立核算成熟度。")
        for key in ("assigned", "signed", "final_failed", "in_transit"):
            number(cod[key], key)
    return seen


def bind_teardown(site, p, library_lookup, fetch):
    check(p, "site scope currency job_id owner library_id product_id library_task_id version period starts_at ends_at as_of feedback_due observations prior_review_task_id", "拆解周期复盘")
    as_of=header(p,site)
    start,end=_day_window(p,as_of)
    for key in ("library_id","product_id"):
        identify(p[key],key)
    task_id(p["library_task_id"],"拆解库任务")
    amount(p["version"],"拆解库修订",1,10**6,False)
    original,pin=fetch(p["library_task_id"],site,LIBRARY)
    lookup,history_pins=library_lookup(site,p)
    if ((original["library_id"],original["product_id"],original["version"],original["mode"]) !=
            (p["library_id"],p["product_id"],p["version"],"save") or lookup["current_task_id"]!=p["library_task_id"]):
        raise WorkflowError("须引用截至复盘时点的本站同商品当前拆解库版本。")
    pins={"library":pin,"library_history":history_pins}
    previous=None
    if p["prior_review_task_id"] is not None:
        task_id(p["prior_review_task_id"],"上期复盘编号")
        previous,pins["previous"]=fetch(p["prior_review_task_id"],site,TEARDOWN)
        if ((previous["library_id"],previous["product_id"],previous["version"])!=(p["library_id"],p["product_id"],p["version"])
                or not stamp(previous["as_of"])<as_of or stamp(previous["ends_at"])>end):
            raise WorkflowError("反馈须来自本站同商品同版本的此前复盘。")
    elements={x["id"]:x for x in lookup["elements"]}
    if not isinstance(p["observations"],list) or len(p["observations"])>400:
        raise WorkflowError("观测须为最多 400 条合成结构化记录。")
    for observation in p["observations"]:
        _observation(observation,p,elements)
    return pins,(lookup,start,end,previous)


def _cod(row):
    if row is None:
        return None
    vals=[row[k] for k in ("assigned","signed","final_failed","in_transit")]
    assigned,signed,failed,transit=vals
    if None in vals:
        state="MISSING_EVIDENCE"
    elif signed+failed+transit!=assigned:
        state="CONFLICT"
    elif not row["mature"] or transit:
        state="PENDING_MATURITY"
    elif not assigned:
        state="ZERO_DENOMINATOR"
    else:
        state="OBSERVED_FIXTURE"
    return {**row,"state":state,"signed_rate":signed/assigned if state=="OBSERVED_FIXTURE" else None}


def _channel_pair(arms):
    samples={arm:rows[0] if len(rows)==1 else None for arm,rows in arms.items()}
    reference,variant=(samples.get(arm) for arm in ("reference","variant"))
    rates={}
    for arm,row in samples.items():
        views,clicks=(row[k] for k in ("views","clicks")) if row else (None,None)
        if None in (views,clicks) or clicks>views:
            rates[arm]=None
        else:
            rates[arm]=clicks/views if views else None
    state="COMPARABLE"
    if any(len(rows)>1 for rows in arms.values()):
        state="CONFLICT"
    elif reference is None or variant is None:
        state="MISSING_EVIDENCE"
    elif (reference["basis"],reference["account_id"]) != (variant["basis"],variant["account_id"]):
        state="NONCOMPARABLE"
    elif any(row["views"] is not None and row["clicks"] is not None and row["clicks"]>row["views"]
             for row in (reference,variant)):
        state="CONFLICT"
    elif None in rates.values():
        state="INSUFFICIENT_DENOMINATOR"
    delta=(round((rates["variant"]-rates["reference"])*100,4) if state=="COMPARABLE" else None)
    cod={arm:_cod(row["cod"]) if row else None for arm,row in samples.items()}
    cohort_ids={c["cohort_id"] for c in cod.values() if c}
    cod_comparable=bool(len(cohort_ids)==2 and all(c and c["state"]=="OBSERVED_FIXTURE" for c in cod.values())
                        and state=="COMPARABLE")
    basis={"account_id":reference["account_id"],"definition":reference["basis"],"observation_age_days":7} if state=="COMPARABLE" else None
    return {"state":state,"comparison_basis":basis,
            "observations_at":{k:v["recorded_at"] for k,v in samples.items() if v},"evidence_ids":{k:[o["evidence_id"] for o in v] for k,v in arms.items()},
            "click_rates":rates,"difference_pp":delta,"cod":cod,"cod_comparable":cod_comparable,
            "cod_difference_pp":round((cod["variant"]["signed_rate"]-cod["reference"]["signed_rate"])*100,4)
            if cod_comparable else None}


def _review(p, library, start, end, previous):
    as_of=stamp(p["as_of"])
    by_id=defaultdict(dict)
    for row in p["observations"]:
        by_id[row["evidence_id"]][encode(row)]=row
    covered_days=set()
    by_pair=defaultdict(lambda:defaultdict(list))
    raw_audit={}
    for key,group in sorted(by_id.items()):
        rows=list(group.values())
        current=[row for row in rows if start<=stamp(row["recorded_at"])<end and stamp(row["recorded_at"])<=as_of]
        state="OUT_OF_WINDOW" if not current else "CONFLICT" if len(current)>1 else "IN_WINDOW"
        raw_audit[key]={"state":state,"raw":rows,"excluded_rows":len(rows)-len(current)}
        for row in current:
            covered_days.add(stamp(row["recorded_at"]).astimezone(ZoneInfo(TIMEZONES[p["site"]])).date().isoformat())
            by_pair[(row["element_id"],row["channel"])][row["arm"]].append(row)
    results=[]
    for (eid,channel),arms in sorted(by_pair.items()):
        pair=_channel_pair(arms)
        if any(raw_audit[x["evidence_id"]]["state"]=="CONFLICT" for rows in arms.values() for x in rows):
            pair={**pair,"state":"CONFLICT","comparison_basis":None,"difference_pp":None,"cod_comparable":False,"cod_difference_pp":None}
        results.append({"element_id":eid,"channel":channel,**pair})
    registry={e["id"]:e for e in library["elements"]}
    coverage={eid:{"state":"AVAILABLE" if any(x["element_id"]==eid and x["state"]=="COMPARABLE" for x in results) else "PENDING_EVIDENCE",
                   "lifecycle":row["lifecycle"],"metadata_state":row["metadata_state"],"source":row["source"],
                   "provenance":row["provenance"],"localization":row.get("localization")}
              for eid,row in registry.items()}
    allowed=library["rights_state"]=="allowed_offline_reference"
    instructions={
        "search":"只比较同词意图及相同商品页，编导复核首镜和标题对应，跟踪搜索点击。",
        "recommendation":"固定同商品及渠道观察窗，只替换一个首镜变量，拆解岗下一期核对点击证据。",
        "product_card":"对齐同 SKU 卡片展示与标题，商城岗核对卡片入口点击，分开记搜索。",
        "affiliate":"核对联盟素材版本及合作来源；先等 COD 终态，再比较签收率。",
    }
    actions=[]
    for r in results:
        element=registry[r["element_id"]]
        can_propose=(allowed and element["metadata_state"]=="REFERENCE_METADATA_READY"
                     and element["lifecycle"]["state"]=="ACTIVE_RECORD" and r["state"]=="COMPARABLE"
                     and (r["channel"]!="affiliate" or r["cod_comparable"]))
        actions.append({"action_id":f"{p['job_id']}:{r['element_id']}:{r['channel']}","element_id":r["element_id"],
                        "channel":r["channel"],"owner":p["owner"],"due":p["feedback_due"],
                        "status":"PLAN_OFFLINE_SINGLE_VARIABLE" if can_propose else "VERIFY_EVIDENCE_FIRST",
                        "instruction":instructions[r["channel"]] if can_propose else "先补足来源、可比样本或素材权利，再审查使用条件。",
                        "result_followup":"PENDING","verified_increment":None})
    groups=defaultdict(list)
    for r,action in zip(results,actions):
        if action["status"]=="PLAN_OFFLINE_SINGLE_VARIABLE":
            b=r["comparison_basis"]
            groups[(r["channel"],b["account_id"],b["definition"])].append(r)
    priority=defaultdict(list)
    for (channel,account,definition),eligible in sorted(groups.items()):
        axis="cod_difference_pp" if channel=="affiliate" else "difference_pp"
        ranked=sorted(eligible,key=lambda r:(-r[axis],r["element_id"]))
        values=sorted({r[axis] for r in ranked},reverse=True)
        priority[channel].extend({"element_id":r["element_id"],"dense_rank":values.index(r[axis])+1,
             "delta_pp":r[axis],"metric":axis,"account_id":account,"definition":definition} for r in ranked)
    def follows(a,r,earlier):
        key=(a["element_id"],a["channel"])
        if (r["element_id"],r["channel"])!=key or r["comparison_basis"] is None:
            return False
        old=earlier.get(key)
        return bool(old and r["comparison_basis"]==old["comparison_basis"] and
                    (r["channel"]!="affiliate" or r["cod_comparable"]) and
                    all(stamp(t)>stamp(previous["as_of"]) for t in r["observations_at"].values()) and
                    not ({i for ids in r["evidence_ids"].values() for i in ids} &
                         {i for ids in old["evidence_ids"].values() for i in ids}))
    prior=None
    if previous:
        earlier={(r["element_id"],r["channel"]):r for r in previous["channel_results"]}
        prior={"task_id":previous["task_id"],"actions":[{"action_id":a["action_id"],"owner":a["owner"],"due":a["due"],
                "feedback_state":"DESCRIPTIVE_FOLLOWUP_AVAILABLE" if any(follows(a,r,earlier) for r in results)
                else "FEEDBACK_PENDING","causal_effect":None} for a in previous["actions"]]}
    return {"state":"REVIEW_RECORDED" if results and all(c["state"]=="AVAILABLE" for c in coverage.values()) else "PENDING_EVIDENCE",
            "library_id":p["library_id"],"library_history":library["history"],"product_id":p["product_id"],"version":p["version"],
            "period":p["period"],"starts_at":p["starts_at"],"ends_at":p["ends_at"],"timezone":TIMEZONES[p["site"]],
            "rights_state":library["rights_state"],"reference_only":True,"coverage":coverage,"channel_results":results,
            "period_coverage":{"days_in_window":(end-start).days,"observed_dates":sorted(covered_days),
              "all_dates_observed":len(covered_days)==(end-start).days},
            "raw_observations":raw_audit,"exact_repeats_ignored":len(p["observations"])-sum(map(len,by_id.values())),
            "actions":actions,"next_test_priority_by_channel":priority,"prior_feedback":prior,"pooled_across_channels":False,"verified_actual_increment":None,
            "ranking_basis":"差值仅用于同渠道描述，按要素现有生命周期保留或归档，无证不排名。",
            "actual_market_rule_verified":False,"actual_publication":False}


def _card_snapshot(row, p, as_of):
    if row is None:
        return False
    check(row, "site sku_id revision observed_at valid_until source_ref quality_score sales positive_reviews total_reviews detailed_10_chars_and_image services red_lines price_minor expected_card_price_minor fixture_claims image_slots listing_specs after_sales", "冻结商品卡模拟快照")
    if (row["site"],row["sku_id"],row["revision"]) != (p["site"],p["sku_id"],p["version"]):
        raise WorkflowError("商品卡快照须属同站、同 SKU、同版本。")
    seen,expires=absolute_time(row["observed_at"]),absolute_time(row["valid_until"])
    if seen>=expires:
        raise WorkflowError("商品卡快照时间窗无效。")
    term(row["source_ref"],"合成快照原件")
    for name in NUMBER_FIELDS+("price_minor","expected_card_price_minor"):
        number(row[name],name,100 if name=="quality_score" else 10**9)
    check(row["red_lines"]," ".join(RED_LINES),"合成独立对照")
    for val in row["red_lines"].values():
        if val is not None and type(val) is not bool:
            raise WorkflowError("红线证据只接受已证符合、已证不符或未知。")
    if row["services"] is not None:
        if not isinstance(row["services"],list) or len(row["services"])>24:
            raise WorkflowError("服务权益须为有证据的项目列表。")
        for service in row["services"]:
            term(service,"服务权益")
    if row["fixture_claims"] is not None:
        if not isinstance(row["fixture_claims"],list) or not 1<=len(row["fixture_claims"])<=12:
            raise WorkflowError("卖点证据须为非空的独立合成对照。")
        for value in row["fixture_claims"]:
            term(value,"合成对照卖点")
    if row["image_slots"] is not None:
        check(row["image_slots"],"P01 P02 P03 P04 P05","五个原图审阅记录")
        for r in row["image_slots"].values():
            if r is None:
                continue
            check(r,"sha256 state observed","独立合成图位记录")
            identify(r["sha256"],"图位原件哈希",r"[a-f0-9]{64}")
            if r["state"] not in ("PASS","FAIL","PENDING_EVIDENCE"):
                raise WorkflowError("图位检查状态无效。")
            term(r["observed"],"原图所见")
    for key in ("listing_specs","after_sales"):
        if row[key] is not None:
            check(row[key],"value source_ref","规格或售后模拟对照")
            term(row[key]["value"],key)
            term(row[key]["source_ref"],"冻结来源")
    return seen<=as_of<expires


def bind_shop(site,p,load_listing,fetch,load_image):
    check(p,"site scope currency job_id owner sku_id version as_of title_task_id title_variant intake_task_id cover_review_task_id detail_review_task_id selling_points card_snapshot video","商品卡体检")
    as_of=header(p,site)
    if site!="CN":
        raise WorkflowError("商品卡体检仅按抖音内部分站配置。")
    identify(p["sku_id"],"SKU")
    amount(p["version"],"商品版本",1,10**6,False)
    amount(p["title_variant"],"标题变体",1,5,False)
    if not isinstance(p["selling_points"],list) or len(p["selling_points"])>12:
        raise WorkflowError("卖点须使用独立合成对照，缺失可留空。")
    for value in p["selling_points"]:
        term(value,"待核页面卖点")
    if p["video"] is not None:
        v=p["video"]
        check(v,"name sha256 source_ref site sku_id version","本机视频绑定")
        term(v["source_ref"],"视频出处")
        identify(v["sha256"],"视频哈希",r"[a-f0-9]{64}")
        identify(v["name"],"视频文件名",r"[0-9A-Za-z][0-9A-Za-z_.-]{0,74}\.mp4")
        if (v["site"],v["sku_id"],v["version"]) != (site,p["sku_id"],p["version"]):
            raise WorkflowError("主图视频的站点、SKU 或版本不匹配。")
    fresh=_card_snapshot(p["card_snapshot"],p,as_of)
    pins={}
    contexts={}
    for key in ("intake","title","cover_review","detail_review"):
        ident=p[key+"_task_id"]
        if ident is None:
            contexts[key]=None
            continue
        task_id(ident,key)
        if key=="intake":
            record,pins[key]=load_listing(ident,site,as_of)
            bound=(record["sku_id"],record["facts_version"])
        elif key=="title":
            record,pins[key]=fetch(ident,site,SEARCH)
            bound=(record["sku_id"],record["revision"])
        else:
            record,pins[key]=load_image(ident,site)
            bound=(record["sku_id"],record["version"])
        if bound!=(p["sku_id"],p["version"]):
            raise WorkflowError("已归档来源的 SKU 或版本与当前商品卡不同。")
        contexts[key]=record
    intake=p["intake_task_id"]
    if any(row and row["intake_task_id"]!=intake for row in (contexts["cover_review"],contexts["detail_review"])):
        raise WorkflowError("图审回执须来自当前商品事实及视觉包。")
    if contexts["title"] and contexts["title"]["upstream"].get("listing",{}).get("task_id")!=intake:
        raise WorkflowError("标题必须使用同一版商品事实包。")
    if ((contexts["cover_review"] and contexts["cover_review"]["image_id"]!="P01") or
            (contexts["detail_review"] and not contexts["detail_review"]["image_id"].startswith("S"))):
        raise WorkflowError("封面审核须为 P01，详情审核须为详情模块。")
    cover=contexts["cover_review"]
    if cover:
        ids=sorted(key for key in cover["manifest_sha256_by_id"] if key.startswith("P"))
        if ids!=["P01","P02","P03","P04","P05"]:
            raise WorkflowError("图审回执未绑定五个原始主图槽位。")
    pin=pins.get("intake")
    return pins,(contexts,fresh,bool(pin and absolute_time(pin["as_of"])<=as_of<absolute_time(pin["expires_at"])))


def _video(p,assets):
    v=p["video"]
    if assets and not v:
        raise WorkflowError("上传视频前必须提供同 SKU 及版本的原件回执。")
    if not v or not assets:
        return {"state":"PENDING_EVIDENCE","runtime_ms":None,"width":None,"height":None,"sha256":None}
    if set(assets)!={v["name"]}:
        raise WorkflowError("提交视频文件名与冻结来源不同。")
    data=assets[v["name"]].read_bytes()
    actual=sha(data)
    if actual!=v["sha256"]:
        raise WorkflowError("原主图视频与冻结哈希不同。")
    duration,dimension=None,None
    if not any(tag==b"mdat" and right>left for tag,left,right in mp4_boxes(data,0,len(data))):
        return {"state":"PENDING_EVIDENCE","runtime_ms":None,"width":None,"height":None,"sha256":actual}
    for tag,left,right in mp4_boxes(data,0,len(data)):
        if tag!=b"moov":
            continue
        for name,begin,end in mp4_boxes(data,left,right):
            if name!=b"trak":
                continue
            boxes=list(mp4_boxes(data,begin,end))
            handler=next(((l,r) for sub,l,r in boxes if sub==b"mdia"),None)
            info={sub:(l,r) for sub,l,r in mp4_boxes(data,*handler)} if handler else {}
            hdlr=info.get(b"hdlr")
            if not hdlr or hdlr[0]+12>hdlr[1] or data[hdlr[0]+8:hdlr[0]+12]!=b"vide":
                continue
            mdhd,minf=info.get(b"mdhd"),info.get(b"minf")
            if not (mdhd and minf and mdhd[1]>mdhd[0]):
                continue
            table=next(((l,r) for sub,l,r in mp4_boxes(data,*minf) if sub==b"stbl"),None)
            if not table or not any(sub==b"stsz" and l+12<=r and unpack_from(">I",data,l+8)[0]>0
                                    for sub,l,r in mp4_boxes(data,*table)):
                continue
            at,limit=mdhd
            if data[at]==0 and at+20<=limit:
                unit,ticks=unpack_from(">II",data,at+12)
            elif data[at]==1 and at+32<=limit:
                unit,ticks=unpack_from(">IQ",data,at+20)
            else:
                continue
            if not (unit and ticks):
                continue
            track=next(((l,r) for sub,l,r in boxes if sub==b"tkhd"),None)
            if track and track[1]-track[0]>=84:
                w,h=unpack_from(">II",data,track[1]-8)
                if w>>16 and h>>16:
                    dimension=(w>>16,h>>16)
                    duration=ticks*1000/unit
                    break
        if duration is not None:
            break
    width,height=dimension or (None,None)
    state="PENDING_EVIDENCE" if duration is None or dimension is None else "PASS" if duration>=6000 else "FAIL"
    return {"state":state,"runtime_ms":float(duration) if duration else None,"width":width,"height":height,
            "portrait":height>width if height and width else None,
            "recommended_length":bool(20000<=duration<=30000) if duration else None,
            "source_ref":v["source_ref"],"sha256":actual,"visual_claims_verified":False,"metadata_only":True}


def _card(p,contexts,fresh,intake_current,video):
    row=p["card_snapshot"] if fresh else None
    title=contexts["title"]
    variant=next((v for v in title["titles"] if v["variant"]==p["title_variant"]),None) if title else None
    title_state="PENDING_EVIDENCE"
    if variant and intake_current and title["state"]=="READY_OFFLINE":
        value=variant.get("title")
        core=title["core_term"]
        pos=value.find(core) if value else -1
        title_state="PASS" if value and len(value)<=30 and 0<=pos and pos+len(core)<=15 else "FAIL"
    cover,detail=(contexts[k] for k in ("cover_review","detail_review"))
    def image_state(record):
        if not record or not intake_current:
            return "PENDING_EVIDENCE"
        return {"visual_review_passed":"PASS","returned_for_revision":"FAIL"}.get(record["status"],"PENDING_EVIDENCE")
    def gate(val):
        return "PENDING_EVIDENCE" if val is None else "PASS" if val else "FAIL"
    values={k:(row[k] if row else None) for k in NUMBER_FIELDS}
    quality=values["quality_score"]
    sales,pos,total,detailed=(values[k] for k in NUMBER_FIELDS[1:])
    invalid_review=(pos is not None and total is not None and pos>total) or (
        detailed is not None and total is not None and detailed>total)
    pct=None if invalid_review else pos/total if pos is not None and total else None
    services=sorted(set(row["services"])) if row and row["services"] is not None else None
    fixed_claims=set(row["fixture_claims"] or []) if row else set()
    point_state=gate(set(p["selling_points"])<=fixed_claims if fixed_claims and p["selling_points"] else None)
    red={k:gate(row["red_lines"][k] if row else None) for k in RED_LINES}
    red["quality_score"]=gate(quality>=85 if quality is not None else None)
    if point_state=="FAIL":
        red["claims_supported"]="FAIL"
    if row:
        current,reference=row["price_minor"],row["expected_card_price_minor"]
        if None not in (current,reference) and current!=reference:
            red["price_consistent"]="FAIL"
        elif None in (current,reference) and red["price_consistent"]!="FAIL":
            red["price_consistent"]="PENDING_EVIDENCE"
    expected_slots=cover["manifest_sha256_by_id"] if cover and intake_current else {}
    declared_slots=row["image_slots"] if row else None
    slot_checks={}
    for slot in ("P01","P02","P03","P04","P05"):
        candidate=declared_slots.get(slot) if declared_slots else None
        slot_checks[slot]=(candidate["state"] if candidate and candidate["sha256"]==expected_slots.get(slot)
                           else "PENDING_EVIDENCE")
    five_slot_state=("FAIL" if "FAIL" in slot_checks.values() else "PENDING_EVIDENCE"
                     if "PENDING_EVIDENCE" in slot_checks.values() else "PASS")
    cover_state=image_state(cover)
    combined_main="FAIL" if "FAIL" in (cover_state,five_slot_state) else (
        "PASS" if cover_state==five_slot_state=="PASS" else "PENDING_EVIDENCE")
    health={
        "sales":gate(sales>10 if sales is not None else None),
        "positive_review_rate":gate(100*pos>=85*total if pct is not None else None),
        "detailed_reviews":gate(detailed>=10 if not invalid_review and detailed is not None and total is not None else None),
        "services":gate(len(services)>=2 if services is not None else None),
    }
    checks={"title":title_state,"main_images":combined_main,"detail":image_state(detail),
            "listing_specs":gate(True if row and row["listing_specs"] else None),
            "after_sales":gate(True if row and row["after_sales"] else None),
            "selling_points":point_state,"video":video["state"],**health}
    violations=[k for k,v in red.items() if v=="FAIL"]
    todo=[k for table in (red,checks) for k,v in table.items() if v!="PASS"]
    state="FROZEN_RED_LINE" if violations else "RETURN_FOR_OPTIMIZATION" if "FAIL" in checks.values() else (
        "PENDING_EVIDENCE" if todo else "READY_OFFLINE")
    return {"state":state,"sku_id":p["sku_id"],"version":p["version"],"method":CARD_METHOD,"red_lines":red,"checks":checks,
            "red_line_failures":violations,"title":variant.get("title") if variant else None,
            "main_images":{"cover_sha256":cover["asset"]["sha256"] if cover else None,
                           "five_slot_checks":slot_checks,"five_slot_sha256":{k:v for k,v in cover["manifest_sha256_by_id"].items() if k.startswith("P")} if cover else {},
                           "visually_reviewed_image_ids":[x["image_id"] for x in (cover,detail) if x]},
            "selling_points":p["selling_points"],"video":video,"raw_snapshot":p["card_snapshot"],
            "snapshot_state":"CURRENT_SYNTHETIC_EVIDENCE" if fresh else "PENDING_EVIDENCE",
            "health_values":{**values,"positive_review_rate":pct,"review_counts_conflict":invalid_review,"services":services},
            "thresholds":{"quality_score_min":85,"quality_target":90,"sales_strictly_above":10,"positive_review_rate_min":0.85,
                          "detailed_review_min":10,"service_count_min":2,"main_video_ms_min":6000,"basis":"frozen internal offline checklist"},
            "actions":[{"field":k,"owner":p["owner"],"action":"补齐或重核当前同 SKU 证据，修订后复审。"} for k in dict.fromkeys(todo)],
            "scale_gate":"FROZEN" if violations else "HOLD_FOR_REVIEW" if todo else "OFFLINE_CHECK_COMPLETE",
            "physical_product_verified":False,"actual_platform_rule_verified":False,"actual_listing_or_scaling":False,
            "external_use_rights_verified":False}


def _receipt(result,raw,report):
    return {**{k:result[k] for k in ("task_id","job_id","site","tool_id","upstream","input_sha256","state")},
            "result_sha256":sha(raw),"report_sha256":sha(report),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    pins,context=linked
    if tool==TEARDOWN:
        result=_review(p,*context)
    else:
        result=_card(p,*context,_video(p,assets))
        method=project/METHOD_PATH
        result["method_source"]={"path":METHOD_PATH,"sha256":sha(method.read_bytes())}
    result.update(task_id=folder.name,job_id=p["job_id"],site=site,tool_id=tool,scope=SYNTHETIC,
                  currency=p["currency"],as_of=p["as_of"],upstream=pins,input_sha256=sha(encode(p)),
                  execution_authorized=False,provider_called=False,model_called=False)
    raw=encode(result)
    if tool==TEARDOWN:
        summary=[f"- {v['element_id']} / {v['channel']} · {v['state']} · 差值 {v['difference_pp']} pp"
                 for v in result["channel_results"]]
        time=f"{result['starts_at']} 至 {result['ends_at']}（{result['timezone']}）"
        coverage=result["period_coverage"]
        days="、".join(coverage["observed_dates"]) or "无"
        summary[:0]=[f"本期 {coverage['days_in_window']} 个自然日。有效观测日期 {days}。",
                     f"回看档案权利状态 {result['rights_state']}。渠道保持分列。"]
        for row in result["channel_results"]:
            if row["channel"]=="affiliate":
                summary.extend(f"- COD {row['element_id']} {arm} · {cod['state']} · 签收 {cod['signed']} · 在途 {cod['in_transit']}"
                               for arm,cod in row["cod"].items() if cod)
        summary.extend(f"- 反馈 {action['action_id']} · {action['instruction']} · {action['owner']} · {action['due']}"
                       for action in result["actions"])
        if result["prior_feedback"]:
            summary.extend(f"- 上期任务 {a['action_id']} · {a['feedback_state']}" for a in result["prior_feedback"]["actions"])
    else:
        summary=[f"- {key} · {value}" for key,value in result["checks"].items()]
        summary.extend(f"- 红线 {key} · {value}" for key,value in result["red_lines"].items())
        time=f"{p['as_of']} · {p['sku_id']} v{p['version']}"
    report=("\n".join([f"# {site} · {p['job_id']} · {result['state']}","",time,
                      "合成离线复核。实际增量、实物属性、商业授权及发布状态均未经验证。","",*summary,
                      "","## 结构化原始观测与追溯","","详见同号 JSON、上游哈希与回执。",""])).encode("utf-8")
    paths={k:folder/v for k,v in FILES.items()}
    for k,data in {"json":raw,"report":report,"receipt":encode(_receipt(result,raw,report))}.items():
        with paths[k].open("xb") as stream:
            stream.write(data)
    return result,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or json.loads(files["json"])!=r or
        r["upstream"]!=task["source"]["role_packet_pins"] or
        (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
        json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("复盘原件、来源或回执无法核验。")


def public_tools():
    return [
        {"id":TEARDOWN,"sites":["CN","PH"],"role_suffix":"video-teardown","label":"拆解周月复盘与反馈",
         "description":"绑定当前拆解版本，分渠道对照，留存权利、时间及反馈边界。","input_example":{}},
        {"id":SHOP,"sites":["CN"],"role_suffix":"shop-ops","label":"抖音商品卡体检及健康红线",
         "description":"核对同版标题、图审、视频及模拟商品健康证据。","input_example":{},
         "max_media_files":1,"media_required":False}
    ]

