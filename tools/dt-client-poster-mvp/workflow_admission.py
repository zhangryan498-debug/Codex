"""Station-isolated synthetic admission checks. Results cannot authorize external actions."""
from hashlib import sha256
import json

from role_tools import ROUTES,SYNTHETIC
from workflow_lead_weekly import encode,ratio,term
from workflow_live_rehearsal import amount,check,identify,stamp

AD="ad-offline-admission-review"
CERT="ph-selection-offline-certification"
RULE_SOURCE=".agents/skills/tiktok-selection-playbook/SKILL.md"
TOOLS=(AD,CERT)
FILES={"json":"admission.json","report":"admission.md","receipt":"admission-receipt.json"}
COMMON="site scope currency job_id owner as_of"
IDENTITY=("account_id","shop_id","sku_id","asset_id","room_id","anchor_id","certification_id")
BUCKETS=("base","reserve","brand")
ZONES={"CN":"Asia/Shanghai","PH":"Asia/Manila"}


class WorkflowError(ValueError):
    pass


def subject(site,p):
    if (p["site"],p["scope"],p["currency"])!=(site,SYNTHETIC,ROUTES[site][1]):
        raise WorkflowError("只验同站本币、明确标记的合成输入。")
    identify(p["job_id"],"任务号");term(p["owner"],"负责人")
    return stamp(p["as_of"])


def check_interval(from_at,until):
    start,end=stamp(from_at),stamp(until)
    if start>=end:
        raise WorkflowError("生效窗口须有先后。")
    return start,end


def _permissions(p,start,end,required):
    ids=p["identity"]
    check(ids," ".join(IDENTITY)+" sku_revision","账号、商品、素材和场次身份")
    for k in IDENTITY:
        if ids[k] is not None:identify(ids[k],k)
    amount(ids["sku_revision"],"商品规格修订",1,10**6,False)
    perms=p["permissions"]
    if not isinstance(perms,list) or len(perms)>20:
        raise WorkflowError("合成授权列表过长。")
    evidence_by_kind={}
    for item in perms:
        check(item,"kind site account_id shop_id sku_revision target issued_at from_at until status source_ref","授权证据")
        # The result retains every record even when it is inapplicable.
        kind=item["kind"]
        if kind not in required or kind in evidence_by_kind or item["status"] not in ("fixture_allowed","fixture_denied"):
            raise WorkflowError("授权类别须属于当前场景并且各自唯一。")
        for k in ("account_id","shop_id","target"):identify(item[k],k)
        amount(item["sku_revision"],"授权规格修订",1,10**6,False)
        term(item["source_ref"],"合成授权出处")
        if stamp(item["issued_at"])>stamp(p["as_of"]):raise WorkflowError("授权记录晚于本次评估。")
        check_interval(item["from_at"],item["until"])
        evidence_by_kind[kind]=item
    verified={}
    for kind in required:
        key=kind
        item=evidence_by_kind.get(kind)
        state="PENDING_EVIDENCE"
        if item:
            begin,expire=check_interval(item["from_at"],item["until"])
            exact=((item["site"],item["account_id"],item["shop_id"],item["sku_revision"],item["target"])==
                   (p["site"],ids["account_id"],ids["shop_id"],ids["sku_revision"],ids[key]))
            if not exact:
                state="IDENTITY_CONFLICT"
            elif not begin<=start<end<=expire:
                state="EXPIRED_OR_OUTSIDE_WINDOW"
            elif item["status"]=="fixture_denied":
                state="DENIED_SYNTHETIC_SCOPE"
            else:
                state="MATCHED_SYNTHETIC_SCOPE"
        verified[kind]={"state":state,"target":ids[key],"record":item,"actual_permission_verified":False}
    return ids,verified


