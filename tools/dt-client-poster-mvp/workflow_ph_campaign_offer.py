"""PH campaign offer review and bound local shop receipt, for synthetic evidence only."""
from copy import deepcopy
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

from role_tools import RoleToolError, _trusted_module, _validate_json
from role_tools_extra import _load

REVIEW = "ph-campaign-offer-review"
INTAKE = "ph-campaign-shop-intake"
RECEIVER = "tiktok-shop-ops"
SENDER = "tiktok-campaign-ops"
SCOPE = "synthetic_offline"
SCHEMA = "ph-campaign-shop-handoff/1.0"
ZONE = "Asia/Manila"
TEST_REVIEW_ACTOR = "PH-CAMPAIGN-QA-REVIEWER"
TEST_RECEIPT_ACTOR = "PH-SHOP-QA-RECEIVER"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
TASK_ID = re.compile(r"[0-9a-f]{32}")
OUTPUTS = {"json": "ph-campaign-result.json", "receipt": "ph-campaign-receipt.json"}
RULES = {
    "strict_price_review": "3866643adc0ada92582f8932d6a6a2825d205cb783186ce91618a31d083367af",
    "campaign_price_handoff": "cfd08fa22fffee70f2db2ee403065226e33f669ea911a26f1a03caeab21d0656",
}
IDENTITY = {"platform": "tiktok", "country": "PH", "currency": "PHP", "floor_scope": "each_sku",
            "floor_basis": "buyer_goods_per_unit"}
AUTHORITY = {"scope": SCOPE, "release_status": "not_authorized",
             "price_handoff_accepted": False, "business_execution_authorized": False,
             "model_called": False, "shop_api_called": False}

class WorkflowError(ValueError):
    """A synthetic input or bound local receipt is malformed."""


def public_tools():
    return [
        {"id": REVIEW, "label": "菲律宾活动券离线价审", "role_suffix": "campaign-ops", "sites": ["PH"],
         "description": "按原始金额及显式模拟券核算单 SKU 单件方案，保存供商城取件的价审凭据。",
         "input_example": {}},
        {"id": INTAKE, "label": "菲律宾商城接收活动价审", "role_suffix": "shop-ops", "sites": ["PH"],
         "description": "读取同一工作台已完成的活动价审任务，复核 SKU、版本、券和有效时段。仅为本机模拟签收。",
         "input_example": {}},
    ]


def _fields(value, required, optional=()):
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - set(optional):
        raise WorkflowError("输入缺必填字段或含未支持字段。")


def _id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _version(value):
    return type(value) is int and 1 <= value <= 10**9


def _module(project, name):
    if sha256((project / "scripts" / (name + ".py")).read_bytes()).hexdigest() != RULES[name]:
        raise WorkflowError("既有价审规则版本已变化，请先复核依赖。")
    return _trusted_module(project, name) if name == "strict_price_review" else _load(project, name)


def _when(gate, value):
    try:
        return gate.timestamp(value, ZoneInfo(ZONE))
    except (ValueError, TypeError) as error:
        raise WorkflowError("须填写符合 Asia/Manila 的显式评估时点与时区偏移。") from error


def validate_intake(payload):
    _fields(payload, {"upstream_task_id", "campaign_id", "sku", "revision", "coupon_id",
                      "coupon_version", "price_evidence_version", "evaluation_time"}, {"test_recipient"})
    if "test_recipient" in payload and payload["test_recipient"] != TEST_RECEIPT_ACTOR:
        raise WorkflowError("Unknown synthetic shop test role.")
    if (not isinstance(payload["upstream_task_id"], str) or not TASK_ID.fullmatch(payload["upstream_task_id"])
            or not all(_id(payload[k]) for k in ("campaign_id", "sku", "coupon_id"))
            or not all(_version(payload[k]) for k in ("revision", "coupon_version", "price_evidence_version"))):
        raise WorkflowError("上游任务号、活动、SKU、券或版本格式错误。")


