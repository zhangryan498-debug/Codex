"""Deterministic local shoot plan and traceable category shortlist."""

from collections import defaultdict
from hashlib import sha256
import json
import re
import unicodedata

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id
from workflow_director_topic import TOOL as TOPICS


DIR = "director-cn-shoot-script"
PH_DIR = "director-ph-shoot-script"
SEL = "selection-category-shortlist"
TOOLS = (DIR,PH_DIR,SEL)
FILES = {"json":"planning-result.json","report":"planning-report.md","receipt":"planning-receipt.json"}
HOOKS = ("pain","outcome_first","process")
SHOT = "start end lens action spoken subtitle sound product_exposure shoot_check cta original_in original_out"
PROHIBITED = re.compile(r"人民币|[¥￥₱]|[0-9０-９]|几折|秒杀|下单立减|库存|仅剩|承重|防水|质保|免费|认证|无毒|销量|价格|包邮")


class WorkflowError(ValueError):
    pass


PH_CLAIMS = re.compile(r"\d|[¥￥₱$]|https?://|\b(?:cash on delivery|only cod|cod only|free|price|discount|coupon|in stock|limited stock|waterproof|guaranteed|certified|next.day delivery|return policy|refund)\b", re.I)


def _words(values, label, low, high):
    if not isinstance(values,list) or not low<=len(values)<=high:
        raise WorkflowError(label+"数量不符合本机验收范围。")
    for v in values:
        term(v,label)
    if len({"".join(unicodedata.normalize("NFKC",v).casefold().split()) for v in values})!=len(values):
        raise WorkflowError(label+"须逐一不同。")


def _shot(row, site):
    for k in ("start","end"):
        amount(row[k],k,0,45,False)
    if row["start"]>=row["end"] or type(row["cta"]) is not bool:
        raise WorkflowError("镜头的起止或行动指令标记无效。")
    for k in ("lens","action","spoken","subtitle","sound","product_exposure","shoot_check"):
        term(row[k],k)
    if row["original_in"] is not None or row["original_out"] is not None:
        raise WorkflowError("没有获准原片的可拍计划，原片入出点须保留未知。")
    if site == "PH":
        if row["spoken"] != row["subtitle"]:
            raise WorkflowError("原语口播和字幕须对应。")
        words = ph_draft_word_count(row["spoken"], "口播")
        if words > 3 * (row["end"] - row["start"]):
            raise WorkflowError("菲律宾口播超过本机纸面参考负载。")
        return words
    for k in ("spoken","subtitle"):
        if PROHIBITED.search(row[k]) or any(c in row[k] for c in "[]［］"):
            raise WorkflowError("口播／字幕包含价格、库存、数量、功效或未核实占位；本入口只规划可见演示。")
    if row["spoken"]!=row["subtitle"]:
        raise WorkflowError("本轮原语口播和字幕须对应，不能在字幕新增商品声明。")
    han=sum("\u3400"<=c<="\u9fff" for c in row["spoken"])
    if not han:
        raise WorkflowError("此本机抖音纸面评估须提供中文口播。")
    load=sum(("\u3400"<=c<="\u9fff") or c.isalnum() for c in row["spoken"])
    if load>5*(row["end"]-row["start"]):
        raise WorkflowError("中文口播超过本地制作参考负载。")
    return load


