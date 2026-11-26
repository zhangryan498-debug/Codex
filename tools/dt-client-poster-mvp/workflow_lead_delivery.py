"""Site-bound review, return and actual local recipient approval of role deliverables."""

from hashlib import sha256
import json

from role_tools import ROUTES, SYNTHETIC
from workflow_lead_weekly import encode, term
from workflow_live_rehearsal import check, identify, amount, stamp, task_id


TOOL = "lead-nine-role-delivery-review"
TOOLS = (TOOL,)
FILES = {"json":"role-delivery-review.json","report":"role-delivery-review.md","receipt":"role-delivery-receipt.json"}
SPECS = {
    "selection": ("selection-offline-screen", ("screening_id","candidates","ranking"), "screening_id"),
    "shop-ops": ("shop-funnel-alignment", ("job_id","target","views","reconciled"), "job_id"),
    "campaign-ops": ("campaign-offline-opportunity", ("campaign_id","sku_id","options","recommended_option_ids"), "campaign_id"),
    "live-ops": ("live-assortment-plan", ("identity","window","products","allocation_timeline"), "identity"),
    "visual-designer": ("visual-media-library", ("asset_id","rights","source","version"), "asset_id"),
    "video-director": ("director-offline-topic-schedule", ("campaign_id","sku_id","topics","resource_sources"), "campaign_id"),
    "video-teardown": ("teardown-offline-element-library", ("library_id","product_id","elements","media_registration"), "library_id"),
    "ad-buyer": ("ad-period-review", ("measurement_basis","windows","experiment_plan","current_research_state"), "measurement_basis"),
    "competitor-analyst": ("competitor-surface-alignment", ("job_id","target","views","reconciled"), "job_id"),
}
MEDIA = {"visual-designer","video-teardown"}
EXPECTED_STATE = {
    "selection":"SCREENING_RECORDED", "shop-ops":"ALIGNED_FIXTURE", "campaign-ops":"OPTIONS_RECORDED",
    "live-ops":"READY_OFFLINE", "visual-designer":"allowed_offline_reference", "video-director":"TOPICS_RECORDED",
    "video-teardown":"ELEMENT_VERSION_RECORDED", "ad-buyer":None, "competitor-analyst":"ALIGNED_FIXTURE",
}


class WorkflowError(ValueError):
    pass


def request_key(p):
    return f"{p['batch_id']}:{p['version']}"


def validate(site,p):
    check(p,"site currency scope job_id batch_id version prior_review_task_id owner evaluated_at deadline submissions","九岗收件计划")
    if (site,p["site"],p["currency"],p["scope"])!=(site,site,ROUTES[site][1],SYNTHETIC):
        raise WorkflowError("退改仅接受本站及原币的合成本机任务。")
    for name in ("job_id","batch_id"):
        identify(p[name],name)
    amount(p["version"],"审阅版本",1,10**6,False)
    if (p["version"]==1)!=(p["prior_review_task_id"] is None):
        raise WorkflowError("后续审阅须引用同一编号的上一版。")
    if p["prior_review_task_id"]:
        task_id(p["prior_review_task_id"],"上一版审阅")
    term(p["owner"],"总负责人")
    at=stamp(p["evaluated_at"])
    if at>=stamp(p["deadline"]):
        raise WorkflowError("本机审阅或整改期限须晚于审阅时点。")
    check(p["submissions"]," ".join(SPECS),"各岗位逐条提交")
    for k,r in p["submissions"].items():
        if r is None:
            continue
        check(r,"source_task_id recipient owner correction_due rights expected_sha256 expected_revision subject","来源与责任")
        task_id(r["source_task_id"],"岗位源任务号")
        term(r["owner"],"岗位返修责任人")
        term(r["subject"],"同一原件主体")
        if r["recipient"] not in SPECS:
            raise WorkflowError("收件人须为本站既有岗位。")
        if not at<stamp(r["correction_due"])<=stamp(p["deadline"]):
            raise WorkflowError("整改须有本次审核之后的明确到期时点。")
        if r["rights"] not in ("synthetic_fixture_review","archived_metadata_review","commercial_asset_use",None):
            raise WorkflowError("使用范围未明确。")
        check(r["expected_sha256"],"result_sha256 receipt_sha256","原件与回执哈希")
        for v in r["expected_sha256"].values():
            identify(v,"应有原件哈希",r"[a-f0-9]{64}")
        amount(r["expected_revision"],"原件修订（缺时用哈希）",1,10**6)
    return at


