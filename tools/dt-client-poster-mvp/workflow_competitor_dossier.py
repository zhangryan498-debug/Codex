"""Save an evidence-bound comparison dossier for two shops and two scoped products."""

from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_competitor_price_band import TOOL as PRICE
from workflow_competitor_bulletin import TOOL as MONITOR
from workflow_lead_weekly import encode, ratio, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL="competitor-two-shop-dossier"
FILES={"json":"competitor-dossier.json","report":"competitor-dossier.md","receipt":"competitor-dossier-receipt.json"}
IDS=("shop_id","product_id","sku_id")
BLOCKS=("shop","content","affiliate","fulfillment")
LABELS=(
    ("identity","识别信息"),("product","商品侧对比"),("content","内容与流量侧对比"),
    ("affiliate","达人生态对比"),("pricing","活动与价格策略"),("fulfillment","履约与服务对比"),
    ("swot","SWOT 与结论"),("actions","可执行结论"),("update","情报更新"),
)


class WorkflowError(ValueError):
    pass


def identity(r):
    return tuple(r[k] for k in IDS)


def validate(site,p):
    check(p,"site currency scope dossier_id revision previous_task_id revision_reason period evaluated_at "
            "price_task_id monitor_task_id subjects checkout_tie_minor owner due_at","双店双品档案")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("只接受所选站点、本币和明确标识的本机合成资料。")
    identify(p["dossier_id"],"档案编号")
    amount(p["revision"],"档案版本",1,1000000,False)
    if (p["revision"]==1)!=(p["previous_task_id"] is None):
        raise WorkflowError("修订须给出同档案上一版。")
    if p["previous_task_id"]:
        task_id(p["previous_task_id"],"上一版")
        term(p["revision_reason"],"修订理由")
    elif p["revision_reason"] is not None:
        raise WorkflowError("首版没有修订理由。")
    check(p["period"],"start end","观察窗")
    start,end,cutoff=(stamp(t) for t in (p["period"]["start"],p["period"]["end"],p["evaluated_at"]))
    if not start<end<=cutoff or end-start!=timedelta(days=7) or stamp(p["due_at"])<cutoff:
        raise WorkflowError("对比锚定已闭合的本地七日监测窗和复核期限。")
    amount(p["checkout_tie_minor"],"合成同等价差门槛",0,10**6,False)
    term(p["owner"],"复核责任")
    for k in ("price_task_id","monitor_task_id"):
        task_id(p[k],k)
    if not isinstance(p["subjects"],list) or len(p["subjects"])!=2:
        raise WorkflowError("归档必须有两个明确对象。")
    for s in p["subjects"]:
        check(s,"shop_id product_id sku_id detail "+" ".join(BLOCKS),"两店两品对象")
        for k in IDS:
            identify(s[k],k)
        detail=s["detail"]
        check(detail,"site currency shop_id product_id sku_id captured_at listing_ref visual_ref use "
                     "listing_spec picture_spec listing_units picture_units declared_model shared_manufacturer_ref",
                      "商品规格与声明")
        if (detail["site"],detail["currency"],identity(detail))!=(site,p["currency"],identity(s)):
            raise WorkflowError("详情或图文资料不能借给别的站点、店、商品或 SKU。")
        if not start<=stamp(detail["captured_at"])<end:
            raise WorkflowError("详情需在同一观察窗内提供抓取时点。")
        for k in ("listing_ref","visual_ref","use","listing_spec","picture_spec","declared_model","shared_manufacturer_ref"):
            term(detail[k],k,True)
        for k in ("listing_units","picture_units"):
            amount(detail[k],k,1,1000)
        for name in BLOCKS:
            r=s[name]
            check(r,"site currency shop_id product_id sku_id source_ref captured_at metric numerator denominator domain","分域观察")
            if r["domain"]!=("shop" if name=="shop" else "target_product"):
                raise WorkflowError("店铺经营和目标商品证据不能混用。")
            target=identity(s) if name!="shop" else (s["shop_id"],None,None)
            if (r["site"],r["currency"],identity(r))!=(site,p["currency"],target):
                raise WorkflowError("分域资料必须与对象、币种及站点匹配。")
            term(r["source_ref"],"分域来源",True)
            term(r["metric"],"指标口径")
            if not start<=stamp(r["captured_at"])<end:
                raise WorkflowError("分域观察必须在同一时间窗口。")
            for k in ("numerator","denominator"):
                amount(r[k],k)
    for k in ("shop_id","product_id"):
        if p["subjects"][0][k]==p["subjects"][1][k]:
            raise WorkflowError("两店身份与两个商品编号须分别独立。")
    return start,end,cutoff