def _script(p, site):
    fields = ("site scope currency job_id as_of owner topic_task_id topic_id sku_id revision fact_scope_ref "
              "hooks main_hook_id body search_terms review shot_context")
    if site == "PH":
        fields += " localization cod variant_structure"
    check(p, fields, "可拍脚本")
    for k in ("job_id","topic_id","sku_id","main_hook_id"):
        identify(p[k],k)
    task_id(p["topic_task_id"],"同 SKU 选题回执")
    amount(p["revision"],"商品修订",1,10**6,False)
    term(p["owner"],"剪辑交接人")
    term(p["shot_context"],"拍摄交底")
    term(p["fact_scope_ref"],"同 SKU 商品事实范围",True)
    _words(p["search_terms"],"搜索词候选",3,3)
    if not isinstance(p["hooks"],list) or len(p["hooks"])!=3 or not isinstance(p["body"],list) or not 1<=len(p["body"])<=12:
        raise WorkflowError("输入须为三个完整钩子和非空的连续主体分镜。")
    ids,methods,lengths=set(),set(),{}
    for h in p["hooks"]:
        check(h,"id method "+SHOT,"钩子")
        identify(h["id"],"钩子编号")
        if h["id"].startswith("body_") or h["id"] in ids or h["method"] not in HOOKS or (h["start"],h["end"],h["cta"])!=(0,3,False):
            raise WorkflowError("三个互异钩子均须覆盖前 3 秒，且不单独增加 CTA。")
        ids.add(h["id"]);methods.add(h["method"])
        lengths[h["id"]]=_shot(h, site)
    if methods!=set(HOOKS) or p["main_hook_id"] not in ids:
        raise WorkflowError("明示痛点、结果前置和过程示范各一条及主钩子。")
    for field in ("lens","action","spoken"):
        _words([h[field] for h in p["hooks"]],"不同钩子的"+field,3,3)
    pos=3
    n_cta=0
    for shot in p["body"]:
        check(shot,SHOT,"同一主体分镜")
        if shot["start"]!=pos:
            raise WorkflowError("主线镜头须无间隙，从第 3 秒连续编排。")
        count=_shot(shot, site)
        lengths[f'body_{shot["start"]}']=count
        pos=shot["end"]
        n_cta+=shot["cta"]
    if n_cta!=1 or not 15<=pos<=45:
        raise WorkflowError("本地制作测试需一条 CTA、15 至 45 秒。")
    if p["review"] is not None:
        v=p["review"]
        check(v,"reviewer ref hooks_sha256","独立文字复核")
        term(v["reviewer"],"独立校对人")
        term(v["ref"],"复核来源")
        identify(v["hooks_sha256"],"三条钩子的校对摘要",r"[a-f0-9]{64}")
    return lengths,pos


def ph_draft_word_count(value, label, error_type=WorkflowError):
    term(value, label)
    if (PH_CLAIMS.search(unicodedata.normalize("NFKC", value)) or
            any(c in value for c in ("[", "]")) or
            any("\u3400" <= c <= "\u9fff" for c in value)):
        raise error_type("菲律宾本机草案不能口播未核实的价格、库存、政策或商品声明。")
    words = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", value, flags=re.UNICODE)
    if not words:
        raise error_type(label+"必须是可读的菲律宾站口播草案。")
    return len(words)


def _ph_script(p, topic):
    loc = p["localization"]
    check(loc, "language units scene culture", "四层本地化")
    check(loc["language"], "locale cues", "口播与字幕")
    if loc["language"]["locale"] != topic["locale"]:
        raise WorkflowError("菲律宾语言必须与本站选题一致。")
    term(loc["language"]["cues"], "菲律宾口播读法")
    check(loc["units"], "currency display_units specification_ref", "本地货币和度量")
    if loc["units"]["currency"] != "PHP":
        raise WorkflowError("菲律宾商品课件的币种为 PHP。")
    if loc["units"]["display_units"] not in (None, "cm", "mm", "g", "kg", "ml", "L", "piece"):
        raise WorkflowError("保留未知或填写可审核的本地展示单位。")
    check(loc["scene"], "setting casting props", "本地场景")
    for key, value in loc["scene"].items():
        term(value, key)
    check(loc["culture"], "cues sound_rights_ref hashtags", "文化和标签")
    term(loc["culture"]["cues"], "文化核对")
    _words(loc["culture"]["hashtags"], "仅供本机复核的标签", 2, 6)
    if any(not re.fullmatch(r"#[A-Za-z][A-Za-z0-9_]{1,39}", tag) for tag in loc["culture"]["hashtags"]):
        raise WorkflowError("标签须为明确的候选文本，热度未核。")
    for ref in (loc["units"]["specification_ref"], loc["culture"]["sound_rights_ref"]):
        if ref is not None:
            term(ref, "本地来源", True)
    differences = p["variant_structure"]
    check(differences, " ".join(h["id"] for h in p["hooks"]), "钩子结构差异")
    for obj in differences.values():
        check(obj, "narration on_camera rhythm", "钩子的结构")
        for key, value in obj.items():
            term(value, key)
    for key in ("narration", "on_camera", "rhythm"):
        _words([d[key] for d in differences.values()], "逐条结构差异 "+key, 3, 3)
    cod = p["cod"]
    check(cod, "specification_visual variation_visual payment_verification", "COD 预期管理")
    for key, value in cod.items():
        if value is not None:
            term(value, key)
    if p["review"] is not None:
        raise WorkflowError("此入口不接收自报的复核。母语、首三秒独立复核和实拍需独立补证。")


