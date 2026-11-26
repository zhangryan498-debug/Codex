"""Single-field creative tests in the original, offline role workbench."""

from fractions import Fraction
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, ratio, term
from workflow_live_paid_slot import TOOL as MEDIA
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

AD = "ad-offline-single-factor"
HANDOFF = "director-offline-ad-experiment-intake"
TOOLS = (AD, HANDOFF)
FILES = {"json":"ad-experiment.json","report":"ad-experiment.md","receipt":"ad-experiment-receipt.json"}
ARMS = ("A", "B")


class WorkflowError(ValueError):
    pass


def window(row):
    return stamp(row["starts_at"]),stamp(row["ends_at"])


def plan(site,p):
    task_id(p["media_task_id"],"本机素材回执")
    start,end=window(p)
    when=stamp(p["as_of"])
    if not when<start<end:
        raise WorkflowError("须先冻结规则，再开始合成观察窗。")
    common=p["shared"]
    check(common,"audience_id placement currency budget_per_arm_minor price_minor locale account_id","两臂共同设置")
    for k in ("audience_id","placement","account_id"):
        identify(common[k],k)
    for k in ("budget_per_arm_minor","price_minor"):
        amount(common[k],k,1,10**12,False)
    locales=("zh-CN",) if site=="CN" else ("en-PH","fil-PH")
    if common["locale"] not in locales or common["currency"]!=p["currency"]:
        raise WorkflowError("共同币种、站点语言不相符。")
    if p["variable"]!="headline":
        raise WorkflowError("本机入口只支持预先声明标题这一变因。")
    check(p["arms"],"A B","A/B两臂")
    for a in ARMS:
        check(p["arms"][a],"headline","仅标题可以随组变化")
        term(p["arms"][a]["headline"],"标题")
    if p["arms"]["A"]["headline"].strip()==p["arms"]["B"]["headline"].strip():
        raise WorkflowError("两臂所声明的单一字段须有不同测试值。")
    a=p["assignment"]
    check(a,"method sample_unit buckets source_ref","固定样本分组")
    if (a["method"],a["sample_unit"])!=("fixed_disjoint_fixture","synthetic_cohort_bucket"):
        raise WorkflowError("本机只支持可复算的合成固定分组。")
    term(a["source_ref"],"分组来源")
    check(a["buckets"],"A B","分组")
    for entries in a["buckets"].values():
        if not isinstance(entries,list) or not 1<=len(entries)<=20:
            raise WorkflowError("每臂提供 1 至 20 个合成分组编号。")
        for item in entries:
            identify(item,"合成分组编号")
    x,y=a["buckets"]["A"],a["buckets"]["B"]
    if len(x)!=len(y) or len(set(x+y))!=len(x+y):
        raise WorkflowError("固定分组须等大、互斥且无重复。")
    c=p["criteria"]
    check(c,"min_exposures max_relative_paid_drop_bps min_signed_bps source_ref rollback_owner","窗口与回退规则")
    amount(c["min_exposures"],"最少曝光样本",1,10**10,False)
    amount(c["max_relative_paid_drop_bps"],"按支付/曝光计算的相对下滑线",1,10000,False)
    if site=="PH":
        amount(c["min_signed_bps"],"PH成熟签收触发线",1,10000,False)
    elif c["min_signed_bps"] is not None:
        raise WorkflowError("抖音使用本地支付口径。")
    for k in ("source_ref","rollback_owner"):
        term(c[k],k)
    allowed=("owned_fixture","spark_fixture") if site=="PH" else ("owned_fixture",)
    if p["rights_mode"] not in allowed:
        raise WorkflowError("素材用途必须按本站的合成用例声明。")
    r=p["rights"]
    if r is not None:
        check(r,"scope site sku_id revision mode sha256 starts_at ends_at asset_owner authorization_ref locale_review_ref","合成使用记录")
        amount(r["revision"],"素材商品修订",1,10**6,False)
        if (r["scope"],r["site"],r["sku_id"],r["revision"],r["mode"])!=(SYNTHETIC,site,p["sku_id"],p["revision"],p["rights_mode"]):
            raise WorkflowError("素材使用记录与站点、SKU 版本或用途不符。")
        if window(r)[0]>=window(r)[1]:
            raise WorkflowError("素材使用记录时间无效。")
        for k in ("asset_owner","authorization_ref","locale_review_ref"):
            term(r[k],k,True)
        identify(r["sha256"],"素材 SHA",r"[a-f0-9]{64}")


