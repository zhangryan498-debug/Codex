"""Evidence-bound research matching and anonymous simulated supplier screening."""

from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_competitor_dossier import TOOL as DOSSIER
from workflow_competitor_bulletin import TOOL as BULLETIN
from workflow_lead_weekly import encode, safe_md, term
from workflow_live_rehearsal import amount, check, identify, stamp, task_id

TOOL="competitor-candidate-supplier-research"
FILES={"json":"candidate-supplier.json","report":"candidate-supplier.md","receipt":"candidate-supplier-receipt.json"}
TRACK=("shop_id","product_id","sku_id")
UNITS=("piece","pack","carton",None)
FX=("CNY","PHP","USD",None)


class WorkflowError(ValueError):
    pass


def identity(r):
    return tuple(r[k] for k in TRACK)


def source_ref(value,nullable=False):
    if value is None and nullable:
        return
    identify(value,"匿名合成资料来源",r"SYN-[A-Za-z0-9_-]{1,68}")


def check_amount_choices(r,k,choices):
    if r[k] not in choices:
        raise WorkflowError("报价币种、单位或状态取值不明确。")


def validate(site,p):
    check(p,"site scope currency research_id revision previous_task_id reason evaluated_at dossier_task_id "
            "bulletin_task_id candidates suppliers series owner due_at","竞品机会和匿名货源")
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("仅接受当前站点本币与明确标注的合成任务。")
    at=stamp(p["evaluated_at"])
    if stamp(p["due_at"])<at:
        raise WorkflowError("复核期限须晚于取证截止。")
    identify(p["research_id"],"研究档")
    term(p["owner"],"负责复核岗位")
    amount(p["revision"],"版本",1,1000000,False)
    for k in ("dossier_task_id","bulletin_task_id"):
        task_id(p[k],k)
    if (p["revision"]==1)!=(p["previous_task_id"] is None):
        raise WorkflowError("修订须引用同档历史。")
    if p["previous_task_id"]:
        task_id(p["previous_task_id"],"上版任务")
        term(p["reason"],"修订说明")
    elif p["reason"] is not None:
        raise WorkflowError("首版无修订原因。")
    ranked=p["candidates"]
    check(ranked,"version captured_at valid_until source_ref rows","高潜选品合成版本")
    amount(ranked["version"],"候选版本",1,1000000,False)
    source_ref(ranked["source_ref"])
    if stamp(ranked["captured_at"])>=stamp(ranked["valid_until"]):
        raise WorkflowError("候选版本有效期矛盾。")
    if not isinstance(ranked["rows"],list) or len(ranked["rows"])>40:
        raise WorkflowError("候选列表最多四十行。")
    seen=set()
    for c in ranked["rows"]:
        check(c,"candidate_id platform site product_id sku_id use spec units manufacturer_code disposition source_ref",
              "高潜行")
        for k in ("candidate_id","product_id"):
            identify(c[k],k)
        if c["sku_id"] is not None:
            identify(c["sku_id"],"候选 SKU")
        if (c["platform"],c["site"])!=(ROUTES[site][0],site) or c["candidate_id"] in seen:
            raise WorkflowError("候选须本平台/站点且 ID 唯一。")
        seen.add(c["candidate_id"])
        check_amount_choices(c,"disposition",("high","hold","declined"))
        for k in ("use","spec","manufacturer_code"):
            term(c[k],k,True)
        amount(c["units"],"候选包装数量",1,1000)
        source_ref(c["source_ref"],True)
    foo=p["suppliers"]
    if not isinstance(foo,list) or len(foo)>100:
        raise WorkflowError("匿名供应商合成清单最多一百行。")
    for r in foo:
        check(r,"listing_id merchant_key business_ref listing_ref source_type captured_at candidate_id "
                "spec units manufacturer_code sku_proof_ref quote qualification_ref sample_ref", "匿名供应商行")
        for k in ("listing_id","merchant_key","candidate_id"):
            identify(r[k],k)
        if r["candidate_id"] not in seen:
            raise WorkflowError("货源关联了不存在的候选。")
        check_amount_choices(r,"source_type",("merchant_profile","directory_lead"))
        stamp(r["captured_at"])
        for k in ("business_ref","listing_ref","sku_proof_ref","qualification_ref","sample_ref"):
            source_ref(r[k],True)
        for k in ("spec","manufacturer_code"):
            term(r[k],k,True)
        amount(r["units"],"货源包装数量",1,1000)
        q=r["quote"]
        check(q,"currency amount_minor unit pieces_per_unit moq moq_unit source_ref","公开模拟报价")
        check_amount_choices(q,"currency",FX)
        for k in ("unit","moq_unit"):
            check_amount_choices(q,k,UNITS)
        amount(q["amount_minor"],"报价")
        amount(q["moq"],"最低起订",1,1000000)
        amount(q["pieces_per_unit"],"报价单位件数",1,10000)
        if q["unit"]=="piece" and q["pieces_per_unit"] not in (1,None):
            raise WorkflowError("按只报价与每单位件数冲突。")
        source_ref(q["source_ref"],True)
    if not isinstance(p["series"],list) or len(p["series"])>24:
        raise WorkflowError("合成趋势最多二十四行。")
    for t in p["series"]:
        check(t,"shop_id product_id sku_id start end same_basis_settled_units full_window "
                "promotion source_ref observed_at","同窗趋势")
        for k in TRACK:
            identify(t[k],k)
        lo,hi,observed=(stamp(t[k]) for k in ("start","end","observed_at"))
        if hi-lo!=timedelta(days=7) or observed<hi:
            raise WorkflowError("趋势样本须分别覆盖并截止完整七日。")
        for k in ("full_window","promotion"):
            if t[k] is not None and type(t[k]) is not bool:
                raise WorkflowError("覆盖和营销状态用确定值或 null。")
        amount(t["same_basis_settled_units"],"同口径结算件数")
        source_ref(t["source_ref"],True)
    return at