def _compose_ph(p, topic, lengths, duration):
    result = _compose(p, topic, lengths, duration, None)
    source = result["facts"]["fixture_observation"]
    loc = p["localization"]
    cod = p["cod"]
    pending = ["native_speech_subtitle_review", "independent_three_second_review",
               "revised_filming", "real_cod_and_product_anchor"]
    if not source:
        pending.append("fixture_product_observation")
    spec_ref = loc["units"]["specification_ref"]
    spec_verified = bool(source and spec_ref == source["ref"] and loc["units"]["display_units"])
    if not spec_verified or not cod["specification_visual"]:
        pending.append("specification_evidence")
    if not cod["variation_visual"]:
        pending.append("batch_or_colour_differences")
    if not cod["payment_verification"]:
        pending.append("payment_scope_source")
    result.update(state="PENDING_EVIDENCE", spoken_word_counts=lengths,
                  script_stage="PROPOSED_LOCAL_FIXTURE" if source and spec_verified and all(cod.values()) else "PENDING_FIXTURE_EVIDENCE",
                  variant_timelines=[{"id":h["id"],"structure":p["variant_structure"][h["id"]],
                                      "shots":[h,*p["body"]]} for h in p["hooks"]],
                  localization={**loc,"native_review_verified":False,"live_hashtag_trends_verified":False,
                                "sound_rights_verified":False,"fixture_specification_source_matched":spec_verified},
                  cod={**cod,"actual_payment_methods_verified":False,"shipping_or_return_commitment":None},
                  pending=pending)
    result.pop("spoken_character_counts")
    return result


def _compose(p, topic, lengths, duration, host_review):
    source=[d for d in topic["sourced_inputs"] if d["kind"]=="product_fixture" and d["usable_for_fixture"]
            and d["ref"]==p["fact_scope_ref"]]
    approved=bool(host_review)
    main=next(h for h in p["hooks"] if h["id"]==p["main_hook_id"])
    return {"state":"SHOOT_PLAN_READY" if source and approved else "PENDING_EVIDENCE",
            "sku_id":p["sku_id"],"revision":p["revision"],"topic_id":p["topic_id"],"duration_seconds":duration,
            "owner":p["owner"],"opening_variants":p["hooks"],"primary_hook_id":main["id"],
            "timeline":[main,*p["body"]],"hook_review":{"text_review_only":True,"matched":approved,"record":p["review"],
                "host_independent_evidence":host_review,"approval_scope":"internal_synthetic_paper_shoot_plan_review"},
            "shared_body_for_all_variants":True,"spoken_character_counts":lengths,"planned_reference_only":True,
            "shot_context":p["shot_context"],"search_terms":p["search_terms"],"search_positions_verified":False,
            "facts":{"fixture_observation":source[0] if source else None,"fixture_scope":"caller_supplied_synthetic_reference",
                     "physical_item_verified":None,"price_minor":None,
                     "stock_units":None,"performance_claim":None,"material_verified":None},
            "listing":{"planned_route":topic["route"],"own_product_id":None,"verification":"PENDING_NO_ACTUAL_PRODUCT_LINK"},
            "shoot_or_publishing_performed":False}



