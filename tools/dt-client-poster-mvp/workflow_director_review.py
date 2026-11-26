"""Video director reviews linked to the real local AD experiment receipts."""
from fractions import Fraction
from hashlib import sha256
import json
from struct import unpack_from

from role_tools import ROUTES, SYNTHETIC
from workflow_ad_experiment import AD, HANDOFF, ARMS, MEDIA
from workflow_lead_weekly import encode, ratio, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL="director-offline-video-review"
TOOLS=(TOOL,)
FILES={"json":"director-review.json","report":"director-review.md","receipt":"director-review-receipt.json"}


class WorkflowError(ValueError):
    pass


def window(r):
    return stamp(r["starts_at"]),stamp(r["ends_at"])


def metric(n,d,*,valid=True,closed=True):
    if not valid:
        state="PENDING_CONFLICT"
    elif not closed:
        state="PENDING_MATURITY"
    elif n is None or d is None:
        state="MISSING_EVIDENCE"
    elif d==0:
        state="ZERO_DENOMINATOR"
    else:
        state="OBSERVED_FIXTURE"
    return {"state":state,"numerator":n,"denominator":d,"ratio":ratio(n,d) if state=="OBSERVED_FIXTURE" else None}


def measurement(row,p,label):
    if window(row)!=window(p) or not window(p)[1]<=stamp(row["captured_at"])<=stamp(p["as_of"]):
        raise WorkflowError(f"{label} 须同销售/观看窗口，证据截点不早于结束也不晚于评估。")
    term(row["source_ref"],f"{label}出处",True)


def mp4_boxes(data,start,end):
    while start+8<=end:
        size,tag=unpack_from(">I4s",data,start)
        header=8
        if size==1:
            if start+16>end:
                return
            size=unpack_from(">Q",data,start+8)[0]
            header=16
        elif size==0:
            size=end-start
        if size<header or start+size>end:
            return
        yield tag,start+header,start+size
        start+=size


def movie_duration(data):
    for tag,left,right in mp4_boxes(data,0,len(data)):
        if tag!=b"moov":
            continue
        for child,payload,limit in mp4_boxes(data,left,right):
            if child!=b"mvhd" or payload>=limit:
                continue
            v=data[payload]
            if v==0 and payload+20<=limit:
                unit,duration=unpack_from(">II",data,payload+12)
            elif v==1 and payload+32<=limit:
                unit,duration=unpack_from(">IQ",data,payload+20)
            else:
                return None
            if unit and duration:
                return Fraction(1000*duration,unit)
    return None


def validation(site,p):
    check(p,"site currency scope packet_id experiment_id sku_id revision starts_at ends_at as_of owner review_due ack_task_id retention_at_ms arms",
          "编导本机复盘")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("复盘限定本站、本币和合成数据。")
    for k in ("packet_id","experiment_id","sku_id"):
        identify(p[k],k)
    amount(p["revision"],"商品修订",1,10**6,False)
    task_id(p["ack_task_id"],"编导已签收任务")
    amount(p["retention_at_ms"],"留存检查点毫秒",1,600000,False)
    if not window(p)[0]<window(p)[1]<=stamp(p["as_of"])<=stamp(p["review_due"]):
        raise WorkflowError("同窗复盘须在观察窗口结束后，保留有效负责人截止时间。")
    term(p["owner"],"负责人")
    check(p["arms"],"A B","两臂")
    for r in p["arms"].values():
        check(r,"creative context playback commerce","版本、口径与合成观测")
        asset=r["creative"]
        check(asset,"media_task_id media_sha256 revision headline","已冻结素材与标题")
        task_id(asset["media_task_id"],"本机素材")
        identify(asset["media_sha256"],"素材哈希",r"[a-f0-9]{64}")
        amount(asset["revision"],"素材修订",1,10**6,False)
        term(asset["headline"],"实际使用的合成标题")
        ctx=r["context"]
        check(ctx,"audience_id page_revision source_ref attribution_basis","商品页、受众与归因口径")
        for k in ctx:
            term(ctx[k],k,True)
        pb=r["playback"]
        check(pb,"starts_at ends_at captured_at source_ref started_views reached_checkpoint_views completed_views","播放事件样本")
        measurement(pb,p,"播放")
        for k in ("started_views","reached_checkpoint_views","completed_views"):
            amount(pb[k],k)
        cm=r["commerce"]
        extra=" signed_goods_gmv_minor" if site=="PH" else ""
        check(cm,"starts_at ends_at captured_at source_ref paid_goods_gmv_minor currency settlement refund costs"+extra,
              "支付、退款与结算")
        measurement(cm,p,"支付")
        if cm["currency"]!=p["currency"]:
            raise WorkflowError("营销观察不可跨币种拼接。")
        amount(cm["paid_goods_gmv_minor"],"支付 GMV")
        if site=="PH":
            amount(cm["signed_goods_gmv_minor"],"签收商品 GMV")
        for k in ("settlement","refund","costs"):
            sub=cm[k]
            check(sub,"amount_minor state source_ref captured_at","独立财务字段")
            term(sub["source_ref"],f"{k} 出处",True)
            amount(sub["amount_minor"],k)
            if sub["state"] not in ("closed_fixture","open_fixture","unknown"):
                raise WorkflowError("分别声明已结、未成熟或未知。")
            if not window(p)[1]<=stamp(sub["captured_at"])<=stamp(p["as_of"]):
                raise WorkflowError("财务截点与同一销售批次不符。")


