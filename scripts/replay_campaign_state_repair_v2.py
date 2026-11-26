"""Versioned deterministic repair replay; no model calls or original-file writes.

Uses the original public input, independent calculation and bound campaign answer.
New caller bytes invalidate old receiver receipts. This command never manufactures
completed host evidence, reads a hidden key, or claims a fresh blind score.
"""
import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import campaign_workflow_state as state_policy


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def original_snapshot(h):
    paths = {h.B / "typed-callers/manifest.json", h.B / "original-campaign-freeze.json",
             h.B / "evidence/closeout-facts.json", h.B / "evidence/coverage-matrix.json",
             h.B / "evidence/package-manifest.json", h.B / "batch2-diagnostic/event-model-response.json"}
    original = h.read(h.B / "original-campaign-freeze.json")
    for name, record in original["runs"].items():
        root = h.B / "campaign-runs" / name
        paths.add(root / "result.json")
        paths.update(root / name for name in record["files_sha256"])
    manifest = h.read(h.B / "typed-callers/manifest.json")
    for group in ("files_sha256", "dependencies_sha256"):
        for path, expected in manifest[group].items():
            path = Path(path)
            if sha(path) != expected:
                raise ValueError("Original typed dependency changed: " + str(path))
            paths.add(path)
    return {str(path): sha(path) for path in sorted(paths)}


def repaired_pack(old, case, proof, model, sku, binding, provenance):
    review = state_policy.compare_workflow_state(
        proof.get("state"), model.get("state"), case["facts"].get("workflow"))
    effective = copy.deepcopy(model)
    if review["equivalent"]:
        effective["state"] = review["canonical_state"]
    # The frozen packer retains every original price/identity/pending-field gate.
    # The frozen packer retains references to caller objects. Isolate the entire
    # returned graph before adversarial copies or later cases can touch it.
    row = copy.deepcopy(old.pack(case, proof, effective, sku, binding, provenance))
    gate = old.gate
    policy_sha = sha(state_policy.__file__)
    review.update(raw_state_sha256=gate.digest(model.get("state")),
                  independent_state_sha256=gate.digest(proof.get("state")),
                  comparison_source_sha256=policy_sha)
    source, calc, authority = row["source_input"], row["calculation"], row["authority"]
    source["workflow_state_review"] = copy.deepcopy(review)
    source["raw_workflow_state"] = copy.deepcopy(model.get("state"))
    calc["rule_versions"]["workflow_comparison_sha256"] = policy_sha
    calc["rule_versions"]["replay_builder_sha256"] = sha(__file__)
    calc["rule_versions"]["host_policy"] += "+" + state_policy.POLICY_VERSION
    authority["rule_versions"] = copy.deepcopy(calc["rule_versions"])
    calc["input_sha256"] = authority["input_sha256"] = gate.digest(source)
    authority["calculation_sha256"] = gate.digest(calc)
    authority["campaign_review"]["raw_model_result_summary"] = copy.deepcopy(old.proof_summary(model))
    authority["campaign_review"]["workflow_state_review"] = copy.deepcopy(review)
    row["packet"].update(packet_id=row["case_id"] + "-state-repair-v2",
                         input_sha256=authority["input_sha256"],
                         authority_sha256=gate.digest(authority), payload=copy.deepcopy(authority))
    return row, review


def validate(h, row, instant):
    return h.gate.validate(row["authority"], row["packet"], row["packet"]["receiver_role"],
                           instant, row["source_input"], row["calculation"])


def ensure_replay_rows_intact(rows, hashes, gate):
    if len(rows) != len(hashes) or any(gate.digest(row) != expected for row, expected in zip(rows, hashes)):
        raise AssertionError("Adversarial checks mutated a baseline/caller object")