def _category(p):
    check(p,"site currency scope job_id as_of owner category_id specification demand_unit budget_min_minor budget_max_minor "
          "allowed_fulfillments excluded_seller_ids shortlist_limit recent_windows raw_snapshots","类目与来源采样")
    for k in ("job_id","category_id","specification"):
        identify(p[k],k)
    term(p["owner"],"选品责任")
    for k in ("budget_min_minor","budget_max_minor"):
        amount(p[k],k,0,10**12,False)
    if p["budget_min_minor"]>p["budget_max_minor"] or p["demand_unit"]!="observed_fixture_order_ids":
        raise WorkflowError("只接受递增报价范围和合成去重订单样本。")
    amount(p["shortlist_limit"],"短名单上限",1,5,False)
    modes=("cn_domestic","seller_delivery") if p["site"]=="CN" else ("ph_local_warehouse","cross_border")
    _words(p["allowed_fulfillments"],"履约范围",1,2)
    if set(p["allowed_fulfillments"])-set(modes):
        raise WorkflowError("履约方式不能跨站套用。")
    _words(p["excluded_seller_ids"],"排除商家",0,30)
    for v in p["excluded_seller_ids"]:
        identify(v,"排除商家")
    at=stamp(p["as_of"])
    windows=p["recent_windows"]
    if not isinstance(windows,list) or len(windows)!=2:
        raise WorkflowError("提交两个独立等长、已结束的合成观察窗口。")
    starts,ends=[],[]
    for w in windows:
        check(w,"start end","统计时间窗")
        a,b=stamp(w["start"]),stamp(w["end"])
        if not a<b<=at:
            raise WorkflowError("时间窗须在评估截点结束。")
        starts.append(a);ends.append(b)
    if ends[0]!=starts[1] or ends[0]-starts[0]!=ends[1]-starts[1]:
        raise WorkflowError("两个窗口须连续等长，避免不等口径直接比较。")
    captures=p["raw_snapshots"]
    if not isinstance(captures,list) or not 1<=len(captures)<=30:
        raise WorkflowError("提交 1–30 份有编号的本机原始快照或未取得回执。")
    seen=set()
    for cap in captures:
        check(cap,"id site currency status captured_at locator rows","原始快照")
        identify(cap["id"],"原始快照编号")
        if cap["id"] in seen or (cap["site"],cap["currency"])!=(p["site"],p["currency"]):
            raise WorkflowError("快照编号重复、错站或币种不一致。")
        seen.add(cap["id"])
        term(cap["locator"],"输入来源及定位",True)
        when=stamp(cap["captured_at"])
        if when>at or cap["status"] not in ("captured_fixture","source_unavailable"):
            raise WorkflowError("来源时间或返回状态无效。")
        if not isinstance(cap["rows"],list) or len(cap["rows"])>80 or (cap["status"]=="source_unavailable" and cap["rows"]):
            raise WorkflowError("源不可用时不接受伪造数据行。")
        for row in cap["rows"]:
            check(row,"seller_id product_id sku_id category_id specification revision price_minor fulfillment warehouse_ref "
                   "standard_product observed_orders window_index metric_definition","商品观察原文")
            for key in ("seller_id","product_id","sku_id","category_id","specification"):
                identify(row[key],key)
            amount(row["revision"],"来源中的 SKU 修订",1,10**6,False)
            amount(row["price_minor"],"合成报价最小单位")
            amount(row["observed_orders"],"去重订单样本数")
            amount(row["window_index"],"观察窗口编号",0,1,False)
            if row["fulfillment"] not in (*modes,None):
                raise WorkflowError("原件写入了其他站点的履约方式。")
            if row["standard_product"] is not None and type(row["standard_product"]) is not bool:
                raise WorkflowError("标准品应为有证据的状态或未知。")
            term(row["warehouse_ref"],"独立仓配出处",True)
            term(row["metric_definition"],"订单统计定义",True)
    return windows,ends


def _identity(row):
    return tuple(row[k] for k in ("seller_id","product_id","sku_id"))


