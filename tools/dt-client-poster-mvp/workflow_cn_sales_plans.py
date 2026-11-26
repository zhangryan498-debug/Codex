"""Same-revision local talkbooks and the Douyin search matrix."""

from datetime import datetime, timedelta
from hashlib import sha256
import json
import unicodedata

from role_tools import SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_offline_planning import ph_draft_word_count
from workflow_live_program import INTAKE, SAVE, PREFLIGHT
from workflow_live_rehearsal import amount, check, identify, stamp, task_id


TALK = "cn-live-seven-stage-talkbook"
PH_TALK = "ph-live-seven-stage-talkbook"
SEARCH = "cn-shop-search-title-matrix"
PH_TALK_INT = "ph-live-stage-talkbook-intake"
TOOLS = (TALK, PH_TALK, SEARCH, PH_TALK_INT)
FILES = {"json":"cn-plan.json","report":"cn-plan.md","receipt":"cn-plan-receipt.json"}
SEGMENTS = ("pain","selling","demo","trust","offer","objections","close")
PH_COD = ("payment", "refusal", "returns", "specification", "shipping")
TALK_FIELDS = "pain selling demo trust anchor coupon returns refund_only shipping quality urgency interaction ad_asset control"
KEYWORDS = ("category","scene","need","selling","marketing","audience","competitor")
PINNED_COUPON_CONTEXT = "合成券价审回执范围"
STRATEGIES = (("category","selling"),("scene","need"),("marketing","audience"),("fixture_trend","category"),("competitor","need"))


class WorkflowError(ValueError):
    pass


def _fact(f, row, at, site='CN'):
    check(f,"site sku_id revision source_ref observed_at valid_until value","逐项直接证据")
    if (f["site"],f["sku_id"],f["revision"])!=(site,row["sku_id"],row["revision"]):
        raise WorkflowError("证据错站、商品或修订。")
    term(f["source_ref"],"证据来源",True)
    if not stamp(f["observed_at"])<=at<stamp(f["valid_until"]) and (
            stamp(f["observed_at"])>at or stamp(f["valid_until"])<=stamp(f["observed_at"])):
        raise WorkflowError("证据采集时点或有效区间不符。")


def _valid(f, now):
    return bool(f and f["source_ref"] and stamp(f["observed_at"])<=now<stamp(f["valid_until"]) and f["value"] is not None)


def _schedule(p,at):
    for key in ("plan_task_id","intake_task_id"):
        task_id(p[key],key)
    amount(p["plan_version"],"货盘版本",1,10**6,False)
    rows=p["scripts"]
    if not isinstance(rows,list) or not 1<=len(rows)<=40:
        raise WorkflowError("需要逐 SKU 提供七环节话本素材和时长。")
    seen=set()
    for row in rows:
        check(row,"sku_id revision owner facts segment_seconds","话本对象")
        identify(row["sku_id"],"SKU")
        if row["sku_id"] in seen:
            raise WorkflowError("重复商品话本。")
        seen.add(row["sku_id"])
        amount(row["revision"],"商品修订",1,10**6,False)
        term(row["owner"],"口播核对责任人")
        check(row["segment_seconds"]," ".join(SEGMENTS),"七段时长")
        for k,v in row["segment_seconds"].items():
            amount(v,k,1,600,False)
        check(row["facts"],TALK_FIELDS,"可核素材")
        for key,fact in row["facts"].items():
            if fact is None:
                continue
            _fact(fact,row,at)
            val=fact["value"]
            if val is None:
                continue
            if key=="anchor":
                amount(val,"明确的合成锚点最小货币单位",0,10**12,False)
            elif key=="coupon":
                check(val,"mechanism eligible buyer_paid_minor price_version","合成优惠")
                term(val["mechanism"],"已给优惠条款")
                if type(val["eligible"]) is not bool:
                    raise WorkflowError("优惠必须有明确资格。")
                amount(val["buyer_paid_minor"],"优惠后实付最小货币单位",0,10**12,False)
                amount(val["price_version"],"价审版本",1,10**6)
                if val["mechanism"]=="none" and (val["eligible"] or val["price_version"] is not None):
                    raise WorkflowError("明确无券的合成占位不登记券价版本或资格。")
            else:
                term(val,key)


