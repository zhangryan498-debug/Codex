"""Synthetic China campaign price review and same-workbench shop intake."""
from copy import deepcopy
from hashlib import sha256
import json
from zoneinfo import ZoneInfo

from role_tools import RoleToolError, _validate_json
from workflow_ph_campaign_offer import RULES, SCOPE, TASK_ID, WorkflowError, _fields, _id, _module, _version

REVIEW = "cn-campaign-offer-review"
INTAKE = "cn-campaign-shop-intake"
RECEIVER = "douyin-shop-ops"
SENDER = "douyin-campaign-ops"
ZONE = "Asia/Shanghai"
SCHEMA = "cn-campaign-shop-synthetic/1.0"
OUTPUTS = {"json":"cn-campaign-result.json","receipt":"cn-campaign-receipt.json"}
IDENTITY = {"platform":"douyin","country":"CN","currency":"CNY","floor_scope":"each_sku",
            "floor_basis":"buyer_goods_per_unit"}
AUTH = {"scope":SCOPE,"release_status":"not_authorized","price_handoff_accepted":False,
        "business_execution_authorized":False,"current_price_eligible_for_business":False,
        "shop_api_called":False,"model_called":False,"receiver_model_status":"not_run"}
SUBJECT = ("campaign_id","contract_id","contract_version","sku","revision","scenario","receiver_role","price_evidence_version")
TEMPS = {"valid_from_inclusive","valid_until_exclusive"}
COUPON_ID = {"coupon_id","version"}


def _window(gate, doc, key, now, errors, unknowns, windows):
    vals=[]
    for field in ("valid_from_inclusive","valid_until_exclusive"):
        value=doc.get(field)
        if value is None:
            unknowns.append(f"{key}.{field}")
            vals.append(None)
        else:
            vals.append(gate.timestamp(value, ZoneInfo(ZONE)))
    start,end=vals
    if ((start is not None and now<start) or (end is not None and now>=end)
            or (start is not None and end is not None and start>=end)):
        errors.append("OUTSIDE_WINDOW:"+key)
    elif start is not None and end is not None:
        windows.append((start,end))


def _pair(ids):
    if not isinstance(ids,list) or not 1<=len(ids)<=2:
        raise WorkflowError("券标识须有一张或两张。")
    pairs=[]
    for code in ids:
        _fields(code,COUPON_ID)
        if not _id(code["coupon_id"]) or not _version(code["version"]):
            raise WorkflowError("券标识或版本无效。")
        pairs.append((code["coupon_id"],code["version"]))
    if len(set(pairs))!=len(pairs) or len({k for k,_ in pairs})!=len(pairs):
        raise WorkflowError("券 ID 不可重复。")
    return sorted(pairs)


def validate_intake(payload):
    _fields(payload,set(SUBJECT)|{"upstream_task_id","coupon_refs","evaluation_time","actual_approval"})
    if (not isinstance(payload["upstream_task_id"],str) or not TASK_ID.fullmatch(payload["upstream_task_id"])
            or not all(_id(payload[k]) for k in ("campaign_id","contract_id","sku","receiver_role"))
            or not all(_version(payload[k]) for k in ("contract_version","revision","price_evidence_version"))
            or payload["scenario"] not in ("current","counterfactual") or type(payload["actual_approval"]) is not bool
            or not isinstance(payload["evaluation_time"],str) or not payload["evaluation_time"].strip()):
        raise WorkflowError("活动、券、接收对象或审批字段无效。")
    _pair(payload["coupon_refs"])


