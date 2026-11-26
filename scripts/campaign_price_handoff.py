"""Fail-closed offline price-floor receipt gate. This is NOT a pricing engine.

Trusted callers supply source inputs and independently produced calculation evidence.
Hashes prove consistency, not authenticity or platform applicability. Model-origin
'verified' flags or self-reported hashes must never be used as trusted evidence.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROUTES = {"douyin-shop-ops": ("douyin", "CN", "CNY"),
          "tiktok-shop-ops": ("tiktok", "PH", "PHP")}
SUBJECT_FIELDS = ("platform", "country", "currency", "sku", "revision", "floor_scope", "floor_basis")
BASIS_SCOPE = {"buyer_goods_per_unit":"each_sku", "merchant_net_per_unit":"each_sku",
               "buyer_goods_order":"order", "merchant_net_order":"order"}
AUTH_FIELDS = ("schema_version", "platform", "country", "currency", "sku", "revision",
               "timezone", "valid_from_inclusive", "valid_until_exclusive",
               "floor_cents", "floor_scope", "floor_basis", "floor_inclusive", "lower_bound_cents",
               "lower_bound_basis", "lower_bound_verified", "seller_cost_complete",
               "fees_complete", "unknowns", "conflicts", "rule_versions",
               "input_sha256", "calculation_sha256", "evidence_scope")
CALC_FIELDS = SUBJECT_FIELDS + ("method", "producer", "input_sha256", "rule_versions", "lower_bound_cents",
               "lower_bound_basis", "seller_cost_complete", "fees_complete",
               "unknowns", "conflicts")
MARKERS = ("PRICE_AUTHORITY", "PRICE_PACKET", "PRICE_CALCULATION",
           "PRICE_SOURCE_INPUT", "PRICE_EVALUATION_TIME", "PRICE_RECEIVER")
HEX = re.compile(r"[0-9a-f]{64}")

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False).encode("utf8")).hexdigest()

def text_ok(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()

def timestamp(value, zone):
    if not text_ok(value):
        raise ValueError("timestamp must be nonempty ISO 8601 text")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("offset-aware timestamp required")
    if dt.astimezone(zone).utcoffset() != dt.utcoffset():
        raise ValueError("timestamp offset does not match named timezone")
    return dt

def validate(authority, packet, receiver, evaluation_time, source_input, calculation):
    """Trusted authority/calculation/source_input are application inputs, NOT model output."""
    errors = []
    def fail(field, kind): errors.append({"field": field, "kind": kind})
    def hash_or_error(value, label):
        try: return digest(value)
        except (TypeError, ValueError, OverflowError):
            fail(label, "not_canonical_json")
            return None
    a = authority if isinstance(authority, dict) else {}
    c = calculation if isinstance(calculation, dict) else {}
    p = packet if isinstance(packet, dict) else {}
    if not isinstance(authority, dict): fail("authority", "object_required")
    if not isinstance(calculation, dict): fail("calculation", "object_required")
    if not isinstance(packet, dict): fail("packet", "object_required")
    ah = hash_or_error(authority, "authority")
    ih = hash_or_error(source_input, "source_input")
    ch = hash_or_error(calculation, "calculation")
    ph = hash_or_error(packet, "packet")
    if not isinstance(receiver, str) or receiver not in ROUTES: fail("receiver", "unsupported_receiver")
    for k in AUTH_FIELDS:
        if k not in a: fail("authority." + k, "missing")
    for k in CALC_FIELDS:
        if k not in c: fail("calculation." + k, "missing")
    if a.get("schema_version") != "1.1": fail("authority.schema_version", "unsupported")
    if a.get("evidence_scope") != "synthetic_offline": fail("authority.evidence_scope", "offline_only")
    raw = source_input if isinstance(source_input, dict) else {}
    if not isinstance(source_input, dict) or not source_input:
        fail("source_input", "nonempty_object_required")
    for owner, record in (("source_input", raw), ("calculation", c)):
        for key in SUBJECT_FIELDS:
            if key not in record:
                fail(owner+"."+key, "subject_missing")
            elif key not in a or type(record[key]) is not type(a.get(key)) or record[key] != a.get(key):
                fail(owner+"."+key, "subject_mismatch")
    basis = a.get("floor_basis")
    if not isinstance(basis, str) or basis not in BASIS_SCOPE:
        fail("authority.floor_basis", "unsupported_basis")
    elif a.get("floor_scope") != BASIS_SCOPE[basis]:
        fail("authority.floor_scope", "basis_scope_mismatch")

    if isinstance(receiver, str) and receiver in ROUTES:
        for k, expected in zip(("platform", "country", "currency"), ROUTES[receiver]):
            if a.get(k) != expected: fail("authority." + k, "receiver_scope_mismatch")
    for k in ("sku", "timezone", "floor_basis", "lower_bound_basis"):
        if not text_ok(a.get(k)): fail("authority." + k, "nonempty_text_required")
    for k in ("revision", "floor_cents", "lower_bound_cents"):
        if type(a.get(k)) is not int or a[k] < 0: fail("authority." + k, "nonnegative_integer_required")
    if type(a.get("floor_inclusive")) is not bool: fail("authority.floor_inclusive", "explicit_boolean_required")
    for k in ("lower_bound_verified", "seller_cost_complete", "fees_complete"):
        if a.get(k) is not True: fail("authority." + k, "not_verified_complete")
    for owner, record in (("authority", a), ("calculation", c)):
        for k in ("unknowns", "conflicts"):
            if type(record.get(k)) is not list: fail(owner + "." + k, "explicit_list_required")
            elif record[k]: fail(owner + "." + k, "unresolved")
        rv = record.get("rule_versions")
        if not isinstance(rv, dict) or not rv or any(not text_ok(k) or not text_ok(v) for k,v in rv.items()):
            fail(owner + ".rule_versions", "nonempty_version_mapping_required")
    if not text_ok(c.get("producer")): fail("calculation.producer", "nonempty_text_required")
    if c.get("method") not in ("deterministic_calculation", "independently_reviewed"):
        fail("calculation.method", "independent_evidence_required")
    for k in ("input_sha256", "calculation_sha256"):
        if not isinstance(a.get(k), str) or not HEX.fullmatch(a[k]): fail("authority." + k, "sha256_required")
    if ih is None or a.get("input_sha256") != ih: fail("authority.input_sha256", "source_input_mismatch")
    if ch is None or a.get("calculation_sha256") != ch: fail("authority.calculation_sha256", "calculation_mismatch")
    for k in ("input_sha256", "rule_versions", "lower_bound_cents", "lower_bound_basis",
              "seller_cost_complete", "fees_complete", "unknowns", "conflicts"):
        if k not in c or k not in a or type(c.get(k)) is not type(a.get(k)) or c.get(k) != a.get(k):
            fail("calculation." + k, "authority_mismatch")
    if a.get("floor_basis") != a.get("lower_bound_basis"):
        fail("authority.lower_bound_basis", "floor_basis_mismatch")
    if type(a.get("floor_cents")) is int and type(a.get("lower_bound_cents")) is int:
        lower, floor = a["lower_bound_cents"], a["floor_cents"]
        if lower < floor or (lower == floor and a.get("floor_inclusive") is not True):
            fail("authority.lower_bound_cents", "floor_not_satisfied")
    try:
        zone = ZoneInfo(a["timezone"])
        start = timestamp(a.get("valid_from_inclusive"), zone)
        end = timestamp(a.get("valid_until_exclusive"), zone)
        now = timestamp(evaluation_time, zone)
        if start >= end: fail("authority.validity", "empty_or_reversed_interval")
        if not start <= now < end: fail("evaluation_time", "outside_validity")
    except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError):
        fail("authority.validity", "invalid_timezone_or_timestamp")
    if not text_ok(p.get("packet_id")): fail("packet.packet_id", "nonempty_text_required")
    if p.get("receiver_role") != receiver: fail("packet.receiver_role", "receiver_mismatch")
    if p.get("authority_sha256") != ah or ah is None: fail("packet.authority_sha256", "authority_mismatch")
    if p.get("input_sha256") != ih or ih is None: fail("packet.input_sha256", "source_input_mismatch")
    payload = p.get("payload")
    if not isinstance(payload, dict): fail("packet.payload", "object_required")
    else:
        for k in sorted(set(a) | set(payload)):
            if k not in a or k not in payload or type(a.get(k)) is not type(payload.get(k)) or a.get(k) != payload.get(k):
                fail("packet.payload." + k, "missing_extra_or_mismatch")
    return {"schema_version": "1.1", "scope": "synthetic_offline",
            "release_status": "not_authorized", "receiver_role": receiver,
            "packet_id": p.get("packet_id"), "authority_sha256": ah,
            "input_sha256": ih, "calculation_sha256": ch, "packet_sha256": ph,
            "evaluation_time": evaluation_time, "field_gate": "pass" if not errors else "fail",
            "system_receipt": "accepted" if not errors else "rejected", "errors": errors}

def combine(authority, packet, receiver, evaluation_time, source_input, calculation, model_receipt, binding):
    """binding must be independently verified by the caller, never copied from model prose."""
    gate = validate(authority, packet, receiver, evaluation_time, source_input, calculation)
    expected = {k: gate[k] for k in ("authority_sha256", "input_sha256", "calculation_sha256",
                                    "packet_sha256", "receiver_role", "evaluation_time")}
    expected["model_receipt_sha256"] = digest(model_receipt)
    if (not isinstance(binding, dict) or any(binding.get(k) != v for k,v in expected.items())
        or not text_ok(binding.get("run_id")) or not HEX.fullmatch(str(binding.get("task_sha256", "")))):
        raise ValueError("missing or stale trusted run binding")
    if not isinstance(model_receipt, dict): raise ValueError("model receipt must be an object")
    for k, v in {"scope":"synthetic_offline", "release_status":"not_authorized",
                 "receiver_role":receiver, "packet_id":packet.get("packet_id"),
                 "authority_sha256":gate["authority_sha256"], "input_sha256":gate["input_sha256"]}.items():
        if model_receipt.get(k) != v: raise ValueError("model receipt scope/binding mismatch: " + k)
    status = model_receipt.get("offline_receipt")
    if status not in ("accepted", "rejected"): raise ValueError("invalid model receipt")
    gate.update(model_receipt=status, model_gate_conflict=gate["field_gate"]=="fail" and status=="accepted",
                system_receipt="accepted" if gate["field_gate"]=="pass" and status=="accepted" else "rejected",
                verified_run_binding=binding)
    return gate

def task_bundle(authority, packet, calculation, source_input, evaluation_time, receiver):
    values = (authority, packet, calculation, source_input, evaluation_time, receiver)
    return "\n".join(k+"\n"+json.dumps(v,ensure_ascii=False,allow_nan=False) for k,v in zip(MARKERS,values))+"\nPRICE_END\n"

def combine_run(authority, packet, receiver, evaluation_time, source_input, calculation, run_dir):
    run = Path(run_dir)
    result = json.loads((run/"result.json").read_text(encoding="utf-8-sig"))
    tb, ab = (run/"task.txt").read_bytes(), (run/"answer.md").read_bytes()
    if (result.get("status") != "completed" or result.get("exit_code") != 0
        or result.get("turn_completed") is not True or result.get("run_id") != run.name
        or result.get("task_sha256") != hashlib.sha256(tb).hexdigest()
        or result.get("answer_sha256") != hashlib.sha256(ab).hexdigest()):
        raise ValueError("incomplete or changed run evidence")
    task = tb.decode("utf-8-sig").replace("\r\n","\n")
    values = []
    for marker, following in zip(MARKERS, MARKERS[1:]+("PRICE_END",)):
        needle, stop = marker+"\n", "\n"+following+"\n"
        if task.count(needle) != 1 or task.count(stop) != 1: raise ValueError("ambiguous task markers")
        values.append(json.loads(task.split(needle,1)[1].split(stop,1)[0]))
    supplied = (authority, packet, calculation, source_input, evaluation_time, receiver)
    if any(digest(x) != digest(y) for x,y in zip(values,supplied)): raise ValueError("run inputs differ")
    model = json.loads(ab.decode("utf-8-sig"))
    binding = {"authority_sha256":digest(authority),"input_sha256":digest(source_input),
               "calculation_sha256":digest(calculation),"packet_sha256":digest(packet),
               "receiver_role":receiver,"evaluation_time":evaluation_time,
               "model_receipt_sha256":digest(model),"run_id":run.name,"task_sha256":result["task_sha256"]}
    return combine(authority, packet, receiver, evaluation_time, source_input, calculation, model, binding)


def batch_task_bundle(cases):
    """Format public integration cases. No expected decisions are included."""
    if not isinstance(cases,list) or not cases: raise ValueError("nonempty case list required")
    normalized=[]
    seen=set()
    for case in cases:
        if not isinstance(case,dict) or not text_ok(case.get("case_id")) or case["case_id"] in seen:
            raise ValueError("unique case_id required")
        seen.add(case["case_id"])
        row=copy.deepcopy(case)
        row["trusted_authority_sha256"]=digest(row["authority"])
        row["trusted_input_sha256"]=digest(row["source_input"])
        normalized.append(row)
    return "PRICE_HANDOFF_CASES\n"+json.dumps(normalized,ensure_ascii=False,allow_nan=False)+"\nPRICE_HANDOFF_END\n"

def combine_batch_run(cases, run_dir):
    """Bind a single real CLI run covering multiple independent synthetic handoffs."""
    run=Path(run_dir)
    result=json.loads((run/"result.json").read_text(encoding="utf-8-sig"))
    tb,ab=(run/"task.txt").read_bytes(),(run/"answer.md").read_bytes()
    return combine_batch_evidence(cases, result, tb, ab, run.name)


def combine_batch_evidence(cases, result, tb, ab, run_id):
    """Pure combiner for the trusted host before it atomically publishes result.json."""
    if (result.get("status")!="completed" or result.get("exit_code")!=0 or result.get("turn_completed") is not True
        or result.get("run_id")!=run_id or result.get("task_sha256")!=hashlib.sha256(tb).hexdigest()
        or result.get("answer_sha256")!=hashlib.sha256(ab).hexdigest()):
        raise ValueError("incomplete or changed batch run evidence")
    task=tb.decode("utf-8-sig").replace("\r\n","\n")
    start,stop="PRICE_HANDOFF_CASES\n","\nPRICE_HANDOFF_END\n"
    if task.count(start)!=1 or task.count(stop)!=1: raise ValueError("ambiguous batch markers")
    actual=json.loads(task.split(start,1)[1].split(stop,1)[0])
    wanted=json.loads(batch_task_bundle(cases).split(start,1)[1].split(stop,1)[0])
    if digest(actual)!=digest(wanted):raise ValueError("batch inputs differ")
    model=json.loads(ab.decode("utf-8-sig"))
    if not isinstance(model,dict) or model.get("scope")!="synthetic_offline" or model.get("release_status")!="not_authorized":
        raise ValueError("invalid batch model scope")
    receipts=model.get("receipts")
    if not isinstance(receipts,list) or any(not isinstance(r,dict) for r in receipts):raise ValueError("receipt list required")
    ids=[r.get("case_id") for r in receipts]
    if any(not text_ok(x) for x in ids) or len(set(ids))!=len(ids) or set(ids)!={c["case_id"] for c in cases}:
        raise ValueError("batch receipt coverage mismatch")
    mapped={r["case_id"]:r for r in receipts}
    output=[]
    for c in cases:
        raw=mapped[c["case_id"]]
        m=copy.deepcopy(raw)
        inherited=[]
        for key,expected in (("scope","synthetic_offline"),("release_status","not_authorized")):
            if key in raw and raw[key] != expected:
                raise ValueError("nested receipt conflicts with verified batch scope: "+key)
            if key not in raw:
                m[key]=expected
                inherited.append(key)
        params={k:c[k] for k in ("authority","packet","receiver","evaluation_time","source_input","calculation")}
        binding={"authority_sha256":digest(c["authority"]),"input_sha256":digest(c["source_input"]),
                 "calculation_sha256":digest(c["calculation"]),"packet_sha256":digest(c["packet"]),
                 "receiver_role":c["receiver"],"evaluation_time":c["evaluation_time"],
                 "model_receipt_sha256":digest(m),"run_id":run_id,"task_sha256":result["task_sha256"],
                 "raw_model_receipt_sha256":digest(raw),"effective_model_receipt_sha256":digest(m),
                 "raw_answer_sha256":hashlib.sha256(ab).hexdigest(),
                 "batch_receipt_policy":"verified_top_level_scope_inheritance_v1"}
        checked=combine(**params,model_receipt=m,binding=binding)
        checked.update(case_id=c["case_id"], raw_model_receipt=copy.deepcopy(raw),
                       effective_model_receipt=m, inherited_scope_fields=inherited,
                       raw_model_receipt_sha256=digest(raw), effective_model_receipt_sha256=digest(m))
        output.append(checked)
    return {"schema_version":"1.1","scope":"synthetic_offline","release_status":"not_authorized",
            "run_id":run_id,"task_sha256":result["task_sha256"],
            "raw_answer_sha256":hashlib.sha256(ab).hexdigest(),
            "batch_receipt_policy":"verified_top_level_scope_inheritance_v1","records":output}