def _shortlist(p, view):
    windows,ends=view
    groups=defaultdict(list)
    evidence=[]
    outages=[]
    cut=stamp(p["as_of"])
    for d in p["raw_snapshots"]:
        digest=sha256(encode(d)).hexdigest()
        evidence.append({"source_id":d["id"],"canonical_sha256":digest,"captured_at":d["captured_at"],
                         "source_locator":d["locator"],"original":d})
        if d["status"]=="source_unavailable" or not d["locator"]:
            outages.append(d["id"])
            continue
        for idx,r in enumerate(d["rows"]):
            groups[_identity(r)].append((r,{"source_id":d["id"],"sha256":digest,"pointer":f"/rows/{idx}",
                                          "captured_at":d["captured_at"]}))
    filtered=[]
    cards=[]
    for id,records in sorted(groups.items()):
        excluded=[]
        base=records[0][0]
        constants=("category_id","specification","revision","price_minor","fulfillment","warehouse_ref","standard_product")
        variants={tuple(r[k] for k in constants) for r,_ in records}
        if id[0] in p["excluded_seller_ids"]:
            excluded.append("seller_filtered")
        if len(variants)==1:
            if (base["category_id"],base["specification"])!=(p["category_id"],p["specification"]):
                excluded.append("different_category_or_specification")
            if base["price_minor"] is not None and not p["budget_min_minor"]<=base["price_minor"]<=p["budget_max_minor"]:
                excluded.append("price_outside_fixture_budget")
            if base["fulfillment"] is not None and base["fulfillment"] not in p["allowed_fulfillments"]:
                excluded.append("fulfillment_filtered")
        if excluded:
            filtered.append({"identity":list(id),"reasons":excluded,"evidence":[r for _,r in records]})
            continue
        pending=["snapshot_identity_or_terms_conflict"] if len(variants)!=1 else []
        values=[set(),set()]
        for row,ref in records:
            n=row["window_index"]
            if stamp(ref["captured_at"])>=ends[n] and row["metric_definition"]==p["demand_unit"] and row["observed_orders"] is not None:
                values[n].add(row["observed_orders"])
        series=[next(iter(v)) if len(v)==1 else None for v in values]
        if None in series:
            pending.append("demand_windows_missing_or_conflicting")
        if base["price_minor"] is None:
            pending.append("price_unknown")
        if not base["warehouse_ref"] or not base["fulfillment"] or (p["site"]=="PH" and base["standard_product"] is None):
            pending.append("fulfillment_or_standardization_unknown")
        if outages:
            pending.append("source_coverage_incomplete")
        trend=None if None in series else "up_two_windows" if series[1]>series[0] else "down_two_windows" if series[1]<series[0] else "flat_two_windows"
        if len(variants)!=1:
            base_fields=dict.fromkeys(constants)
        else:
            base_fields={k:base[k] for k in constants}
        cards.append({"identity":list(id),**base_fields,"evidence":[r for _,r in records],
                 "demand":{"matched_orders":series,"two_window_observation":trend,"long_term_trend":None,
                           "count_source":"supplied_order_count_fixture_not_individual_order_ids"},
                 "competition":{"observed_independent_sellers":None,"scope":"submitted_fixture_sellers_not_whole_market"},
                 "state":"PENDING_EVIDENCE" if pending else "COMPARABLE_FIXTURE",
                 "pending":pending})
    sellers=sorted({r["identity"][0] for r in cards})
    for r in cards:
        r["competition"]["observed_independent_sellers"]=len(sellers) if not outages else None
    sorted_cards=sorted((r for r in cards if r["state"]=="COMPARABLE_FIXTURE"),key=lambda r:(
         -min(r["demand"]["matched_orders"]),-r["demand"]["matched_orders"][1],r["price_minor"],r["identity"]))
    nominated=[]
    used=set()
    for r in sorted_cards:
        seller=r["identity"][0]
        if seller in used:
            continue
        nominated.append({"identity":r["identity"],"position":len(nominated)+1,
            "reason":"同规格两个样本窗较低订单数优先，其次近期订单数、预算内价格与固定身份顺序。每卖家最多一件。"})
        used.add(seller)
        if len(nominated)==p["shortlist_limit"]:
            break
    return {"state":"CATEGORY_RECORDED" if nominated else "PENDING_CATEGORY_EVIDENCE","category_id":p["category_id"],
            "specification":p["specification"],"owner":p["owner"],"raw_evidence":evidence,"requested_windows":windows,
            "source_unavailable_ids":outages,"cards":cards,"filtered":filtered,"ranked_shortlist":nominated,
            "independent_sellers_in_observed_scope":sellers,"requested_limit":p["shortlist_limit"],
            "shortlist_was_padded":False,"real_market_or_supplier_checked":False}


