"""Local period reporting. Inputs and results are isolated synthetic observations."""
from datetime import timedelta
from hashlib import sha256
from statistics import median
import json

from role_tools import ROUTES,SYNTHETIC
from workflow_cn_selection_lifecycle import TOOL as LIFECYCLE
from workflow_lead_weekly import encode,ratio,term
from workflow_live_rehearsal import amount,check,identify,stamp,task_id

SEL="cn-selection-period-report"
AD="ad-offline-brand-measurement"
TD="teardown-offline-tracking"
TOOLS=(SEL,AD,TD)
FILES={"json":"local-insight.json","report":"local-insight.md","receipt":"local-insight-receipt.json"}
COMMON="site scope currency job_id as_of owner"
COUNTS=("opening_units","received_units","sold_units","opening_inventory_minor","closing_inventory_minor",
        "net_sales_minor","cost_of_sold_goods_minor")
ACTIONS={"customer_service":"核对同 SKU 样本与客服问题；实际联系待授权。",
         "selection":"复核补货、滞销和报价口径；实际采购和调价待授权。"}


class WorkflowError(ValueError):
    pass


def header(site,p):
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("仅使用本站和本币的明确合成观察。")
    identify(p["job_id"],"任务编号");term(p["owner"],"记录责任人")
    return stamp(p["as_of"])


def period_window(p,at):
    start,end=stamp(p["start"]),stamp(p["end"])
    closed=start<end<=at and not any(x.hour or x.minute or x.second or x.microsecond for x in (start,end))
    month_gap=(end.year-start.year)*12+end.month-start.month
    kind=p["period"]
    valid=(kind=="week" and end-start==timedelta(days=7)
           or kind=="month" and start.day==end.day==1 and month_gap==1
           or kind=="quarter" and start.day==end.day==1 and start.month in (1,4,7,10) and month_gap==3)
    if not (closed and valid):
        raise WorkflowError("须使用同站、已结束且边界明确的周、月或自然季。")
    return start,end


def _values(row,keys):
    for k in keys:amount(row[k],k,0,10**12)
    return {k:row[k] for k in keys}


def difference(a,b):
    return a-b if None not in (a,b) else None


def total(*v):
    return sum(v) if None not in v else None


def _sel_observation(raw,p,at,start,end,identity):
    if raw is None:return None
    check(raw,"site currency own_product_id sku_id revision start end captured_at source_ref values","同 SKU 期间数据")
    if ((raw["site"],raw["currency"],raw["own_product_id"],raw["sku_id"],raw["revision"])!=
            (p["site"],p["currency"],p["own_product_id"],*identity)
            or (stamp(raw["start"]),stamp(raw["end"]))!=(start,end) or not end<=stamp(raw["captured_at"])<=at):
        raise WorkflowError("数值快照须为同站同 SKU 修订、同关闭窗及有效采集截点。")
    term(raw["source_ref"],"合成原件出处")
    check(raw["values"]," ".join(COUNTS),"选品周月季基础值")
    return _values(raw["values"],COUNTS)


def _sel_context(row,p,at,end):
    check(row,"kind site sku_id revision subject_id start end captured_at source_ref detail rating_x100 review_count","竞品与客服反馈")
    kind=row["kind"]
    identify(row["subject_id"],"独立反馈对象")
    if ((row["site"],row["sku_id"],row["revision"])!=(p["site"],p["sku_id"],p["revision"])
            or stamp(row["start"])!=stamp(p["start"]) or stamp(row["end"])!=end
            or not end<=stamp(row["captured_at"])<=at):
        raise WorkflowError("反馈与店内 SKU 修订或复盘截点不符。")
    term(row["source_ref"],"反馈出处");term(row["detail"],"业务信号")
    if kind not in ("competitor_context","customer_service_dsr"):
        raise WorkflowError("只区分竞品参考与客服/DSR 自有模拟观察。")
    if kind=="customer_service_dsr":
        if row["subject_id"]!=p["own_product_id"]:
            raise WorkflowError("DSR 反馈需绑定自己的商品身份，不能用竞品对象。")
        amount(row["rating_x100"],"DSR (0–500)",0,500)
        amount(row["review_count"],"DSR 样本量",0,10**9)
        return {**row,"rating_for_interpretation":row["rating_x100"] if row["review_count"] else None,
                 "used_as_own_actual_data":False}
    if row["subject_id"]==p["own_product_id"] or row["rating_x100"] is not None or row["review_count"] is not None:
        raise WorkflowError("竞品观察不可进入自有 DSR。")
    return {**row,"used_as_own_actual_data":False}