def _approve(payload,project,evaluation=None):
    _fields(payload,{"scope","campaign_id","contract","source","price_evidence","coupons","evaluation_time"})
    _validate_json(payload)
    c,source,evidence,docs=(payload[key] for key in ("contract","source","price_evidence","coupons"))
    _fields(c,{"contract_id","version","receiver_role","scenario","actual_approval","stacking"}|TEMPS)
    _fields(evidence,{"source_id","version","floor_inclusive"}|TEMPS)
    _fields(source,set(IDENTITY)|{"sku","revision","fixture_type","scope","release_status","quantity","cost_unit"},
            {"floor_cents","amounts"})
    if (payload["scope"]!=SCOPE or not _id(payload["campaign_id"])
            or not _id(c["contract_id"]) or not _version(c["version"]) or not _id(c["receiver_role"])
            or c["scenario"] not in ("current","counterfactual") or type(c["actual_approval"]) is not bool
            or not _id(evidence["source_id"]) or not _version(evidence["version"])
            or any(source[k]!=v for k,v in IDENTITY.items()) or not _id(source["sku"]) or not _version(source["revision"])
            or type(source["quantity"]) is not int or source["quantity"]!=1
            or source["scope"]!=SCOPE or source["fixture_type"]!=SCOPE or source["release_status"]!="not_authorized"
            or source["cost_unit"]!="order_total_do_not_multiply_by_quantity"):
        raise WorkflowError("只接受明确标记的 CN/CNY 单 SKU 单件合成价审。")
    if not isinstance(docs,list) or not 1<=len(docs)<=2:
        raise WorkflowError("须明确提交一张或两张合成券。")
    for doc in docs:
        _fields(doc,COUPON_ID|{"sku","kind","source_ref","eligible","minimum_goods_cents","discount_cents"}|TEMPS)
        if doc["kind"] not in ("seller","platform") or not _id(doc["source_ref"]) or not _id(doc["sku"]):
            raise WorkflowError("券须有对应 SKU、显式来源与固定类型。")
    refs=[{key:d[key] for key in COUPON_ID} for d in docs]
    chosen=_pair(refs)
    if len({doc["kind"] for doc in docs})!=len(docs):
        raise WorkflowError("本合同仅支持一张卖家券和一张平台券。")
    price,gate=(_module(project,name) for name in RULES)
    when=payload["evaluation_time"] if evaluation is None else evaluation
    now=gate.timestamp(when,ZoneInfo(ZONE))
    started=gate.timestamp(payload["evaluation_time"],ZoneInfo(ZONE))
    ledger=price.compute_ledger(source)
    errors,unknowns,windows=list(ledger["errors"]),list(ledger["unknowns"]),[]
    if now<started:errors.append("INTAKE_BEFORE_REVIEW")
    if c["receiver_role"]!=RECEIVER:errors.append("RECIPIENT_NOT_IN_SYNTHETIC_CONTRACT")
    if c["actual_approval"]:errors.append("ACTUAL_APPROVAL_OUTSIDE_SYNTHETIC_SCOPE")
    _window(gate,c,"campaign",now,errors,unknowns,windows)
    _window(gate,evidence,"price_evidence",now,errors,unknowns,windows)
    amounts=source.get("amounts")
    amounts=amounts if isinstance(amounts,dict) else {}
    by_kind={doc["kind"]:doc for doc in docs}
    for kind,field in (("seller","merchant_coupon_cents"),("platform","platform_coupon_cents")):
        declared=amounts.get(field)
        doc=by_kind.get(kind)
        if doc is None:
            if type(declared) is int and declared:
                errors.append("UNBOUND_DISCOUNT:"+kind)
            continue
        if doc["sku"]!=source["sku"]:errors.append("COUPON_SKU_MISMATCH:"+kind)
        _window(gate,doc,"coupon:"+kind,now,errors,unknowns,windows)
        for fieldname in ("eligible","discount_cents","minimum_goods_cents"):
            value=doc[fieldname]
            if value is None:unknowns.append(f"coupon.{kind}.{fieldname}")
            elif fieldname=="eligible":
                if value is not True:errors.append("COUPON_NOT_ELIGIBLE:"+kind)
            elif type(value) is not int or value<0:errors.append("INVALID_COUPON_AMOUNT:"+kind)
        minimum,listed,discount=doc["minimum_goods_cents"],amounts.get("list_cents"),doc["discount_cents"]
        if type(minimum) is int and type(listed) is int and listed<minimum:
            errors.append("COUPON_MINIMUM_NOT_MET:"+kind)
        if type(discount) is int and type(declared) is int and declared!=discount:
            errors.append("COUPON_DISCOUNT_LEDGER_MISMATCH:"+kind)
    stack=c["stacking"]
    if len(docs)==2:
        if stack is None:unknowns.append("stacking")
        else:
            _fields(stack,{"allowed","approval_ref","coupon_refs"}|TEMPS)
            if stack["allowed"] is None:unknowns.append("stacking.allowed")
            elif stack["allowed"] is not True:errors.append("STACKING_NOT_APPROVED")
            if stack["approval_ref"] is None:unknowns.append("stacking.approval_ref")
            elif not _id(stack["approval_ref"]):raise WorkflowError("叠加审批来源无效。")
            if stack["coupon_refs"] is None:unknowns.append("stacking.coupon_refs")
            elif _pair(stack["coupon_refs"])!=chosen:errors.append("STACKING_APPROVAL_COUPONS_MISMATCH")
            _window(gate,stack,"stacking",now,errors,unknowns,windows)
    elif stack is not None:
        errors.append("STACKING_RECORD_REQUIRES_TWO_COUPONS")
    buyer=ledger["numbers"].get("buyer_paid_cents")
    floor=source.get("floor_cents")
    include=evidence["floor_inclusive"]
    if include is None:unknowns.append("price_evidence.floor_inclusive")
    elif type(include) is not bool:raise WorkflowError("等于底价的政策须明确填写。")
    elif not include and buyer is not None and buyer==floor:errors.append("EXCLUSIVE_FLOOR_EQUALITY")
    typed=None
    if not errors and not unknowns:
        keys={key:source[key] for key in gate.SUBJECT_FIELDS}
        raw={**keys,"campaign":payload}
        calc={**keys,"method":"deterministic_calculation","producer":price.VERSION,"input_sha256":gate.digest(raw),
              "rule_versions":RULES,"lower_bound_cents":buyer,"lower_bound_basis":source["floor_basis"],
              "seller_cost_complete":ledger["computed_seller_cost_complete"],
              "fees_complete":ledger["computed_fee_inputs_complete"],"unknowns":[],"conflicts":[]}
        a={**calc,"schema_version":"1.1","timezone":ZONE,"valid_from_inclusive":max(x[0] for x in windows).isoformat(),
           "valid_until_exclusive":min(x[1] for x in windows).isoformat(),"floor_cents":floor,"floor_inclusive":include,
           "lower_bound_verified":True,"calculation_sha256":gate.digest(calc),"evidence_scope":SCOPE}
        packet={"packet_id":c["contract_id"],"receiver_role":RECEIVER,"authority_sha256":gate.digest(a),
                "input_sha256":gate.digest(raw),"payload":deepcopy(a)}
        typed=gate.validate(a,packet,RECEIVER,when,raw,calc)
        typed.pop("system_receipt",None)
        if typed["field_gate"]!="pass":errors.append("TYPED_PRICE_GATE_REJECTED")
    disposition="REJECT" if errors else "PENDING" if unknowns else "PASS"
    return {"site":"CN","currency":"CNY","contract_kind":"synthetic_test_only","disposition":disposition,
            "errors":sorted(set(errors)),"unknowns":sorted(set(unknowns)),"evaluation_time":when,
            "campaign_id":payload["campaign_id"],"contract_id":c["contract_id"],"contract_version":c["version"],
            "receiver_role":c["receiver_role"],"scenario":c["scenario"],"sku":source["sku"],"revision":source["revision"],
            "coupon_refs":refs,"price_evidence_version":evidence["version"],
            "price_source_sha256":gate.digest(payload),"rule_sha256":RULES,"host_calculation":ledger,
            "typed_field_gate":typed if disposition=="PASS" else None,"local_receipt_only":True}