def bind(site,p,load):
    window=validate(site,p)
    pins={}
    def fetch(ident,kind):
        row,pins[ident]=load(ident,site,kind)
        if row["currency"]!=p["currency"] or row["scope"]!=SYNTHETIC:
            raise WorkflowError("仅引用同站同币合成材料。")
        return row
    price=fetch(p["price_task_id"],PRICE)
    monitor=fetch(p["monitor_task_id"],MONITOR)
    cur=monitor["coverage"]["current"]
    if (monitor["cadence"]!="week" or
            (stamp(cur["start"]),stamp(cur["end_exclusive"]))!=window[:2] or stamp(monitor["cutoff_at"])>window[2] or
            monitor["upstream"].get(p["price_task_id"])!=pins[p["price_task_id"]]):
        raise WorkflowError("仅引用同窗 C09 周报及其确切绑定的 C04 价格源。")
    old=None
    if p["previous_task_id"]:
        old=fetch(p["previous_task_id"],TOOL)
        if ((old["dossier_id"],old["revision"]+1,old["period"],old["checkout_tie_minor"])!=
                (p["dossier_id"],p["revision"],p["period"],p["checkout_tie_minor"])
            or stamp(old["evaluated_at"])>=window[2] or
            set(map(tuple, old["subject_keys"]))!={identity(s) for s in p["subjects"]}):
            raise WorkflowError("旧版、对标对象、时窗、排序口径或版本不匹配。")
    return window,pins,price,monitor,old


def metric(block):
    n,d=block["numerator"],block["denominator"]
    usable=block["source_ref"] is not None and None not in (n,d) and d>0
    subtotal=ratio(n,d) if usable else None
    return {**block,"ratio":subtotal,"state":"SYNTHETIC_SCOPED_SAMPLE" if subtotal is not None else "PENDING"}


def report_subject(row,p,linked):
    window,pins,price,monitor,_=linked
    start,end,_=window
    key=identity(row)
    # Consume exact C04 leaves and the C09 identifying observation; aggregates are not copied across subjects.
    q=[x for x in price["observations"] if tuple(x["identity"])==key and start<=stamp(x["observed_at"])<end]
    q0=q[0] if len(q)==1 else None
    cited=bool(q0 and any(tuple(x["identity"])==key and x["source_task_id"]==p["price_task_id"]
                           and x["observation_id"]==q0["id"] and x["captured_at"]==q0["observed_at"]
                           for x in monitor["observations"]["current"]))
    quotation=None
    if q0 is not None:
        fields=("id","observed_at","source_ref","conditional_checkout_minor","comparison_fields","state","list_minor","paid_evidence")
        quotation={**{k:q0[k] for k in fields},"price_task_id":p["price_task_id"],"confirmed_by_monitor_reference":cited}
    observed_change=next((x for x in monitor["same_sku_quote_pairs"] if tuple(x["identity"])==key),None)
    momentum=({"source_task_id":p["monitor_task_id"],"kind":"same_sku_quote_change_only","delta_minor":observed_change["delta_minor"],
                "business_momentum_confirmed":False} if observed_change is not None else
              {"source_task_id":p["monitor_task_id"],"kind":"NO_MATCHED_CHANGE","delta_minor":None,"business_momentum_confirmed":False})
    detail=row["detail"]; errors=[]; pending=[]
    if detail["listing_ref"] is None or detail["visual_ref"] is None or any(detail[k] is None for k in (
            "listing_spec","picture_spec","listing_units","picture_units","use","declared_model")):
        pending.append("product_spec_or_source_unknown")
    if ((None not in (detail["listing_spec"],detail["picture_spec"]) and detail["listing_spec"]!=detail["picture_spec"]) or
            (None not in (detail["listing_units"],detail["picture_units"]) and detail["listing_units"]!=detail["picture_units"])):
        errors.append("listing_and_picture_spec_conflict")
    if q0 and (detail["listing_spec"] not in (None,q0["comparison_fields"][1]) or
               detail["listing_units"] not in (None,q0["comparison_fields"][2])):
        errors.append("detail_and_price_spec_conflict")
    if not q0 or not cited or q0["state"]!="READY_OFFLINE" or q0["conditional_checkout_minor"] is None:
        pending.append("unverified_same_sku_checkout")
    nonrank={k:metric(row[k]) for k in BLOCKS}
    return {"identity":list(key),"detail":detail,"quote":quotation,"local_diagnostics":nonrank,
            "short_term_momentum":momentum,"critical_conflicts":errors,"ranking_evidence_gaps":pending}