def bind_selection(site,p,fetch):
    extras="period start end own_product_id lifecycle_task_id lifecycle_version rows"
    check(p,COMMON+" "+extras,"抖音选品周期复盘")
    at=header(site,p)
    if site!="CN":raise WorkflowError("本入口为抖音选品周期复盘。")
    start,end=period_window(p,at)
    identify(p["own_product_id"],"内部合成商品")
    task_id(p["lifecycle_task_id"],"本站货盘维护回执")
    amount(p["lifecycle_version"],"货盘维护版本",1,10**6,False)
    source,pin=fetch(p["lifecycle_task_id"],site,LIFECYCLE)
    if ((source["own_product_id"],source["version"])!=(p["own_product_id"],p["lifecycle_version"])
            or stamp(source["as_of"])>at):
        raise WorkflowError("只接受明确的本站同商品货盘维护版本。")
    variants={r["sku_id"]:r["revision"] for r in source["variants"]}
    if not isinstance(p["rows"],list) or not 1<=len(p["rows"])<=24:
        raise WorkflowError("限定本地货盘逐 SKU 复盘。")
    by_sku={}
    actions=set()
    for row in p["rows"]:
        check(row,"sku_id revision snapshot context action","本机选品复盘单位")
        key=row["sku_id"]
        identify(key,"SKU");amount(row["revision"],"SKU 修订",1,10**6,False)
        if key in by_sku or variants.get(key)!=row["revision"]:
            raise WorkflowError("SKU 重复或版本偏离货盘。")
        v=_sel_observation(row["snapshot"],p,at,start,end,(key,row["revision"]))
        if not isinstance(row["context"],list) or len(row["context"])>12:
            raise WorkflowError("只接收有来源的有限反馈。")
        notes=[_sel_context(n,{**p,**row},at,end) for n in row["context"]]
        ids=[(n["kind"],n["source_ref"]) for n in notes]
        if len(set(ids))!=len(ids):
            raise WorkflowError("同一反馈来源只登记一次。")
        dsr={ (n["rating_for_interpretation"],n["review_count"]) for n in notes
              if n["kind"]=="customer_service_dsr" and n["rating_for_interpretation"] is not None}
        dsr_conflict=len(dsr)>1
        action=row["action"]
        if action is not None:
            check(action,"id role owner due evidence_source_ref","改进及客服联络单")
            identify(action["id"],"改进号");term(action["owner"],"负责人员")
            if action["role"] not in ACTIONS or stamp(action["due"])<=at or not any(
                    x["source_ref"]==action["evidence_source_ref"] and
                    (action["role"]!="customer_service" or x["kind"]=="customer_service_dsr") for x in notes):
                raise WorkflowError("改进单应有本站关联证据、接收角色和未来期限。")
            if action["id"] in actions:
                raise WorkflowError("原改进编号不能跨多个 SKU 解释。")
            actions.add(action["id"])
            note=next(n for n in notes if n["source_ref"]==action["evidence_source_ref"])
            use=("RECONCILE_DSR_FIRST" if dsr_conflict else "COLLECT_EVIDENCE"
                 if action["role"]=="customer_service" and note.get("rating_for_interpretation") is None
                 else "OFFLINE_FOLLOWUP")
            action={**action,"evidence_use":use,"next_step":ACTIONS[action["role"]],"actual_contact_performed":False}
        by_sku[key]=(row,v,notes,action,dsr_conflict)
    if set(variants)!=set(by_sku):
        raise WorkflowError("必须逐项保留原货盘 SKU，缺项使用明确的未知快照。")
    return {"catalog":pin},by_sku


def metric(n,d):
    return ratio(n,d) or {"numerator":n,"denominator":d,"value":None}


def _sel_metrics(v):
    if v is None:v=dict.fromkeys(COUNTS)
    stock=total(v["opening_units"],v["received_units"])
    sold=v["sold_units"]
    conflict=stock is not None and sold is not None and sold>stock
    gross=difference(v["net_sales_minor"],v["cost_of_sold_goods_minor"])
    inventory=total(v["opening_inventory_minor"],v["closing_inventory_minor"])
    denom_turn=inventory/2 if inventory is not None else None
    return {"raw":v,"available_units":stock,"stock_conflict":conflict,
            "variant_moved":None if sold is None or conflict else sold>0,
            "sellthrough":metric(sold if not conflict else None,stock),
            "gross_profit_minor":gross,"gross_margin":metric(gross,v["net_sales_minor"]),
            "turnover_times_on_period_cost_basis":metric(v["cost_of_sold_goods_minor"],denom_turn),
            "annualized":False}