def observations(site,p):
    task_id(p["plan_task_id"],"冻结实验")
    check(p["observations"],"A B","同窗两臂观测")
    for row in p["observations"].values():
        extra=" placed_orders signed_orders refused_orders open_orders" if site=="PH" else ""
        check(row,"starts_at ends_at captured_at source_ref buckets impressions clicks paid_orders booked_spend_minor"+extra,
              "同窗合成指标")
        if stamp(row["captured_at"])>stamp(p["as_of"]):
            raise WorkflowError("使用了快照截点之后的观察。")
        left,right=window(row)
        if not left<right or right>stamp(row["captured_at"]):
            raise WorkflowError("测量窗口不完整或超过取样时刻。")
        term(row["source_ref"],"指标来源",True)
        for name in ("impressions","clicks","paid_orders","booked_spend_minor",*extra.split()):
            amount(row[name],name)


def bind(site,tool,p,fetch):
    shared="site currency scope packet_id experiment_id sku_id revision starts_at ends_at as_of owner "
    suffix="plan_task_id result_task_id recipient" if tool==HANDOFF else {
        "plan":"kind media_task_id shared variable arms assignment criteria rights_mode rights",
        "snapshot":"kind plan_task_id observations",
    }.get(p.get("kind") if isinstance(p,dict) else None)
    if suffix is None:
        raise WorkflowError("请选择冻结方案、回收样本或交接。")
    check(p,shared+suffix,"本机广告实验")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("仅使用本站、本币合成实验。")
    for k in ("packet_id","experiment_id","sku_id"):
        identify(p[k],k)
    amount(p["revision"],"SKU修订",1,10**6,False)
    term(p["owner"],"责任人")
    start,end=window(p);at=stamp(p["as_of"])
    if not start<end:
        raise WorkflowError("观察窗口无效。")
    pins={};links={}
    def pull(key,ident,t):
        r,pin=fetch(ident,site,t)
        if (r["site"],r["currency"],r["scope"])!=(site,p["currency"],SYNTHETIC):
            raise WorkflowError("来源跨站点、币种或用途。")
        pins[key]=pin;links[key]=r
        return r
    def pull_trial(key,ident,kind):
        task_id(ident,"实验回执")
        row=pull(key,ident,AD)
        if (row["kind"]!=kind or window(row)!=(start,end) or stamp(row["as_of"])>at
            or any(row[k]!=p[k] for k in ("experiment_id","sku_id","revision"))):
            raise WorkflowError("相同实验、商品修订和窗口才能交接。")
        return row
    if tool==AD and p["kind"]=="plan":
        plan(site,p)
        m=pull("media",p["media_task_id"],MEDIA)
        clip=m["clip"]
        claim=clip.get("claim",{}) if clip else {}
        if (m["state"]!="ALIGNED_OFFLINE" or not clip or not clip["local_synthetic_only"] or
            stamp(m["as_of"])>at or (claim.get("sku_id"),claim.get("revision"),claim.get("account_id"))!=(
                p["sku_id"],p["revision"],p["shared"]["account_id"])):
            raise WorkflowError("仅使用已核文件、用途与商品修订吻合的合成素材。")
        if p["rights"] and p["rights"]["sha256"]!=clip["sha256"]:
            raise WorkflowError("素材标识与独立回执 SHA 不一致。")
    elif tool==AD:
        observations(site,p)
        r=pull_trial("plan",p["plan_task_id"],"plan")
        if at<start:
            raise WorkflowError("冻结计划启动前不能提交样本。")
        for a,row in p["observations"].items():
            left,right=window(row)
            if left!=start or right!=min(end,at) or row["buckets"]!=r["plan"]["assignment"]["buckets"][a]:
                raise WorkflowError("两臂须用原固定分组与同一观察截点。")
    else:
        want="douyin-video-director" if site=="CN" else "tiktok-video-director"
        if p["recipient"]!=want:
            raise WorkflowError("创意交接角色或站点不符。")
        pull_trial("plan",p["plan_task_id"],"plan")
        if p["result_task_id"]:
            r=pull_trial("result",p["result_task_id"],"snapshot")
            if r["upstream"]["plan"]["task_id"]!=p["plan_task_id"]:
                raise WorkflowError("该复盘不是当前冻结计划的子任务。")
    return pins,links


