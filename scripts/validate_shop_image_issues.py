"""Validate standard shop image issue reports and optional independent image-read proof."""
import argparse, json
from pathlib import Path
from run_codex_role import validate_report, validate_proof, manifest_digest
import shop_image_recheck as recheck
import shop_image_recheck_v12 as recheck_v12
import shop_run_acceptance as acceptance

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True,help="images.json written by normal entrypoint")
    p.add_argument("--proof",type=Path)
    p.add_argument("--result",type=Path,help="Defaults to result.json next to proof")
    p.add_argument("--previous",type=Path,help="Required frozen previous-issues.json for schema 1.1")
    p.add_argument("--mode",choices=["accept","structure-only"],default="accept")
    p.add_argument("--expected-run-id")
    p.add_argument("--expected-contract-sha256")
    p.add_argument("--expected-receipt-sha256")
    p.add_argument("--review-context",type=Path,help="Frozen review-context.json required for v1.3 structure-only inspection")
    a=p.parse_args()
    if a.mode == "accept":
        root=a.report.resolve().parent
        pairs=[(a.report,"issues.json"),(a.manifest,"images.json"),(a.proof,"image-read-proof.json"),(a.result,"result.json"),(a.previous,"previous-issues.json"),(a.review_context,"review-context.json")]
        if any(path is not None and path.resolve() != root/name for path,name in pairs):
            verdict={"accepted":False,"errors":["Supplied files must belong to the same canonical run"]}
        else:
            verdict=acceptance.accept(root,a.expected_run_id,a.expected_contract_sha256,a.expected_receipt_sha256)
        verdict["valid"]=verdict["accepted"];print(json.dumps(verdict,ensure_ascii=False));return 0 if verdict["accepted"] else 1
    try:
        report=json.loads(a.report.read_text(encoding="utf-8-sig"))
        manifest=json.loads(a.manifest.read_text(encoding="utf-8-sig"))
        previous = None
        if report.get("schema_version") in ("1.3","1.4"):
            from run_codex_role import review_module
            v13=review_module(report["schema_version"])
            if not a.review_context:raise ValueError("Version1.3 structure-only requires --review-context; this is not full acceptance")
            context=json.loads(a.review_context.read_text(encoding="utf-8-sig"))
            raw=dict(report);metadata=raw.pop("system_metadata",None)
            errors=v13.validate(raw,manifest,context,validate_report)
            if metadata is not None and metadata!=v13.decorate(raw,context)["system_metadata"]:errors.append("System metadata mismatch")
        elif report.get("schema_version") in ("1.1","1.2"):
            if not a.previous: raise ValueError("Schema 1.1 requires --previous")
            if not a.proof: raise ValueError("Schema 1.1 requires --proof and successful run receipt")
            previous = recheck.load_previous(a.previous,manifest,validate_report)
            errors = recheck_v12.validate(report,manifest,previous,validate_report) if report["schema_version"] == "1.2" else recheck.validate_recheck(report,manifest,previous,validate_report)
        else:
            if a.previous: raise ValueError("--previous is only valid for schema 1.1")
            errors=validate_report(report,manifest)
        if a.proof:
            proof=json.loads(a.proof.read_text(encoding="utf-8-sig"))
            receipt_path = a.result or a.proof.parent/"result.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            if receipt.get("exit_code") != 0 or receipt.get("turn_completed") is not True or receipt.get("status") != "completed":
                errors.append("Run receipt is not a completed successful run")
            if receipt.get("manifest_sha256") != manifest_digest(manifest):
                errors.append("Run receipt manifest hash mismatch")
            errors += validate_proof(proof, manifest, receipt.get("run_id"))
            if previous and (receipt.get("previous_report_sha256") != previous["sha256"] or receipt.get("previous_report_unchanged") is not True or receipt.get("review_mode") != "recheck"):
                errors.append("Recheck receipt historical input binding/integrity failure")
            if report.get("schema_version") == "1.2" and receipt.get("recheck_version") != "1.2":
                errors.append("Recheck receipt version mismatch")
            if receipt.get("input_images_unchanged") is not True:
                errors.append("Run has not verified unchanged input bytes")
        print(json.dumps({"schema_valid":not errors,"accepted":False,"mode":"structure-only","errors":errors,
                          "scope":"Structure and coverage only; not factual accuracy, blind score, or release approval"},ensure_ascii=False))
        return 1 if errors else 0
    except Exception as e:
        print(json.dumps({"schema_valid":False,"accepted":False,"mode":"structure-only","error":str(e)},ensure_ascii=False));return 2

if __name__=="__main__":
    raise SystemExit(main())

