"""Per-market offer sensitivity and whole-session synthetic finance replay."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path

from role_tools import ROUTES, SYNTHETIC
from role_tools_extra import _load
import workflow_live as live

SELECTION = "selection-profit-sensitivity"
LIVE = "live-whole-session-review"
TOOLS = (SELECTION, LIVE)
OUTPUTS = {"json":"finance-result.json","report":"finance-report.md","receipt":"finance-receipt.json"}
SHARED = ("price_minor","floor_minor","goods_minor","creator_bps","platform_bps","outbound_minor","other_minor")
CN = SHARED + ("ordinary_return_bps","refund_only_bps","packing_minor","ads_minor","tax_minor")
PH = SHARED + ("sign_bps","transaction_bps","ads_bps","post_sign_refund_bps","cod_minor","refusal_return_minor","cert_total_minor","cert_signed_units")
AXES = {"CN":("ordinary_return_bps","ads_minor","creator_bps","price_minor"),
        "PH":("sign_bps","creator_bps","price_minor")}
METRICS = ("unique_uv","views","comments","likes","mean_concurrent","peak_concurrent","watch_seconds")
COD_FIELDS = ("cohort_orders","signed","refused","open","warehouse_due","warehouse_on_time",
              "collectible_minor","remitted_minor")
WorkflowError = live.WorkflowError


def fields(value, keys, label):
    if not isinstance(value,dict) or set(value)!=set(keys):
        raise WorkflowError(f"{label}字段不符，未知项请填 null。")


def quantity(value,label):
    return live._nullable_units(value,label)


def ref(value,label):
    return live._text_identifier(value,label)


def together(fn,*args):
    return fn(*args) if all(x is not None for x in args) else None


def add(*items):
    return together(lambda *xs:sum(xs),*items)


def difference(a,b):
    return together(lambda x,y:x-y,a,b)


def percent(amount,bps):
    return together(lambda a,b:Decimal(a)*b/10000,amount,bps)


def decimal_ratio(n,d):
    return together(lambda a,b:Decimal(a)/b if b else None,n,d)


def encoded(value):
    return None if value is None else format(value.quantize(Decimal("0.00000001"),rounding=ROUND_HALF_UP),"f")


def minor(value):
    return None if value is None else int(Decimal(value).quantize(Decimal("1"),rounding=ROUND_HALF_UP))


def ceiling(value):
    return None if value is None else int(Decimal(value).to_integral_value(rounding=ROUND_CEILING))


def price_at(fixed,multiplier):
    return ceiling(together(lambda f,m:f/m if m>0 else None,fixed,multiplier))


def threshold_share(value):
    return encoded(together(lambda x:x*10000,value))


def model_cn(a):
    p=a["price_minor"]
    ordinary=percent(p,a["ordinary_return_bps"])
    refund_only=percent(p,a["refund_only_bps"])
    platform=percent(p,a["platform_bps"])
    creator=percent(p,a["creator_bps"])
    fixed=add(*(a[k] for k in ("goods_minor","packing_minor","outbound_minor","ads_minor","tax_minor","other_minor")))
    settled=difference(p,add(ordinary,refund_only,platform,creator))
    net=difference(settled,fixed)
    slope=percent(Decimal(1),difference(10000,add(*(a[k] for k in
        ("ordinary_return_bps","refund_only_bps","platform_bps","creator_bps")))))
    price_be=price_at(fixed,slope)
    remaining_return=percent(Decimal(1),difference(10000,add(a["refund_only_bps"],a["platform_bps"],a["creator_bps"])))
    remaining_creator=percent(Decimal(1),difference(10000,add(a["ordinary_return_bps"],a["refund_only_bps"],a["platform_bps"])))
    ad_budget=difference(settled,add(*(a[k] for k in ("goods_minor","packing_minor","outbound_minor","tax_minor","other_minor"))))
    return {"checkout_gross_minor":minor(p),"ordinary_return_minor":minor(ordinary),"refund_only_minor":minor(refund_only),
            "platform_fee_minor":minor(platform),"creator_commission_minor":minor(creator),
            "settlement_minor":minor(settled),"net_profit_minor":minor(net),
            "cost_breakdown":{k:a[k] for k in ("goods_minor","packing_minor","outbound_minor","ads_minor","tax_minor","other_minor")},
            "net_margin_on_checkout":encoded(decimal_ratio(net,p)),
            "break_even":{"minimum_price_minor":price_be,
                          "max_ordinary_return_bps":threshold_share(difference(remaining_return,decimal_ratio(fixed,p))),
                          "max_creator_bps":threshold_share(difference(remaining_creator,decimal_ratio(fixed,p))),
                          "max_ad_spend_minor":minor(ad_budget)}}


def model_ph(a):
    p=a["price_minor"]
    rate=a["sign_bps"]
    fees={k.removesuffix("_bps")+"_minor":percent(p,a[k]) for k in
          ("platform_bps","transaction_bps","creator_bps","ads_bps")}
    post_sign_refunds=percent(p,a["post_sign_refund_bps"])
    signed_settlement=difference(p,add(*fees.values(),post_sign_refunds))
    cert=decimal_ratio(a["cert_total_minor"],a["cert_signed_units"])
    signed_cost=add(a["goods_minor"],a["outbound_minor"],a["cod_minor"],cert,a["other_minor"])
    refusal_cost=add(a["outbound_minor"],a["refusal_return_minor"])
    signed_profit=difference(signed_settlement,signed_cost)
    weighted_cost=add(percent(signed_cost,rate),percent(refusal_cost,difference(10000,rate)))
    expected=difference(percent(signed_settlement,rate),weighted_cost)
    signed_net_share=percent(Decimal(1),difference(10000,add(*(a[k] for k in
        ("platform_bps","transaction_bps","creator_bps","ads_bps","post_sign_refund_bps")))))
    creator_remaining=percent(Decimal(1),difference(10000,add(a["platform_bps"],a["transaction_bps"],a["ads_bps"],a["post_sign_refund_bps"])))
    minimum_sign=decimal_ratio(refusal_cost,add(signed_profit,refusal_cost))
    return {"checkout_gross_minor":minor(p),"expected_signed_gross_minor":minor(percent(p,rate)),
            "signed_settlement_minor":minor(signed_settlement),"settlement_minor":minor(percent(signed_settlement,rate)),
            "signed_profit_minor":minor(signed_profit),"refusal_loss_minor":minor(refusal_cost),
            "net_profit_minor":minor(expected),"signed_post_sign_refund_minor":minor(post_sign_refunds),
            "expected_post_sign_refund_minor":minor(percent(post_sign_refunds,rate)),
            "conditional_signed_fees":{k:minor(v) for k,v in fees.items()},
            "signed_cost_breakdown":{**{k:a[k] for k in ("goods_minor","outbound_minor","cod_minor","other_minor")},
                                      "certification_per_signed_minor":minor(cert)},
            "refusal_cost_breakdown":{k:a[k] for k in ("outbound_minor","refusal_return_minor")},
            "break_even":{"minimum_price_minor":price_at(weighted_cost,percent(signed_net_share,rate)),
                          "minimum_sign_bps":threshold_share(minimum_sign),
                          "max_refusal_bps":threshold_share(difference(Decimal(1),minimum_sign)),
                          "max_creator_bps":threshold_share(difference(creator_remaining,decimal_ratio(weighted_cost,percent(p,rate))))}}


def validate_rates(site,a):
    for k,v in a.items():
        quantity(v,k)
        if k.endswith("_bps") and v is not None and v>10000:
            raise WorkflowError("合成费率须用 0–10000 基点。")
    if site=="CN":
        rv=add(a["ordinary_return_bps"],a["refund_only_bps"])
        if rv is not None and rv>10000:raise WorkflowError("普通退货与仅退款为互斥分支，合计超过 100%。")
    else:
        if a["cert_signed_units"]==0:raise WorkflowError("认证摊销的签收数量必须大于零。")
    fee_keys=("platform_bps","creator_bps") if site=="CN" else ("platform_bps","transaction_bps","creator_bps","ads_bps")
    if sum(a[k] for k in fee_keys if a[k] is not None)>10000:
        raise WorkflowError("同一结算基础上的费率合计超过 100%。")


def selection(site,payload):
    fields(payload,("site","scope","currency","sku_id","source_ref","calculation_basis","facts","scenarios"),"利润")
    ref(payload["sku_id"],"SKU")
    ref(payload["source_ref"],"合成合同引用")
    basis="fees_on_order_gross_not_refunded" if site=="CN" else "fees_on_signed_gross_before_post_sign_refund"
    if payload["calculation_basis"]!=basis:
        raise WorkflowError("合成费用基数不明确，须从本站显式合同提交。")
    a=payload["facts"]
    fields(a,CN if site=="CN" else PH,"成本和费率")
    validate_rates(site,a)
    plot=payload["scenarios"]
    if not isinstance(plot,dict) or not set(AXES[site])<=plot.keys() or plot.keys()-a.keys() or len(plot)>10:
        raise WorkflowError("请提交本站必须的单项敏感性变化和对应字段。")
    calculate=model_cn if site=="CN" else model_ph
    base=calculate(a)
    rows=[]
    for k,v in plot.items():
        d={**a,k:v}
        validate_rates(site,d)
        changed=calculate(d)
        rows.append({"factor":k,"from":a[k],"to":v,"result":changed,
                     "net_profit_delta_minor":difference(changed["net_profit_minor"],base["net_profit_minor"])})
    floor=together(lambda p,f:p>=f,a["price_minor"],a["floor_minor"])
    missing=[k for k in a if a[k] is None]
    state="REJECT" if floor is False or (base["net_profit_minor"] is not None and base["net_profit_minor"]<0)         else "PENDING" if missing or any(row["result"]["net_profit_minor"] is None for row in rows) else "SIMULATED_REVIEWED"
    return {"state":state,"sku_id":payload["sku_id"],"source_ref":payload["source_ref"],
            "formula_basis":basis,"original_facts":a,"missing_fields":missing,"floor_met":floor,
            "baseline":base,"scenarios":rows,
            "rounding_policy":"carry full precision, round display half up; ceil minimum price in minor units"}


def sum_or_none(rows,name):
    return add(*(row[name] for row in rows))


def financial(leaves,engine):
    valid=[row["mature"]["components"] for row in leaves if row["mature"] is not None]
    return {"mature_leaf_count":len(valid),"expected_leaf_count":len(leaves),
            "complete_mature_rollup":engine.ledger({k:sum(r[k] for r in valid) for k in live.MONEY})
               if len(valid)==len(leaves) and leaves else None}


def audience(data,expected):
    fields(data,("source_ref","session_unique_uv","by_window"),"观众指标")
    ref(data["source_ref"],"场次独立 UV 来源")
    quantity(data["session_unique_uv"],"去重 UV")
    rows=data["by_window"]
    if not isinstance(rows,list) or len(rows)!=len(expected):
        raise WorkflowError("观众指标须逐窗给出，未知保留 null。")
    for row,window in zip(rows,expected,strict=True):
        fields(row,("start","end",*METRICS),"时段指标")
        if (row["start"],row["end"])!=(window["start"],window["end"]):
            raise WorkflowError("观众指标时窗与场次不符。")
        for k in METRICS:quantity(row[k],k)
    counts={k:sum_or_none(rows,k) for k in ("views","comments","likes","watch_seconds")}
    durations=[Decimal(str((live._time(r["end"],"结束")-live._time(r["start"],"开始")).total_seconds())) for r in rows]
    weighted=[together(lambda a,b:a*b,r["mean_concurrent"],d) for r,d in zip(rows,durations,strict=True)]
    return {**data,"aggregate":{**counts,"interaction_per_view":encoded(decimal_ratio(add(counts["comments"],counts["likes"]),counts["views"])),
               "average_stay_seconds":encoded(decimal_ratio(counts["watch_seconds"],counts["views"])),
               "weighted_average_concurrent":encoded(decimal_ratio(add(*weighted),sum(durations))),
               "peak_concurrent":together(max,*(r["peak_concurrent"] for r in rows))},
            "uv_basis":"independently supplied de-duplicated session count; not a window sum"}


def labor(data):
    fields(data,("basis","rows"),"人力")
    rows=data["rows"]
    if data["basis"] not in ("separate","included_in_sku_cost",None) or not isinstance(rows,list) or len(rows)>20:
        raise WorkflowError("人力费用须显式声明是否已含在 SKU 成本内。")
    seen=set()
    for row in rows:
        fields(row,("role","source_ref","amount_minor"),"人工费用")
        ref(row["role"],"人力角色")
        ref(row["source_ref"],"费用来源")
        quantity(row["amount_minor"],"人力金额")
        if row["role"] in seen:raise WorkflowError("人力角色费用重复。")
        seen.add(row["role"])
    subtotal=sum_or_none(rows,"amount_minor") if rows else None
    deduction=0 if data["basis"]=="included_in_sku_cost" else subtotal if data["basis"]=="separate" else None
    return {**data,"documented_minor":subtotal,"extra_deduction_minor":deduction}


def cod(data):
    fields(data,("source_ref",*COD_FIELDS),"PH 履约及回款")
    ref(data["source_ref"],"独立履约来源")
    for k in COD_FIELDS:quantity(data[k],k)
    orders,signed,refused,opened=(data[k] for k in ("cohort_orders","signed","refused","open"))
    total=add(signed,refused,opened)
    unremitted=difference(data["collectible_minor"],data["remitted_minor"])
    if (orders is not None and total is not None and total!=orders
            or data["warehouse_due"] is not None and data["warehouse_on_time"] is not None
            and data["warehouse_on_time"]>data["warehouse_due"]
            or unremitted is not None and unremitted<0):
        raise WorkflowError("COD、仓配或回款独立来源内部矛盾。")
    settled=opened==0 and total is not None and orders==total
    return {**data,"observed_signed_fraction":encoded(decimal_ratio(signed,orders)),
            "observed_refused_fraction":encoded(decimal_ratio(refused,orders)),
            "final_signed_fraction":encoded(decimal_ratio(signed,orders)) if settled else None,
            "final_refused_fraction":encoded(decimal_ratio(refused,orders)) if settled else None,
            "warehouse_timely_fraction":encoded(decimal_ratio(data["warehouse_on_time"],data["warehouse_due"])),
            "unremitted_minor":unremitted,"included_again_in_financial_totals":False}


def live_session(project,site,payload):
    required=("site","scope","currency","session_id","starts_at","ends_at","evaluation_time",
              "expected_windows","sku_records","audience","labor")
    fields(payload,required+(("cod",) if site=="PH" else ()),"整场复盘")
    session=ref(payload["session_id"],"场次")
    start,end,evaluated=(live._time(payload[k],k) for k in ("starts_at","ends_at","evaluation_time"))
    if not start<end<=evaluated:raise WorkflowError("场次起止及复盘时点顺序不符。")
    windows=payload["expected_windows"]
    if not isinstance(windows,list) or not 1<=len(windows)<=12:raise WorkflowError("请提交 1–12 个连续时段。")
    last=start
    for w in windows:
        fields(w,("start","end"),"时段")
        a,b=(live._time(w[k],k) for k in ("start","end"))
        if a!=last or not a<b<=end:raise WorkflowError("时段须连续、无重叠并覆盖整场。")
        last=b
    if last!=end:raise WorkflowError("时段未覆盖整场。")
    sku_records=payload["sku_records"]
    if not isinstance(sku_records,list) or not 1<=len(sku_records)<=8:raise WorkflowError("请提交最多 8 个明确 SKU。")
    ids,events,groups=set(),{},[]
    finance=_load(project,"live_window_review_v6")
    for item in sku_records:
        fields(item,("sku_id","records"),"SKU 记录")
        sku=ref(item["sku_id"],"SKU")
        if sku in ids:raise WorkflowError("同场 SKU 分组重复。")
        ids.add(sku)
        request={k:payload[k] for k in ("site","scope","currency","session_id","evaluation_time","expected_windows")}
        request.update(sku_id=sku,records=item["records"])
        data=live._input(site,request)
        for row in data["records"]:
            if events.setdefault(row["event_id"],sku)!=sku:raise WorkflowError("同场观测编号跨 SKU 重复。")
        checked,sources=live._review(project,site,data)
        groups.append((sku,checked,sources))
    by_sku=[]; by_windows=defaultdict(list)
    for sku,entry,_ in groups:
        leaves=entry["windows"]
        assert len(leaves)==len(windows)
        by_sku.append({"sku_id":sku,"windows":leaves,"data_errors":entry["data_errors"],**financial(leaves,finance)})
        for i,x in enumerate(leaves):by_windows[i].append(x)
    aggregate=financial([x for rows in by_windows.values() for x in rows],finance)
    work=labor(payload["labor"])
    people=audience(payload["audience"],windows)
    if any(x["data_errors"] for x in by_sku):
        aggregate["complete_mature_rollup"]=None
    whole=aggregate["complete_mature_rollup"]
    final_profit=difference(whole["post_ad_profit_minor"] if whole else None,work["extra_deduction_minor"])
    in_time=evaluated-end<=timedelta(hours=24)
    carry=cod(payload["cod"]) if site=="PH" else None
    missing_money=whole is None or any(x["data_errors"] for x in by_sku) or final_profit is None
    missing_context=people["session_unique_uv"] is None or any(v is None for v in people["aggregate"].values())
    if carry:missing_context=missing_context or any(carry[k] is None for k in COD_FIELDS)
    state="PENDING" if not in_time or missing_money or missing_context else "SIMULATED_REVIEWED"
    return {"session_id":session,"starts_at":payload["starts_at"],"ends_at":payload["ends_at"],
            "evaluation_time":payload["evaluation_time"],"post_session_24h_met":in_time,"state":state,
            "by_sku":by_sku,"by_window":[{**w,**financial(by_windows[i],finance)} for i,w in enumerate(windows)],
            "session_finance":aggregate,"audience":people,"labor":work,"ph_cod":carry,
            "session_profit_after_shared_labor_minor":final_profit,
            "after_labor_profit_to_ad_spend":encoded(decimal_ratio(final_profit,whole["components"]["ad_spend_minor"] if whole else None)),
            "money_source_hashes":groups[0][2],"ad_incremental_causation_verified":False,
            "aggregation_basis":"accepted disjoint SKU/window leaves only; SKU/window rollups and COD balances are alternative views"}


def digest(data):
    return sha256(data).hexdigest()


def receipt_for(result,raw,report):
    return {"task_id":result["task_id"],"site":result["site"],"tool_id":result["tool_id"],
            "result_sha256":digest(raw),"report_sha256":digest(report),"synthetic_offline":True,
            "business_execution_authorized":False,"input_sha256":result["input_sha256"]}


def run_workflow(project,site,tool,payload,task_dir,assets,progress):
    if site not in ROUTES or tool not in TOOLS or assets or not isinstance(payload,dict) or (
            payload.get("site"),payload.get("currency"),payload.get("scope"))!=(site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("仅接收同站点、原币种的结构化合成离线数据。")
    progress("正在按同站成本和结算口径独立复算。")
    readings=selection(site,payload) if tool==SELECTION else live_session(project,site,payload)
    def encode(x):return (json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+"\n").encode()
    result={**readings,"task_id":task_dir.name,"site":site,"tool_id":tool,"currency":payload["currency"],
            "scope":SYNTHETIC,"input_sha256":digest(encode(payload)),"actual_fees_or_source_verified":False,
            "business_execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(result)
    report=(f"# {site} · 合成离线财务核验\n\n状态 {result['state']} · 标的 {result.get('sku_id') or result['session_id']}\n\n"
            "金额为原币最小单位。实际交易、费用来源和广告增量未核验。\n").encode()
    files={k:Path(task_dir)/v for k,v in OUTPUTS.items()}
    for k,data in {"json":raw,"report":report,"receipt":encode(receipt_for(result,raw,report))}.items():
        with files[k].open("xb") as stream:stream.write(data)
    return result,files


def verify_artifacts(task,files):
    result=task["result"]
    if (task["local_files"]!=OUTPUTS or result!=json.loads(files["json"]) or result["task_id"]!=task["id"]
            or result["site"]!=task["site"] or result["tool_id"]!=task["source"]["tool_id"]
            or json.loads(files["receipt"])!=receipt_for(result,files["json"],files["report"])):
        raise WorkflowError("财务取件与本机保存的计算不符。")


def public_tools():
    return [
        {"id":SELECTION,"sites":["CN","PH"],"role_suffix":"selection","label":"分站利润与保本敏感性",
         "description":"仅合成费率、分开结算与成本，按本站合同复算保本和单项变化。","input_example":{}},
        {"id":LIVE,"sites":["CN","PH"],"role_suffix":"live-ops","label":"同场多 SKU 整场离线复盘",
         "description":"仅合成场次。用逐窗成熟金额、独立人力与观众、菲律宾 COD 证据核对。","input_example":{}},
    ]