def selection_result(p,groups):
    rows=[]
    for key,(row,v,notes,action,dsr_conflict) in groups.items():
        metrics=_sel_metrics(v)
        ready=(not metrics["stock_conflict"] and not dsr_conflict
               and all(metrics[k]["value"] is not None for k in
                       ("sellthrough","gross_margin","turnover_times_on_period_cost_basis"))
               and {n["kind"] for n in notes}>={"competitor_context","customer_service_dsr"}
               and any(n.get("rating_for_interpretation") is not None for n in notes if n["kind"]=="customer_service_dsr")
               and action is not None and action["evidence_use"]=="OFFLINE_FOLLOWUP")
        rows.append({"sku_id":key,"dsr_source_conflict":dsr_conflict,"revision":row["revision"],"snapshot":row["snapshot"],
                     "metrics":metrics,"context":notes,"action":action,"state":"COMPLETE_FIXTURE" if ready else "PENDING_EVIDENCE"})
    moves=[r["metrics"]["variant_moved"] for r in rows]
    return {"state":"REPORT_RECORDED" if all(r["state"]=="COMPLETE_FIXTURE" for r in rows) else "PENDING_EVIDENCE",
            **{k:p[k] for k in ("period","start","end","own_product_id","lifecycle_version")},
            "rows":rows,"assortment_moving_rate":metric(sum(v is True for v in moves) if None not in moves else None,len(rows)),
            "counts_added_across_period_types":False,"shop_or_customer_contact":False,
            "actual_dsr_or_competitor_observed":False}


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","tool_id","site","job_id","upstream","input_sha256","state")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:raise WorkflowError("只用已保存本机回执和合成字段。")
    pins,ctx=linked
    result={SEL:selection_result,AD:ad_result,TD:td_result}[tool](p,ctx)
    r={**result,**{k:p[k] for k in COMMON.split()},"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(r)
    report=(f"# {site} · {r['job_id']} · {r['state']}\n\n"
            "合成本机。真实经营数据、对外联络和业务执行保持未验证。\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(v)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or json.loads(files["json"])!=r or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("本机日报和来源回执无法核验。")


def public_tools():
    return [{"id":SEL,"role_suffix":"selection","sites":["CN"],"label":"抖音选品周／月／季复盘",
             "description":"同规格动销、毛利、周转与有来源的客服改进单。","input_example":{}},
            {"id":AD,"role_suffix":"ad-buyer","sites":list(ROUTES),"label":"品广独立合成测量",
             "description":"品牌独立费用、审批及去重触达，不读取账户。","input_example":{}},
            {"id":TD,"role_suffix":"video-teardown","sites":list(ROUTES),"label":"拆解对标账号周期追踪",
             "description":"固定同账号口径，保留同龄、版本和样本分层。","input_example":{}}]



def _approval(p,at,starts,ends):
    item=p["approval"]
    if item is None:return False
    check(item,"account_id campaign_id currency purpose max_minor valid_from valid_until source_ref","独立品广模拟批准")
    if (item["account_id"],item["campaign_id"],item["currency"],item["purpose"])!=(
            p["account_id"],p["campaign_id"],p["currency"],"brand_measurement_fixture"):
        raise WorkflowError("品广批准须绑定本站账户、活动、原币和用途。")
    term(item["source_ref"],"模拟审批来源")
    amount(item["max_minor"],"模拟批准额度",0,10**12)
    return item["max_minor"] is not None and stamp(item["valid_from"])<=starts<ends<=stamp(item["valid_until"])


def bind_ad(site,p):
    check(p,COMMON+" period start end account_id campaign_id objective brand_budget_minor effect_spend_minor approval placements "
          "reported_cpm_minor reported_cpm_source room_gmv_minor","品广独立测量")
    at=header(site,p)
    start,end=period_window(p,at)
    if p["objective"]!="UNIQUE_REACH_FIXTURE":
        raise WorkflowError("只验历史合成独立触达目标，不能填效果订单。")
    for k in ("account_id","campaign_id"):identify(p[k],k)
    for k in ("brand_budget_minor","effect_spend_minor","reported_cpm_minor","room_gmv_minor"):
        amount(p[k],k,0,10**12)
    approved=_approval(p,at,start,end)
    report=p["reported_cpm_source"]
    if report is not None:
        check(report,"id account_id campaign_id currency start end captured_at basis","原报 CPM 口径")
        identify(report["id"],"原报数出处")
    comparable=bool(report and (report["account_id"],report["campaign_id"],report["currency"],report["basis"])==
                    (p["account_id"],p["campaign_id"],p["currency"],"BRAND_MINOR_PER_1000_IMPRESSIONS") and
                    (stamp(report["start"]),stamp(report["end"]))==(start,end) and end<=stamp(report["captured_at"])<=at)
    rows=p["placements"]
    if not isinstance(rows,list) or not 1<=len(rows)<=30:
        raise WorkflowError("需有有限且可追溯的品广分项。")
    by_id={}
    by_source={}
    for row in rows:
        check(row,"id site currency account_id campaign_id start end captured_at source_ref brand_spend_minor impressions "
                  "audience_namespace reached_ids","独立品广测量分项")
        identify(row["id"],"分项编号")
        if ((row["site"],row["currency"],row["account_id"],row["campaign_id"])!=
                (site,p["currency"],p["account_id"],p["campaign_id"])
                or (stamp(row["start"]),stamp(row["end"]))!=(start,end)
                or not end<=stamp(row["captured_at"])<=at):
            raise WorkflowError("源必须同站、同账户、同品广活动、同闭窗及原币。")
        term(row["source_ref"],"分项观察来源")
        for k in ("brand_spend_minor","impressions"):amount(row[k],k,0,10**12)
        ids=row["reached_ids"]
        if ids is not None:
            term(row["audience_namespace"],"匿名去重命名空间")
            if not isinstance(ids,list) or len(ids)>2000:
                raise WorkflowError("合成匿名触达人群最多 2000。")
            for participant in ids:identify(participant,"匿名模拟人群编号")
        key=encode({k:v for k,v in row.items() if k!="id"})
        by_id.setdefault(row["id"],set()).add(key)
        by_source.setdefault(row["source_ref"],{})[key]=row
    conflict=sorted({k for k,v in by_id.items() if len(v)>1}|{k for k,v in by_source.items() if len(v)>1})
    distinct=[next(iter(v.values())) for v in by_source.values() if len(v)==1]
    repeats=len(rows)-sum(map(len,by_source.values()))
    return {"direct_input_sha256":sha256(encode(p)).hexdigest()},(distinct,approved,conflict,repeats,comparable)


def ad_result(p,c):
    rows,approved,conflicts,repeats,comparable=c
    metric_bad=bool(conflicts or any(x["reached_ids"] is not None and x["impressions"] is not None and
                    (len(set(x["reached_ids"]))>x["impressions"] or x["impressions"]>0 and not x["reached_ids"]) for x in rows))
    show_cost=not conflicts
    spend=total(*(r["brand_spend_minor"] for r in rows)) if show_cost else None
    budget=p["brand_budget_minor"]
    max_approved=(p["approval"] or {}).get("max_minor") if approved else None
    blocked=(spend is not None and any(spend>l for l in (budget,max_approved) if l is not None))
    impressions=total(*(r["impressions"] for r in rows)) if not metric_bad else None
    known=(not metric_bad and len({r["audience_namespace"] for r in rows})==1 and
           all(r["reached_ids"] is not None for r in rows))
    reach=len({u for r in rows for u in r["reached_ids"]}) if known else None
    cpm=metric(spend*1000 if spend is not None else None,impressions)
    claimed=p["reported_cpm_minor"]
    # The reported fixture is integer minor units rounded half-up.
    rounded_cpm=(2*spend*1000+impressions)//(2*impressions) if spend is not None and impressions else None
    conflict=comparable and claimed is not None and rounded_cpm is not None and claimed!=rounded_cpm
    state="BLOCKED_BY_FIXTURE_BUDGET" if blocked else "PENDING_EVIDENCE" if (
        metric_bad or conflict or claimed is not None and not comparable or not approved or None in (budget,reach,impressions,spend) or not reach or not impressions
    ) else "BRAND_MEASUREMENT_RECORDED"
    return {"state":state,"objective":p["objective"],"account_id":p["account_id"],"campaign_id":p["campaign_id"],
            "period":p["period"],"start":p["start"],"end":p["end"],
            "brand_spend_minor":spend,"effect_spend_minor_separate":p["effect_spend_minor"],
            "brand_budget_minor":budget,"brand_approval_scope_matches_fixture":approved,
            "approval_evidence":p["approval"],"reported_cpm_source":p["reported_cpm_source"],
            "reported_cpm_same_basis":comparable,
            "deduplicated_reach":reach,"frequency":metric(impressions,reach),
            "impressions":impressions,"calculated_cpm_minor":cpm,"reported_cpm_minor":claimed,
            "cpm_source_conflict":bool(conflict),"same_id_conflicts":conflicts,"exact_duplicate_rows_ignored":repeats,
            "room_gmv_observation_only":p["room_gmv_minor"],"actual_reach_verified":False,
            "actual_brand_control_verified":False,"brand_increment":None,"brand_increment_roi":None,
            "raw_observations":p["placements"],"actual_campaign_action":False}


def bind_teardown(site,p,fetch):
    fields="period start end tracker_id version previous_task_id rule accounts observations"
    check(p,COMMON+" "+fields,"拆解跨周期对标追踪")
    at=header(site,p);start,end=period_window(p,at)
    identify(p["tracker_id"],"追踪组号");amount(p["version"],"追踪版本",1,10**6,False)
    rule=p["rule"]
    check(rule,"metric observed_after_hours min_sample cutoff_multiplier source_ref","同账号冻结测量规则")
    if rule["metric"]!="VIEWS_SAME_AGE_FIXTURE":
        raise WorkflowError("固定同龄累计播放的合成测量。")
    for k,lo,hi in (("observed_after_hours",1,168),("min_sample",2,40),("cutoff_multiplier",1,100)):
        amount(rule[k],k,lo,hi,False)
    term(rule["source_ref"],"合成基线契约出处")
    pins={}
    previous=None
    if p["version"]==1:
        if p["previous_task_id"] is not None:raise WorkflowError("首轮没有原追踪回执。")
    else:
        task_id(p["previous_task_id"],"原追踪回执")
        previous,pins["previous"]=fetch(p["previous_task_id"],site,TD)
        if ((previous["tracker_id"],previous["version"]+1,previous["rule"],previous["period"])!=
                (p["tracker_id"],p["version"],rule,p["period"])
                or stamp(previous["end"])>start or not stamp(previous["as_of"])<at):
            raise WorkflowError("延续原基线口径和本站非重叠观察窗，不接受跳号或篡改。")
    accounts=p["accounts"]
    if not isinstance(accounts,list) or not 1<=len(accounts)<=24:
        raise WorkflowError("需逐个声明当前匿名样本账号。")
    roster={}
    grades={"small","mid","large"} if site=="CN" else {"longtail","mid","head"}
    origins={"cn_domestic"} if site=="CN" else {"ph_local","crossborder"}
    traffic={"shelf","search","recommendation",None} if site=="CN" else {"affiliate","recommendation","direct",None}
    for a in accounts:
        check(a,"account_id tier origin cohort source_ref","同站账号与群组")
        identify(a["account_id"],"匿名账号");identify(a["cohort"],"匿名群组")
        term(a["source_ref"],"登记出处")
        if a["account_id"] in roster or a["tier"] not in tuple(grades) or a["origin"] not in tuple(origins):
            raise WorkflowError("账号分层、群组身份或站点来源不一致。")
        roster[a["account_id"]]=a
    if previous and roster!=previous["accounts"]:
        raise WorkflowError("当前版本应延续原登记的账号与分组。")
    rows=p["observations"]
    if not isinstance(rows,list) or len(rows)>250:
        raise WorkflowError("仅有限合成作品观测。")
    for o in rows:
        check(o,"id site account_id topic video_id published_at measured_at captured_at views channel channel_source_ref "
                "seasonal_scope source_ref","同龄独立作品观测")
        identify(o["account_id"],"匿名账号")
        if o["site"]!=site or o["account_id"] not in roster or o["channel"] not in tuple(traffic):
            raise WorkflowError("作品必须使用本站登记账号渠道。")
        for n in ("id","video_id","topic"):identify(o[n],n)
        term(o["source_ref"],"作品原件出处")
        if o["channel_source_ref"] is not None:term(o["channel_source_ref"],"渠道观察依据")
        if o["seasonal_scope"] not in ("festival_only","transferability_unknown","nonseasonal_fixture",None):
            raise WorkflowError("本地节庆标签须保持合成或未知。")
        for n in ("published_at","measured_at","captured_at"):stamp(o[n])
        amount(o["views"],"累计播放",0,10**12)
    return pins,(roster,start,end,at,previous)


def td_result(p,c):
    roster,start,end,at,previous=c
    rule=p["rule"]
    by_work={}
    observation_ids={}
    exact=0;exclusions=[]
    for o in p["observations"]:
        k=(o["account_id"],o["video_id"])
        publication,measurement,capture=(stamp(o[n]) for n in ("published_at","measured_at","captured_at"))
        if not (start<=publication<end and measurement-publication==timedelta(hours=rule["observed_after_hours"])
                and measurement<=capture<=at):
            exclusions.append({"record":o,"reason":"wrong_window_observation_age_or_cutoff"})
            continue
        group=by_work.setdefault(k,{})
        fingerprint=encode({k:v for k,v in o.items() if k!="id"})
        observation_ids.setdefault(o["id"],{})[fingerprint]=k
        if fingerprint in group:exact+=1
        else:group[fingerprint]=o
    conflicting_ids={k:v for k,v in observation_ids.items() if len(v)>1}
    conflicted_work={k for v in conflicting_ids.values() for k in v.values()}
    creators_by_video={}
    for account,video in by_work:
        creators_by_video.setdefault(video,set()).add(account)
    ambiguous={video for video,v in creators_by_video.items() if len(v)>1}
    account_rows={}
    groups={}
    pending=[]
    for account,profile in roster.items():
        candidates=[obs for (a,v),obs in by_work.items() if a==account and v not in ambiguous and (a,v) not in conflicted_work]
        collisions=[list(g.values()) for (a,v),g in by_work.items()
                    if a==account and (len(g)>1 or v in ambiguous or (a,v) in conflicted_work)]
        known=[next(iter(g.values())) for g in candidates if len(g)==1 and next(iter(g.values()))["views"] is not None]
        unknown=[next(iter(g.values())) for g in candidates if len(g)==1 and next(iter(g.values()))["views"] is None]
        med=median(x["views"] for x in known) if len(known)>=rule["min_sample"] and not collisions and not unknown else None
        threshold=med*rule["cutoff_multiplier"] if med else None
        if threshold is None:pending.append(account)
        work=[]
        for o in sorted(known,key=lambda x:x["video_id"]):
            reached=None if threshold is None else o["views"]>=threshold
            channel=o["channel"] if o["channel_source_ref"] else None
            work.append({**o,"breakout_in_fixture":reached,"eligible_channel":channel})
            k=(profile["cohort"],profile["tier"],profile["origin"],o["topic"],o["seasonal_scope"],channel)
            record=groups.setdefault(k,{"accounts":set(),"known_works":0,"classified_works":0,"breakouts":0})
            record["accounts"].add(account)
            record["known_works"]+=1
            if reached is not None:record["classified_works"]+=1;record["breakouts"]+=int(reached)
        account_rows[account]={"baseline_median":med,"breakout_threshold":threshold,
            "works":work,"unknown_works":unknown,"conflicting_work_sources":collisions}
    summaries=[]
    for key,row in groups.items():
        summaries.append({**dict(zip(("cohort","tier","origin","topic","seasonal_scope","channel"),key,strict=True)),
                          **row,"accounts":sorted(row["accounts"]),"actual_market_trend":None})
    movement=[]
    dims=("cohort","tier","origin","topic","seasonal_scope","channel")
    if previous:
        prior={tuple(r[k] for k in dims):r for r in previous["cohort_topics"]}
        for row in summaries:
            original=prior.get(tuple(row[k] for k in dims))
            if original and not pending and previous["state"]=="OBSERVATION_RECORDED":
                movement.append({**{k:row[k] for k in dims},
                    "previous_samples":original["known_works"],"current_samples":row["known_works"],
                    "previous_breakouts":original["breakouts"],"current_breakouts":row["breakouts"],
                    "same_account_sample":row["accounts"]==original["accounts"],
                    "market_trend_inferred":False})
    return {"state":"OBSERVATION_RECORDED" if not pending else "PENDING_EVIDENCE",
            "tracker_id":p["tracker_id"],"version":p["version"],"previous_task_id":previous["task_id"] if previous else None,
            "rule":rule,"period":p["period"],"start":p["start"],"end":p["end"],"accounts":roster,"account_results":account_rows,
            "cohort_topics":summaries,"out_of_scope_observations":exclusions,"pending_accounts":pending,
            "cross_account_video_conflicts":sorted(ambiguous),"conflicting_observation_ids":sorted(conflicting_ids),
            "cohort_window_comparison":movement,
            "exact_duplicates_ignored":exact,"actual_platform_volume_verified":False,"actual_market_trend":None,
            "actual_outcome_or_attribution_verified":False}