def bind(site,tool,p,fetch,artifact):
    validation(site,p)
    pins={};links={}
    def load(name,ident,expected):
        r,pin=fetch(ident,site,expected)
        if (r["site"],r["currency"],r["scope"])!=(site,p["currency"],SYNTHETIC):
            raise WorkflowError("只复用本站同币种的本机合成回执。")
        pins[name]=pin;links[name]=r
        return r
    a=load("director_ack",p["ack_task_id"],HANDOFF)
    actor="douyin-video-director" if site=="CN" else "tiktok-video-director"
    if a["state"]!="CREATIVE_REVIEW_RECEIVED" or a["recipient"]!=actor or not a["source_result_task_id"]:
        raise WorkflowError("须由本站编导收到含原结果的创意实验回执。")
    original=load("ad_plan",a["source_plan_task_id"],AD)
    result=load("ad_result",a["source_result_task_id"],AD)
    for r in (a,original,result):
        if window(r)!=window(p) or stamp(r["as_of"])>stamp(p["as_of"]) or any(
            r[k]!=p[k] for k in ("experiment_id","sku_id","revision")
        ):
            raise WorkflowError("不能拼接不同实验、SKU 修订、窗口或晚于截点的回执。")
    if (original["kind"],result["kind"],a["one_variable"])!=("plan","snapshot",["headline"]) or (
        a["upstream"]!={"plan":pins["ad_plan"],"result":pins["ad_result"]} or result["upstream"]["plan"]!=pins["ad_plan"]
    ):
        raise WorkflowError("编导收到的计划和结果摘要与原文件不一致。")
    media=original["media"]
    clip=load("original_clip",media["task_id"],MEDIA)
    raw=artifact(media["task_id"],"video")
    if (sha256(raw).hexdigest()!=media["sha256"] or clip["clip"]["sha256"]!=media["sha256"]
        or original["upstream"]["media"]!=pins["original_clip"] or a["same_base_asset"]!=media):
        raise WorkflowError("复盘的素材文件摘要、引用或收件记录已不一致。")
    links["duration"]=movie_duration(raw)
    for arm in ARMS:
        asset=p["arms"][arm]["creative"]
        if (asset["media_task_id"],asset["media_sha256"],asset["revision"],asset["headline"])!=(
            media["task_id"],media["sha256"],original["revision"],original["plan"]["arms"][arm]["headline"]
        ):
            raise WorkflowError("回收素材哈希、版本或标题与冻结方案不符。")
        if window(result["by_arm"][arm]["input"])!=window(p):
            raise WorkflowError("不能把较短的广告快照并入完整视频窗口。")
    return pins,links


def request_key(p):
    return p["packet_id"]


def observed_amount(row):
    if row["state"]=="open_fixture":
        mark="PENDING_MATURITY"
    elif row["state"]!="closed_fixture" or not row["source_ref"] or row["amount_minor"] is None:
        mark="MISSING_EVIDENCE"
    else:
        mark="OBSERVED_FIXTURE"
    return {"state":mark,"amount_minor":row["amount_minor"] if mark=="OBSERVED_FIXTURE" else None,"source":row}