def _subject(role,obj):
    key=SPECS[role][2]
    v=obj.get(key)
    if role=="ad-buyer":
        return v.get("plan_id") if isinstance(v,dict) else None
    return v.get("session_id") if isinstance(v,dict) else v


def _media_bounds(role,source,at):
    if role=="visual-designer":
        rights=source["rights"]
        recorded=rights.get("state")=="recorded_offline_only" and bool(rights.get("reference"))
        return {"archival":recorded,"right_ends_at":rights.get("expires_at"),
                "current_asset_use_allowed":False,"raw":rights}
    media=source["media_registration"]
    rights=media and media["rights"]
    date=rights.get("expires_at") if rights else None
    complete={"hook","selling_point","demonstration","trust","cta","emotion"}
    supported=(rights and rights.get("state")=="recorded_offline_only" and rights.get("reference")
               and source.get("rights_state")=="allowed_offline_reference"
               and set(source.get("complete_categories",[]))==complete)
    return {"archival":bool(supported),"right_ends_at":date,"current_asset_use_allowed":False,"raw":rights}


def _revision(result):
    return result.get("version",result.get("revision"))


def _ad_source_readback(result):
    """Expose verified archived values without recalculating either ledger."""
    basis = result["measurement_basis"]
    findings = result["findings"]
    coverage = result["coverage_and_gaps"]
    return {
        "readback_mode": "sealed_values_only",
        "account_id": basis["account_id"],
        "plan_id": basis["plan_id"],
        "channel": basis["channel"],
        "currency": basis["currency"],
        "timezone": basis["timezone"],
        "windows": [
            {key: window[key] for key in ("source_id", "start", "end", "captured_at", "state",
                                          "finance_maturity", "measurement_basis")}
            for window in result["facts"]
        ],
        "period_comparability": {key: coverage[key] for key in (
            "adjacent_windows", "equal_length_windows", "ad_attribution_and_maturity_basis_match",
            "shop_dedup_definition_match", "cross_source_order_mapping")},
        "financial_ledger": {key: findings[key] for key in (
            "ad_comparable_window_totals_minor", "ad_weighted_report_scope_roi", "mature_financial_rollup")},
        "independent_shop_observation": {key: findings[key] for key in (
            "shop_independent_window_total_minor", "latest_vs_prior_shop_dedup_sales_change_minor")},
        "causal_evidence": {key: findings[key] for key in (
            "causal_increment_minor", "incremental_roi", "incremental_buyers")},
        "experiment_executed": result["experiment_plan"]["executed"],
        "research_state": result["current_research_state"],
        "execution_authorized": False,
    }