def _budget(p,start,end,at):
    body=p["budget"]
    check(body,"total_cap_minor allocations approval","基础、机动和品广预算")
    amount(body["total_cap_minor"],"独立总限额",0,10**12)
    check(body["allocations"]," ".join(BUCKETS),"三路合成账本")
    misses=[];hard=[];buckets={}
    approval=body["approval"]
    approved={}
    if approval is not None:
        check(approval,"site currency account_id shop_id from_at until issued_at caps source_ref","独立合成预算批准")
        term(approval["source_ref"],"合成预算审批来源")
        if ((approval["site"],approval["currency"],approval["account_id"],approval["shop_id"])!=
                (p["site"],p["currency"],p["identity"]["account_id"],p["identity"]["shop_id"])):
            hard.append("budget_approval_identity_mismatch")
        else:
            a,b=check_interval(approval["from_at"],approval["until"])
            if not stamp(approval["issued_at"])<=at or not a<=start<end<=b:
                misses.append("budget_approval_outside_window")
            else:
                approved=approval["caps"]
                check(approved," ".join(BUCKETS),"独立分项预算限额")
                for k,n in approved.items():amount(n,k,0,10**12)
    else:
        misses.append("budget_approval_missing")
    total=0
    lower=0
    for k in BUCKETS:
        row=body["allocations"][k]
        check(row,"allocated_minor spent_minor committed_not_posted_minor","每桶同币用量")
        for n in row.values():amount(n,"分项金额",0,10**12)
        limit,spent,committed=(row[n] for n in ("allocated_minor","spent_minor","committed_not_posted_minor"))
        cap=approved.get(k)
        if limit is None or spent is None or committed is None or cap is None:misses.append(k+"_unknown_budget")
        exposure=spent+committed if None not in (spent,committed) else None
        measured=sum(n for n in (spent,committed) if n is not None)
        if any(measured>n for n in (limit,cap) if n is not None):
            hard.append(k+"_spent_and_committed_exceed_cap")
        if limit is not None:
            total+=limit
            if cap is not None and limit>cap:hard.append(k+"_allocation_exceeds_approved_cap")
        if exposure is not None:lower+=exposure
        else:lower+=measured
        buckets[k]={**row,"exposure_minor":exposure,"approved_fixture_cap_minor":cap,
                    "bucket_remaining_minor":min(limit,cap)-exposure if None not in (limit,cap,exposure) else None}
    cap=body["total_cap_minor"]
    if cap is None:misses.append("total_cap_missing")
    elif total>cap or lower>cap:hard.append("declared_allocations_or_exposure_above_total")
    return {"allocation_total_minor":total if all(r["allocated_minor"] is not None for r in buckets.values()) else None,
            "allocation_lower_bound_minor":total,"exposure_lower_bound_minor":lower,
            "buckets":buckets,"total_cap_minor":cap,"approval":approval,"can_borrow_between_buckets":False},hard,misses


def _ph_delivery(p,at):
    row=p["ph_delivery"]
    if row is None:
        return {"state":"PENDING_EVIDENCE","signed_rate":None,"refused_rate":None,"actual_realtime_verified":False}
    check(row,"site shop_id sku_id revision observed_at valid_until source_ref assigned signed refused in_transit other_terminal",
          "合成签收与拒收观察")
    ids=p["identity"]
    if (row["site"],row["shop_id"],row["sku_id"],row["revision"])!=("PH",ids["shop_id"],ids["sku_id"],ids["sku_revision"]):
        raise WorkflowError("签收记录必须同一 PH 店铺和 SKU 修订。")
    term(row["source_ref"],"履约采集来源")
    start,end=check_interval(row["observed_at"],row["valid_until"])
    keys=("assigned","signed","refused","in_transit","other_terminal")
    for k in keys:amount(row[k],k,0,10**12)
    n,signed,refused,in_transit,other=(row[k] for k in keys)
    current=start<=at<end
    complete=None not in (n,signed,refused,in_transit,other)
    conflict=complete and signed+refused+in_transit+other!=n
    ready=bool(current and complete and not conflict and n and in_transit==0)
    return {"state":"OBSERVED_SYNTHETIC_CLOSED" if ready else "SOURCE_CONFLICT" if conflict and current else "PENDING_EVIDENCE",
            "raw":row,"signed_rate":ratio(signed,n) if ready else None,"refused_rate":ratio(refused,n) if ready else None,
            "actual_realtime_verified":False}


