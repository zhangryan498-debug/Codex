import unittest,copy,sys,tempfile,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import campaign_handoff as c
class Tests(unittest.TestCase):
 def setUp(self):
  self.a=dict(sku='B6',revision=3,payable_cents=11900,currency='CNY',timezone='Asia/Shanghai',valid_from_inclusive='2026-12-01T00:00:00+08:00',valid_until_exclusive='2026-12-03T00:00:00+08:00')
  self.a.update(list_price_cents=12900,coupon='100-10 once, no stacking',shipping='free')
  self.p=[dict(packet_id='H1',payload=dict(role='douyin-shop-ops',sku='B6',revision=3,payable_cents=11900,terms={**{k:self.a[k] for k in c.TERMS},'evidence_scope':'synthetic fixture'}))]
 def test_valid(self):self.assertEqual(c.validate(self.a,self.p,'douyin-shop-ops')['records'][0]['field_gate'],'pass')
 def test_missing_and_mismatch_every_field(self):
  for key in c.IDENTITY+c.TERMS:
   for mode in ('missing','mismatch'):
    with self.subTest(key=key,mode=mode):
     p=copy.deepcopy(self.p);o=p[0]['payload'] if key in c.IDENTITY else p[0]['payload']['terms']
     if mode=='missing':o.pop(key)
     else:o[key]='wrong'
     self.assertEqual(c.validate(self.a,p,'douyin-shop-ops')['records'][0]['field_gate'],'fail')
 def test_no_infer_timezone_from_offset(self):
  self.p[0]['payload']['terms'].pop('timezone');g=c.validate(self.a,self.p,'douyin-shop-ops');self.assertEqual(g['records'][0]['errors'],[{'field':'terms.timezone','kind':'missing'}])
 def test_no_boolean_money(self):
  self.p[0]['payload']['payable_cents']=True;self.assertEqual(c.validate(self.a,self.p,'douyin-shop-ops')['records'][0]['field_gate'],'fail')
 def test_missing_evidence(self):
  self.p[0]['payload']['terms']['evidence_scope']='';self.assertEqual(c.validate(self.a,self.p,'douyin-shop-ops')['records'][0]['field_gate'],'fail')
 def test_receiver_mismatch(self):self.assertEqual(c.validate(self.a,self.p,'douyin-ad-buyer')['records'][0]['field_gate'],'fail')
 def test_duplicates(self):
  with self.assertRaises(ValueError):c.validate(self.a,self.p*2,'douyin-shop-ops')
 def test_bad_authority(self):
  for k in c.IDENTITY+c.TERMS:
   a=copy.deepcopy(self.a);a.pop(k)
   with self.subTest(k=k),self.assertRaises(ValueError):c.validate(a,self.p,'douyin-shop-ops')
 def test_model_cannot_override_gate(self):
  self.p[0]['payload']['terms'].pop('timezone');m=dict(scope='synthetic_offline',release_status='not_authorized',receiver_role='douyin-shop-ops',receipts=[dict(packet_id='H1',offline_receipt='accepted')]);r=c.combine(self.a,self.p,'douyin-shop-ops',m,dict(authority_sha256=c.digest(self.a),packets_sha256=c.digest(self.p),model_receipts_sha256=c.digest(m),receiver_role='douyin-shop-ops',run_id='test',task_sha256='test'));self.assertEqual(r['records'][0]['system_receipt'],'rejected');self.assertTrue(r['records'][0]['model_gate_conflict']);self.assertEqual(r['release_status'],'not_authorized')
 def test_invalid_model_scope(self):
  with self.assertRaises(ValueError):c.combine(self.a,self.p,'douyin-shop-ops',{'scope':'live','release_status':'approved'},None)
 def test_hash_binds_input(self):
  g=c.validate(self.a,self.p,'douyin-shop-ops');p=copy.deepcopy(self.p);p[0]['payload']['revision']=2;self.assertNotEqual(g['packets_sha256'],c.validate(self.a,p,'douyin-shop-ops')['packets_sha256'])
 def test_missing_binding(self):
  with self.assertRaises(ValueError):c.combine(self.a,self.p,'douyin-shop-ops',{},None)
 def test_stale_binding(self):
  m=dict(scope='synthetic_offline',release_status='not_authorized',receiver_role='douyin-shop-ops',receipts=[dict(packet_id='H1',offline_receipt='accepted')]);b=dict(authority_sha256=c.digest(self.a),packets_sha256=c.digest(self.p),model_receipts_sha256=c.digest(m),receiver_role='douyin-shop-ops',run_id='test',task_sha256='test')
  self.a['revision']=4;self.p[0]['payload']['revision']=4
  with self.assertRaises(ValueError):c.combine(self.a,self.p,'douyin-shop-ops',m,b)
 def test_real_run_binding_and_mutation(self):
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp)/'run-1';run.mkdir();model=dict(scope='synthetic_offline',release_status='not_authorized',receiver_role='douyin-shop-ops',receipts=[dict(packet_id='H1',offline_receipt='accepted')])
   task=('AUTHORITY\n'+json.dumps(self.a)+'\nPACKETS\n'+json.dumps(self.p)+'\nFIELD_CHECKS\n{}').replace('\n','\r\n').encode();answer=json.dumps(model).encode();(run/'task.txt').write_bytes(task);(run/'answer.md').write_bytes(answer);(run/'result.json').write_text(json.dumps(dict(status='completed',run_id=run.name,task_sha256=hashlib.sha256(task).hexdigest(),answer_sha256=hashlib.sha256(answer).hexdigest())),encoding='utf-8')
   self.assertEqual(c.combine_run(self.a,self.p,'douyin-shop-ops',run)['records'][0]['system_receipt'],'accepted')
   a=copy.deepcopy(self.a);p=copy.deepcopy(self.p);a['revision']=4;p[0]['payload']['revision']=4
   with self.assertRaises(ValueError):c.combine_run(a,p,'douyin-shop-ops',run)
   (run/'answer.md').write_text('{}',encoding='utf-8')
   with self.assertRaises(ValueError):c.combine_run(self.a,self.p,'douyin-shop-ops',run)
if __name__=='__main__':unittest.main()