def bind(site,p,load):
    at=validate(site,p)
    pins={}
    def read(ident,k):
        row,pins[ident]=load(ident,site,k)
        if row["currency"]!=p["currency"] or row["scope"]!=SYNTHETIC:
            raise WorkflowError("来源不属于本机合成站点口径。")
        return row
    dossier=read(p["dossier_task_id"],DOSSIER)
    monitor=read(p["bulletin_task_id"],BULLETIN)
    if dossier["upstream"].get(p["bulletin_task_id"])!=pins[p["bulletin_task_id"]] or stamp(dossier["evaluated_at"])>at:
        raise WorkflowError("周报须为档案原引用，且档案必须在当前截点之前。")
    keys=set(map(tuple,dossier["subject_keys"]))
    if any(identity(t) not in keys for t in p["series"]):
        raise WorkflowError("趋势观测不是当前同店、同品、同 SKU。")
    old=None
    if p["previous_task_id"]:
        old=read(p["previous_task_id"],TOOL)
        if ((old["research_id"],old["revision"]+1,old["dossier_task_id"],old["bulletin_task_id"])!=
                (p["research_id"],p["revision"],p["dossier_task_id"],p["bulletin_task_id"])
            or stamp(old["evaluated_at"])>=at or old["candidates"]["version"]>p["candidates"]["version"]):
            raise WorkflowError("修订须同档、同来源、版本连续及截点更新。")
        if old["candidates"]["version"]==p["candidates"]["version"] and old["candidates"]!=p["candidates"]:
            raise WorkflowError("同一候选源版本不得静默更改内容。")
    return at,pins,dossier,monitor,old


def trend_for(key,p,cutoff,end):
    rows=[t for t in p["series"] if identity(t)==key]
    late=[r for r in rows if stamp(r["observed_at"])>cutoff]
    ready=[r for r in rows if stamp(r["observed_at"])<=cutoff]
    by_window=defaultdict(list)
    for r in ready:
        by_window[stamp(r["start"])].append(r)
    newest=[]
    for i in (3,2,1):
        lo=end-i*timedelta(days=7)
        found=by_window[lo]
        if len(found)==1:
            newest.append(found[0])
    adequate=len(newest)==3 and all(t["full_window"] is True and t["source_ref"] and
                                    t["same_basis_settled_units"] is not None for t in newest)
    state="INSUFFICIENT_MATCHED_WINDOWS"
    changes=[]
    if adequate:
        vals=[r["same_basis_settled_units"] for r in newest]
        changes=[b-a for a,b in zip(vals,vals[1:])]
        if any(t["promotion"] is True for t in newest) and (changes[0]*changes[1]<0):
            state="PROMOTIONAL_SHORT_SWING"
        elif any(t["promotion"] is not False for t in newest):
            state="PROMOTION_OR_TIMING_NOT_DISENTANGLED"
        else:
            state=("MATCHED_RISING_SAMPLE" if all(x>0 for x in changes) else "MATCHED_FALLING_SAMPLE" if all(x<0 for x in changes)
                   else "FLAT_OR_MIXED_SAMPLE")
    return {"state":state,"selected_windows":newest,"late_rows":late,"deltas_units":changes,
            "same_basis": "settled_units_for_exact_site_shop_product_sku", "business_trend_confirmed":False,
            "promotion_causation_established":False}