def ranking(rows,p):
    a,b=rows
    q=[x["quote"] for x in rows]
    da,db=(x["detail"] for x in rows)
    reasons=[k for x in rows for k in x["critical_conflicts"]]
    gaps=[k for x in rows for k in x["ranking_evidence_gaps"]]
    if all(q):
        if q[0]["comparison_fields"]!=q[1]["comparison_fields"]:
            reasons.append("different_quote_terms_or_spec")
        if q[0]["observed_at"]!=q[1]["observed_at"]:
            reasons.append("different_observation_time")
    if None not in (da["use"],db["use"]) and da["use"]!=db["use"]:
        reasons.append("different_use")
    common_ref=da["shared_manufacturer_ref"]
    equivalent=bool(common_ref and common_ref==db["shared_manufacturer_ref"]
                    and da["declared_model"]==db["declared_model"] and da["declared_model"] is not None)
    fit="NONCOMPARABLE" if reasons else "UNKNOWN" if gaps else "SAME_MODEL_SYNTHETIC_PROOF" if equivalent else "NEAR_SAME_USE"
    items=[]
    if not reasons and not gaps:
        gap=abs(q[0]["conditional_checkout_minor"]-q[1]["conditional_checkout_minor"])
        tie=gap<=p["checkout_tie_minor"]
        ordered=sorted(rows,key=lambda x:x["quote"]["conditional_checkout_minor"])
        for i,row in enumerate(ordered,1):
            items.append({"identity":row["identity"],"rank":1 if tie else i,
                          "checkout_minor":row["quote"]["conditional_checkout_minor"],
                          "basis":"observed_synthetic_same_scope_checkout",
                          "why":"price_difference_within_predeclared_tie_band" if tie else "lower_matched_checkout_first",
                          "quote_observation_id":row["quote"]["id"]})
    return {"state":"BLOCKED_COMPARISON" if reasons else "PENDING" if gaps else "CO_PRIORITY" if items[1]["rank"]==1 else "ORDERED",
            "product_match":fit,"critical_conflicts":sorted(set(reasons)),"ranking_evidence_gaps":sorted(set(gaps)),
            "order":items,"tie_threshold_minor":p["checkout_tie_minor"],"shop_rank_produced":False,
            "business_results_confirmed":False,"whole_store_strength_used_as_product_score":False}