def score_branch(site,r,ad):
    pb=r["playback"];cm=r["commerce"];raw=ad["input"]
    started,early,full=(pb[k] if pb["source_ref"] else None for k in ("started_views","reached_checkpoint_views","completed_views"))
    valid=(started is None or all(x is None or x<=started for x in (early,full))) and (
        early is None or full is None or full<=early
    )
    count_known=bool(raw["source_ref"] and ad["ctr"] is not None)
    i,c,b=(raw[k] if raw["source_ref"] else None for k in ("impressions","clicks","paid_orders"))
    coherent=not (None not in (i,c,b) and not (b<=c<=i))
    rates={"early_retention":metric(early,started,valid=valid),"completion":metric(full,started,valid=valid),
           "ctr":metric(c,i,valid=coherent),"paid_per_click":metric(b,c,valid=coherent),
           "paid_per_exposure":metric(b,i,valid=coherent)}
    gross=cm["paid_goods_gmv_minor"] if cm["source_ref"] and count_known else None
    if b==0 and gross not in (None,0):
        gross=None
    settled,refund,cost=(observed_amount(cm[k]) for k in ("settlement","refund","costs"))
    sg=None
    verified_signed=None
    placed=None
    if site=="PH":
        placed=ad["placed_orders"]
        n,d,o=(raw[k] if raw["source_ref"] else None for k in ("signed_orders","refused_orders","open_orders"))
        all_counts=None not in (placed,n,d,o)
        cohort_valid=not all_counts or n+d+o==placed
        mature=all_counts and cohort_valid and o==0
        verified_signed=n if mature else None
        # A verified zero cohort, an open cohort and missing counts remain distinct.
        rates.update(placed_per_click=metric(placed,c,valid=coherent),
                     mature_signed_per_placed=metric(n if all_counts else None,placed,valid=cohort_valid,closed=not (o and o>0)),
                     mature_signed_per_click=metric(n if all_counts else None,c,valid=coherent and cohort_valid,closed=not (o and o>0)))
        sg=cm["signed_goods_gmv_minor"] if mature and cm["source_ref"] else None
        if n==0 and sg not in (None,0):
            sg=None
    basis=gross if site=="CN" else sg
    settlement=settled["amount_minor"];returned=refund["amount_minor"];expenses=cost["amount_minor"]
    if None in (basis,settlement,returned):
        reconciliation="PENDING_AMOUNT_OR_MATURITY"
    elif basis-returned!=settlement:
        reconciliation="PENDING_CONFLICT"
    else:
        reconciliation="RECONCILED_FIXTURE"
    contribution=settlement-expenses if reconciliation=="RECONCILED_FIXTURE" and expenses is not None else None
    return {"rates":rates,"sources":{"playback":pb,"commerce":cm,"ad_window":raw},
            "commerce":{"paid_goods_gmv_minor":gross,"placed_orders":placed,"mature_signed_orders":verified_signed,
                        "signed_goods_gmv_minor":sg,"settlement":settled,"refund":refund,"costs":cost,
                        "reconciliation":reconciliation,"contribution_minor":contribution,
                        "contribution_maturity":"COMPLETE_FIXTURE" if contribution is not None else "PENDING",
                        "refund_subtracted_again":False,"actual_bank_receipt":None},
            "context":r["context"],"creative":r["creative"]}


def delta(v,w):
    a,b=v["ratio"],w["ratio"]
    if a is None or b is None:
        return None
    diff=Fraction(b["numerator"],b["denominator"])-Fraction(a["numerator"],a["denominator"])
    return ratio(diff.numerator,diff.denominator)


