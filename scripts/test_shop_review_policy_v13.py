"""Generic synthetic policy tests; no blind material or model execution."""
import copy,json,tempfile,unittest
from pathlib import Path
import shop_review_policy_v13 as p
import shop_product_contract as f
from test_shop_product_contract import sample
from run_codex_role import validate_report

class Tests(unittest.TestCase):
 def setUp(self):
  self.images=[{'image_id':'IMAGE-A','file':'synthetic.png'}]
  self.target={'target_id':'T','image_ids':['IMAGE-A'],'subject':'Supplied pack list price','fact_scope':'provided_information','assessment_mode':'provided_consistency','required_evidence':[],'source_refs':[{'pack_id':'PACK-SYN-A','source_id':'same-source-name'}],'fact_refs':[{'pack_id':'PACK-SYN-A','fact_id':'price'}],'comparison_image_ids':[]}
  self.goals={'schema_version':'shop_review_goals/1.0','assessment_time':'2026-01-02T00:00:00Z','targets':[self.target],'historical_bindings':[]}
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);path=Path(self.temp.name)/'facts.json';path.write_text(json.dumps(sample()),encoding='utf8');self.bundle=f.make_bundle([path])
  self.report={'schema_version':'1.3','summary':'Synthetic comparison only','release_decision':'not_authorized','images':[{'image_id':'IMAGE-A','image_file':'synthetic.png','read_status':'read','observation':'Printed text','uncertainties':[]}],'issues':[],'previous_report_sha256':None,'rechecks':[],'target_results':[{'target_id':'T','status':'consistent','basis':'Against supplied information only'}],'target_gaps':[],'coverage_status':'complete'}
 def ctx(self):return p.prepare(self.goals,self.images,self.bundle)
 def validate(self):return p.validate(self.report,self.images,self.ctx(),validate_report)
 def issue(self):
  r={'issue_id':'I','image_id':'IMAGE-A','image_file':'synthetic.png','original_text':'Synthetic mismatch','basis':'Supplied fact comparison','severity':'P1','action':'Correct copy','repair_copy':'Corrected text','retest_status':'pending','resolution_status':'open','fact_verification_status':'contradicted','fact_basis':'Relative to supplied facts, not physical verification','fact_subject':self.target['subject'],'fact_scope':self.target['fact_scope'],'resolution_basis_type':'other','fact_evidence_type':'user_confirmed','unsupported_claim_present':False,'resolved_parts':[],'unresolved_parts':['copy mismatch'],'target_id':'T','repair_object':'copy_compliance','finding_type':'provided_conflict','claim_present':True,'severity_basis':'provided_conflict:material_information:claim_present','impact':'material_information'}
  self.report['issues']=[r];self.report['target_results'][0]['status']='issue';return r
 def test_initial_valid(self):self.assertEqual(self.validate(),[])
 def test_issue_valid(self):self.issue();self.assertEqual(self.validate(),[])
 def test_source_unknown_expiry(self):self.assertEqual(self.ctx()['system_metadata']['source_checks'][0]['status'],'unknown_expiry')
 def test_source_expired(self):
  self.change_source(expires_at='2026-01-02T00:00:00Z');self.assertEqual(self.ctx()['system_metadata']['source_checks'][0]['status'],'expired')
 def test_source_future(self):
  self.change_source(as_of='2026-01-03T00:00:00Z');self.assertEqual(self.ctx()['system_metadata']['source_checks'][0]['status'],'future')
 def test_source_valid(self):
  self.change_source(expires_at='2026-01-03T00:00:00Z');self.assertEqual(self.ctx()['system_metadata']['source_checks'][0]['status'],'valid')
 def change_source(self,**kw):
  import base64
  row=self.bundle['entries'][0];row['facts']['sources'][0].update(kw);raw=json.dumps(row['facts']).encode();row['raw_facts_base64']=base64.b64encode(raw).decode();row['raw_facts_sha256']=f.digest(raw)
 def test_invalid_source_dates(self):
  self.change_source(expires_at='2025-01-01T00:00:00Z')
  with self.assertRaises(ValueError):self.ctx()
 def test_assessment_time_requires_utc(self):
  self.goals['assessment_time']='2026-01-02'
  with self.assertRaises(ValueError):self.ctx()
 def test_unknown_goals_version(self):
  self.goals['schema_version']='2'
  with self.assertRaises(ValueError):self.ctx()
 def test_target_impact_forbidden(self):
  self.target['impact']='material_information'
  with self.assertRaises(ValueError):self.ctx()
 def test_unknown_row_impact_rejected(self):
  self.issue()['impact']='automatic_pass'
  self.assertTrue(self.validate())
 def test_unknown_goals_field(self):
  self.goals['approve']=True
  with self.assertRaises(ValueError):self.ctx()
 def test_unknown_image(self):
  self.target['image_ids']=['OTHER']
  with self.assertRaises(ValueError):self.ctx()
 def test_duplicate_target(self):
  self.goals['targets']*=2
  with self.assertRaises(ValueError):self.ctx()
 def test_reference_required(self):
  self.target.update(source_refs=[],fact_refs=[])
  with self.assertRaises(ValueError):self.ctx()
 def test_unknown_source(self):
  self.target['source_refs'][0]['source_id']='OTHER'
  with self.assertRaises(ValueError):self.ctx()
 def test_unknown_fact(self):
  self.target['fact_refs'][0]['fact_id']='OTHER'
  with self.assertRaises(ValueError):self.ctx()
 def test_cross_image_needs_two(self):
  self.target['comparison_image_ids']=['IMAGE-A']
  with self.assertRaises(ValueError):self.ctx()
 def test_mandatory_requires_evidence(self):
  self.target['assessment_mode']='mandatory_evidence'
  with self.assertRaises(ValueError):self.ctx()
 def test_missing_target_result(self):self.report['target_results']=[];self.assertTrue(self.validate())
 def test_gap_requires_partial(self):
  self.report['target_gaps']=[{'image_ids':['IMAGE-A'],'subject':'New concern','reason':'Outside fixed goals'}];self.assertTrue(self.validate());self.report['coverage_status']='partial';self.assertEqual(self.validate(),[])
 def test_unread_requires_partial(self):self.report['images'][0]['read_status']='unreadable';self.assertTrue(self.validate())
 def test_image_only_no_automatic_issue(self):
  self.target.update(assessment_mode='image_only',source_refs=[],fact_refs=[]);self.report['target_results'][0]['status']='uncertain';self.report['images'][0]['uncertainties']=['Not supplied in facts'];self.assertEqual(self.validate(),[])
 def test_image_only_conflict_rejected(self):self.issue();self.target['assessment_mode']='image_only';self.assertTrue(self.validate())
 def test_missing_evidence_not_false(self):
  r=self.issue();self.target.update(assessment_mode='mandatory_evidence',required_evidence=['certificate']);r.update(finding_type='mandatory_evidence_missing',severity_basis='mandatory_evidence_missing:material_information:claim_present');self.assertTrue(self.validate());r['fact_verification_status']='unverified';self.assertEqual(self.validate(),[])
 def test_subject_swap(self):self.issue()['fact_subject']='Changed proposition';self.assertTrue(self.validate())
 def test_scope_swap(self):self.issue()['fact_scope']='image_content';self.assertTrue(self.validate())
 def test_severity_inflation(self):self.issue()['severity']='P0';self.assertTrue(self.validate())
 def test_severity_table(self):
  for impact,expected in [('critical_transaction','P0'),('high_risk_claim','P0'),('material_information','P1'),('minor_presentation','P2')]:self.assertEqual(p.severity('provided_conflict',impact,True),expected)
  self.assertEqual(p.severity('mandatory_evidence_missing','high_risk_claim',True),'P0')
  self.assertEqual(p.severity('presentation_defect','minor_presentation',False),'P2')
  with self.assertRaises(ValueError):p.severity('presentation_defect','high_risk_claim',True)
 def test_raw_metadata_rejected(self):
  self.report['system_metadata']={}
  with self.assertRaises(ValueError):p.decorate(self.report,self.ctx())
  self.assertTrue(self.validate())
 def test_decorate_nonmutating(self):
  before=copy.deepcopy(self.report);out=p.decorate(self.report,self.ctx());self.assertEqual(self.report,before);self.assertEqual(out['system_metadata']['origin'],'system_contract');self.assertFalse(out['system_metadata']['is_physical_verification'])
 def test_context_json_serializable(self):self.assertEqual(json.loads(json.dumps(self.ctx())),self.ctx())
 def test_initial_fake_history_rejected(self):self.report['previous_report_sha256']='a'*64;self.assertTrue(self.validate())
 def test_history_requires_all_bindings(self):
  previous={'sha256':'a'*64,'data':{'schema_version':'1.0','images':[{'image_id':'IMAGE-A'}],'issues':[{'issue_id':'OLD','image_id':'IMAGE-A'}]}}
  with self.assertRaises(ValueError):p.prepare(self.goals,self.images,self.bundle,previous)
 def test_history_closed_unverified_fact_rejected(self):
  r=self.issue();self.report['issues']=[];self.report['target_results'][0]['status']='uncertain'
  row={k:v for k,v in r.items() if k not in ('issue_id','original_text','severity','repair_copy','retest_status','finding_type','claim_present','severity_basis','impact')};row.update(previous_issue_id='OLD',current_text='Claim removed',evidence='Current image',resolution_status='closed',fact_verification_status='unverified',resolution_basis_type='claim_removed',resolved_parts=['claim removed'],unresolved_parts=[],repair_object='fact_confirmation')
  self.report['rechecks']=[row];self.report['previous_report_sha256']='a'*64
  self.goals['historical_bindings']=[{'previous_issue_id':'OLD','target_id':'T','repair_object':'fact_confirmation'}]
  previous={'sha256':'a'*64,'data':{'schema_version':'1.0','images':[{'image_id':'IMAGE-A'}],'issues':[{'issue_id':'OLD','image_id':'IMAGE-A'}]}}
  ctx=p.prepare(self.goals,self.images,self.bundle,previous);self.assertTrue(p.validate(self.report,self.images,ctx,validate_report))
  self.goals['historical_bindings'][0]['repair_object']='copy_compliance';row['repair_object']='copy_compliance';ctx=p.prepare(self.goals,self.images,self.bundle,previous);self.assertEqual(p.validate(self.report,self.images,ctx,validate_report),[])
 def test_cross_sku_sourced_target_rejected(self):
  path=Path(self.temp.name)/'other.json';path.write_text(json.dumps(sample('SYN-B','IMAGE-B')),encoding='utf8');self.bundle['entries']+=f.make_bundle([path])['entries'];self.images.append({'image_id':'IMAGE-B','file':'other.png'});self.target['image_ids'].append('IMAGE-B')
  with self.assertRaises(ValueError):self.ctx()
 def test_cross_sku_issue_cannot_get_context(self):
  self.issue()['image_id']='IMAGE-B'
  path=Path(self.temp.name)/'other.json';path.write_text(json.dumps(sample('SYN-B','IMAGE-B')),encoding='utf8');self.bundle['entries']+=f.make_bundle([path])['entries'];self.images.append({'image_id':'IMAGE-B','file':'other.png'});self.target['image_ids'].append('IMAGE-B')
  with self.assertRaises(ValueError):self.validate()
 def test_multiple_pack_refs_rejected(self):
  path=Path(self.temp.name)/'other.json';path.write_text(json.dumps(sample('SYN-B','IMAGE-B')),encoding='utf8');self.bundle['entries']+=f.make_bundle([path])['entries'];self.images.append({'image_id':'IMAGE-B','file':'other.png'});self.target['source_refs'].append({'pack_id':'PACK-SYN-B','source_id':'same-source-name'})
  with self.assertRaises(ValueError):self.ctx()
 def test_history_target_aggregation(self):
  r=self.issue();self.report['issues']=[]
  row={k:v for k,v in r.items() if k not in ('issue_id','original_text','severity','repair_copy','retest_status','finding_type','claim_present','severity_basis','impact')};row.update(previous_issue_id='OLD',current_text='Current copy',evidence='Current image',fact_verification_status='unverified')
  self.report['rechecks']=[row];self.report['previous_report_sha256']='a'*64
  self.goals['historical_bindings']=[{'previous_issue_id':'OLD','target_id':'T','repair_object':'copy_compliance'}]
  previous={'sha256':'a'*64,'data':{'schema_version':'1.0','images':[{'image_id':'IMAGE-A'}],'issues':[{'issue_id':'OLD','image_id':'IMAGE-A'}]}}
  for state,good in [('open','issue'),('partial','issue'),('unknown','uncertain'),('closed','uncertain')]:
   row.update(resolution_status=state,resolved_parts=['corrected'] if state in ('partial','closed') else [],unresolved_parts=[] if state=='closed' else ['unresolved'],resolution_basis_type='claim_removed' if state=='closed' else 'other')
   self.report['target_results'][0]['status']='consistent';ctx=p.prepare(self.goals,self.images,self.bundle,previous)
   self.assertTrue(p.validate(self.report,self.images,ctx,validate_report),state)
   self.report['target_results'][0]['status']=good;self.assertEqual(p.validate(self.report,self.images,ctx,validate_report),[],state)
 def test_history_issue_priority_over_unknown(self):
  # Reuse generic history setup, then add a second unresolved binding to same target.
  self.test_history_target_aggregation()
  row=copy.deepcopy(self.report['rechecks'][0]);row.update(previous_issue_id='OLD2',resolution_status='open',resolved_parts=[],unresolved_parts=['still present'],resolution_basis_type='other');self.report['rechecks'].append(row)
  self.goals['historical_bindings'].append({'previous_issue_id':'OLD2','target_id':'T','repair_object':'copy_compliance'})
  previous={'sha256':'a'*64,'data':{'schema_version':'1.0','images':[{'image_id':'IMAGE-A'}],'issues':[{'issue_id':x,'image_id':'IMAGE-A'} for x in ('OLD','OLD2')]}}
  ctx=p.prepare(self.goals,self.images,self.bundle,previous);self.assertTrue(p.validate(self.report,self.images,ctx,validate_report));self.report['target_results'][0]['status']='issue';self.assertEqual(p.validate(self.report,self.images,ctx,validate_report),[])
 def rewrite_fact(self,status):
  import base64
  entry=self.bundle['entries'][0];fact=entry['facts']['facts'][0];fact['status']=status;fact['value']=None
  if status=='conflicting':fact['alternatives']=[{'value':{'minor':100,'currency':'CNY','scale':2},'source_ids':['same-source-name']},{'value':{'minor':200,'currency':'CNY','scale':2},'source_ids':['same-source-name']}]
  raw=json.dumps(entry['facts']).encode();entry['raw_facts_base64']=base64.b64encode(raw).decode();entry['raw_facts_sha256']=f.digest(raw)
 def test_unknown_fact_cannot_contradict(self):
  self.rewrite_fact('unknown');self.issue();self.assertFalse(self.ctx()['comparison_availability']['T']['available']);self.assertTrue(self.validate())
 def test_conflicting_fact_cannot_contradict(self):
  self.rewrite_fact('conflicting');self.issue();self.assertTrue(self.validate())
 def test_source_metadata_alone_not_value(self):
  self.target['fact_refs']=[];self.issue();self.assertTrue(self.validate())
 def test_no_value_target_remains_uncertain(self):
  self.rewrite_fact('unknown');self.assertTrue(self.validate());self.report['target_results'][0]['status']='uncertain';self.assertEqual(self.validate(),[])
 def test_no_value_image_content_not_blanket_rejected(self):
  self.rewrite_fact('unknown');self.target['fact_scope']='image_content';self.report['target_results'][0]['status']='uncertain';self.assertEqual(self.validate(),[])
 def test_explicit_cross_image_comparison_available(self):
  self.rewrite_fact('unknown');self.target.update(source_refs=[],fact_refs=[],image_ids=['IMAGE-A','IMAGE-B'],comparison_image_ids=['IMAGE-A','IMAGE-B']);self.images.append({'image_id':'IMAGE-B','file':'second.png'});self.assertTrue(self.ctx()['comparison_availability']['T']['available'])
 def test_known_ref_mixed_unknown_available(self):
  self.target['fact_refs'].append({'pack_id':'PACK-SYN-A','fact_id':'inventory'});self.assertTrue(self.ctx()['comparison_availability']['T']['available']);self.issue();self.assertEqual(self.validate(),[])
 def test_partial_requires_both_parts(self):
  row=self.issue();row['resolution_status']='partial';self.assertTrue(self.validate())

 def test_missing_fact_open_requires_target_issue(self):
  row=self.issue();self.target.update(assessment_mode='mandatory_evidence',required_evidence=['record'],fact_scope='real_world_claim');row.update(finding_type='mandatory_evidence_missing',fact_scope='real_world_claim',fact_verification_status='unverified',fact_evidence_type='missing',repair_object='fact_confirmation',claim_present=False,severity_basis='mandatory_evidence_missing:material_information:claim_absent')
  self.report['target_results'][0]['status']='uncertain';self.assertIn('Target must disclose new/open/partial issues',self.validate())
  self.report['target_results'][0]['status']='issue';self.assertEqual(self.validate(),[])
 def test_same_image_distinct_scope_not_merged(self):
  self.test_missing_fact_open_requires_target_issue()
  other=copy.deepcopy(self.target);other.update(target_id='INFORMATION',subject='Supplied information only',fact_scope='provided_information',assessment_mode='provided_consistency',required_evidence=[])
  self.goals['targets'].append(other);self.report['target_results'].append({'target_id':'INFORMATION','status':'consistent','basis':'Comparison with supplied value only'})
  self.assertEqual(self.validate(),[])
  self.report['target_results'][1]['status']='issue';self.assertIn('Target issue status lacks an active issue',self.validate())
 def test_history_open_cannot_hide_as_uncertain(self):
  self.test_history_issue_priority_over_unknown()
  self.report['target_results'][0]['status']='uncertain'
  previous={'sha256':'a'*64,'data':{'schema_version':'1.0','images':[{'image_id':'IMAGE-A'}],'issues':[{'issue_id':x,'image_id':'IMAGE-A'} for x in ('OLD','OLD2')]}}
  ctx=p.prepare(self.goals,self.images,self.bundle,previous);self.assertIn('Target must disclose new/open/partial issues',p.validate(self.report,self.images,ctx,validate_report))
 def test_prompt_distinguishes_truth_and_obligations(self):
  text=p.prompt(self.ctx());self.assertIn('Status is an obligation summary, NOT fact truth',text);self.assertIn('even when its fact_verification_status is unverified',text)
 def test_prompt_exact_target_and_priority(self):
  text=p.prompt(self.ctx());self.assertIn('never by image_id alone',text);self.assertIn('ONLY if no same-target new/open/partial issue takes priority',text)
 def test_schema_target_aggregation_description(self):
  text=json.loads(p.SCHEMA.read_text(encoding='utf8'))['properties']['target_results']['description'];self.assertIn('not fact truth',text);self.assertIn('Do not merge different targets',text)

if __name__=='__main__':unittest.main(verbosity=2)