def bind_ad(site,p):
    extras="timezone window_start window_end channel identity permissions objective roi_target_bp roi_floor_bp target_roi_basis floor_roi_basis budget ph_delivery"
    check(p,COMMON+" "+extras,"广告前置准入")
    at=subject(site,p)
    start,end=check_interval(p["window_start"],p["window_end"])
    if at>start or p["timezone"]!=ZONES[site]:
        raise WorkflowError("评估在排期前，按本站时区填写。")
    if p["objective"] not in ("product_trial","shop_conversion","live_conversion"):
        raise WorkflowError("明确本站广告目标。")
    if p["channel"] not in ("product_card","short_video","live"):
        raise WorkflowError("推广入口须为商品卡、视频或直播。")
    if (p["objective"]=="live_conversion")!=(p["channel"]=="live"):
        raise WorkflowError("直播目标必须与直播场景对应。")
    required=["account_id","shop_id","sku_id","asset_id","anchor_id"]
    if p["channel"]=="live":required.append("room_id")
    if site=="PH":required.append("certification_id")
    ids,verification=_permissions(p,start,end,required)
    budgets,hard,misses=_budget(p,start,end,at)
    for k in ("roi_target_bp","roi_floor_bp"):amount(p[k],k,0,10**9)
    target,floor=p["roi_target_bp"],p["roi_floor_bp"]
    basis=(p["target_roi_basis"],p["floor_roi_basis"])
    allowed=("buyer_gmv_over_spend","merchant_net_over_spend",None)
    if any(v not in allowed for v in basis):raise WorkflowError("ROI 分子和分母口径应有明确说明。")
    roi=("PENDING_EVIDENCE" if target is None or floor is None or None in basis or basis[0]!=basis[1]
         else "BELOW_FIXTURE_FLOOR" if target<floor else "MEETS_FIXTURE_FLOOR")
    if roi=="BELOW_FIXTURE_FLOOR":hard.append("roi_below_fixture_floor")
    if roi=="PENDING_EVIDENCE":misses.append("roi_unknown")
    if site=="CN" and p["ph_delivery"] is not None:
        raise WorkflowError("菲律宾履约观察不适用于抖音报表。")
    ph=_ph_delivery(p,at) if site=="PH" else None
    if ph and ph["state"]=="SOURCE_CONFLICT":hard.append("ph_delivery_source_conflict")
    elif ph and ph["state"]!="OBSERVED_SYNTHETIC_CLOSED":misses.append("ph_delivery_unresolved")
    for key,row in verification.items():
        if row["state"] in ("IDENTITY_CONFLICT","DENIED_SYNTHETIC_SCOPE"):
            hard.append(key+"_"+row["state"].lower())
        elif row["state"]!="MATCHED_SYNTHETIC_SCOPE":
            misses.append(key+"_"+row["state"].lower())
    status="BLOCKED_SYNTHETIC" if hard else "PENDING_EVIDENCE" if misses else "SYNTHETIC_GATE_CHECKED"
    data={"state":status,"identity":ids,"scope_evidence":verification,"block_reasons":sorted(set(hard)),
          "evidence_needed":sorted(set(misses)),"goal":p["objective"],"channel":p["channel"],
          "window":[p["window_start"],p["window_end"]],"timezone":p["timezone"],
          "budget":budgets,"roi":{"state":roi,"target_bp":target,"floor_bp":floor,"target_basis":basis[0],"floor_basis":basis[1]},"ph_fulfillment":ph,
          "actual_platform_state":"PENDING_REAL_PERMISSION","actual_account_rights_verified":False,
          "actual_certified_sku_verified":False,"actual_ad_action_taken":False}
    return {"synthetic_input_sha256":sha256(encode(p)).hexdigest()},data


def _receipt(r,raw,report):
    return {**{k:r[k] for k in ("task_id","tool_id","site","job_id","upstream","input_sha256","state")},
            "result_sha256":sha256(raw).hexdigest(),"report_sha256":sha256(report).hexdigest(),"execution_authorized":False}


def run_workflow(project,site,tool,p,folder,assets,progress,*,linked):
    if assets:
        raise WorkflowError("只接收明确合成的证据字段和本机记录。")
    pins,result=linked
    if tool==CERT:
        local_source=project/RULE_SOURCE
        result={**result,"local_project_requirement_source":{
            "path":RULE_SOURCE,"sha256":sha256(local_source.read_bytes()).hexdigest()},
            "local_source_defines_current_category_documents":False}
    r={**result,**{k:p[k] for k in COMMON.split()},"task_id":folder.name,"tool_id":tool,"upstream":pins,
       "input_sha256":sha256(encode(p)).hexdigest(),"execution_authorized":False,
       "model_called":False,"provider_called":False}
    raw=encode(r)
    report=(f"# {site} · {p['job_id']} · {r['state']}\n\n"
            "合成门禁验收。真实平台权限、资质、投放或调价未经授权。\n\n"+raw.decode()).encode()
    paths={k:folder/n for k,n in FILES.items()}
    for k,v in {"json":raw,"report":report,"receipt":encode(_receipt(r,raw,report))}.items():
        with paths[k].open("xb") as f:f.write(v)
    return r,paths