def bind(site,tool,p,load,approved_reviews):
    scope = {DIR:("CN","CNY"), PH_DIR:("PH","PHP")}.get(tool, (site, ROUTES[site][1]))
    if not isinstance(p,dict) or (p.get("site"),p.get("currency"),p.get("scope"))!=(*scope,SYNTHETIC) or p.get("site")!=site:
        raise WorkflowError("仅评估本站、本币以及明示的合成输入。")
    if tool==SEL:
        return {},_category(p)
    lengths,duration=_script(p, site)
    r,pin=load(p["topic_task_id"],site,TOPICS)
    t=next((x for x in r["topics"] if x["id"]==p["topic_id"]),None)
    if ((r["sku_id"],r["revision"])!=(p["sku_id"],p["revision"]) or stamp(r["as_of"])>stamp(p["as_of"])
            or t is None or not t["input_ready"]):
        raise WorkflowError("须绑定同站、SKU、修订及不晚于脚本的可用选题。")
    if tool == PH_DIR:
        _ph_script(p, t)
        return {"topic":pin},(t,lengths,duration)
    if t["locale"] != "zh-CN":
        raise WorkflowError("抖音脚本须使用 zh-CN 可用选题。")
    doc=p["review"]
    host=None
    if doc and doc["reviewer"].strip().casefold()!=p["owner"].strip().casefold() and (
            doc["hooks_sha256"]==sha256(encode(p["hooks"])).hexdigest()):
        key=sha256(encode({k:p[k] for k in ("hooks","body","search_terms","shot_context","fact_scope_ref")})).hexdigest()
        identity={k:p[k] for k in ("site","sku_id","revision","topic_id")}
        host=next((r for r in approved_reviews if r["scope"]==identity and r["content_sha256"]==key
                   and (r["review_ref"],r["reviewer"])==(doc["ref"],doc["reviewer"])
                   and r["purpose"]=="internal_synthetic_paper_shoot_plan_review"),None)
    return {"topic":pin,**({"review":host} if host else {})},(t,lengths,duration,host)


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("job_id","task_id","site","tool_id","upstream","input_sha256","state")},
             "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("本机规划只接受结构化输入和既有回执。")
    pins,c=linked
    if tool == PH_DIR:
        result = _compose_ph(p,*c)
    elif tool == DIR:
        result = _compose(p,*c)
    else:
        result = _shortlist(p,c)
    r={**result,"task_id":folder.name,"tool_id":tool,"site":site,"scope":SYNTHETIC,"currency":p["currency"],
       "job_id":p["job_id"],"as_of":p["as_of"],"upstream":pins,"input_sha256":sha256(encode(p)).hexdigest(),
       "execution_authorized":False,"provider_called":False,"model_called":False}
    raw=encode(r)
    report=(f"# {site} · {p['job_id']} · {r['state']}\n\n"
        "纸面拍摄规划或合成候选证据，不证明实拍、真实经营、招商、发布或投放。\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(v)
    return r,paths


def verify_saved(t,files):
    r=t["result"]
    if (t["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=t["source"]["role_packet_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(t["id"],t["site"],t["source"]["tool_id"])
            or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("用例、任务与本机原件回执不一致。")


def public_tools():
    return [
        {"id":DIR,"sites":["CN"],"role_suffix":"video-director","label":"抖音三钩子可拍脚本",
         "description":"同 SKU 证据、三条首镜、共用连续分镜，未知不写成实物。","input_example":{},"max_media_files":0},
        {"id":PH_DIR,"sites":["PH"],"role_suffix":"video-director","label":"菲律宾三钩子可拍脚本",
         "description":"同 SKU 合成选题，三种差异化首镜、连续分镜、四层本地化和 COD 待核。","input_example":{},"max_media_files":0},
        {"id":SEL,"sites":list(ROUTES),"role_suffix":"selection","label":"本机类目候选、原证据与短名单",
         "description":"保留合成原文和时间，过滤、排序及无证分列。","input_example":{},"max_media_files":0},
    ]