def match_candidate(subject,source,cutoff):
    if not stamp(source["captured_at"])<=cutoff<stamp(source["valid_until"]):
        return "PENDING_SOURCE",[],None
    d=subject["detail"]; key=subject["identity"]
    rows=[x for x in source["rows"] if x["disposition"]=="high" and x["source_ref"]]
    exact=[r for r in rows if r["product_id"]==key[1]]
    if len(exact)>1:
        aligned=[r for r in exact if r["sku_id"]==key[2]]
        if len(aligned)==1:
            exact=aligned
        else:
            return "PENDING_EXACT_CONFLICT",exact,None
    if exact:
        c=exact[0]
        sku=(None if c["sku_id"] is None else c["sku_id"]==key[2])
        return "EXACT_PRODUCT",exact,sku
    near=[r for r in rows if r["product_id"]!=key[1] and d["use"] is not None and r["use"]==d["use"]
          and r["spec"] is not None and r["units"] is not None]
    return ("NEAR_SAME_USE",near,False) if near else ("NO_MATCH",[],None)


def supply_line(row,c,matched,sku_verified,detail):
    q=row["quote"]
    differences=[]
    for k,v in (("spec",c["spec"]),("units",c["units"]),("manufacturer_code",c["manufacturer_code"])):
        if row[k] is None or v is None:
            differences.append(k+"_unknown")
        elif row[k]!=v:
            differences.append(k+"_differs")
    credible=bool(row["source_type"]=="merchant_profile" and row["listing_ref"] and row["business_ref"])
    hard=bool(matched=="EXACT_PRODUCT" and sku_verified is True and not differences and
              row["sku_proof_ref"] and row["manufacturer_code"] and detail["listing_spec"]==row["spec"]
              and detail["listing_units"]==row["units"] and detail["visual_ref"])
    grade="A_SYNTHETIC_EXACT_SKU" if credible and hard else "B_NEAR_QUOTE_REFERENCE" if credible and row["spec"] else "C_LEAD_ONLY"
    unit_price=None
    moq_units=None
    if None not in (q["currency"],q["amount_minor"],q["unit"],q["pieces_per_unit"],q["source_ref"]):
        unit_price={"numerator_minor":q["amount_minor"],"denominator_items":q["pieces_per_unit"],"currency":q["currency"]}
    if q["moq"] is not None and q["moq_unit"] is not None:
        if q["moq_unit"]=="piece":
            moq_units=q["moq"]
        elif q["unit"]==q["moq_unit"] and q["pieces_per_unit"] is not None:
            moq_units=q["moq"]*q["pieces_per_unit"]
    return {**row,"grade":grade,"match_differences":differences,"price_per_piece_math":unit_price,"minimum_order_items_math":moq_units,
            "actual_landed_cost_minor":None,"qualification_state":"REFERENCE_UNVERIFIED" if row["qualification_ref"] else "UNKNOWN",
            "sampling_state":"REFERENCE_UNVERIFIED" if row["sample_ref"] else "UNKNOWN",
            "actual_business_contact_fetched":False,
            "quote_basis_state":"LABELLED_SYNTHETIC" if unit_price else "PENDING"}


def group_sources(lines):
    groups={}
    for line in lines:
        if line["source_type"]!="merchant_profile" or not line["business_ref"] or not line["listing_ref"]:
            continue
        key=line["business_ref"]
        old=groups.setdefault(key,{"key":key,"merchant_keys":set(),"listing_ids":set(),"appearances":0})
        old["appearances"]+=1
        old["merchant_keys"].add(line["merchant_key"])
        old["listing_ids"].add(line["listing_id"])
    final=[]
    for g in groups.values():
        expanded={**g,"business_refs":{g["key"]}}
        overlap=[x for x in final if x["merchant_keys"] & g["merchant_keys"]]
        for x in overlap:
            final.remove(x)
            for k in ("merchant_keys","listing_ids","business_refs"):
                expanded[k].update(x[k])
            expanded["appearances"]+=x["appearances"]
        final.append(expanded)
    count=sum(g["appearances"] for g in final)-len(final)
    return [{**g,"merchant_keys":sorted(g["merchant_keys"]),"listing_ids":sorted(g["listing_ids"]),
             "business_refs":sorted(g["business_refs"])} for g in final],count