def _read_review(payload, project, evaluation_time=None):
    _fields(payload, {"scope", "campaign_id", "source", "price_evidence", "coupon", "evaluation_time"}, {"test_approval"})
    _validate_json(payload)
    if payload["scope"] != SCOPE or not _id(payload["campaign_id"]):
        raise WorkflowError("只接收有活动编号的模拟输入。")
    source, evidence, coupon = (payload[key] for key in ("source", "price_evidence", "coupon"))
    _fields(source, set(IDENTITY) | {"sku", "revision", "fixture_type", "scope", "release_status", "quantity", "cost_unit"},
            {"floor_cents", "amounts"})
    if (any(source.get(k) != expected for k, expected in IDENTITY.items())
            or not _id(source.get("sku")) or not _version(source.get("revision"))
            or type(source.get("quantity")) is not int or source["quantity"] != 1
            or source["fixture_type"] != SCOPE or source["scope"] != SCOPE
            or source["release_status"] != "not_authorized"
            or source["cost_unit"] != "order_total_do_not_multiply_by_quantity"):
        raise WorkflowError("只接受 PH/PHP 的合成单 SKU 单件价审，且不包含经营授权。")
    _fields(evidence, {"source_id", "version", "timezone"}, {"valid_from_inclusive", "valid_until_exclusive", "floor_inclusive"})
    _fields(coupon, {"coupon_id", "version", "sku", "kind", "source_ref"},
            {"eligible", "applicable", "minimum_goods_cents", "discount_cents", "valid_from_inclusive", "valid_until_exclusive"})
    if (not all(_id(evidence[k]) for k in ("source_id",)) or not _version(evidence["version"])
            or not all(_id(coupon[k]) for k in ("coupon_id", "sku", "source_ref"))
            or not _version(coupon["version"]) or evidence["timezone"] != ZONE
            or coupon["kind"] != "synthetic_single_seller_fixed_coupon"):
        raise WorkflowError("模拟券、价审来源或时区与受支持的本机合同不匹配。")

    price, gate = (_module(project, name) for name in RULES)
    now = payload["evaluation_time"] if evaluation_time is None else evaluation_time
    now_dt = _when(gate, now)
    initial = _when(gate, payload["evaluation_time"])
    ledger = price.compute_ledger(source)
    errors, unknowns = list(ledger["errors"]), list(ledger["unknowns"])
    amounts = source["amounts"] if type(source.get("amounts")) is dict else {}
    if now_dt < initial:
        errors.append("INTAKE_BEFORE_CAMPAIGN_REVIEW")
    if coupon["sku"] != source["sku"]:
        errors.append("COUPON_SKU_MISMATCH")
    for field in ("eligible", "minimum_goods_cents", "discount_cents"):
        fact = coupon.get(field)
        if fact is None:
            unknowns.append("coupon." + field)
        elif field == "eligible":
            if type(fact) is not bool or not fact:
                errors.append("COUPON_NOT_ELIGIBLE")
        elif type(fact) is not int or fact < 0:
            errors.append("INVALID_COUPON_AMOUNT:" + field)
    minimum, listed = coupon.get("minimum_goods_cents"), amounts.get("list_cents")
    if type(minimum) is int and type(listed) is int and listed < minimum:
        errors.append("COUPON_MINIMUM_NOT_MET")
    discount, merchant = coupon.get("discount_cents"), amounts.get("merchant_coupon_cents")
    if type(discount) is int and type(merchant) is int and discount != merchant:
        errors.append("COUPON_DISCOUNT_LEDGER_MISMATCH")
    for field in ("platform_coupon_cents", "confirmed_platform_reimbursement_cents"):
        if type(amounts.get(field)) is int and amounts[field] != 0:
            errors.append("COMBINED_COUPON_OUTSIDE_SYNTHETIC_SCOPE")
    inclusive = evidence.get("floor_inclusive")
    if inclusive is None:
        unknowns.append("price_evidence.floor_inclusive")
    elif type(inclusive) is not bool:
        errors.append("INVALID_FLOOR_EQUALITY_POLICY")
    buyer, floor = ledger["numbers"].get("buyer_paid_cents"), source.get("floor_cents")
    if inclusive is False and buyer is not None and buyer == floor:
        errors.append("EXCLUSIVE_FLOOR_EQUALITY")

    windows = []
    for label, doc in (("price_evidence", evidence), ("coupon", coupon)):
        keys = ("valid_from_inclusive", "valid_until_exclusive")
        missing = [f"{label}.{key}" for key in keys if doc.get(key) is None]
        unknowns.extend(missing)
        try:
            values = [_when(gate, doc[key]) if doc.get(key) is not None else None for key in keys]
        except WorkflowError:
            errors.append("INVALID_WINDOW:" + label)
            continue
        since, until = values
        if ((since is not None and now_dt < since) or (until is not None and now_dt >= until)
                or (since is not None and until is not None and since >= until)):
            errors.append("OUTSIDE_WINDOW:" + label)
        if not missing:
            windows.append((since, until))

    if "test_approval" in payload or "applicable" in coupon:
        applicable = coupon.get("applicable")
        if applicable is None:
            unknowns.append("coupon.applicable")
        elif applicable is not True:
            errors.append("COUPON_NOT_APPLICABLE")
    if "test_approval" in payload:
        approval = payload["test_approval"]
        if approval is None:
            unknowns.append("test_approval")
        else:
            _fields(approval, {"actor", "decision", "at"})
            if approval["actor"] != TEST_REVIEW_ACTOR or approval["decision"] not in ("approve_fixture", "reject_fixture"):
                raise WorkflowError("Unknown synthetic campaign approval actor or decision.")
            approved_at = _when(gate, approval["at"])
            if approval["decision"] == "reject_fixture":
                errors.append("TEST_APPROVAL_REJECTED")
            if approved_at < initial:
                errors.append("APPROVAL_BEFORE_PRICE_REVIEW")
            if any(not since <= approved_at < until for since, until in windows):
                errors.append("APPROVAL_OUTSIDE_EVIDENCE_WINDOW")
            if evaluation_time is not None and now_dt <= approved_at:
                errors.append("INTAKE_NOT_AFTER_TEST_APPROVAL")

    typed = None
    if not errors and not unknowns:
        subject = {k: source[k] for k in gate.SUBJECT_FIELDS}
        original = {**subject, "campaign": payload}
        calc = {**subject, "method": "deterministic_calculation", "producer": price.VERSION,
                "input_sha256": gate.digest(original), "rule_versions": RULES,
                "lower_bound_cents": buyer, "lower_bound_basis": source["floor_basis"],
                "seller_cost_complete": ledger["computed_seller_cost_complete"],
                "fees_complete": ledger["computed_fee_inputs_complete"], "unknowns": [], "conflicts": []}
        authority = {**calc, "schema_version": "1.1", "timezone": ZONE,
                     "valid_from_inclusive": max(window[0] for window in windows).isoformat(),
                     "valid_until_exclusive": min(window[1] for window in windows).isoformat(),
                     "floor_cents": floor, "floor_inclusive": inclusive, "lower_bound_verified": True,
                     "calculation_sha256": gate.digest(calc), "evidence_scope": SCOPE}
        packet = {"packet_id": coupon["coupon_id"], "receiver_role": RECEIVER, "authority_sha256": gate.digest(authority),
                  "input_sha256": gate.digest(original), "payload": deepcopy(authority)}
        typed = gate.validate(authority, packet, RECEIVER, now, original, calc)
        typed.pop("system_receipt", None)
        if typed["field_gate"] != "pass":
            errors.append("TYPED_PRICE_GATE_REJECTED")
    disposition = "REJECT" if errors else "PENDING" if unknowns else "PASS"
    return {"disposition": disposition, "errors": sorted(set(errors)), "unknowns": sorted(set(unknowns)),
            "site": "PH", "currency": "PHP", "evaluation_time": now, "campaign_id": payload["campaign_id"],
            "sku": source["sku"], "revision": source["revision"], "price_evidence_version": evidence["version"],
            "coupon_id": coupon["coupon_id"], "coupon_version": coupon["version"], "price_evidence": evidence,
            "coupon": coupon, "price_source_sha256": gate.digest(payload), "rule_sha256": RULES,
            "host_calculation": ledger, "host_calculation_scope": "synthetic_proposal_arithmetic_only",
            "current_price_eligible_for_business": False, "deterministic_price_field_gate": typed,
            "receiver_model_status": "not_run", "local_receipt_only": True}


