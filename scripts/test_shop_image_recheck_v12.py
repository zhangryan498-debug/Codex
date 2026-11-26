"""Only synthetic v1.2 dual-state regressions."""
import copy,json,unittest
import run_codex_role as m
import shop_image_recheck_v12 as v
from test_shop_image_recheck import RecheckTests
class DualTests(RecheckTests):
 def setUp(self):
  super().setUp()
  self.report["schema_version"]="1.2"
  self.row=self.report["rechecks"][0];self.row.pop("status")
  self.row.update(resolution_status="closed",fact_verification_status="unverified",fact_subject="Underlying performance",fact_scope="real_world_claim",fact_basis="No supporting evidence supplied",fact_evidence_type="missing",resolution_basis_type="claim_removed",unsupported_claim_present=False,resolved_parts=["Unsupported copy removed"],unresolved_parts=[])
 def validate(self):return v.validate(self.report,self.images,self.previous,m.validate_report)
 def test_standalone_requires_proof_v12(self):RecheckTests.test_standalone_rejects_no_proof(self)
 def test_not_applicable_mismatch(self):
  self.row.update(fact_verification_status="not_applicable",fact_evidence_type="not_applicable",resolution_basis_type="other");self.assertTrue(self.validate())
 def test_verified_na_scope(self):
  self.row.update(fact_scope="not_applicable",fact_verification_status="verified",fact_evidence_type="direct_image");self.assertTrue(self.validate())
 def test_migration_duplicate_rejected(self):
  old=copy.deepcopy(self.report);old["schema_version"]="1.1";row=old["rechecks"][0]
  for f in v.FIELDS+("fact_evidence_type","unsupported_claim_present"):row.pop(f)
  row["status"]="closed";old["rechecks"]*=2
  with self.assertRaises(ValueError):v.migration_view(old,m.validate_report)
 def test_removed_claim_closed_unverified(self):self.assertEqual(self.validate(),[])
 def test_remaining_claim_open_unverified(self):
  self.row.update(resolution_status="open",unsupported_claim_present=True,resolved_parts=[],unresolved_parts=["Claim remains"]);self.assertEqual(self.validate(),[])
 def test_partial(self):
  self.row.update(resolution_status="partial",resolved_parts=["Price fixed"],unresolved_parts=["Claim remains"],unsupported_claim_present=True);self.assertEqual(self.validate(),[])
 def test_partial_cannot_close(self):
  self.row.update(unresolved_parts=["Claim remains"]);self.assertTrue(self.validate())
 def test_partial_must_have_both(self):
  self.row.update(resolution_status="partial");self.assertTrue(self.validate())
 def test_missing_not_false(self):
  self.row.update(fact_verification_status="contradicted",fact_scope="provided_information");self.assertTrue(self.validate())
 def test_missing_not_true(self):
  self.row.update(fact_verification_status="verified",fact_scope="provided_information");self.assertTrue(self.validate())
 def test_real_performance_not_verified(self):
  self.row.update(fact_verification_status="verified",fact_evidence_type="direct_image");self.assertTrue(self.validate())
 def test_relative_verification(self):
  self.row.update(fact_verification_status="verified",fact_scope="provided_information",fact_subject="Displayed pack count matches provided count",fact_evidence_type="user_confirmed");self.assertEqual(self.validate(),[])
 def test_remaining_unsupported_not_closed(self):
  self.row["unsupported_claim_present"]=True;self.assertTrue(self.validate())
 def test_unclear_resolution_kept_unknown(self):
  self.row.update(resolution_status="unknown",resolved_parts=[],unresolved_parts=["Cannot read claim"]);self.assertEqual(self.validate(),[])
 def test_no_empty_fact_subject(self):
  self.row["fact_subject"]=" ";self.assertTrue(self.validate())
 def test_no_arbitrary_closed_unverified(self):
  self.row["resolution_basis_type"]="other";self.assertTrue(self.validate())
 def test_migration_closed_does_not_verify(self):
  old=copy.deepcopy(self.report);old["schema_version"]="1.1";row=old["rechecks"][0]
  for f in v.FIELDS+("fact_evidence_type","unsupported_claim_present"):row.pop(f)
  for state in ["closed","open","unknown"]:
   row["status"]=state;view=v.migration_view(old,m.validate_report)
   self.assertEqual(view["issues"][0]["resolution_status"],state);self.assertEqual(view["issues"][0]["fact_verification_status"],"unverified");self.assertFalse(view["is_current_recheck"]);self.assertTrue(self.validate_schema_rejects_view(view))
 def validate_schema_rejects_view(self,view):return m.validate_report(view,self.images,json.loads(v.SCHEMA.read_text()))
 # Inherited tests mutate v1.1-specific fields; do not inherit them into this suite.
def load_tests(loader,tests,pattern):
 return unittest.TestSuite(DualTests(name) for name in DualTests.__dict__ if name.startswith("test_"))
if __name__=="__main__":unittest.main(verbosity=2)