def request_key(p):
    if "recipient" in p:
        return f'{p["plan_task_id"]}:{p["result_task_id"] or "plan"}:{p["recipient"]}'
    if p["kind"]=="plan":
        return f'{p["experiment_id"]}:plan'
    return f'{p["plan_task_id"]}:{stamp(p["as_of"]).timestamp()}'


def review_plan(site,p,media):
    r=p["rights"];start,end=window(p)
    buyer_proof=bool(r and r["authorization_ref"] and r["asset_owner"] and window(r)[0]<=start and end<=window(r)[1])
    locale_ok=site=="CN" or bool(r and r["locale_review_ref"])
    known=buyer_proof and locale_ok
    return {"state":"PLAN_FROZEN_FOR_LOCAL_REVIEW" if known else "PENDING_RIGHTS_OR_LOCALE",
            "plan":p,"media":{"task_id":media["task_id"],"sha256":media["clip"]["sha256"],"bytes":media["clip"]["bytes"],
                              "use_scope":"synthetic_offline_reference","same_media_for_both_arms":True},
            "changed_fields":["headline"],"rights_evidence_supported_in_fixture":buyer_proof,
            "locale_review_fixture_present":locale_ok,"actual_ad_rights_verified":False}


def snapshot(site,p,source):
    cfg=source["plan"];closed=stamp(p["as_of"])>=window(p)[1]
    rows={};pending=[];stops=[]
    if source["state"]!="PLAN_FROZEN_FOR_LOCAL_REVIEW":
        pending.append("plan_rights_or_locale_pending")
    for a,r in p["observations"].items():
        sourced=bool(r["source_ref"])
        i,c,b=(r[k] if sourced else None for k in ("impressions","clicks","paid_orders"))
        spent=r["booked_spend_minor"] if sourced else None
        counts=bool(None not in (i,c,b) and 0<=b<=c<=i)
        if not closed or not counts or i<cfg["criteria"]["min_exposures"] or spent is None:
            pending.append(f"{a}:incomplete_window_sample_or_cost")
        if spent is not None and spent>cfg["shared"]["budget_per_arm_minor"]:
            stops.append(f"{a}:budget_exceeded")
        v={"input":r,"ctr":ratio(c,i) if counts else None,"paid_per_click":ratio(b,c) if counts else None,
           "paid_per_exposure":ratio(b,i) if counts else None,"booked_spend_minor":spent}
        if site=="PH":
            placed,n,d,o=(r[k] if sourced else None for k in ("placed_orders","signed_orders","refused_orders","open_orders"))
            coherent=counts and None not in (placed,n,d,o) and n+d+o==placed and b<=placed<=c
            v["placed_orders"]=placed
            v["signed_progress_only"]=ratio(n,placed) if coherent else None
            v["mature_signed_per_placed"]=ratio(n,placed) if coherent and closed and o==0 else None
            if v["mature_signed_per_placed"] is None:
                pending.append(f"{a}:signed_cohort_pending")
            elif n*10000 < cfg["criteria"]["min_signed_bps"]*placed:
                stops.append(f"{a}:fixture_signed_guard")
        rows[a]=v
    drop=None
    if not pending:
        xv,yv=(rows[k]["paid_per_exposure"] for k in ARMS)
        if xv is not None and yv is not None and xv["numerator"]>0:
            x,y=(Fraction(v["numerator"],v["denominator"]) for v in (xv,yv))
            change=(x-y)/x
            drop=ratio(change.numerator,change.denominator)
            if change*10000>=cfg["criteria"]["max_relative_paid_drop_bps"]:
                stops.append("B:paid_efficiency_guard")
        else:
            pending.append("base_paid_denominator_or_baseline_zero")
    state=("ROLLBACK_REVIEW_ONLY" if stops else "PENDING_DATA" if pending else "OBSERVATIONS_REVIEWABLE")
    return {"state":state,"by_arm":rows,"evidence_gaps":pending,"fixture_triggers":stops,
            "relative_paid_rate_drop":drop,"incremental_effect":None,"causal_conclusion":None,
            "spend_pause_review":any(x.endswith(":budget_exceeded") for x in stops),
            "rollback":{"owner":cfg["criteria"]["rollback_owner"],"previous_field":cfg["variable"],
                        "previous_value":cfg["arms"]["A"]["headline"],"executed":False}}