def compute(site,p,links):
    data=links["ad_result"]["by_arm"]
    rows={a:score_branch(site,p["arms"][a],data[a]) for a in ARMS}
    context_a,context_b=(rows[a]["context"] for a in ARMS)
    common=links["ad_plan"]["plan"]["shared"]
    differences=[k for k in ("audience_id","page_revision","attribution_basis") if (
        not context_a[k] or context_a[k]!=context_b[k]
    )]
    if context_a["audience_id"]!=common["audience_id"] and "audience_id" not in differences:
        differences.append("audience_id")
    if (context_a["attribution_basis"]!="eligible_product_impressions_same_frozen_cohort"
        and "attribution_basis" not in differences):
        differences.append("attribution_basis")
    if not context_a["source_ref"] or not context_b["source_ref"]:
        differences.append("context_source_missing")
    comparison=not differences
    runtime=links["duration"]
    for arm in rows.values():
        if runtime is None:
            status="CLIP_DURATION_UNKNOWN"
        elif p["retention_at_ms"]>runtime:
            status=("IMPOSSIBLE_CHECKPOINT_SAMPLE" if arm["rates"]["early_retention"]["numerator"] not in (0,None)
                    else "NOT_APPLICABLE_SHORT_CLIP")
        else:
            continue
        arm["rates"]["early_retention"].update(state=status,ratio=None)
    diffs={k:delta(rows["A"]["rates"][k],rows["B"]["rates"][k]) if comparison else None
           for k in rows["A"]["rates"]}
    # State expresses metric comparability separately from financial maturity.
    if not comparison:
        state="CONTEXT_NOT_COMPARABLE"
    elif any(diffs[k] is None for k in ("early_retention","completion","ctr","paid_per_click")):
        state="PENDING_METRICS"
    elif any(r["commerce"]["contribution_maturity"]=="PENDING" for r in rows.values()):
        state="REVIEW_WITH_FINANCIAL_GAPS"
    else:
        state="REVIEW_RECORDED"
    return {"state":state,"actual_observed_change_field":"headline","edited_hook_verified":False,
            "comparability":{"matched_fixture_context":comparison,"mismatched_or_unknown":differences},
            "by_arm":rows,"observed_B_minus_A":diffs,"causal_increment":None,"cause_established":False,
            "clip_duration_ms":float(runtime) if runtime is not None else None,"retention_at_ms":p["retention_at_ms"],
            "next_checks":[{"dimension":"hook","basis":"指定时点留存与完播只提示注意力变化；这轮登记的是标题。",
                            "action":"冻结同标题、商品页和人群，另设计单一首镜变体再核。"},
                           {"dimension":"product_page","basis":"点击与下单或签收口径独立。",
                            "action":"在同 SKU、同承诺、同价的候选页核支付和退款线索。"},
                           {"dimension":"audience","basis":"人群或口径不一致时保留原分组，不作组间归因。",
                            "action":"核同窗归因和互斥样本。"}],
            "next_review":{"owner":p["owner"],"due_at":p["review_due"]}}


def receipt(r,b):
    return {**{k:r[k] for k in ("task_id","site","input_sha256","upstream")},
            "result_sha256":sha256(b["json"]).hexdigest(),"report_sha256":sha256(b["report"]).hexdigest(),
            "execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("使用原编导签收中已核的本机素材。")
    pins,links=linked
    keys=("site","currency","scope","packet_id","experiment_id","sku_id","revision","starts_at","ends_at","as_of")
    r={**{k:p[k] for k in keys},**compute(site,p,links),"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "actual_release_or_ad_action":False,"model_called":False}
    lines=[f"# {site} 编导复盘 · {p['packet_id']}","","仅本机合成证据，费用与结算按同批口径。",
           f"状态：{r['state']}。",f"观察窗：{p['starts_at']} 至 {p['ends_at']}。"
           ,f"素材时长和留存检查点（毫秒）：{r['clip_duration_ms']} / {r['retention_at_ms']}。"]
    for a,row in r["by_arm"].items():
        lines+=["",f"## {a} 组"]
        for name,m in row["rates"].items():
            lines.append(f"- {name}：{m['ratio']['value'] if m['ratio'] else m['state']}")
        c=row["commerce"]
        lines.append(f"- 支付／签收商品额／结算／退款／贡献（最小货币单位）："
                     f"{c['paid_goods_gmv_minor']} / {c['signed_goods_gmv_minor']} / {c['settlement']['amount_minor']} / "
                     f"{c['refund']['amount_minor']} / {c['contribution_minor']}。复核：{c['reconciliation']}。")
    lines.extend(f"- {x['dimension']}：{x['action']}" for x in r["next_checks"])
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
        raise WorkflowError("编导复盘回执或证据无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":["CN","PH"],"role_suffix":"video-director",
             "label":"素材指标与财务分段复盘","description":"读回编导实验收件，按同 SKU、窗口与素材版本计算合成证据。",
             "input_example":{}}]