def summarize(p,linked):
    _,pins,price,monitor,old=linked
    rows=[report_subject(s,p,linked) for s in p["subjects"]]
    rank=ranking(rows,p)
    all_known=bool(rank["order"])
    guidance=("复核同 SKU 条件到手价、原图规格与可比窗口，按样本优先级核货盘。" if all_known else
              "补同 SKU 商品页、图片规格、结算条件和价格证据；证据对齐前暂缓排序。")
    notes={
        "strengths":[{"identity":x["identity"],"evidence":x["quote"]["id"]} for x in rows if x["quote"] is not None
                     and not x["ranking_evidence_gaps"] and not x["critical_conflicts"]],
        "weaknesses":[{"identity":x["identity"],"findings":x["ranking_evidence_gaps"]+x["critical_conflicts"]} for x in rows],
        "opportunities":rank["order"],
        "threats":["样本日期与覆盖受限；同价不代表利润、转化或者可全量铺货。","活动、全店表现与站点规则需独立证据。"],
        "conclusion":rank["state"],"actual_business_advantage":None,
    }
    action={"owner":p["owner"],"due_at":p["due_at"],"action":guidance,
            "evidence_task_ids":[p["price_task_id"],p["monitor_task_id"]],
            "release_authorized":False}
    sa,sb=(x["local_diagnostics"]["shop"] for x in rows)
    shape=sa["ratio"] and sb["ratio"] and sa["metric"]==sb["metric"]
    comparison=(sa["numerator"]*sb["denominator"]-sb["numerator"]*sa["denominator"]) if shape else None
    shop_axis={"scope":"limited_synthetic_shop_sample","metric":sa["metric"] if shape else None,
               "higher_sample_ratio_shop":(rows[0]["identity"][0] if comparison>0 else rows[1]["identity"][0])
                  if comparison else None,
               "tied":comparison==0,"actual_whole_store_advantage":None}
    sections={
        "identity":{"subject_keys":[x["identity"] for x in rows],"product_match":rank["product_match"],"shop_axis":shop_axis,
                    "shop_advantage":[{"shop_id":x["identity"][0],"limited_sample":x["local_diagnostics"]["shop"],
                                       "whole_store_advantage":None} for x in rows]},
        "product":[{"identity":x["identity"],"detail":x["detail"],"critical_conflicts":x["critical_conflicts"]} for x in rows],
        "content":[{"identity":x["identity"],"scope":x["local_diagnostics"]["content"]} for x in rows],
        "affiliate":[{"identity":x["identity"],"scope":x["local_diagnostics"]["affiliate"]} for x in rows],
        "pricing":[{"identity":x["identity"],"quote":x["quote"],"observation_momentum":x["short_term_momentum"]} for x in rows],
        "fulfillment":[{"identity":x["identity"],"scope":x["local_diagnostics"]["fulfillment"]} for x in rows],
        "swot":notes,"actions":action,"update":{"period":p["period"],"coverage":monitor["coverage"],"pins":pins,
                      "confidence":"retained_synthetic_observation_only","next_review_at":p["due_at"]},
    }
    prior=({"task_id":old["task_id"],"revision":old["revision"],"state":old["ranking"]["state"],
            "reason":p["revision_reason"],"became_rankable":not old["ranking"]["order"] and all_known} if old else None)
    return {"state":rank["state"],"ranking":rank,"subjects":rows,"sections":sections,
            "section_order":[x[0] for x in LABELS],"subject_keys":[x["identity"] for x in rows],"upstream":pins,
            "period":p["period"],"evaluated_at":p["evaluated_at"],"checkout_tie_minor":p["checkout_tie_minor"],
            "revision":p["revision"],"revision_history":prior,"dossier_id":p["dossier_id"],
            "freshness":"archived_synthetic","new_market_capture_started":False,"supplier_outreach_started":False}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","input_sha256","upstream","dossier_id","revision")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def render(r):
    lines=[f"# {r['site']} 两店两品档案 · {r['dossier_id']} v{r['revision']}","",
           f"结论：{r['ranking']['state']}。商品对标 {r['ranking']['product_match']}。币种 {r['currency']}。",
           "本机留档和明确标识的合成补充。条件价格用于样本研究；全店优势及业务结果保持待核。",""]
    for i,(key,name) in enumerate(LABELS,1):
        lines.extend([f"## {i}. {name}","",encode(r["sections"][key]).decode().rstrip(),""])
    lines.append("复核："+safe_md(r["sections"]["actions"]["action"]))
    return "\n".join(lines)+"\n"


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets or tool!=TOOL:
        raise WorkflowError("此入口只接收本机档案和明确合成对照。")
    r={**summarize(p,linked),"task_id":folder.name,"site":site,"currency":p["currency"],"scope":SYNTHETIC,"tool_id":tool,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(r);body=render(r).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":body,"receipt":encode(receipt(r,raw,body))}.items():
        with paths[k].open("xb") as f:f.write(b)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["dossier_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("两店两品档案的版本或来源回执无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"competitor-analyst","label":"两店两品档案与样本排序",
             "description":"按站点、店、商品、SKU、可信引用与同窗输出九段档案。","input_example":{}}]