def _ph_schedule(p, at):
    for key in ("plan_task_id", "intake_task_id"):
        task_id(p[key], key)
    amount(p["plan_version"], "货盘版本", 1, 10**6, False)
    rows = p["scripts"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 40:
        raise WorkflowError("需要逐 SKU 七环节话本和五项条件化 COD 证据。")
    seen = set()
    for row in rows:
        check(row, "sku_id revision owner locale segment_seconds copy cod", "菲律宾话本对象")
        identify(row["sku_id"], "SKU")
        if row["sku_id"] in seen:
            raise WorkflowError("重复商品话本。")
        seen.add(row["sku_id"])
        amount(row["revision"], "商品修订", 1, 10**6, False)
        term(row["owner"], "口播核对责任人")
        if row["locale"] not in ("en-PH", "fil-PH"):
            raise WorkflowError("菲律宾话本语言须与本站一致。")
        check(row["segment_seconds"], " ".join(SEGMENTS), "七段时长")
        check(row["copy"], " ".join(SEGMENTS), "七段口播草案")
        for key, seconds in row["segment_seconds"].items():
            amount(seconds, key, 1, 600, False)
            if row["copy"][key] is not None and ph_draft_word_count(row["copy"][key], key, WorkflowError) > 3 * seconds:
                raise WorkflowError("有口播超出该环节的本机纸面参考负载。")
        check(row["cod"], " ".join(PH_COD), "五项条件化 COD 证据")
        for key, fact in row["cod"].items():
            if fact is None:
                continue
            _fact(fact, row, at, "PH")
            v = fact["value"]
            if v is None:
                continue
            if key == "payment":
                check(v, "checkout_options cod_available", "支付选项")
                options = v["checkout_options"]
                if (not isinstance(options, list) or not options or
                        any(x not in ("cod", "prepaid") for x in options) or
                        len(set(options)) != len(options) or not isinstance(v["cod_available"], bool) or
                        v["cod_available"] != ("cod" in options)):
                    raise WorkflowError("结账方式与是否可 COD 的合成输入冲突。")
            elif key == "refusal":
                if v != "customer_review_before_ordering":
                    raise WorkflowError("本机话本仅核对订购前确认提示，不能声称拒收限制或处罚。")
            elif key == "returns":
                check(v, "channel guidance", "售后说明")
                if v["channel"] != "in_app_order_support" or v["guidance"] != "confirm_order_terms":
                    raise WorkflowError("只记录合成站内查看入口，不能编造外部客服或退货承诺。")
            elif key == "specification":
                check(v, "specification reference_cue", "可见规格")
                term(v["specification"], "同 SKU 规格")
                term(v["reference_cue"], "尺寸或件数展示线索")
            else:
                check(v, "min_days max_days basis", "逐窗物流参考")
                for name in ("min_days", "max_days"):
                    amount(v[name], name, 0, 365, False)
                term(v["basis"], "站内结账或合成仓窗依据")
                if v["min_days"] > v["max_days"]:
                    raise WorkflowError("时效窗口起止冲突。")


def _ph_talk(p, ctx):
    plan, intake, pre = ctx
    items = {r["sku_id"]:r for r in plan["products"]}
    out = {}
    at = stamp(p["evaluated_at"])
    for row in p["scripts"]:
        sku = row["sku_id"]
        subject = items[sku]
        alloc = subject["allocations"]
        first = [x for x in alloc if x["stage"] == "first_pass"]
        start = min((stamp(x["start"]) for x in alloc), default=None)
        end = max((stamp(x["end"]) - timedelta(microseconds=1) for x in alloc), default=None)
        blocked = []
        pending = []
        if not first or at > start:
            blocked.append("script_after_slot_or_missing_first_slot")
        elif sum(row["segment_seconds"].values()) > min((stamp(x["end"])-stamp(x["start"])).total_seconds() for x in first):
            blocked.append("seven_stages_exceed_allocated_seconds")
        if intake["state"] == "BLOCK" or subject["state"] == "BLOCK":
            blocked.append("upstream_assortment_blocked")
        elif not intake["received_for_rehearsal"] or subject["state"] != "READY_OFFLINE":
            pending.append("upstream_assortment_pending")
        local_evidence = {}
        for key, f in row["cod"].items():
            valid_window = start is not None and all(_valid(f,t) for t in (at,start,end))
            val = f["value"] if valid_window else None
            if key == "specification" and val and val["specification"] != subject["specification"]:
                val = None
                blocked.append("specification_conflicts_with_pinned_plan")
            local_evidence[key] = {"value":val, "source":f, "state":"SOURCED_FIXTURE" if val is not None else "PENDING"}
            if val is None:
                pending.append("cod_"+key)
        pending.extend("copy_"+key for key, val in row["copy"].items() if val is None)
        current = None if blocked or "upstream_assortment_pending" in pending else subject["reference_price_minor"]
        if current is None and not blocked:
            pending.append("fixture_offer_price")
        stages = []
        for key in SEGMENTS:
            stage = {"id":key, "seconds":row["segment_seconds"][key], "draft_spoken":row["copy"][key]}
            if key == "offer":
                stage.update(synthetic_price_minor=current, currency="PHP", approved_for_live=False)
            if key == "objections":
                stage["cod_evidence_keys"] = list(PH_COD)
            stages.append(stage)
        out[sku] = {"revision":row["revision"], "owner":row["owner"], "locale":row["locale"],
                    "allocations":[x["id"] for x in alloc], "stages":stages, "cod_objections":local_evidence,
                    "handoff":{"role":"tiktok-live-ops", "review_items":["local_language","checkout","return_route","delivery_window"],
                               "can_use_as_live_copy":False,"native_review_verified":False,"receipt_verified":False},
                    "draft_can_enter_internal_review":not blocked and not pending,
                    "blocked":sorted(set(blocked)), "pending":sorted(set(pending))}
    state = "BLOCK" if any(r["blocked"] for r in out.values()) else "PENDING_EVIDENCE"
    review_ready = all(r["draft_can_enter_internal_review"] for r in out.values())
    return {"state":state,"draft_stage":"READY_FOR_LOCAL_REVIEW" if review_ready else state,
            "plan_task_id":plan["task_id"],"intake_task_id":intake["task_id"],"plan_version":plan["version"],
            "session_id":plan["identity"]["session_id"], "scripts":out, "native_review_required":True,
            "platform_copy_approved":False, "real_price_or_platform_terms_verified":False,
            "real_account_or_checkout_verified":False, "actual_copy_or_spend_launched":False}


def _search(p,at):
    task_id(p["listing_task_id"],"商品事实包")
    identify(p["sku_id"],"SKU")
    amount(p["revision"],"商品修订",1,10**6,False)
    term(p["owner"],"搜索配置负责人")
    if not isinstance(p["keywords"],list) or not 1<=len(p["keywords"])<=80:
        raise WorkflowError("词库须提供有类型的简短关键词。")
    unique=set()
    for word in p["keywords"]:
        check(word,"id kind term fit","关键词")
        identify(word["id"],"关键词编号")
        term(word["term"],"关键词")
        if len(word["term"])>24 or word["id"] in unique or word["kind"] not in (*KEYWORDS,"fixture_trend"):
            raise WorkflowError("关键词编号重复、超长或类型不在测试词库。")
        unique.add(word["id"])
        if word["fit"] is not None:
            _fact(word["fit"],p,at)
            if word["fit"]["value"] not in ("synthetic_fit","synthetic_trend_marker",None):
                raise WorkflowError("词库只接受合成语义适配或合成趋势标记。")
    if p["core"] not in unique or not isinstance(p["title_plans"],list) or len(p["title_plans"])!=5:
        raise WorkflowError("须填已登记核心词与五个标题方案。")
    for row in p["title_plans"]:
        if not isinstance(row,list) or not 2<=len(row)<=6 or len(set(row))!=len(row) or not set(row)<=unique:
            raise WorkflowError("标题按已登记且不重复的词编号排列。")
    v=p["video_search"]
    check(v,"terms positions custom_term_plan review_interval_days","短视频定坑方案")
    if (not isinstance(v["terms"],list) or len(v["terms"])!=3 or len(set(v["terms"]))!=3
            or not set(v["terms"])<=unique or v["positions"]!=[1,2,3,4,5]):
        raise WorkflowError("须填 3 个不同的登记词及计划检查的 1–5 号位置。")
    terms={r["id"]:r["term"] for r in p["keywords"]}
    normalized={"".join(unicodedata.normalize("NFKC",terms[k]).casefold().split()) for k in v["terms"]}
    if len(normalized)!=3:
        raise WorkflowError("短视频定坑须使用三个不同的词面。")
    term(v["custom_term_plan"],"自定义词维护计划")
    amount(v["review_interval_days"],"复核间隔",1,30,False)
    ab=p["experiment"]
    check(ab,"baseline_variant challenger_variant single_variable start end audience budget_minor_per_arm","本机 A/B 设计")
    for key in ("baseline_variant","challenger_variant"):
        amount(ab[key],key,1,5,False)
    if ab["baseline_variant"]==ab["challenger_variant"] or ab["single_variable"]!="title" or not at<=stamp(ab["start"])<stamp(ab["end"]):
        raise WorkflowError("A/B 仅改变标题，两组用同一时间窗。")
    term(ab["audience"],"共同样本范围")
    amount(ab["budget_minor_per_arm"],"计划的同额预算",0,10**12,False)


def bind(site,tool,p,load,listing,history):
    tail="plan_task_id intake_task_id plan_version scripts" if tool in (TALK, PH_TALK) else "listing_task_id sku_id revision owner keywords core title_plans video_search experiment"
    check(p,"site currency scope job_id evaluated_at "+tail,"站点合成方案")
    expected_site, currency = ("PH", "PHP") if tool == PH_TALK else ("CN", "CNY")
    if (site,p["site"],p["currency"],p["scope"])!=(expected_site,expected_site,currency,SYNTHETIC):
        raise WorkflowError("仅接受指定站、原币及明确标记的合成证据。")
    identify(p["job_id"],"任务号")
    at=stamp(p["evaluated_at"])
    if tool==SEARCH:
        _search(p,at)
        source,pin=listing(p["listing_task_id"],site)
        if not source["valid"] or (source["sku_id"],source["facts_version"])!=(p["sku_id"],p["revision"]):
            raise WorkflowError("商城事实包的 SKU 或修订不匹配。")
        start,end=(datetime.fromisoformat(pin[k].replace("Z","+00:00")) for k in ("as_of","expires_at"))
        if not start<=at<end:
            raise WorkflowError("商品事实包在矩阵时点尚未生效或已经到期。")
        return {"listing":pin},None
    (_ph_schedule if tool == PH_TALK else _schedule)(p,at)
    plan,rp=load(p["plan_task_id"],site,SAVE)
    intake,ip=load(p["intake_task_id"],site,INTAKE)
    newest=max((r["version"] for r in history(site,SAVE,plan["identity"]["session_id"])),default=0)
    if ((plan["version"],intake["version"],newest)!=(p["plan_version"],)*3
            or intake["plan_task_id"]!=plan["task_id"] or intake["upstream"]["plan"]!=rp
            or stamp(intake["evaluated_at"])>at):
        raise WorkflowError("只消费同场当前货盘版本及其实际收件。")
    if {r["sku_id"]:r["revision"] for r in p["scripts"]}!={r["sku_id"]:r["revision"] for r in plan["products"]}:
        raise WorkflowError("每个当前货盘 SKU 和修订都必须有一份七环节记录。")
    pins={"plan":rp,"intake":ip}
    pre={}
    for prod in plan["products"]:
        if prod["readiness_task_id"]:
            name="preflight:"+prod["sku_id"]
            pre[prod["sku_id"]],pins[name]=load(prod["readiness_task_id"],site,PREFLIGHT)
            if pins[name]!=plan["upstream"][name]:
                raise WorkflowError("话本不可使用与原货盘不一致的价审来源。")
    return pins,(plan,intake,pre)


def _talk(p,ctx):
    plan,intake,pre=ctx
    items={r["sku_id"]:r for r in plan["products"]}
    out={}
    for row in p["scripts"]:
        sku=row["sku_id"]
        subject=items[sku]
        alloc=subject["allocations"]
        first=[x for x in alloc if x["stage"]=="first_pass"]
        window=([min(stamp(x["start"]) for x in alloc),max(stamp(x["end"])-timedelta(microseconds=1) for x in alloc)]
                if alloc else None)
        at=stamp(p["evaluated_at"])
        invalid_window=not first or at>min(stamp(x["start"]) for x in alloc)
        blocked=["script_after_slot_or_missing_first_slot"] if invalid_window else []
        if first and sum(row["segment_seconds"].values())>min((stamp(x["end"])-stamp(x["start"])).total_seconds() for x in first):
            blocked.append("seven_stages_exceed_allocated_seconds")
        facts={}
        for k,f in row["facts"].items():
            supported=window and _valid(f,at) and all(_valid(f,t) for t in window)
            facts[k]={"value":f["value"] if supported else None,"source":f,"state":"SOURCED_FIXTURE" if supported else "PENDING"}
        count=subject["pieces_per_sale_unit"]
        selling=facts["selling"]
        if selling["value"] is not None and (count is None or selling["value"]!=f"本合成样品每包展示 {count} 件的组合。"):
            selling.update(value=None,state="PENDING",reason="pack_claim_not_supported_by_pinned_plan")
        val=lambda name:facts[name]["value"]
        pending=[k for k,r in facts.items() if r["state"]=="PENDING"]
        current=subject["reference_price_minor"]
        if intake["state"]=="BLOCK" or subject["state"]=="BLOCK":
            blocked.append("upstream_assortment_blocked")
        elif not intake["received_for_rehearsal"] or subject["state"]!="READY_OFFLINE":
            pending.append("upstream_assortment_pending")
        if blocked or not intake["received_for_rehearsal"] or subject["state"]!="READY_OFFLINE":
            current=None
        original=val("anchor")
        if current is None or original is not None and original<current:
            if original is not None and current is not None:
                pending.append("anchor_below_current")
            original=None
        coupon=val("coupon")
        if coupon is not None:
            quote=pre.get(sku,{}).get("price_quote")
            if coupon["mechanism"]=="none":
                supported=not (quote and quote.get("coupon_refs")) and coupon["buyer_paid_minor"]==current
            else:
                supported=(coupon["mechanism"]==PINNED_COUPON_CONTEXT and quote and quote["passed"] and coupon["eligible"] and
                           (coupon["buyer_paid_minor"],coupon["price_version"])==(current,quote["price_version"]) and
                           bool(quote["coupon_refs"]) and stamp(quote["valid_until_exclusive"])>window[1])
            if not supported:
                coupon=None
                pending.append("coupon_scope_or_quote_unconfirmed")
            elif quote and coupon["mechanism"]!="none":
                coupon={**coupon,"coupon_refs":quote["coupon_refs"]}
        product_proofs={k:facts[k] for k in ("returns","refund_only","shipping","quality")}
        payloads={
            "pain":{"spoken":val("pain")},"selling":{"spoken":val("selling")},"demo":{"spoken":val("demo")},
            "trust":{"spoken":val("trust")},"offer":{"current_price_minor":current,"reference_anchor_minor":original,"coupon":coupon,
                                                   "plan_currency":"CNY","scope":"synthetic_quote_only"},
            "objections":{"topics":product_proofs},
            "close":{"spoken":"演练口令：请先看本站商品信息，再核对条件。","verified_urgency_fixture":val("urgency")},
        }
        stages=[{"id":k,"seconds":row["segment_seconds"][k],**payloads[k]} for k in SEGMENTS]
        out[sku]={"revision":row["revision"],"owner":row["owner"],"stages":stages,"allocation_ids":[x["id"] for x in alloc],
                  "evidence":facts,"interaction":val("interaction"),"ad_asset_reference":val("ad_asset"),"control_cue":val("control"),
                  "actual_copy_or_spend_launched":False,"blocked":sorted(set(blocked)),"pending":sorted(set(pending))}
    state="BLOCK" if any(r["blocked"] for r in out.values()) else "PENDING_EVIDENCE" if any(r["pending"] for r in out.values()) else "READY_OFFLINE"
    return {"state":state,"plan_task_id":plan["task_id"],"intake_task_id":intake["task_id"],"plan_version":plan["version"],
            "session_id":plan["identity"]["session_id"],"scripts":out,"real_price_or_platform_terms_verified":False}


def _matrix(p):
    at=stamp(p["evaluated_at"])
    words={w["id"]:w for w in p["keywords"]}
    supported={k for k,w in words.items() if _valid(w["fit"],at)
        and w["fit"]["value"]==("synthetic_trend_marker" if w["kind"]=="fixture_trend" else "synthetic_fit")}
    kinds=set(words[k]["kind"] for k in supported)
    missing=sorted(set(KEYWORDS)-kinds)
    core=words[p["core"]]
    omitted=[]
    titles=[]
    for n,(keys,required) in enumerate(zip(p["title_plans"],STRATEGIES),1):
        candidate="".join(words[k]["term"] for k in keys)
        prefix=candidate.find(core["term"])
        placement=prefix>=0 and prefix+len(core["term"])<=15
        if len(candidate)>30 or not placement:
            raise WorkflowError("拟定标题不超过 30 字，完整核心词应在前 15 字。")
        if not set(required)<={words[k]["kind"] for k in keys}:
            raise WorkflowError("标题组合和对应策略不匹配。")
        valid=set(keys)<=supported and p["core"] in keys and p["core"] in supported
        if not valid:
            omitted.append(n)
        titles.append({"variant":n,"title":candidate if valid else None,"proposal":candidate,
                        "core_end":prefix+len(core["term"]),"strategy":list(required),"source_keyword_ids":keys})
    if len({t["proposal"] for t in titles})!=5:
        raise WorkflowError("五个标题不可重复。")
    video=p["video_search"]
    video_ok=set(video["terms"])<=supported and all(words[i]["kind"] not in ("competitor","fixture_trend") for i in video["terms"])
    return {"state":"PENDING_EVIDENCE" if missing or omitted or not video_ok else "READY_OFFLINE","sku_id":p["sku_id"],
            "revision":p["revision"],"owner":p["owner"],"missing_keyword_kinds":missing,"pending_title_variants":omitted,
            "keyword_records":{k:{**w,"local_fit":k in supported,"real_search_volume":None,"real_competition":None} for k,w in words.items()},
            "core_term":core["term"] if p["core"] in supported else None,"titles":titles,
            "video_search_plan":{**video,"terms":video["terms"] if video_ok else None,"positions_already_configured":False},
            "ab_plan":{**p["experiment"],"actual_experiment":False,"ctr":None,"cvr":None},
            "real_search_heat_verified":False,"search_positions_written":False}


def _receipt(result,raw,report):
    return {**{k:result[k] for k in ("site","task_id","job_id","tool_id","upstream","state","input_sha256")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("该入口只使用已核对的合成结构输入。")
    pins,ctx=linked
    if tool == PH_TALK_INT:
        body = ctx
    elif tool == PH_TALK:
        body = _ph_talk(p,ctx)
    elif tool == TALK:
        body = _talk(p,ctx)
    else:
        body = _matrix(p)
    result={**body,"task_id":folder.name,"job_id":p["job_id"],"tool_id":tool,"scope":SYNTHETIC,"site":site,
            "currency":p["currency"],"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
            "model_called":False,"provider_called":False}
    raw=encode(result)
    report=(f"# {p['job_id']} · {result['state']}\n\n合成本机方案。话本、搜索词、标题和投流仅供校对。\n\n"+raw.decode()).encode()
    files={k:folder/v for k,v in FILES.items()}
    for key,data in {"json":raw,"report":report,"receipt":encode(_receipt(result,raw,report))}.items():
        with files[key].open("xb") as f:
            f.write(data)
    return result,files


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("方案、证据或取件回执不一致。")


def public_tools():
    standard = [{"id":tool,"sites":[site],"role_suffix":role,"label":label,"input_example":{},
             "description":"只核对本站合成方案和当前任务版本；未知待核，不实际开播、定坑或投放。","max_media_files":0}
            for tool,site,role,label in ((TALK,"CN","live-ops","抖音七环节逐 SKU 话本"),
                                    (PH_TALK,"PH","live-ops","菲律宾七环节逐 SKU 与 COD 话本"),
                                    (SEARCH,"CN","shop-ops","抖音搜索词、五标题与定坑方案"))]



    return [*standard, {"id": PH_TALK_INT, "sites": ["PH"], "role_suffix": "live-ops",
             "label": "菲律宾场控接收七环节话本（离线校对）",
             "description": "按同场货盘、SKU、修订、排品时段及话本双哈希接收；缺项待核，不能开播。",
             "input_example": {"site":"PH", "currency":"PHP", "scope":SYNTHETIC, "job_id":"",
                               "session_id":"", "plan_version":1, "sku_id":"", "revision":1,
                               "allocation_id":"", "allocation_start":"", "allocation_end":"",
                               "reviewed_at":"", "talkbook_task_id":"", "talkbook_result_sha256":"",
                               "talkbook_receipt_sha256":"", "recipient":"", "desk":"stage_control"},
             "max_media_files": 0}]


def bind_ph_talk_intake(site, p, load, history):
    check(p, "site currency scope job_id session_id plan_version sku_id revision allocation_id "
          "allocation_start allocation_end reviewed_at talkbook_task_id talkbook_result_sha256 "
          "talkbook_receipt_sha256 recipient desk", "菲律宾场控话本接件")
    if (site, p["site"], p["currency"], p["scope"], p["desk"]) != (
            "PH", "PH", "PHP", SYNTHETIC, "stage_control"):
        raise WorkflowError("仅菲律宾场控离线校对接件，须使用 PHP 合成资料。")
    for field in ("job_id", "session_id", "sku_id", "allocation_id", "recipient"):
        identify(p[field], field)
    amount(p["plan_version"], "货盘版本", 1, 10**6, False)
    amount(p["revision"], "SKU 修订", 1, 10**6, False)
    task_id(p["talkbook_task_id"], "话本任务")
    at = stamp(p["reviewed_at"])
    talk, talk_pin = load(p["talkbook_task_id"], site, PH_TALK)
    if (p["talkbook_result_sha256"], p["talkbook_receipt_sha256"]) != (
            talk_pin["result_sha256"], talk_pin["receipt_sha256"]):
        raise WorkflowError("指定话本的结果或回执 SHA-256 不符。")
    if (p["session_id"], p["plan_version"]) != (talk["session_id"], talk["plan_version"]):
        raise WorkflowError("话本场次或货盘版本与收件请求不符。")
    if next((s for s in talk["scripts"].values() if p["recipient"] == s["owner"]), None):
        raise WorkflowError("场控测试收件人须与话本作者区分。")
    plan, plan_pin = load(talk["plan_task_id"], site, SAVE)
    intake, intake_pin = load(talk["intake_task_id"], site, INTAKE)
    newest = max((row["version"] for row in history(site, SAVE, p["session_id"])), default=0)
    if (talk["upstream"]["plan"] != plan_pin or talk["upstream"]["intake"] != intake_pin
            or (plan["identity"]["session_id"], plan["version"], newest)
            != (p["session_id"], p["plan_version"], p["plan_version"])
            or intake["upstream"]["plan"] != plan_pin or stamp(intake["evaluated_at"]) > at):
        raise WorkflowError("场控需匹配当前同场货盘及其已留档收件。")
    scripts = talk["scripts"]
    sku = p["sku_id"]
    row = scripts.get(sku)
    product = next((r for r in plan["products"] if r["sku_id"] == sku), None)
    if not row or not product or (row["revision"], product["revision"]) != (p["revision"],)*2:
        raise WorkflowError("话本与货盘 SKU、修订不匹配。")
    alloc = next((a for a in product["allocations"] if a["id"] == p["allocation_id"]), None)
    if (not alloc or p["allocation_id"] not in row["allocations"]
            or (alloc["start"], alloc["end"]) != (p["allocation_start"], p["allocation_end"])):
        raise WorkflowError("话本、货盘和收件排品时段不匹配。")
    if not stamp(plan["evaluated_at"]) <= at < stamp(alloc["start"]):
        raise WorkflowError("校对时点须在货盘已准备且该排品未开始的范围内。")
    blocked = list(row["blocked"])
    pending = set(row["pending"])
    cod = {}
    for key, evidence in row["cod_objections"].items():
        usable = evidence["state"] == "SOURCED_FIXTURE" and _valid(evidence["source"], at)
        cod[key] = {"state":"SOURCED_FIXTURE" if usable else "PENDING",
                    "value":evidence["value"] if usable else None}
        if not usable:
            pending.add("cod_"+key)
    ready = row["draft_can_enter_internal_review"] and not (blocked or pending)
    state = "READY_FOR_INTERNAL_REVIEW" if ready else "HOLD_FOR_EVIDENCE"
    return {"talkbook":talk_pin,"plan":plan_pin,"plan_intake":intake_pin}, {
        "state":state,"session_id":p["session_id"],"plan_version":p["plan_version"],"sku_id":sku,
        "revision":row["revision"],"allocation":alloc,"reviewed_at":p["reviewed_at"],
        "recipient":p["recipient"],"desk":"stage_control","local_test_receipt_recorded":True,
        "actual_recipient_confirmation":False,"native_review_verified":False,"platform_conditions_verified":False,
        "approved_for_live":False,"stage_ids":[s["id"] for s in row["stages"]],
        "stages_for_internal_review":row["stages"] if ready else None,
        "cod_objections":cod,"blocked":blocked,"pending":sorted(pending),
    }
