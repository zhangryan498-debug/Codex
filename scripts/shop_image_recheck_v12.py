"""v1.2 dual-state contract; no business actions or historical mutation."""
import copy,json
from pathlib import Path
import shop_image_recheck as legacy
SCHEMA=Path(__file__).resolve().parents[1]/"schemas/shop_image_recheck_v12.schema.json"
FIELDS=("resolution_status","fact_verification_status","fact_basis","resolved_parts","unresolved_parts","fact_subject","fact_scope","resolution_basis_type")
def validate(report,images,previous,validate_report):
 errors=validate_report(report,images,json.loads(SCHEMA.read_text(encoding="utf8")))
 if errors:return errors
 for row in report["rechecks"]+report["issues"]:
  status=row["resolution_status"]
  na=[row["fact_verification_status"]=="not_applicable",row["fact_scope"]=="not_applicable",row["fact_evidence_type"]=="not_applicable"]
  if any(na) and not all(na):errors.append("Not-applicable status/scope/evidence must agree")
  if row["fact_verification_status"] in ("verified","contradicted") and row["fact_scope"] not in ("image_content","provided_information"):errors.append("Verification scope must be image content or provided information")
  if row["fact_scope"]=="real_world_claim" and row["fact_verification_status"] in ("verified","contradicted"):errors.append("Offline evidence cannot verify actual real-world properties")
  if status=="closed" and row["fact_verification_status"]=="unverified" and row["resolution_basis_type"] not in ("claim_removed","claim_corrected"):errors.append("Closed+unverified requires unsupported wording removed/corrected")
  if status=="closed" and row["unresolved_parts"]:errors.append("Closed issue still has unresolved parts")
  if status=="closed" and not row["resolved_parts"]:errors.append("Closed issue requires resolved parts")
  if status=="partial" and (not row["resolved_parts"] or not row["unresolved_parts"]):errors.append("Partial requires resolved and unresolved parts")
  if status in ("open","unknown") and not row["unresolved_parts"]:errors.append("Open/unknown requires unresolved reason")
  if row["fact_verification_status"] in ("verified","contradicted") and row["fact_evidence_type"] not in ("direct_image","user_confirmed","document"):errors.append("Missing evidence cannot verify or contradict a fact")
  if row["fact_verification_status"]=="not_applicable" and row["fact_evidence_type"]!="not_applicable":errors.append("Not-applicable fact must be explicitly scoped")
  if row["unsupported_claim_present"] and status=="closed":errors.append("Cannot close while unsupported claim remains")
 for row in report["issues"]:
  if row["resolution_status"] not in ("open","unknown"):errors.append("New issue cannot be automatically repaired")
 normalized=copy.deepcopy(report);normalized["schema_version"]="1.1"
 for row in normalized["rechecks"]:
  row["status"]={"partial":"open"}.get(row["resolution_status"],row["resolution_status"])
 for row in normalized["rechecks"]+normalized["issues"]:
  for field in FIELDS+("fact_evidence_type","unsupported_claim_present"):row.pop(field,None)
 return errors+legacy.validate_recheck(normalized,images,previous,validate_report)
def prompt(previous):
 return legacy.prompt(previous)+"""
V1.2 OVERRIDE: schema_version 1.2. Replace rechecks.status with TWO independent statuses.
resolution_status: closed=all aspects of that specific issue corrected; open=still unresolved; partial=some fixed but any part remains; unknown=cannot assess resolution. Never close a compound issue after fixing only one part.
fact_verification_status: verified=sufficient explicit evidence confirms the scoped fact; contradicted=explicit evidence disproves it; unverified=missing/insufficient evidence; not_applicable=only non-factual design/layout issue.
fact_subject explicitly names the exact proposition; fact_scope is image_content/provided_information/real_world_claim/not_applicable. verified or contradicted can only concern visible image content or comparison against provided information, NEVER actual real-world authenticity/performance. resolution_basis_type is claim_removed/claim_corrected/other; closed+unverified is allowed only for eliminated/corrected unsupported copy, not unresolved underlying fact verification.
For a purely non-factual design issue, ALL THREE fields MUST be not_applicable together: fact_verification_status, fact_scope, fact_evidence_type. Its visual repair evidence still goes in evidence/basis, NOT fact_evidence_type. Never pair fact_verification_status=not_applicable with direct_image.
fact_basis must state WHICH fact was evaluated, evidence source and limitations. fact_evidence_type: missing/direct_image/user_confirmed/document/not_applicable. Reading a claim on a picture confirms only the printed wording, NOT the real performance or authorization asserted.
Removing unsupported wording may resolve the publication-copy issue (closed) while the underlying product claim remains unverified. If that unsupported wording remains, resolution stays open (or partial if another part was fixed), fact stays unverified, NOT contradicted.
resolved_parts/unresolved_parts refer ONLY to repair obligations in that specific issue. For a copy-removal issue, keep the underlying performance uncertainty solely in fact_basis/fact_verification_status; do not incorrectly list it as an unresolved copy repair. For an identity-verification issue, missing identity evidence IS an unresolved issue part.
Record resolved_parts and unresolved_parts independently; never hide unresolved parts. Set unsupported_claim_present=true when the current image still contains the historical/new unsupported factual promise. Mere absence of a contradiction is not verification.
New issues[] also require both states and evidence fields; new findings cannot be auto-closed. Keep retest_status pending/blocked/not_applicable for NEW issues.
Historical state is not current evidence. Never infer verified from an old closed status. Migration views are not fresh assessments. release_decision remains not_authorized.
"""
def migration_view(report,validate_report):
 version=report.get("schema_version")
 schema_path={"1.0":Path(__file__).resolve().parents[1]/"schemas/shop_image_issues.schema.json","1.1":legacy.SCHEMA}.get(version)
 if not schema_path:raise ValueError("Migration view supports frozen 1.0/1.1 only")
 images=[{"image_id":x["image_id"],"file":x["image_file"]} for x in report["images"]]
 errors=validate_report(report,images,json.loads(schema_path.read_text(encoding="utf8")))
 if errors:raise ValueError("; ".join(errors))
 prior_ids=[x["previous_issue_id"] for x in report.get("rechecks",[])]
 if len(prior_ids)!=len(set(prior_ids)) or set(prior_ids)&{x["issue_id"] for x in report["issues"]}:raise ValueError("Ambiguous duplicate historical/new issue IDs in migration view")
 rows=[]
 for row in report.get("rechecks",[]):
  rows.append(dict(issue_id=row["previous_issue_id"],resolution_status=row["status"],fact_verification_status="unverified",fact_basis="Legacy report did not separate fact verification; no new assessment performed."))
 for row in report["issues"]:
  rows.append(dict(issue_id=row["issue_id"],resolution_status="unknown",fact_verification_status="unverified",fact_basis="Legacy first-review/retest status cannot establish current resolution or fact verification."))
 return dict(kind="migration_view",is_current_recheck=False,view_only=True,model_called=False,source_schema_version=version,release_decision="not_authorized",issues=rows)
if __name__=="__main__":
 import argparse
 from run_codex_role import validate_report
 p=argparse.ArgumentParser();p.add_argument("--migration-view",type=Path,required=True);a=p.parse_args()
 print(json.dumps(migration_view(json.loads(a.migration_view.read_text(encoding="utf-8-sig")),validate_report),ensure_ascii=False,indent=2))
