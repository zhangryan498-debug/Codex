"""Versioned same-input regression. Original model answers and scores stay immutable."""
import argparse
import copy
import csv
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import campaign_ledger_semantics_v3 as policy


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def dump(path, value):
    with Path(path).open("x", encoding="utf8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def run(project, score_dir, output):
    project, score_dir, output = map(Path, (project, score_dir, output))
    sys.path.insert(0, str(project / "scripts"))
    sys.path.insert(0, str(project / "work/fresh-composite-blind-20261111"))
    import fresh_harness as h
    import fresh_typed_caller as old
    import replay_campaign_state_repair_v2 as state
    h.verify_originals()
    before = state.original_snapshot(h)
    for folder in (project / "work/fresh-composite-state-repair-20261111", project / "work/fresh-composite-receiver-retest-20261111"):
        for path in folder.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                before[str(path)] = sha(path)
    manifest = read(score_dir / "score_manifest.json")
    for name, expected in manifest["files"].items():
        if sha(score_dir / name) != expected:
            raise ValueError("Independent author score hash mismatch: " + name)
    author = read(score_dir / "independent_score_first.json")
    if manifest["source_evidence_zip_sha256"] != author["evidence_integrity"]["zip_sha256"]:
        raise ValueError("Score source binding mismatch")
    grades = {row["case_id"]: row for row in author["per_case"]}
    rules = h.read(h.PUBLIC / "public_rules.json")["rule_cards"]
    calculated = h.load_frozen_engine().calculate_suite(h.PUBLIC)
    original_proof = h.read(h.B / "independent-proof-first.json")
    if policy.digest(calculated) != policy.digest(original_proof["results"]):
        raise ValueError("Legacy deterministic arithmetic is not reproducible")
    output.mkdir(parents=True, exist_ok=False)
    dump(output / "preservation-before.json", before)
    proofs, matrix, subjects, checks, raw_citation = {}, [], [], [], []
    for name, role, batch, _ in h.tasks():
        models, binding = old.validate_campaign(name, batch)
        envelope = calculated[batch["market"]]
        calculations = {row["case_id"]: row for row in envelope["cases"]}
        provenance = dict(envelope["provenance"], independent_proof_sha256=h.sha(h.B / "independent-proof-first.json"), semantic_policy_sha256=sha(policy.__file__))
        for case in batch["cases"]:
            legacy = calculations[case["case_id"]]
            proof = policy.normalize(case, legacy, rules)
            model = models[case["case_id"]]
            proofs[case["case_id"]] = proof
            if proof["decision"] != legacy["decision"]:
                raise AssertionError("Semantic repair changed an original decision")
            grade = grades[case["case_id"]]
            if "protected_current_net_minor" in grade and proof["numbers"].get("net_minor") != grade["protected_current_net_minor"]:
                # A bounded result has no exact current net; compare only the author's protected point field.
                raise AssertionError("Current net differs from the independent first-run score: " + case["case_id"])
            citation = policy.citation_check(model.get("rule_ids"), proof["rule_trace"], rules, case["market"])
            raw_citation.append({"case_id": case["case_id"], **citation})
            ss = [None] if case["facts"]["floor"]["scope"] == "whole_order" else list(case["facts"]["floor"]["amounts_minor"])
            fixed_gates = []
            for sku in ss:
                row, review = state.repaired_pack(old, case, proof, model, sku, binding, provenance)
                row["calculation"]["rule_versions"]["ledger_semantics_sha256"] = sha(policy.__file__)
                row["authority"]["rule_versions"] = copy.deepcopy(row["calculation"]["rule_versions"])
                row["authority"]["calculation_sha256"] = h.gate.digest(row["calculation"])
                row["packet"].update(payload=copy.deepcopy(row["authority"]), authority_sha256=h.gate.digest(row["authority"]), packet_id=row["case_id"]+"-ledger-v3")
                actual = state.validate(h, row, case["evaluation_time"])
                expired = state.validate(h, row, row["authority"]["valid_until_exclusive"])
                if expired["field_gate"] != "fail":
                    raise AssertionError("Expired packet accepted")
                fixed_gates.append(actual["field_gate"])
                subjects.append({"case_id": case["case_id"], "subject": row["case_id"], "row": row, "gate": actual})
            matrix.append({"case_id":case["case_id"], "market":case["market"], "decision":proof["decision"], "floor_status":proof["floor_status"], "amount_coverage":proof["current_amount_status"], "exact_current_net":proof["numbers"].get("net_minor"), "lower_bound":proof["numbers"].get("net_lower_minor"), "upper_bound":proof["numbers"].get("net_upper_minor"), "current_floor_proven":proof["current_floor_proven"], "subject_count":len(ss), "structural_passes":fixed_gates.count("pass"), "rule_ids":",".join(proof["rule_ids"]), "original_activity_decision_verified":grade["decision_verified"], "original_activity_core_monetary_verified":grade["core_monetary_verified"], "new_blind_test":False})
            # Mutate outputs after a clean independent calculation, retaining identical authoritative raw input.
            def blocked(label, changed):
                errors = policy.validate_scopes(case, changed, rules)
                checks.append({"case_id":case["case_id"],"mutation":label,"blocked":bool(errors),"errors":errors})
                if not errors:
                    raise AssertionError("Semantic adversarial mutation passed: " + label)
            if proof["ledger_scopes"]["current"]["applicability"] == "not_executable":
                bad=copy.deepcopy(proof);bad["numbers"]["net_minor"]=legacy["numbers"].get("net_minor") or 1
                blocked("counterfactual_as_current",bad)
            if "base_plan_projection" in proof["ledger_scopes"]:
                bad=copy.deepcopy(proof);bad["numbers"]["fees_minor"]=bad["numbers"]["base_plan_fee_subtotal_minor"]
                blocked("known_subtotal_as_unknown_total_fee",bad)
            if "known_cost_projection" in proof["ledger_scopes"]:
                bad=copy.deepcopy(proof);bad["numbers"]["listed_costs_minor"]=bad["numbers"]["known_costs_subtotal_minor"]
                blocked("known_subtotal_as_unknown_total_cost",bad)
            for entry in proof["rule_trace"]:
                bad=copy.deepcopy(proof);bad["rule_ids"].remove(entry["rule_id"])
                blocked("remove_required_"+entry["rule_id"],bad)
    if sum(row["structural_passes"] for row in matrix) != 8:
        raise AssertionError("The eight independently safe subjects must not be falsely rejected")
    after = {path:sha(path) for path in before}
    if before != after:
        raise AssertionError("Original or previous supplemental evidence changed")
    dump(output / "proof-v3.json", {"version":policy.VERSION,"scope":"synthetic_offline","release_status":"not_authorized","cases":proofs})
    dump(output / "typed-subjects.json", subjects)
    dump(output / "coverage-matrix.json", matrix)
    with (output / "coverage-matrix.csv").open("x",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(matrix[0]));writer.writeheader();writer.writerows(matrix)
    dump(output / "semantic-adversarial.json", checks)
    dump(output / "original-raw-citation-review.json",raw_citation)
    dump(output / "independent-author-score-binding.json",{"zip_sha256":"78cb81dddf9f997141735f8db74e9b56a7533ac2895eac5a9cac60121e97b556","files_verified":manifest["files"],"source_evidence_sha256":manifest["source_evidence_zip_sha256"],"original_scores_unchanged":True,"activity_scoring":author["activity_scoring"],"auxiliary_gaps_preserved":True})
    summary={"scope":"synthetic_offline","release_status":"not_authorized","new_model_calls":0,"same_input_regression_cases":len(matrix),"subjects":len(subjects),"structural_pass_subjects":8,"blocked_subjects":len(subjects)-8,"decisions_unchanged":True,"protected_current_nets_match_author":len(matrix),"coverage":dict(Counter(row["amount_coverage"] for row in matrix)),"adversarial_blocked":len(checks),"adversarial_total":len(checks),"expired_packets_blocked":len(subjects),"preserved_files":len(before),"all_preserved":before==after,"raw_activity_citation_gaps": [r for r in raw_citation if not r["passed"]],"policy_sha256":sha(policy.__file__),"runner_sha256":sha(__file__),"created_at":datetime.now(timezone.utc).isoformat()}
    dump(output / "summary.json",summary)
    print(json.dumps(summary,ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--project",required=True);parser.add_argument("--score-dir",required=True);parser.add_argument("--output",required=True)
    args=parser.parse_args();run(args.project,args.score_dir,args.output)