def intake(p,links):
    source=links["plan"];r=links.get("result")
    state="CREATIVE_REVIEW_RECEIVED" if source["state"]=="PLAN_FROZEN_FOR_LOCAL_REVIEW" else "RETURNED_FOR_EVIDENCE"
    return {"state":state,"recipient":p["recipient"],"source_plan_task_id":p["plan_task_id"],
            "source_result_task_id":p["result_task_id"],"plan_state":source["state"],"observed_state":r["state"] if r else None,
            "one_variable":source["changed_fields"],"same_base_asset":source["media"],"causal_conclusion":None}


def receipt(r,b):
    return {**{k:r[k] for k in ("task_id","site","input_sha256","upstream")},
            "result_sha256":sha256(b["json"]).hexdigest(),"report_sha256":sha256(b["report"]).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("使用工作台验证过的本机素材引用。")
    pins,links=linked
    kind=p.get("kind","handoff")
    out=(review_plan(site,p,links["media"]) if kind=="plan" else
         snapshot(site,p,links["plan"]) if kind=="snapshot" else intake(p,links))
    keys=("site","currency","scope","packet_id","experiment_id","sku_id","revision","starts_at","ends_at","as_of","owner")
    r={**{k:p[k] for k in keys},**out,"kind":kind,"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,"real_delivery_checked":False,
       "actual_ad_action":False,"model_called":False}
    lines=[f"# {site} 素材实验 · {p['packet_id']}","","合成计划、快照及创意收件只供本机核验。",
           f"状态：{r['state']}。"]
    if kind=="plan":
        lines+=["双臂共用同一素材、受众、观察窗、预算与价格。仅更改标题。"]
    if kind=="snapshot":
        for a,x in r["by_arm"].items():
            ls=[x.get(k) for k in ("ctr","paid_per_exposure","mature_signed_per_placed")]
            lines.append(f"- {a} · CTR / 支付每曝光 / 成熟签收率："+ " / ".join(v["value"] if v else "待核或不适用" for v in ls))
    b={"json":encode(r),"report":("\n".join(lines)+"\n").encode("utf8")}
    b["receipt"]=encode(receipt(r,b))
    paths={k:folder/n for k,n in FILES.items()}
    for k,f in paths.items():
        with f.open("xb") as stream:
            stream.write(b[k])
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or
        r["upstream"]!=task["source"]["role_packet_pins"] or
        (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"]) or
        json.loads(files["receipt"])!=receipt(r,files)):
        raise WorkflowError("素材实验与来源回执无法核验。")


def public_tools():
    return [{"id":t,"sites":["CN","PH"],"role_suffix":role,"label":label,
             "description":"同素材且只变标题的合成实验。","input_example":{}} for t,role,label in (
                 (AD,"ad-buyer","素材单变量实验"),(HANDOFF,"video-director","素材实验收件"))]