def _entry(site,role,r,loaded,at,live_history):
    if r is None:
        return {"state":"RETURNED","reasons":["required_role_deliverable_missing"],"submission":None}
    result,pin=loaded
    errors=[]
    tool,fields,_=SPECS[role]
    if any(k not in result or result[k] is None for k in fields):
        errors.append("role_template_incomplete")
    declared_scope=result.get("scope",result.get("data_origin"))
    if declared_scope!=SYNTHETIC:
        errors.append("synthetic_scope_unconfirmed")
    for clock in ("evaluated_at","as_of","cutoff"):
        if result.get(clock) and stamp(result[clock])>at:
            errors.append("source_later_than_review")
            break
    # In all cases the workbench verifies site/tool membership and the exact original bytes.
    if any(pin[k]!=v for k,v in r["expected_sha256"].items()):
        errors.append("artifact_pin_mismatch")
    subject=_subject(role,result)
    if subject!=r["subject"]:
        errors.append("subject_mismatch")
    if _revision(result)!=r["expected_revision"]:
        errors.append("source_revision_mismatch")
    artifact_state=result.get("disposition") if role=="visual-designer" else result.get("state")
    if role=="ad-buyer":
        # The deliverable is a budget review, including negative decisions; it does not grant asset use.
        if result.get("current_research_state") not in ("RESEARCH_ONLY","BLOCKED","PENDING") or result.get("execution_authorized") is not False:
            errors.append("ad_review_scope_unconfirmed")
        if any(stamp(w["captured_at"])>at for w in result["facts"]):
            errors.append("source_later_than_review")
    elif artifact_state!=EXPECTED_STATE[role]:
        errors.append("role_artifact_pending_or_unfinished")
    if role=="live-ops":
        if r["recipient"]!="live-ops":
            errors.append("live_recipient_mismatch")
        latest=max((v["version"] for v in live_history(site,tool,subject)),default=0)
        if result.get("version")!=latest:
            errors.append("live_plan_superseded")
    media=None
    if role in MEDIA:
        if not any(err=="role_template_incomplete" for err in errors):
            media=_media_bounds(role,result,at)
            expires=media["right_ends_at"]
            media["as_of_state"]=("EXPIRED_ARCHIVED_METADATA_ONLY" if expires and at>=stamp(expires)
                                  else "ARCHIVED_METADATA_ONLY")
        if r["rights"]!="archived_metadata_review" or not media or not media["archival"]:
            errors.append("media_rights_unproven_for_requested_purpose")
    elif r["rights"]!="synthetic_fixture_review":
        errors.append("offline_structured_purpose_required")
    row = {"state":"RETURNED" if errors else "ACCEPTED_OFFLINE", "source_tool":tool,"source_scope":declared_scope,
            "source_identity":subject,"source_revision":_revision(result),"source":pin,
            "template_required_fields":list(fields),"rights_review":media,"submission":r,
            "reasons":errors,"actual_executable_release":False}
    if role == "ad-buyer" and not errors:
        row["ad_source_readback"] = _ad_source_readback(result)
    return row


def bind(site,p,load,history,live_history):
    at=validate(site,p)
    existing=history(site,p["batch_id"])
    if p["version"]<max((r["version"] for r in existing),default=0):
        raise WorkflowError("已有同一评审批次新版，旧回执只可查看。")
    pins={}
    prior=None
    if p["prior_review_task_id"]:
        prior,pins["previous"]=load(p["prior_review_task_id"],site,TOOL)
        if ((prior["batch_id"],prior["version"]+1)!=(p["batch_id"],p["version"])
                or stamp(prior["evaluated_at"])>=at or prior["owner"]!=p["owner"] or prior["deadline"]!=p["deadline"]):
            raise WorkflowError("前序审批必须属于相同站点、批次和连续版本。")
    used=set()
    result={}
    for role,sub in p["submissions"].items():
        if prior and prior["entries"][role]["state"]=="ACCEPTED_OFFLINE" and prior["entries"][role]["submission"]!=sub:
            raise WorkflowError("只可修订已退回项；已接收的岗位原件、责任范围须保持。")
        packet=None
        if sub:
            if sub["source_task_id"] in used:
                raise WorkflowError("不能用同一源任务冒充两个岗位的交付。")
            used.add(sub["source_task_id"])
            packet=load(sub["source_task_id"],site,SPECS[role][0])
            pins[role]=packet[1]
        row=_entry(site,role,sub,packet,at,live_history)
        if prior and prior["entries"][role]["state"]=="RETURNED" and prior["entries"][role]["submission"]:
            old=prior["entries"][role]
            fields=("owner","recipient","correction_due")
            if (not sub or any(sub[k]!=old["submission"][k] for k in fields) or
                    row["source_identity"]!=old["source_identity"]):
                raise WorkflowError("退改须保留原业务主体、接收岗、责任人和期限。")
        result[role]=row
    return pins,result