def receipt_for(result,content):
    return {**AUTH,"schema":SCHEMA,"site":"CN","tool_id":result["tool_id"],"task_id":result["task_id"],
            "disposition":result["disposition"],"scenario":result["scenario"],"receipt_stage":result["receipt_stage"],
            "result_sha256":sha256(content).hexdigest(),"price_source_sha256":result["price_source_sha256"],
            "upstream":result.get("upstream"),"receiver_role":RECEIVER,"local_receipt_only":True}


def run_workflow(project,site,tool_id,payload,task_dir,assets,progress,*,upstream=None):
    if site!="CN" or assets or tool_id not in (REVIEW,INTAKE):
        raise WorkflowError("本工具仅接受抖音本机合成价审或商城收件。")
    try:
        if tool_id==REVIEW:
            result=_approve(payload,project)
            result.update(role=SENDER,receipt_stage="synthetic_campaign_review")
        else:
            validate_intake(payload)
            if upstream is None or payload["upstream_task_id"]!=upstream[0]["task_id"]:
                raise WorkflowError("商城收件缺已验证的本站上游任务。")
            snapshot,original,prior=upstream
            result=_approve(original,project,payload["evaluation_time"])
            for k in SUBJECT:
                if payload[k]!=result[k]:result["errors"].append("RECEIVER_BINDING_MISMATCH:"+k)
            if _pair(payload["coupon_refs"])!=_pair(result["coupon_refs"]):
                result["errors"].append("RECEIVER_COUPONS_MISMATCH")
            if payload["actual_approval"]:result["errors"].append("ACTUAL_APPROVAL_OUTSIDE_SYNTHETIC_SCOPE")
            if result["errors"] or prior["disposition"]=="REJECT":result["disposition"]="REJECT"
            elif result["unknowns"] or prior["disposition"]=="PENDING":result["disposition"]="PENDING"
            if result["disposition"]!="PASS":result["typed_field_gate"]=None
            result.update(role=RECEIVER,receipt_stage="synthetic_shop_intake",upstream=snapshot,
                          upstream_disposition=prior["disposition"],requested_receiver=payload["receiver_role"])
        result.update(AUTH,schema=SCHEMA,task_id=task_dir.name,tool_id=tool_id)
        output={k:task_dir/v for k,v in OUTPUTS.items()}
        content=(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")
        with output["json"].open("xb") as f:f.write(content)
        with output["receipt"].open("x",encoding="utf-8",newline="\n") as f:
            json.dump(receipt_for(result,content),f,ensure_ascii=False,indent=2)
            f.write("\n")
        return result,output
    except (ValueError,OSError,KeyError,TypeError) as error:
        if isinstance(error,WorkflowError):raise
        raise WorkflowError("合成活动引用、金额、时区或来源未通过校验。") from error


def public_tools():
    return [
        {"id":REVIEW,"role_suffix":"campaign-ops","sites":["CN"],"label":"抖音活动底价与叠券离线复核",
         "description":"仅合成单 SKU、单件及明确独立时窗，生成活动价审证据。","input_example":{}},
        {"id":INTAKE,"role_suffix":"shop-ops","sites":["CN"],"label":"抖音商城接收活动价审",
         "description":"绑定本站活动价审、券版本和接收者后重算。仅本机合成签收。","input_example":{}},
    ]
