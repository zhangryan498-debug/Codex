"""Round2 synthetic sequence checks. No model, no blind data, no frozen-code writes."""
import copy,json,unittest
import run_codex_role as m
import shop_image_recheck as legacy
from test_shop_image_recheck_v12 import DualTests

class SequenceTests(DualTests):
 def test_repeated_same_anchor_is_deterministic(self):
  original=copy.deepcopy(self.previous)
  for _ in range(3):self.assertEqual(self.validate(),[])
  self.assertEqual(self.previous,original)
 def test_closed_then_recurrence_reopens(self):
  first=copy.deepcopy(self.report);self.assertEqual(self.validate(),[])
  self.row.update(resolution_status='open',unsupported_claim_present=True,resolved_parts=[],unresolved_parts=['The removed statement recurs'])
  self.assertEqual(self.validate(),[])
  self.assertEqual(first['rechecks'][0]['resolution_status'],'closed')
  self.assertEqual(self.row['fact_verification_status'],'unverified')
 def test_missing_then_new_scoped_evidence(self):
  self.row.update(resolution_status='unknown',resolved_parts=[],unresolved_parts=['No source'])
  old=copy.deepcopy(self.report);self.assertEqual(self.validate(),[])
  self.row.update(resolution_status='closed',fact_verification_status='verified',fact_subject='Current wording matches supplied synthetic specification',fact_scope='provided_information',fact_basis='Supplied specification v2, not real-world performance verification',fact_evidence_type='document',resolution_basis_type='claim_corrected',resolved_parts=['Wording aligned to source v2'],unresolved_parts=[])
  self.assertEqual(self.validate(),[])
  self.assertEqual(old['rechecks'][0]['fact_verification_status'],'unverified')
 def test_partial_then_closed_needs_no_remaining_obligation(self):
  self.row.update(resolution_status='partial',resolved_parts=['First clause removed'],unresolved_parts=['Second clause remains'],unsupported_claim_present=True)
  self.assertEqual(self.validate(),[])
  self.row['resolution_status']='closed';self.assertTrue(self.validate())
  self.row.update(unsupported_claim_present=False,unresolved_parts=[],resolved_parts=['Both clauses removed'])
  self.assertEqual(self.validate(),[])
 def test_original_history_not_rewritten(self):
  before=self.p.read_bytes();self.assertEqual(self.validate(),[]);self.assertEqual(self.p.read_bytes(),before)
 def test_recheck_reports_cannot_become_initial_anchor(self):
  for version in ['1.1','1.2']:
   candidate=copy.deepcopy(self.report);candidate['schema_version']=version
   self.p.write_text(json.dumps(candidate),encoding='utf8')
   with self.assertRaises(ValueError):legacy.load_previous(self.p,self.images,m.validate_report)
 def test_switched_original_anchor_hash_rejected(self):
  self.report['previous_report_sha256']='0'*64;self.assertTrue(self.validate())
 def test_unknown_never_auto_closed(self):
  self.row.update(resolution_status='unknown',resolved_parts=[],unresolved_parts=['Illegible current statement'])
  self.assertEqual(self.validate(),[])
  self.row['resolution_status']='closed';self.assertTrue(self.validate())
 def test_claim_on_different_image_not_same_issue(self):
  self.row['image_id']='different-panel';self.assertTrue(self.validate())
 def test_legacy_migration_is_not_fresh_assessment(self):
  view=legacy.load_previous(self.p,self.images,m.validate_report)
  self.assertEqual(view['sha256'],self.previous['sha256'])
  from shop_image_recheck_v12 import migration_view
  migrated=migration_view(self.old,m.validate_report)
  self.assertFalse(migrated['is_current_recheck'])
  self.assertTrue(all(x['fact_verification_status']=='unverified' for x in migrated['issues']))

def load_tests(loader,tests,pattern):
 return unittest.TestSuite(SequenceTests(n) for n in SequenceTests.__dict__ if n.startswith('test_'))
if __name__=='__main__':unittest.main(verbosity=2)