def audit_missing_host(h):
    diagnostic = h.read(h.B / "batch2-diagnostic/event-model-response.json")
    message = diagnostic["messages"][0]
    events_path = Path(message["source_events"])
    if not events_path.resolve().is_relative_to((h.ROOT / "work/codex-runs").resolve()):
        raise ValueError("Diagnostic run outside original project")
    before = sha(events_path)
    if before != message["source_events_sha256"]:
        raise ValueError("Missing-host raw events changed")
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line]
    event = events[message["line_number"] - 1]
    text = event.get("item", {}).get("text", "")
    response = h.intake.parse_bytes(text.encode("utf-8"))
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != message["text_sha256"]:
        raise ValueError("Diagnostic original model text detached")
    if h.gate.digest(response) != h.gate.digest(message["structured_model_response"]):
        raise ValueError("Diagnostic extraction differs from original")
    caller = h.B / "typed-callers/batch-2.json"
    bundle = h.read(caller)
    ids = [row["case_id"] for row in response["receipts"]]
    if len(set(ids)) != len(ids) or set(ids) != {row["case_id"] for row in bundle["cases"]}:
        raise ValueError("Diagnostic subject coverage differs")
    run = events_path.parent
    completion = any(event.get("type") == "turn.completed" for event in events)
    blocked, reason = False, None
    try:
        h.intake.revalidate_run(caller, bundle["receiver_role"],
                               "2026-11-11T15:00:00+08:00", run)
    except (OSError, ValueError) as exc:
        blocked, reason = True, type(exc).__name__ + ": " + str(exc)
    return {"run_dir": str(run), "raw_events_sha256": before,
            "raw_events_unchanged": before == sha(events_path),
            "subjects": ids, "raw_model_receipts": [r["offline_receipt"] for r in response["receipts"]],
            "turn_completed": completion, "result_exists": (run / "result.json").exists(),
            "host_receipt_exists": (run / "price-intake-receipt.json").exists(),
            "standard_downstream_blocked": blocked, "block_reason": reason,
            "status": "missing_completion_evidence_not_reconstructed",
            "new_model_run": False, "host_receipt_created": False,
            "counts_as_completed_host": False}