def verify_saved(task,files):
    r=task["result"]
    if (task["local_files"]!=FILES or json.loads(files["json"])!=r or r["upstream"]!=task["source"]["role_packet_pins"]
        or (r["task_id"],r["site"],r["tool_id"])!=(task["id"],task["site"],task["source"]["tool_id"])
        or json.loads(files["receipt"])!=_receipt(r,files["json"],files["report"])):
        raise WorkflowError("门禁产物或来源回执变化。")


def public_tools():
    return [{"id":AD,"role_suffix":"ad-buyer","sites":list(ROUTES),"label":"广告身份和预算离线准入",
             "description":"隔离账号、素材与独立预算，不连接平台。","input_example":{}},
            {"id":CERT,"role_suffix":"selection","sites":["PH"],"label":"菲律宾选品证照证据与排期",
             "description":"校验合成类目要求、持有人、SKU、证据时间、预算与排期。真实证照仍待核。","input_example":{}}]



def _cert_rows(p,at,starts,ends):
    if not isinstance(p["evidence"],list) or len(p["evidence"])>24:
        raise WorkflowError("必须分项提交有限合成凭据。")
    policy=p["fixture_policy"]
    if policy is None:
        if p["evidence"]:raise WorkflowError("没有合成规则时不能推定证据类型或适用范围。")
        return [],False,[],["frozen_rule_mapping_unavailable"]
    check(policy,"kind rule_id site category source_ref valid_from valid_until execution_sequence requirements","证据映射演练规则")
    if (policy["kind"],policy["site"],policy["category"])!=("synthetic_mapping_only","PH",p["category"]):
        raise WorkflowError("规则仅接受 PH 同类目的明确合成映射。")
    identify(policy["rule_id"],"合成规则号");term(policy["source_ref"],"合成映射出处")
    starts_rule,until_rule=check_interval(policy["valid_from"],policy["valid_until"])
    in_window=starts_rule<=at<=starts<ends<=until_rule
    if policy["execution_sequence"] not in ("parallel_fixture","serial_fixture"):
        raise WorkflowError("排期必须明确按独立并行或串行的合成情境。")
    requirements=policy["requirements"]
    if not isinstance(requirements,list) or not 1<=len(requirements)<=12:
        raise WorkflowError("须逐项提供合成证照及证据范围。")
    need={}
    for r in requirements:
        check(r,"requirement_id applies_to estimated_cost_minor lead_days source_ref","单项合成要求")
        ident=r["requirement_id"];identify(ident,"合成要求号")
        if ident in need or r["applies_to"] not in ("holder","sku"):
            raise WorkflowError("每条要求须唯一并区分主体和商品。")
        amount(r["estimated_cost_minor"],"合成预计成本",0,10**12)
        amount(r["lead_days"],"合成准备天数",0,730)
        term(r["source_ref"],"单项冻结依据")
        need[ident]=r
    evidence={}
    for row in p["evidence"]:
        check(row,"evidence_id requirement_id applies_to holder_id sku_id sku_revision site category review_state "
                  "source_ref observed_at effective_from expires_at","合成证据原件摘要")
        ident=row["requirement_id"];identify(ident,"要求号");identify(row["evidence_id"],"证据号")
        if ident not in need or ident in evidence or row["review_state"] not in ("approved_fixture","denied_fixture","unreviewed"):
            raise WorkflowError("证据须绑定单个已定义要求，并列明合成审阅状态。")
        for k in ("holder_id","sku_id"):
            if row[k] is not None:identify(row[k],k)
        amount(row["sku_revision"],"证据 SKU 修订",1,10**6)
        term(row["source_ref"],"合成证据出处")
        if stamp(row["observed_at"])>at:raise WorkflowError("凭据已声明的采集时间晚于本次验证。")
        check_interval(row["effective_from"],row["expires_at"])
        if any(x["evidence_id"]==row["evidence_id"] for x in evidence.values()):
            raise WorkflowError("同一个证据编号不能同时充当主体和商品两项。")
        evidence[ident]=row
    hard=[];missing=[];records=[]
    if not in_window:missing.append("synthetic_mapping_expired_or_outside")
    for ident,r in need.items():
        row=evidence.get(ident)
        state="MISSING_SYNTHETIC_EVIDENCE"
        if row:
            ref_scope=(row["sku_id"],row["sku_revision"])
            if ((row["site"],row["category"],row["applies_to"],row["holder_id"])!=
                    ("PH",p["category"],r["applies_to"],p["holder_id"])
                    or (r["applies_to"]=="holder" and ref_scope!=(None,None))
                    or (r["applies_to"]=="sku" and ref_scope!=(p["sku_id"],p["sku_revision"]))):
                state="SCOPE_MISMATCH"
            elif not stamp(row["effective_from"])<=starts<ends<=stamp(row["expires_at"]):
                state="EXPIRED_OR_NOT_EFFECTIVE_FOR_WINDOW"
            elif row["review_state"]=="denied_fixture":
                state="DENIED_FIXTURE"
            elif row["review_state"]=="approved_fixture":
                state="MATCHED_SYNTHETIC_EVIDENCE"
            else:
                state="UNCHECKED_FIXTURE"
        if state in ("SCOPE_MISMATCH","DENIED_FIXTURE"):
            hard.append(ident+"_"+state.lower())
        elif state!="MATCHED_SYNTHETIC_EVIDENCE":
            missing.append(ident+"_"+state.lower())
        records.append({**r,"state":state,"evidence":row,"actual_document_validity_verified":False})
    return records,in_window,hard,missing


