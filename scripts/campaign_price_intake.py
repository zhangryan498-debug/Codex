"""Trusted-caller offline price intake. Never authorizes live business actions.

The caller, not a model, supplies the source/calculation bundle. Local file paths,
producer labels and hashes prove consistency only; they cannot authenticate truth.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
import campaign_price_handoff as gate

TOP_FIELDS = {"schema_version", "scope", "release_status", "receiver_role", "cases"}
CASE_FIELDS = {"case_id", "authority", "packet", "source_input", "calculation"}
RESERVED = gate.MARKERS + ("PRICE_END", "PRICE_HANDOFF_CASES", "PRICE_HANDOFF_END")
LIMIT = 262144
LOADED_GATE_SHA256 = hashlib.sha256(Path(gate.__file__).read_bytes()).hexdigest()
LOADED_INTAKE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()

def json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+"\n").encode("utf-8")

def parse_bytes(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key: "+key)
            result[key]=value
        return result
    def constant(value):
        raise ValueError("non-finite JSON value: "+value)
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=constant)

def has_reserved(task):
    return any(re.search(r"(?m)^\s*"+re.escape(tag)+r"\s*$",task) for tag in RESERVED)

def load_bundle(path, receiver, evaluation_time):
    if receiver not in gate.ROUTES:
        raise ValueError("price intake supports only Douyin CN and TikTok PH shop-ops")
    if not gate.text_ok(evaluation_time):
        raise ValueError("explicit price evaluation time required")
    origin=Path(path).expanduser().resolve(strict=True)
    if not origin.is_file() or origin.stat().st_size>LIMIT:
        raise ValueError("price input must be a regular UTF-8 JSON file at most 256KiB")
    if any(part.casefold()=="codex-runs" for part in origin.parts):
        raise ValueError("model run output is not a trusted caller price input")
    raw=origin.read_bytes()
    data=parse_bytes(raw)
    if (not isinstance(data,dict) or set(data)!=TOP_FIELDS or data.get("schema_version")!="1.0"
        or data.get("scope")!="synthetic_offline" or data.get("release_status")!="not_authorized"
        or data.get("receiver_role")!=receiver):
        raise ValueError("invalid caller price bundle scope or fields")
    cases=data["cases"]
    if not isinstance(cases,list) or not cases or len(cases)>100:
        raise ValueError("price bundle requires 1 to 100 cases")
    seen=set(); packets=set(); normalized=[]
    for case in cases:
        if not isinstance(case,dict) or set(case)!=CASE_FIELDS or not gate.text_ok(case.get("case_id")):
            raise ValueError("price case requires exact structured fields and case_id")
        if case["case_id"] in seen:
            raise ValueError("duplicate price case_id")
        packet=case["packet"]
        pid=packet.get("packet_id") if isinstance(packet,dict) else None
        if not gate.text_ok(pid) or pid in packets:
            raise ValueError("price cases require unique nonempty packet_id")
        seen.add(case["case_id"]); packets.add(pid)
        row=copy.deepcopy(case)
        row.update(receiver=receiver,evaluation_time=evaluation_time)
        normalized.append(row)
    return {"origin":str(origin),"raw":raw,"cases":normalized,
            "receiver":receiver,"evaluation_time":evaluation_time}

def prepare(role, task, sandbox="read-only", input_file=None, evaluation_time=None,
            incompatible=False):
    requested=input_file is not None or evaluation_time is not None
    if not requested:
        if has_reserved(task):
            raise ValueError("reserved price task requires --price-handoff-file and --price-evaluation-time")
        return None
    if input_file is None or evaluation_time is None:
        raise ValueError("price handoff file and evaluation time must be supplied together")
    if sandbox!="read-only" or incompatible:
        raise ValueError("price intake requires read-only and cannot mix image/recheck/review modes")
    if has_reserved(task):
        raise ValueError("caller must not embed price markers; the host freezes the task")
    context=load_bundle(input_file,role,evaluation_time)
    context["task"]=task+"\n\n"+(
        "OFFLINE STRUCTURED PRICE INTAKE. Business actions are not authorized. "
        "Return one JSON object only: scope=synthetic_offline, "
        "release_status=not_authorized, receipts=[one receipt for each case]. "
        "Each receipt must copy case_id, receiver_role=receiver, packet_id, "
        "authority_sha256=trusted_authority_sha256 and input_sha256=trusted_input_sha256, "
        "and state offline_receipt=accepted or rejected. Never self-certify source truth. "
        "The host independently enforces all price gates; prose cannot override them.\n"
    )+gate.batch_task_bundle(context["cases"])
    if len(context["task"].encode("utf-8"))>LIMIT:
        raise ValueError("frozen price task exceeds 256KiB")
    context["preflight"]=[gate.validate(**{k:c[k] for k in
        ("authority","packet","receiver","evaluation_time","source_input","calculation")})
        for c in context["cases"]]
    return context

def freeze(context, run_dir, rule_paths, expected_sources=None, prompt_bytes=None):
    out=Path(run_dir)
    if Path(context["origin"]).read_bytes()!=context["raw"]:
        raise ValueError("caller price input changed before freeze")
    code=[Path(__file__).resolve(),Path(gate.__file__).resolve()]
    expected=dict(expected_sources or {})
    expected[str(Path(__file__).resolve())]=LOADED_INTAKE_SHA256
    expected[str(Path(gate.__file__).resolve())]=LOADED_GATE_SHA256
    sources={}
    for p in code+list(rule_paths):
        p=Path(p).resolve(strict=True)
        current=sha_bytes(p.read_bytes())
        if str(p) in expected and current!=expected[str(p)]:
            raise ValueError("price source changed between read and freeze: "+str(p))
        sources[str(p)]=current
    actual_prompt=(out/"prompt.txt").read_bytes()
    if prompt_bytes is None or actual_prompt!=prompt_bytes:
        raise ValueError("actual complete price prompt differs from frozen caller prompt")
    contract={"schema_version":"1.0","scope":"synthetic_offline","release_status":"not_authorized",
              "receiver_role":context["receiver"],"origin":context["origin"],
              "caller_input_sha256":sha_bytes(context["raw"]),
              "evaluation_time":context["evaluation_time"],"source_hashes":sources,
              "task_sha256":sha_bytes(context["task"].encode("utf-8")),
              "prompt_sha256":sha_bytes(prompt_bytes),
              "case_ids":[c["case_id"] for c in context["cases"]],
              "calculation_trust":"caller_responsibility_not_authenticated_by_hash"}
    context["contract"]=contract
    raw=json_bytes(contract)
    context["contract_sha256"]=sha_bytes(raw)
    (out/"price-intake-input.json").write_bytes(context["raw"])
    (out/"price-intake-contract.json").write_bytes(raw)
    (out/"price-intake-preflight.json").write_bytes(json_bytes(context["preflight"]))

def check_integrity(context, run_dir):
    out=Path(run_dir); contract=context["contract"]
    if Path(context["origin"]).read_bytes()!=context["raw"]:
        raise ValueError("trusted caller price input changed during run")
    if (out/"price-intake-input.json").read_bytes()!=context["raw"]:
        raise ValueError("frozen price input changed")
    if sha_bytes((out/"price-intake-contract.json").read_bytes())!=context["contract_sha256"]:
        raise ValueError("frozen price contract changed")
    if sha_bytes((out/"task.txt").read_bytes())!=contract["task_sha256"]:
        raise ValueError("frozen price task changed")
    if sha_bytes((out/"prompt.txt").read_bytes())!=contract["prompt_sha256"]:
        raise ValueError("frozen complete price prompt changed")
    for name,expected in contract["source_hashes"].items():
        if sha_bytes(Path(name).read_bytes())!=expected:
            raise ValueError("price source/contract changed: "+name)

def finish(context, run_dir, result):
    out=Path(run_dir)
    check_integrity(context,out)
    if result.get("role")!=context["receiver"]:
        raise ValueError("actual result role differs from price receiver")
    tb,ab=(out/"task.txt").read_bytes(),(out/"answer.md").read_bytes()
    parse_bytes(ab)  # Reject duplicate keys/non-finite values before the existing combiner.
    checked=gate.combine_batch_evidence(context["cases"],result,tb,ab,out.name)
    checked.update(intake_schema_version="1.0",intake_kind="caller_frozen_price_gate",
                   receiver_role=context["receiver"],caller_input_sha256=sha_bytes(context["raw"]),
                   price_intake_contract_sha256=context["contract_sha256"],
                   evaluation_time=context["evaluation_time"])
    target=out/"price-intake-receipt.json"
    pending=out/"price-intake-receipt.pending.json"
    pending.write_bytes(json_bytes(checked))
    pending.replace(target)
    return checked

def revalidate_run(input_file, receiver, evaluation_time, run_dir):
    """Fresh deterministic downstream check, not another model run or live approval."""
    now=load_bundle(input_file,receiver,evaluation_time)
    out=Path(run_dir).resolve(strict=True)
    result=parse_bytes((out/"result.json").read_bytes())
    contract_bytes=(out/"price-intake-contract.json").read_bytes()
    contract=parse_bytes(contract_bytes)
    if (result.get("status")!="completed" or result.get("price_intake_status")!="completed"
        or result.get("role")!=receiver or result.get("price_intake_contract_sha256")!=sha_bytes(contract_bytes)):
        raise ValueError("run has no completed bound price intake")
    if sha_bytes(now["raw"])!=contract.get("caller_input_sha256"):
        raise ValueError("new input/rule/fee version requires a new price review run")
    old=load_bundle(input_file,receiver,contract["evaluation_time"])
    old.update(contract=contract,contract_sha256=sha_bytes(contract_bytes))
    check_integrity(old,out)
    if sha_bytes((out/"price-intake-receipt.json").read_bytes())!=result.get("price_intake_receipt_sha256"):
        raise ValueError("published price receipt is missing or changed")
    # Recompute the original raw model/system combination before reusing its model decision.
    tb,ab=(out/"task.txt").read_bytes(),(out/"answer.md").read_bytes()
    parse_bytes(ab)
    prior=gate.combine_batch_evidence(old["cases"],result,tb,ab,out.name)
    rows=[]
    for c,previous in zip(now["cases"],prior["records"]):
        fresh=gate.validate(**{k:c[k] for k in
            ("authority","packet","receiver","evaluation_time","source_input","calculation")})
        fresh.update(case_id=c["case_id"],previous_system_receipt=previous["system_receipt"],
                     model_receipt=previous["model_receipt"])
        fresh["system_receipt"]="accepted" if (fresh["field_gate"]=="pass" and
            previous["system_receipt"]=="accepted") else "rejected"
        rows.append(fresh)
    return {"schema_version":"1.0","scope":"synthetic_offline","release_status":"not_authorized",
            "intake_kind":"downstream_revalidation_no_new_model_run","origin_run_id":out.name,
            "origin_task_sha256":result["task_sha256"],"origin_answer_sha256":result["answer_sha256"],
            "origin_price_receipt_sha256":result["price_intake_receipt_sha256"],
            "caller_input_sha256":sha_bytes(now["raw"]),"receiver_role":receiver,
            "evaluation_time":evaluation_time,"records":rows}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input",required=True)
    ap.add_argument("--receiver",required=True)
    ap.add_argument("--evaluation-time",required=True)
    ap.add_argument("--run-dir",required=True)
    ap.add_argument("--output",required=True,help="New evidence file; existing files are never overwritten")
    args=ap.parse_args()
    try:
        result=revalidate_run(args.input,args.receiver,args.evaluation_time,args.run_dir)
        with Path(args.output).open("xb") as f:
            f.write(json_bytes(result))
        print(json.dumps({"scope":"synthetic_offline","release_status":"not_authorized",
            "accepted":sum(x["system_receipt"]=="accepted" for x in result["records"]),
            "rejected":sum(x["system_receipt"]=="rejected" for x in result["records"])}))
        return 0
    except Exception as exc:
        print(type(exc).__name__+": "+str(exc),file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