def approval(site,p,plan,plan_ref,load,history):
    r,pin=load(p["lead_review_task_id"],site,TOOL)
    newest=max((x["version"] for x in history(site,r["batch_id"])),default=0)
    e=r["entries"]["live-ops"]
    at=stamp(p["evaluated_at"])
    if (r["version"]!=newest or r["state"]!="APPROVED_OFFLINE" or e["state"]!="ACCEPTED_OFFLINE"
            or not stamp(r["evaluated_at"])<=at<stamp(r["deadline"])
            or e["source"]!=plan_ref or e["source_revision"]!=plan["version"]
            or e["source_identity"]!=plan["identity"]["session_id"] or e["submission"]["recipient"]!="live-ops"):
        raise WorkflowError("仅消费本站未过期的最新批准修订，且须为当前直播货盘。")
    return r,pin


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","site","tool_id","batch_id","version","state","upstream","input_sha256")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("请引用原工作台的可核验交付件。")
    if tool == OPERATING_TOOL:
        return run_operating(site,p,folder,linked)
    pins,entries=linked
    returned={role:{"owner":(e["submission"] or {}).get("owner") or p["owner"],
                    "due_at":(e["submission"] or {}).get("correction_due") or p["deadline"],"reasons":e["reasons"]}
              for role,e in entries.items() if e["state"]=="RETURNED"}
    r={**{k:p[k] for k in ("site","scope","currency","job_id","batch_id","version","owner","evaluated_at","deadline",
                          "prior_review_task_id")},
       "task_id":folder.name,"tool_id":tool,"upstream":pins,"entries":entries,"return_actions":returned,
       "state":"RETURNED" if returned else "APPROVED_OFFLINE","input_sha256":sha256(encode(p)).hexdigest(),
       "approved_role_count":len(entries)-len(returned),"single_product_corroboration":False,
       "execution_authorized":False,"model_called":False,"provider_called":False}
    raw=encode(r)
    report=(f"# {site} · {p['batch_id']} v{p['version']} · {r['state']}\n\n"
            "仅本机合成档案评审。不同岗位对象分别核对，历史素材登记只供审阅，不授予新用途。\n\n"+raw.decode()).encode()
    ad = entries["ad-buyer"].get("ad_source_readback")
    if ad is not None:
        unknown = lambda value: "未知" if value is None else str(value)
        lines = [f"# {site} | {p['batch_id']} v{p['version']} | {r['state']}", "",
                 "## 投放原件收件", "",
                 "以下为已核哈希的合成原件。收件保留原口径和未知值，未重新计算或执行投放。", "",
                 f"账户 {ad['account_id']}　计划 {ad['plan_id']}　币种 {ad['currency']}　时区 {ad['timezone']}", "",
                 "| 窗口 | 起止 | 采集时间 | 财务成熟度 | 成熟度口径 |",
                 "|---|---|---|---|---|"]
        for window in ad["windows"]:
            lines.append(f"| {window['source_id']} | {window['start']} 至 {window['end']} | "
                         f"{window['captured_at']} | {unknown(window['finance_maturity'])} | "
                         f"{unknown(window['measurement_basis']['maturity_id'])} |")
        lines += ["", "## 财务与商城观察", "",
                  f"- 成熟财务合计（最小货币单位）：{unknown(ad['financial_ledger']['mature_financial_rollup'])}",
                  f"- 独立商城去重合计（最小货币单位）：{unknown(ad['independent_shop_observation']['shop_independent_window_total_minor'])}",
                  f"- 因果增量（最小货币单位）：{unknown(ad['causal_evidence']['causal_increment_minor'])}", ""]
        report = ("\n".join(lines) + "\n" + raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,b in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:
            f.write(b)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    verify_receipt = operating_receipt if r["tool_id"] == OPERATING_TOOL else _receipt
    if (task["local_files"]!=FILES or r!=json.loads(files["json"]) or r["upstream"]!=task["source"]["role_packet_pins"]
            or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
            or json.loads(files["receipt"])!=verify_receipt(r,files["json"],files["report"])):
        raise WorkflowError("九岗收件原件、审批或取件回执不一致。")


def public_tools():
    return [{"id":TOOL,"sites":list(ROUTES),"role_suffix":"lead","label":"九岗交付审阅与同编号退改",
             "description":"对照本站岗位原件和版本，只签收可核验的本机合成范围。","input_example":{},"max_media_files":0}, operating_public_tool()]


OPERATING_TOOL = "lead-operating-evidence-receipt"
TOOLS = (TOOL, OPERATING_TOOL)
_OPERATING_SOURCES = {
    "AD-08": ("ad-offline-brand-measurement", "job_id", "as_of",
               ("campaign_id", "account_id", "objective", "period", "start", "end", "as_of",
                "approval_evidence", "brand_approval_scope_matches_fixture", "brand_budget_minor",
                "brand_spend_minor", "effect_spend_minor_separate", "impressions", "deduplicated_reach",
                "frequency", "calculated_cpm_minor", "reported_cpm_minor", "reported_cpm_same_basis",
                "cpm_source_conflict", "brand_increment", "brand_increment_roi", "room_gmv_observation_only")),
    "SHOP-07": ("shop-funnel-alignment", "job_id", "cutoff",
                ("target", "cutoff", "views", "reconciled", "excluded", "different_views_summed")),
    "PH-CAM-09": ("ph-campaign-six-stage-review", "package_id", "as_of",
                  ("campaign_id", "sku_id", "revision", "ordered_stages", "pending_stages",
                   "platform_side", "own_execution_side", "totals", "settlement_source", "cash_source",
                   "cash_is_bank_receipt", "playbook_candidate")),
    "PH-L09": ("ph-campaign-cod-readiness", "packet_id", "as_of",
               ("campaign_id", "sku_id", "revision", "starts_at", "ends_at", "review_due",
                "cod_monitor", "cash", "warehouse_funding_source", "inventory_source", "trigger_actions")),
}


def operating_example(site):
    return {"scope": SYNTHETIC, "job_id": f"SYN-{site}-LEAD-EVIDENCE-RECEIPT",
            "item": "SHOP-07", "reviewer": f"SYN-{site}-LEAD",
            "evaluated_at": "2026-12-13T12:00:00+08:00",
            "source_task_id": "0"*32, "source_subject": f"SYN-{site}-SHOP",
            "source_revision": None,
            "expected_sha256": {"result_sha256":"0"*64, "receipt_sha256":"0"*64}}


def bind_operating(site, p, load):
    check(p, " ".join(operating_example(site)), "offline operating evidence receipt")
    item = p["item"]
    if p["scope"] != SYNTHETIC or item not in _OPERATING_SOURCES or (item in {"PH-CAM-09", "PH-L09"} and site != "PH"):
        raise WorkflowError("Only same-site offline synthetic evidence may be received here.")
    identify(p["job_id"], "receipt job ID")
    task_id(p["source_task_id"], "source task")
    term(p["source_subject"], "original evidence reference")
    identify(p["reviewer"], "synthetic lead identity", rf"SYN-{site}-LEAD(?:-[A-Za-z0-9_-]{{1,40}})?")
    amount(p["source_revision"], "source revision", 1, 10**6)
    expected = p["expected_sha256"]
    check(expected, "result_sha256 receipt_sha256", "original result and receipt checksums")
    for value in expected.values():
        identify(value, "source checksum", r"[a-f0-9]{64}")
    tool, subject, clock, fields = _OPERATING_SOURCES[item]
    source, pin = load(p["source_task_id"], site, tool)
    revision = _revision(source)
    if (source["scope"] != SYNTHETIC or source.get("execution_authorized") is not False
            or source.get(subject) != p["source_subject"] or revision != p["source_revision"]
            or any(pin[k] != value for k, value in expected.items())):
        raise WorkflowError("Source subject, revision, scope or checksum does not match.")
    if stamp(source[clock]) > stamp(p["evaluated_at"]):
        raise WorkflowError("Evidence cannot be received before the source cutoff.")
    evidence = {k: source[k] for k in fields}
    outcome = "SYNTHETIC_EVIDENCE_RECEIVED"
    if item == "AD-08":
        if evidence["brand_increment"] is not None or evidence["brand_increment_roi"] is not None:
            raise WorkflowError("Synthetic brand custody cannot record causal increment.")
        outcome = ("SYNTHETIC_BRAND_MEASUREMENT_RECEIVED" if source["state"] == "BRAND_MEASUREMENT_RECORDED"
                   else "SYNTHETIC_BRAND_MEASUREMENT_RECEIVED_WITH_GAPS")
    if item == "SHOP-07" and source["state"] != "ALIGNED_FIXTURE":
        outcome = "SYNTHETIC_EVIDENCE_RECEIVED_WITH_GAPS"
    if item == "PH-CAM-09":
        candidate = evidence["playbook_candidate"]
        if candidate["published"] or candidate["adopted"] or candidate["state"] != "LOCAL_DRAFT_AWAITING_REVIEW":
            raise WorkflowError("This receipt only carries unapproved PH playbook candidates.")
        outcome = "PLAYBOOK_CANDIDATE_RECEIVED_AWAITING_REVIEW"
    if item == "PH-L09":
        if evidence["cash"]["actual_bank_receipt_minor"] is not None or evidence["cash"]["payout_already_spendable"]:
            raise WorkflowError("This synthetic receipt cannot carry a bank receipt or spendable payout.")
        outcome = "SYNTHETIC_COD_PACKET_RECEIVED"
    return {p["source_task_id"]:pin}, {"state": outcome, "source_state": source["state"],
             "source_tool": tool, "evidence": evidence, "source_version_basis": "revision_and_checksum"
             if revision is not None else "immutable_result_checksum"}


def run_operating(site, p, folder, linked):
    pins, received = linked
    result = {**p, **received, "site":site, "currency":ROUTES[site][1],
              "task_id":folder.name, "tool_id":OPERATING_TOOL, "upstream":pins,
              "input_sha256":sha256(encode(p)).hexdigest(), "recipient_role": "lead",
              "human_signoff_verified":False, "real_business_verified":False,
              "actual_attribution_verified":False, "execution_authorized":False,
              "model_called":False, "provider_called":False}
    raw = encode(result)
    report = (f"# {site} | {p['item']} | {received['state']}\n\n"
              f"Offline synthetic receipt by test lead {p['reviewer']}. Source task: {p['source_task_id']}.\n"
              "No human sign-off, business attribution, action approval, or bank receipt is established.\n"
              "The sections below preserve the original measurements and any unknown values.\n\n").encode() + raw
    paths = {k:folder/n for k,n in FILES.items()}
    for key, content in {"json":raw, "report":report, "receipt":encode(operating_receipt(result, raw, report))}.items():
        with paths[key].open("xb") as handle:
            handle.write(content)
    return result, paths


def operating_receipt(result, raw, report):
    keys = ("task_id", "site", "tool_id", "item", "job_id", "reviewer", "recipient_role", "state",
            "source_subject", "source_revision", "source_version_basis", "upstream", "input_sha256",
            "scope", "human_signoff_verified", "real_business_verified", "actual_attribution_verified",
            "execution_authorized")
    return {**{k:result[k] for k in keys},
            "result_sha256":sha256(raw).hexdigest(), "report_sha256":sha256(report).hexdigest()}


def operating_public_tool():
    return {"id":OPERATING_TOOL, "sites":list(ROUTES), "role_suffix":"lead",
            "label":"负责人：接收经营证据（本机测试）",
            "description":"独立领取同站商城漏斗、菲律宾活动复盘候选或 COD 检查原版，保留出处及待核项。",
            "input_example":operating_example("CN"),
            "input_examples":{site:operating_example(site) for site in ROUTES}, "max_media_files":0}

