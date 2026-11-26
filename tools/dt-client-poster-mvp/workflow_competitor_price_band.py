"""Comparable local price bands with explicit basket, source, and window boundaries."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_live_rehearsal import check, identify, amount, stamp, require_bits


class WorkflowError(ValueError):
    pass


TOOL="competitor-price-band"
FILES={"json":"price-band.json","report":"price-band.md","receipt":"price-band-receipt.json"}
WINDOW=("start","end")
SLOT=("previous","current")
METRICS=("list_minor","conditional_checkout_minor","observed_order_minor")


def total(values):
    return None if any(x is None for x in values) else sum(values)


def difference(a,b):
    return a-b if a is not None and b is not None else None


def ratio(n,d):
    return None if n is None or not d else {"numerator":n,"denominator":d}


def rounded_fraction(n,d):
    return int((Decimal(n)/Decimal(d)).quantize(1,rounding=ROUND_HALF_UP))


def source(value):
    if value is not None:identify(value,"来源编号")


def spans(payload):
    check(payload,"previous current","两周窗口")
    out={}
    for k in SLOT:
        a=payload[k];check(a,"start end","每周起止")
        lo,hi=(stamp(a[t]) for t in WINDOW)
        if hi-lo!=timedelta(days=7):raise WorkflowError("每段须为连续七天。")
        out[k]=(lo,hi)
    if out["previous"][1]!=out["current"][0]:raise WorkflowError("两周须首尾相接。")
    return out


def normalize_rows(site,rows,periods):
    if not isinstance(rows,list) or not 1<=len(rows)<=150:raise WorkflowError("请提供 1 到 150 条合成报价记录。")
    by_id=defaultdict(list)
    for r in rows:
        check(r,"id site currency week observed_at source_ref shop_id product_id sku_id comparison_key specification units unit audience destination shipping_basis channel price paid commission","报价观察")
        if (r["site"],r["currency"])!=(site,ROUTES[site][1]):
            raise WorkflowError("报价观察的站点或币种不匹配；分开处理。")
        for k in ("id","shop_id","product_id","sku_id","comparison_key","channel"):
            identify(r[k],k)
        for k in ("specification","unit","audience","destination","shipping_basis"):
            if r[k] is not None:identify(r[k],k)
        source(r["source_ref"])
        amount(r["units"],"件数",1,10000)
        if r["week"] not in SLOT:raise WorkflowError("每条证据须归属前周或本周。")
        at=stamp(r["observed_at"])
        lo,hi=periods[r["week"]]
        if not lo<=at<hi:raise WorkflowError("报价时点超出所声明的周窗口。")
        by_id[r["id"]].append(r)
    conflicting={k:v for k,v in by_id.items() if any(w!=v[0] for w in v)}
    candidates=[v[0] for k,v in by_id.items() if k not in conflicting]
    buckets=defaultdict(list)
    for r in candidates:buckets[(*identity(r),r["week"])].append(r)
    extra=[v for v in buckets.values() if len(v)>1]
    unique=[v[0] for v in buckets.values() if len(v)==1]
    return unique,list(conflicting.values())+extra,sum(len(v)-1 for k,v in by_id.items() if k not in conflicting)


def identity(row):
    return tuple(row[k] for k in ("shop_id","product_id","sku_id"))


def quoted(r):
    p=r["price"]
    check(p,"list_minor coupon_coverage coupons stacking_allowed freight_minor freight_discount_minor freight_source_ref","标价券费")
    require_bits(p,("stacking_allowed",))
    for k in ("list_minor","freight_minor","freight_discount_minor"):amount(p[k],k)
    source(p["freight_source_ref"])
    if p["coupon_coverage"] not in ("complete",None):raise WorkflowError("券覆盖仅可明确完整或未知。")
    coupons=p["coupons"]
    if not isinstance(coupons,list) or len(coupons)>8:raise WorkflowError("每次最多登记 8 个候选优惠。")
    blocked=[];pending=[];seen=set();applied=[];evaluations=[]
    at=stamp(r["observed_at"])
    for c in coupons:
        check(c,"id version funding source_ref sku_id audience from_at until_at min_goods_minor discount_minor eligible claimed_applied","优惠")
        for k in ("id","sku_id","audience"):identify(c[k],k)
        amount(c["version"],"券版本",1,10**6,False);source(c["source_ref"])
        require_bits(c,("eligible","claimed_applied"))
        if c["funding"] not in ("seller","platform",None):raise WorkflowError("明确优惠承担方或留空。")
        if c["id"] in seen:raise WorkflowError("同一优惠不能重复计入。")
        seen.add(c["id"])
        for k in ("min_goods_minor","discount_minor"):amount(c[k],k)
        start,end=(stamp(c[k]) if c[k] else None for k in ("from_at","until_at"))
        scope=c["sku_id"]==r["sku_id"] and c["audience"]==r["audience"]
        fail=(not scope or c["eligible"] is False or start is not None and at<start or end is not None and at>=end
              or start is not None and end is not None and end<=start
              or p["list_minor"] is not None and c["min_goods_minor"] is not None and p["list_minor"]<c["min_goods_minor"])
        unknown=(not c["source_ref"] or c["funding"] is None or c["eligible"] is None or
                 p["list_minor"] is None or None in (start,end,c["min_goods_minor"],c["discount_minor"],c["claimed_applied"]))
        if c["claimed_applied"] is True:
            if fail:blocked.append("applied_coupon_ineligible_or_expired:"+c["id"])
            elif unknown:pending.append("applied_coupon_unproven:"+c["id"])
            else:applied.append(c)
        elif c["claimed_applied"] is None:pending.append("coupon_application_unknown:"+c["id"])
        evaluations.append({"coupon":c,"state":"ineligible" if fail else "unverified" if unknown else "known",
                            "included":c in applied})
    if len(applied)>1:
        if p["stacking_allowed"] is False:blocked.append("coupon_stack_denied")
        elif p["stacking_allowed"] is None:pending.append("coupon_stack_unknown")
    if p["list_minor"] is None or not r["source_ref"] or p["coupon_coverage"] is None:pending.append("listing_or_coupon_coverage_unknown")
    goods=(difference(p["list_minor"],sum(x["discount_minor"] for x in applied)) if not blocked and not pending else None)
    if goods is not None and goods<0:blocked.append("coupons_exceed_goods");goods=None
    freight=difference(p["freight_minor"],p["freight_discount_minor"])
    if freight is not None and freight<0:blocked.append("discount_exceeds_freight");freight=None
    if freight is None or not p["freight_source_ref"]:pending.append("freight_or_its_basis_unproven");freight=None
    if any(r[k] is None for k in ("specification","units","unit","audience","destination","shipping_basis")):
        pending.append("comparison_stratum_unknown")
    checkout=goods+freight if goods is not None and freight is not None and not blocked and not pending else None
    return {"state":"BLOCK" if blocked else "PENDING" if pending else "READY_OFFLINE","blocking_reasons":blocked,"pending_reasons":pending,
            "list_minor":p["list_minor"] if r["source_ref"] else None,"conditional_goods_minor":goods,"net_freight_minor":freight,
            "conditional_checkout_minor":checkout,"per_item_checkout":ratio(checkout,r["units"]),
            "coupon_evaluation":evaluations}


def paid_observation(r,window,at):
    p=r["paid"]
    if p is None:return None,None
    check(p,"id paid_at sku_id units kind basis amount_minor source_ref","独立成交观察")
    identify(p["id"],"成交记录");source(p["source_ref"]);identify(p["sku_id"],"成交 SKU")
    amount(p["amount_minor"],"支付金额");amount(p["units"],"成交篮子件数",1,10000)
    if p["basis"] not in ("goods_only","with_freight") or p["kind"] not in ("paid_order","signed_order"):
        raise WorkflowError("成交需明确支付或签收及运费口径。")
    t=stamp(p["paid_at"])
    lo,hi=window
    if p["sku_id"]!=r["sku_id"] or p["units"]!=r["units"] or not lo<=t<hi or t>at:
        return {**p,"state":"UNMATCHED"},None
    value=p["amount_minor"] if p["source_ref"] else None
    return {**p,"state":"OBSERVED" if value is not None else "UNKNOWN"},value


def commission_observation(site,r):
    entries=r["commission"]
    if entries is None:return None
    if site!="PH":raise WorkflowError("菲律宾佣金证据不可填入抖音报价。")
    if not isinstance(entries,list) or not 1<=len(entries)<=2:raise WorkflowError("基础与广告佣金须分别登记。")
    found=set();out=[]
    for p in entries:
        check(p,"kind bps basis_minor funding source_ref","卖家佣金")
        if p["kind"] not in ("base","ad") or p["kind"] in found:raise WorkflowError("固定类型的佣金不可重复。")
        found.add(p["kind"])
        if p["funding"] not in ("seller","platform",None):raise WorkflowError("佣金承担方留空或确定登记。")
        amount(p["bps"],"佣金率",0,10000);amount(p["basis_minor"],"佣金基数");source(p["source_ref"])
        value=(rounded_fraction(p["bps"]*p["basis_minor"],10000)
               if None not in (p["bps"],p["basis_minor"],p["funding"],p["source_ref"]) else None)
        out.append({**p,"estimated_commission_minor":value,"buyer_checkout_deduction_minor":0})
    return out


def group_key(r):
    return tuple(r[k] for k in ("comparison_key","specification","units","unit","audience","destination","shipping_basis","channel"))


def metric_band(index,metric):
    v=[r[metric] for r in index if r[metric] is not None]
    return {"min_minor":min(v),"max_minor":max(v),"n":len(v)} if v else {"min_minor":None,"max_minor":None,"n":0}


def paired(left,right,periods,tolerance):
    a={tuple(r["identity"]):r for r in left};b={tuple(r["identity"]):r for r in right}
    if not a or a.keys()!=b.keys():return None
    for id in a:
        r1,r2=a[id],b[id]
        old=stamp(r1["observed_at"])-periods["previous"][0]
        new=stamp(r2["observed_at"])-periods["current"][0]
        if abs((old-new).total_seconds())>tolerance:return None
    return a,b


def bands(rows,periods,tolerance):
    strata=defaultdict(lambda:{x:[] for x in SLOT})
    for r in rows:
        # Unknown labels never enter a price band, even if a listed number exists.
        key=tuple(r["comparison_fields"])
        if None in key:continue
        strata[key][r["week"]].append(r)
    result=[]
    for k,g in sorted(strata.items()):
        offerings={}
        for metric in METRICS:
            if metric=="observed_order_minor":
                kinds=sorted({(r["paid_evidence"]["kind"],r["paid_evidence"]["basis"]) for week in SLOT for r in g[week]
                              if r["observed_order_minor"] is not None})
            else:kinds=[None]
            for basis in kinds:
                indexes={w:[r for r in g[w] if r[metric] is not None and
                           (basis is None or (r["paid_evidence"]["kind"],r["paid_evidence"]["basis"])==basis)] for w in SLOT}
                ranges={w:metric_band(indexes[w],metric) for w in SLOT}
                pair=paired(indexes["previous"],indexes["current"],periods,tolerance)
                delta=({key+"_delta_minor":ranges["current"][key+"_minor"]-ranges["previous"][key+"_minor"]
                        for key in ("min","max")} if pair else None)
                key=metric if not basis else metric+":"+"/".join(basis)
                offerings[key]={"weeks":ranges,"matched_week_delta":delta,
                                "observation_ids":{w:[r["id"] for r in indexes[w]] for w in SLOT},
                                "confidence":"matched_synthetic_observations" if pair else "week_observations_only"}
        result.append({"comparison_fields":list(k),"metrics":offerings})
    return result


def calculate(site,p):
    check(p,"site currency scope report_id evaluated_at windows max_pair_clock_drift_seconds observations","价格带")
    if (p["site"],p["currency"],p["scope"])!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("只处理本站及原币种的合成证据。")
    identify(p["report_id"],"报告编号")
    amount(p["max_pair_clock_drift_seconds"],"采样时点容差",0,7*86400,False)
    window=spans(p["windows"])
    if stamp(p["evaluated_at"])<window["current"][1]:raise WorkflowError("本周尚未结束。")
    input_rows,conflict,dupes=normalize_rows(site,p["observations"],window)
    rows=[]
    for r in input_rows:
        curr=quoted(r)
        payment,paid=paid_observation(r,window[r["week"]],stamp(r["observed_at"]))
        rows.append({**curr,"id":r["id"],"identity":identity(r),"week":r["week"],"observed_at":r["observed_at"],
                     "source_ref":r["source_ref"],"comparison_fields":group_key(r),
                     "paid_evidence":payment,"observed_order_minor":paid,"affiliate_components":commission_observation(site,r)})
    result=bands(rows,window,p["max_pair_clock_drift_seconds"])
    state=("BLOCK" if conflict or any(r["state"]=="BLOCK" for r in rows) else
           "PENDING" if any(r["state"]=="PENDING" for r in rows) else "READY_OFFLINE")
    return {"state":state,"windows":p["windows"],"observations":rows,"price_bands":result,
            "quarantined_conflicting_observations":conflict,"identical_duplicate_rows_skipped":dupes,
            "trend_concluded":False,"comparison_order":["comparison_key","specification","units","unit","audience","destination","shipping_basis","channel"],
            "actual_platform_prices_verified":False}


def encode(x):
    return (json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()


def receipt(a,raw,report):
    return {**{k:a[k] for k in ("task_id","site","tool_id","scope","input_sha256","report_id")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress):
    if assets or tool!=TOOL or site not in ROUTES:raise WorkflowError("价格带只处理本站合成结构化数据。")
    if progress:progress("核对规格、券和运费，按可比口径计算分周价格带。")
    a={**calculate(site,p),"task_id":folder.name,"tool_id":tool,"site":site,"scope":SYNTHETIC,
       "currency":p["currency"],"report_id":p["report_id"],"execution_authorized":False,
       "input_sha256":sha256(encode(p)).hexdigest(),"model_called":False,"provider_called":False}
    # Tuples are internal grouping keys and become arrays in receipt-bound results.
    a=json.loads(encode(a))
    raw=encode(a)
    report=(f"# {site} · {p['report_id']} · {a['state']}\n\n"
            "两周合成证据。标价、条件到手价、成交与佣金分开。单次变化留作观察。\n\n"+raw.decode()).encode()
    files={k:folder/name for k,name in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(receipt(a,raw,report))}.items():
        with files[k].open("xb") as f:f.write(b)
    return a,files


def verify_saved(task,files):
    a=task["result"]
    if (task["local_files"]!=FILES or a!=json.loads(files["json"]) or
            (a["task_id"],a["site"],a["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=receipt(a,files["json"],files["report"])):
        raise WorkflowError("价格带结果或回执已变。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"competitor-analyst","label":"竞品同规格到手价与周价格带",
             "description":"只对本站、同等件数/规格/配送且同窗口径做分周变化。",
             "input_example":{}}]
