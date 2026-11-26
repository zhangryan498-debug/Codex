"""Synthetic v1.4 tests only; no model calls or private material."""
import copy,json,unittest
import shop_review_policy_v14 as p
from test_shop_review_policy_v13 import Tests as Fixtures
from run_codex_role import validate_report

class Tests(unittest.TestCase):
 def setUp(self):
  Fixtures.setUp(self);self.report['schema_version']='1.4';self.report['target_assessments']=self.report.pop('target_results')
 def ctx(self,previous=None):return p.prepare(self.goals,self.images,self.bundle,previous)
 def errors(self,ctx=None):return p.validate(self.report,self.images,ctx or self.ctx(),validate_report)
 def issue(self):
  self.report['target_results']=self.report['target_assessments'];row=Fixtures.issue(self);self.report.pop('target_results');self.report['target_assessments'][0]['status']='contradicted';return row
 def history(self,status='open',fact='unverified'):
  row=self.issue();self.report['issues']=[];self.report['target_assessments'][0]['status']='uncertain'
  row={k:v for k,v in row.items() if k not in ('issue_id','original_text','severity','repair_copy','retest_status','finding_type','claim_present','severity_basis','impact')}
  row.update(previous_issue_id='OLD',current_text='Current image text',evidence='Observed image',resolution_status=status,fact_verification_status=fact,resolved_parts=['fixed'] if status in ('closed','partial') else [],unresolved_parts=[] if status=='closed' else ['still unresolved'],resolution_basis_type='claim_removed' if status=='closed' else 'other')
  self.report['rechecks']=[row];self.report['previous_report_sha256']='a'*64;self.goals['historical_bindings']=[{'previous_issue_id':'OLD','target_id':'T','repair_object':'copy_compliance'}]
  previous={'sha256':'a'*64,'data':{'schema_version':'1.0','images':[{'image_id':'IMAGE-A'}],'issues':[{'issue_id':'OLD','image_id':'IMAGE-A'}]}}
  return previous,row
 def test_initial(self):self.assertEqual(self.errors(),[])
 def test_issue_and_contradicted(self):self.issue();self.assertEqual(self.errors(),[])
 def test_no_value_with_open_is_valid(self):
  previous,row=self.history();Fixtures.rewrite_fact(self,'unknown');ctx=self.ctx(previous);self.assertEqual(self.errors(ctx),[]);self.assertEqual(p.decorate(self.report,ctx)['system_metadata']['target_rollups'][0]['status'],'issue')
 def test_closed_unverified_rollup_clear(self):
  previous,row=self.history('closed');ctx=self.ctx(previous);self.assertEqual(self.errors(ctx),[]);out=p.decorate(self.report,ctx);self.assertEqual(out['target_assessments'][0]['status'],'uncertain');self.assertEqual(out['system_metadata']['target_rollups'][0]['status'],'clear')
 def test_partial_rollup_issue(self):
  previous,row=self.history('partial');ctx=self.ctx(previous);self.assertEqual(self.errors(ctx),[]);self.assertEqual(p.target_rollups(self.report,ctx)[0]['status'],'issue')
 def test_unknown_rollup_uncertain(self):
  previous,row=self.history('unknown');ctx=self.ctx(previous);self.assertEqual(self.errors(ctx),[]);self.assertEqual(p.target_rollups(self.report,ctx)[0]['status'],'uncertain')
 def test_not_assessed_no_obligation_clear(self):
  self.report['target_assessments'][0]['status']='not_assessed';self.report['coverage_status']='partial';ctx=self.ctx();self.assertEqual(self.errors(ctx),[]);self.assertEqual(p.target_rollups(self.report,ctx)[0]['status'],'clear')
 def test_same_image_different_targets(self):
  self.issue();t=copy.deepcopy(self.target);t.update(target_id='T2',subject='Different proposition');self.goals['targets'].append(t);self.report['target_assessments'].append({'target_id':'T2','status':'uncertain','basis':'Distinct proposition'});ctx=self.ctx();self.assertEqual(self.errors(ctx),[]);roll=p.target_rollups(self.report,ctx);self.assertEqual([r['status'] for r in roll],['issue','clear']);self.assertEqual(roll[1]['new_issue_ids'],[])
 def test_model_metadata_rejected(self):
  self.report['system_metadata']={};self.assertTrue(self.errors())
  with self.assertRaises(ValueError):p.decorate(self.report,self.ctx())
 def test_decorate_preserves_raw(self):
  before=copy.deepcopy(self.report);out=p.decorate(self.report,self.ctx());self.assertEqual(self.report,before);out.pop('system_metadata');self.assertEqual(out,before)
 def test_metadata_tamper_changes_composition(self):
  ctx=self.ctx();out=p.decorate(self.report,ctx);out['system_metadata']['target_rollups'][0]['status']='issue';self.assertNotEqual(p.canon(out),p.canon(p.decorate(self.report,ctx)))
 def test_legacy_target_results_rejected(self):self.report['target_results']=self.report['target_assessments'];self.assertTrue(self.errors())
 def test_missing_assessment(self):self.report['target_assessments']=[];self.assertTrue(self.errors())
 def test_duplicate_assessment(self):self.report['target_assessments']*=2;self.assertTrue(self.errors())
 def test_unknown_assessment_target(self):self.report['target_assessments'][0]['target_id']='OTHER';self.assertTrue(self.errors())
 def test_no_value_cannot_claim_truth(self):
  Fixtures.rewrite_fact(self,'unknown')
  for status in ('consistent','contradicted'):
   self.report['target_assessments'][0]['status']=status;self.assertTrue(self.errors())
 def test_unknown_fact_no_false_issue(self):Fixtures.rewrite_fact(self,'unknown');self.issue();self.assertTrue(self.errors())
 def test_missing_history_row(self):
  previous,row=self.history();self.report['rechecks']=[];self.assertTrue(self.errors(self.ctx(previous)))
 def test_swapped_history_target(self):
  previous,row=self.history();row['target_id']='OTHER';self.assertTrue(self.errors(self.ctx(previous)))
 def test_closed_gate_preserved(self):
  previous,row=self.history('closed');row['unresolved_parts']=['still unresolved'];self.assertTrue(self.errors(self.ctx(previous)))
 def test_fact_confirmation_gate_preserved(self):
  previous,row=self.history('closed');row['repair_object']='fact_confirmation';self.goals['historical_bindings'][0]['repair_object']='fact_confirmation';self.assertTrue(self.errors(self.ctx(previous)))
 def test_issue_priority_and_ids(self):
  previous,row=self.history('unknown');new=self.issue();self.report['target_assessments'][0]['status']='uncertain';ctx=self.ctx(previous);self.assertEqual(self.errors(ctx),[]);roll=p.target_rollups(self.report,ctx)[0];self.assertEqual(roll,{'target_id':'T','status':'issue','new_issue_ids':['I'],'previous_issue_ids':['OLD']})
 def test_prompt_separates_layers(self):
  text=p.prompt(self.ctx());self.assertIn('MODEL',text);self.assertNotIn('Use target_results once',text)
 def test_prompt_calibrates_consequence_not_emphasis(self):
  text=p.prompt(self.ctx());self.assertIn('Ordinary performance, durability, insulation duration',text);self.assertIn('Missing tests, mandatory evidence, numerical precision',text);self.assertIn('Do not downgrade explicit electrical, fire, medical',text)
 def test_prompt_preserves_frozen_scope_and_uncertainty(self):
  text=p.prompt(self.ctx());self.assertIn('never change a frozen scope',text);self.assertIn('Unreadable product data does not establish its actual value',text);self.assertIn('do not force unknown or closed',text)
 def test_presentation_image_fact_not_applicable_still_rejected(self):
  self.target.update(fact_scope='image_content',assessment_mode='image_only',source_refs=[],fact_refs=[])
  row=self.issue();row.update(finding_type='presentation_defect',impact='material_information',claim_present=False,severity='P1',severity_basis='presentation_defect:material_information:claim_absent',fact_verification_status='not_applicable',fact_evidence_type='direct_image')
  self.assertTrue(self.errors());row['fact_verification_status']='contradicted';self.assertEqual(self.errors(),[])
 def test_real_world_consistent_rejected(self):
  self.target['fact_scope']='real_world_claim';self.report['target_assessments'][0]['status']='consistent';self.assertTrue(self.errors())
 def test_real_world_contradicted_rejected(self):
  self.target['fact_scope']='real_world_claim';self.report['target_assessments'][0]['status']='contradicted';self.assertTrue(self.errors())
 def test_real_world_unverified_row_cannot_hide_consistent(self):
  previous,row=self.history();self.target['fact_scope']='real_world_claim';row['fact_scope']='real_world_claim';self.report['target_assessments'][0]['status']='consistent';ctx=self.ctx(previous);self.assertTrue(self.errors(ctx));self.report['target_assessments'][0]['status']='uncertain';self.assertEqual(self.errors(ctx),[])
 def test_source_metadata_preserved(self):
  ctx=self.ctx();out=p.decorate(self.report,ctx);self.assertEqual(out['system_metadata']['source_checks'],ctx['system_metadata']['source_checks']);self.assertEqual(out['system_metadata']['origin'],'system_contract')

def load_tests(loader,tests,pattern):
 return loader.loadTestsFromTestCase(Tests)

if __name__=='__main__':unittest.main(verbosity=2)