def compute(p,linked):
    cutoff,pins,dossier,monitor,old=linked
    studied=[]
    lines=[]
    dossiers_ok=dossier["ranking"]["state"] in ("ORDERED","CO_PRIORITY")
    for item in dossier["subjects"]:
        key=tuple(item["identity"])
        mode,matched,sku=match_candidate(item,p["candidates"],cutoff)
        if item["critical_conflicts"] or item["ranking_evidence_gaps"] or not dossiers_ok:
            mode,matched,sku="PENDING_DOSSIER",[],None
        catalogue={x["candidate_id"]:x for x in matched}
        suppliers=[]
        for r in p["suppliers"]:
            if r["candidate_id"] in catalogue and stamp(r["captured_at"])<=cutoff:
                line=supply_line(r,catalogue[r["candidate_id"]],mode,sku,item["detail"])
                suppliers.append(line);lines.append(line)
        descriptor={"spec":item["detail"]["listing_spec"],"units":item["detail"]["listing_units"]}
        differences={r["candidate_id"]:[k for k,value in descriptor.items() if value!=r[k]] for r in matched}
        studied.append({"identity":list(key),"match":mode,"exact_sku":sku,"matched_candidates":matched,
                        "target_candidate_spec_differences":differences,
                        "suppliers":suppliers,"supplier_role":"EXACT_CANDIDATE_SCREEN" if mode=="EXACT_PRODUCT" else "REFERENCE_ONLY",
                        "trend":trend_for(key,p,cutoff,stamp(dossier["period"]["end"])),
                        "quote_motion":item["short_term_momentum"],"dossier_price_is_supplier_cost":False})
    businesses,duplicate=group_sources(lines)
    before={tuple(x["identity"]):x["match"] for x in old["products"]} if old else {}
    changes=[{"identity":x["identity"],"before":before[tuple(x["identity"])],"after":x["match"]}
             for x in studied if old and before[tuple(x["identity"])]!=x["match"]]
    return {"state":"PENDING" if any(x["match"].startswith("PENDING") for x in studied) else "RESEARCH_RECORDED",
            "products":studied,"dossier_task_id":p["dossier_task_id"],"bulletin_task_id":p["bulletin_task_id"],
            "candidates":p["candidates"],"suppliers_raw":p["suppliers"],"merchant_groups":businesses,"distinct_merchant_count":len(businesses),
            "supplier_repeat_rows_skipped_in_count":duplicate,"upstream":pins,"evaluated_at":p["evaluated_at"],"revision":p["revision"],
            "previous_research_task_id":old["task_id"] if old else None,"revision_reason":p["reason"],"match_changes":changes,
            "landed_cost_ranking_computed":False,"provider_called":False,"outreach_authorized":False,"sample_order_started":False,
            "next_review":{"owner":p["owner"],"due_at":p["due_at"],"action":"核实际规格、来源、币种、包装、起订、运税资质和样品；近似仅作线索。"},
            "monitor_price_is_context_only":True}


def receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","input_sha256","upstream","research_id","revision")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets or tool!=TOOL:
        raise WorkflowError("只收本机匿名模拟资料。")
    r={**compute(p,linked),"task_id":folder.name,"site":site,"currency":p["currency"],"scope":SYNTHETIC,"tool_id":tool,
       "research_id":p["research_id"],"input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "model_called":False}
    raw=encode(r)
    lines=[f"# {site} 候选与货源对照 · {r['research_id']} v{r['revision']}","",f"结论：{r['state']}。匿名合成业务来源。",
           "同商品、同 SKU、供货同款分开；促销同期变化及报价条件只在样本范围内成立。","",
           "| 对象 | 匹配 | 趋势观察 |","|---|---|---|"]
    for v in r["products"]:
        lines.append("| "+" | ".join(map(safe_md,[" / ".join(v["identity"]),v["match"],v["trend"]["state"]]))+" |")
    body=("\n".join(lines)+"\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":body,"receipt":encode(receipt(r,raw,body))}.items():
        with paths[k].open("xb") as f:f.write(b)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["supplier_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=receipt(r,files["json"],files["report"])):
        raise WorkflowError("候选、source 或匿名供应商收件报告无法核验。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"competitor-analyst","label":"高潜商品匹配和匿名货源对照",
             "description":"本机匿名合成对照支持精确、近似、无命中和来源待核。","input_example":{}}]