def bind_cert(site,p):
    fields="category holder_id sku_id sku_revision planned_from planned_until budget_minor fixture_policy evidence"
    check(p,COMMON+" "+fields,"菲律宾选品证照映射演练")
    at=subject(site,p)
    if site!="PH":raise WorkflowError("证照映射入口在本机仅支持菲律宾合成场景。")
    for k in ("category","holder_id","sku_id"):identify(p[k],k)
    amount(p["sku_revision"],"SKU 修订",1,10**6,False)
    amount(p["budget_minor"],"合成证照预算",0,10**12)
    starts,ends=check_interval(p["planned_from"],p["planned_until"])
    if at>starts:raise WorkflowError("先复核证据再到计划时点。")
    records,in_window,hard,missing=_cert_rows(p,at,starts,ends)
    costs=[r["estimated_cost_minor"] for r in records]
    timing=[r["lead_days"] for r in records]
    cost_total=sum(costs) if costs and None not in costs else None
    cost_lower=sum(v for v in costs if v is not None)
    if p["budget_minor"] is None:missing.append("unknown_budget")
    elif cost_lower>p["budget_minor"]:hard.append("known_fixture_cost_exceeds_budget")
    if cost_total is None:missing.append("unknown_cost")
    sequence=(p["fixture_policy"] or {}).get("execution_sequence")
    t=(max(timing) if sequence=="parallel_fixture" else sum(timing)) if timing and None not in timing else None
    if t is None:missing.append("unknown_lead_time")
    elif t*86400>(starts-at).total_seconds():hard.append("fixture_timing_cannot_cover_start")
    status="BLOCKED_SYNTHETIC" if hard else "PENDING_EVIDENCE" if missing else "SYNTHETIC_MAPPING_CHECKED"
    result={"state":status,"category":p["category"],"holder_id":p["holder_id"],"sku_id":p["sku_id"],"sku_revision":p["sku_revision"],
            "fixture_rule":p["fixture_policy"],"fixture_rule_current":in_window,"requirement_evidence":records,
            "known_cost_lower_bound_minor":cost_lower if any(v is not None for v in costs) else None,
            "estimated_total_minor":cost_total,"estimated_lead_days":t,"budget_minor":p["budget_minor"],
            "hard_reasons":sorted(set(hard)),"evidence_needed":sorted(set(missing)),
            "actual_category_requirements_verified":False,"actual_document_validity_verified":False,
            "actual_listing_authorized":False,"actual_trial_authorized":False,
            "actual_platform_state":"PENDING_OFFICIAL_CATEGORY_RULE_AND_DOCUMENTS",
            "listing_and_trial_blocked":True}
    return {"synthetic_input_sha256":sha256(encode(p)).hexdigest()},result