def _packet(task_dir, tool_id, data):
    result = {**AUTHORITY, **data, "schema": SCHEMA, "task_id": task_dir.name, "tool_id": tool_id}
    paths = {kind: task_dir / name for kind, name in OUTPUTS.items()}
    content = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with paths["json"].open("xb") as fd:
        fd.write(content)
    receipt = receipt_for(result, content)
    with paths["receipt"].open("x", encoding="utf-8", newline="\n") as fd:
        json.dump(receipt, fd, ensure_ascii=False, indent=2, allow_nan=False)
        fd.write("\n")
    return result, paths


def receipt_for(result, content):
    test_scope = ({"test_approval": result["test_approval"], "test_recipient": result.get("test_recipient"),
                   "local_admission": result.get("local_admission", False)}
                  if "test_approval" in result else {})
    return {**AUTHORITY, **test_scope, "schema": SCHEMA, "tool_id": result["tool_id"], "task_id": result["task_id"], "site": "PH",
               "disposition": result["disposition"], "result_sha256": sha256(content).hexdigest(),
               "price_source_sha256": result["price_source_sha256"], "upstream": result.get("upstream"),
               "receiver_role": RECEIVER, "receiver_model_status": "not_run", "local_receipt_only": True}


def run_workflow(project, site, tool_id, payload, task_dir, assets, progress, *, upstream=None):
    if site != "PH" or assets or tool_id not in (REVIEW, INTAKE):
        raise WorkflowError("仅菲律宾站本机活动及商城任务可使用此链路。")
    try:
        if tool_id == REVIEW:
            progress("正在用原始模拟金额和明确券条件独立核算。")
            result = _read_review(payload, project)
            result["role"] = SENDER
            if "test_approval" in payload:
                result["test_approval"] = payload["test_approval"]
        else:
            progress("商城正读取同站活动价审凭据，并重新核对有效期。")
            validate_intake(payload)
            if upstream is None or upstream[0]["task_id"] != payload["upstream_task_id"]:
                raise WorkflowError("缺少受信的本机上游任务绑定。")
            binding, original, prior = upstream
            result = _read_review(original, project, payload["evaluation_time"])
            for key in ("campaign_id", "sku", "revision", "coupon_id", "coupon_version", "price_evidence_version"):
                if payload[key] != result[key]:
                    result["errors"].append("RECEIVER_IDENTITY_MISMATCH:" + key)
            has_approval_lane = "test_approval" in original
            if has_approval_lane and "test_recipient" not in payload:
                result["unknowns"].append("test_recipient")
            if not has_approval_lane and "test_recipient" in payload:
                result["errors"].append("TEST_APPROVAL_LANE_REQUIRED")
            if result["errors"] or prior["disposition"] == "REJECT":
                result["disposition"] = "REJECT"
            elif result["unknowns"] or prior["disposition"] == "PENDING":
                result["disposition"] = "PENDING"
            if result["disposition"] != "PASS":
                result["deterministic_price_field_gate"] = None
            if has_approval_lane:
                result.update(test_approval=original["test_approval"], test_recipient=payload.get("test_recipient"),
                              local_admission=result["disposition"] == "PASS")
            result.update(role=RECEIVER, upstream=binding, upstream_disposition=prior["disposition"],
                          requested_subject={k: v for k, v in payload.items() if k not in {"upstream_task_id", "evaluation_time"}})
        return _packet(task_dir, tool_id, result)
    except (RoleToolError, IndexError, TypeError, OSError, KeyError) as error:
        raise WorkflowError("活动价审或商城收件的输入和本机依赖未通过校验。") from error