def run(project, output):
    project = Path(project).resolve(strict=True)
    original = project / "work/fresh-composite-blind-20261111"
    output = Path(output).resolve()
    if output == original or output.is_relative_to(original):
        raise ValueError("Repair output must stay outside the frozen first-run directory")
    sys.path.insert(0, str(original))
    import fresh_harness as h
    import fresh_typed_caller as old
    if h.ROOT != project:
        raise ValueError("Unexpected original project")
    h.verify_originals()
    old_bytes = old.build(write=False)
    manifest = h.read(h.B / "typed-callers/manifest.json")
    for path in old_bytes:
        h.verify_typed_bytes(path, manifest, old_bytes)
    before = original_snapshot(h)
    proof = h.read(original / "independent-proof-first.json")
    sources = {str(Path(__file__).resolve()): sha(__file__),
               str(Path(state_policy.__file__).resolve()): sha(state_policy.__file__)}
    output.mkdir(parents=True, exist_ok=False)
    rows, cases, mutations, directed_callers, row_hashes = [], [], [], {}, []
    for name, role, batch, _ in h.tasks():
        models, binding = old.validate_campaign(name, batch)
        envelope = proof["results"][batch["market"]]
        calculations = {row["case_id"]: row for row in envelope["cases"]}
        provenance = dict(envelope["provenance"], independent_proof_sha256=h.sha(original / "independent-proof-first.json"))
        for case in batch["cases"]:
            p, m = calculations[case["case_id"]], models[case["case_id"]]
            subjects = [None] if case["facts"]["floor"]["scope"] == "whole_order" else list(case["facts"]["floor"]["amounts_minor"])
            comparisons = old.compare(p, m, case["market"])
            reviews = []
            for sku in subjects:
                baseline = old.pack(case, p, m, sku, binding, provenance)
                row, state_review = repaired_pack(old, case, p, m, sku, binding, provenance)
                baseline_gate = validate(h, baseline, case["evaluation_time"])
                fixed_gate = validate(h, row, case["evaluation_time"])
                expired = validate(h, row, row["authority"]["valid_until_exclusive"])
                if expired["field_gate"] != "fail":
                    raise AssertionError("Exclusive expiry no longer blocks")
                reviews.append({"subject": row["case_id"], "before_field_gate": baseline_gate["field_gate"],
                                "after_field_gate": fixed_gate["field_gate"], "expired_field_gate": expired["field_gate"]})
                rows.append(row)
                row_hashes.append(h.gate.digest(row))
                if baseline_gate["field_gate"] != fixed_gate["field_gate"]:
                    if comparisons != ["state"] or not state_review["equivalent"]:
                        raise AssertionError("Unexpected change outside state equivalence")
                    key = (row["packet"]["receiver_role"], case["evaluation_time"])
                    directed_callers.setdefault(key, []).append(row)
                    for field, value in (("new_charge_count", 1), ("new_charge_count", None),
                                         ("new_charge_count", False), ("terminal_status", "cancelled")):
                        changed = copy.deepcopy(m)
                        changed["state"][field] = value
                        bad, _ = repaired_pack(old, case, p, changed, sku, binding, provenance)
                        check = validate(h, bad, case["evaluation_time"])
                        mutations.append({"subject": row["case_id"], "mutation": field + "=" + repr(value),
                                          "field_gate": check["field_gate"]})
                    for kind in ("model_net", "unknown_fee", "source_cost", "below_floor"):
                        pp, mm = copy.deepcopy(p), copy.deepcopy(m)
                        if kind == "model_net":
                            mm["numbers"]["net_minor"] += 1
                        elif kind == "unknown_fee":
                            pp["pending_fields"].append("unresolved_cost.new_fee")
                        bad, _ = repaired_pack(old, case, pp, mm, sku, binding, provenance)
                        if kind == "source_cost":
                            bad["source_input"]["original_proposal"]["facts"]["costs"]["overhead_minor"] += 1
                        elif kind == "below_floor":
                            bad["calculation"]["lower_bound_cents"] = bad["authority"]["floor_cents"] - 1
                            bad["authority"]["lower_bound_cents"] = bad["calculation"]["lower_bound_cents"]
                            bad["authority"]["calculation_sha256"] = h.gate.digest(bad["calculation"])
                            bad["packet"].update(payload=copy.deepcopy(bad["authority"]), authority_sha256=h.gate.digest(bad["authority"]))
                        check = validate(h, bad, case["evaluation_time"])
                        mutations.append({"subject": row["case_id"], "mutation": kind, "field_gate": check["field_gate"]})
            cases.append({"case_id": case["case_id"], "market": case["market"],
                          "original_comparison_mismatches": comparisons, "state_review": state_review,
                          "subjects": reviews})
    if any(row["field_gate"] != "fail" for row in mutations):
        raise AssertionError("Adversarial mutation accepted")
    ensure_replay_rows_intact(rows, row_hashes, h.gate)
    missing = audit_missing_host(h)
    if not missing["standard_downstream_blocked"] or missing["host_receipt_exists"]:
        raise AssertionError("Missing completion evidence became accepted")
    h.verify_originals()
    after = original_snapshot(h)
    if before != after or any(sha(path) != expected for path, expected in sources.items()):
        raise AssertionError("Sources changed during repair replay")
    directed_preflights = []
    for index, ((receiver, instant), subjects) in enumerate(directed_callers.items(), 1):
        bundle = {"schema_version": "1.0", "scope": "synthetic_offline",
                  "release_status": "not_authorized", "receiver_role": receiver, "cases": subjects}
        target = output / (receiver + "-directed-caller-" + str(index) + ".json")
        dump(target, bundle)
        prepared = h.intake.prepare(receiver, h.shop_task(receiver), input_file=target,
                                    evaluation_time=instant)
        if not prepared["preflight"] or any(check["field_gate"] != "pass" for check in prepared["preflight"]):
            raise AssertionError("Directed caller must pass its persisted-byte preflight before any model run")
        preflight = {"receiver": receiver, "evaluation_time": instant,
                     "caller_sha256": sha(target), "case_ids": [row["case_id"] for row in subjects],
                     "preflight": prepared["preflight"], "new_model_run": False,
                     "host_receipt_created": False}
        dump(target.with_suffix(".preflight.json"), preflight)
        directed_preflights.append({"caller": str(target), "caller_sha256": sha(target),
                                    "receiver": receiver, "subjects": len(subjects)})
    h.verify_originals()
    if original_snapshot(h) != before:
        raise AssertionError("Original evidence changed during directed preflight")
    dump(output / "repaired-subjects.json", {"scope": "synthetic_offline", "release_status": "not_authorized", "cases": rows})
    dump(output / "case-comparison.json", cases)
    dump(output / "adversarial-regression.json", mutations)
    dump(output / "missing-host-audit.json", missing)
    dump(output / "original-preservation.json", {"before": before, "after": after, "unchanged": True})
    changed = [case["case_id"] for case in cases if any(r["before_field_gate"] != r["after_field_gate"] for r in case["subjects"])]
    summary = {"created_at": datetime.now(timezone.utc).isoformat(), "scope": "synthetic_offline",
               "release_status": "not_authorized", "acceptance_ready": False,
               "kind": "deterministic_replay_of_existing_answers_not_new_blind_test",
               "model_calls": 0, "case_count": len(cases), "subject_count": len(rows),
               "changed_cases": changed, "policy_version": state_policy.POLICY_VERSION,
               "baseline_field_pass_count": sum(r["before_field_gate"] == "pass" for c in cases for r in c["subjects"]),
               "repaired_field_pass_count": sum(r["after_field_gate"] == "pass" for c in cases for r in c["subjects"]),
               "expired_field_pass_count": sum(r["expired_field_gate"] == "pass" for c in cases for r in c["subjects"]),
               "adversarial_checks": len(mutations), "adversarial_all_blocked": True,
               "preserved_original_files": len(before), "originals_unchanged": True,
               "source_sha256": sources,
               "directed_callers_preflighted": directed_preflights,
               "limitations": ["No new receiver model review: changed caller bytes cannot reuse old accepted receipts.",
                               "Deterministic field passes are not completed combined host acceptances or independent hidden-key scores.",
                               "The three missing-host subjects remain incomplete; no completion or CLI exit was invented."]}
    dump(output / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True, help="New directory, never an original evidence directory")
    args = parser.parse_args()
    print(json.dumps(run(args.project, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
