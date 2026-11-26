"""Synthetic contract tests. Mocked P0 positives test protocol, not model accuracy."""
import base64,copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import shop_product_contract as c

NOW="2026-01-02T00:00:00Z"
def money(n=1000,currency="CNY",scale=2):return {"minor":n,"currency":currency,"scale":scale}
def coupon():
 return {"threshold":money(1000),"discount":money(100),"condition_text":"Only with eligible coupon; other applicability unknown",
         "valid_from":None,"valid_until":None,"conditions_complete":False}
def sample(sku="SYN-A",image="IMAGE-A"):
 return {"schema_version":"shop_product_facts/1.0","pack_id":"PACK-"+sku,"version":1,"mode":"simulated",
         "product_id":"PRODUCT-"+sku,"sku_id":sku,"as_of":"2026-01-01T00:00:00Z","expires_at":"2026-01-03T00:00:00Z",
         "image_ids":[image],"sources":[{"source_id":"same-source-name","version":1,"trust_category":"synthetic",
         "applicable_sku_ids":[sku],"as_of":"2026-01-01T00:00:00Z","expires_at":None}],
         "facts":[{"fact_id":"price","subject":"Synthetic pack list price","kind":"money","status":"known","value":money(),
                   "source_ids":["same-source-name"],"alternatives":[]},
                  {"fact_id":"pack-quantity","subject":"Count per sales pack","kind":"quantity","status":"known",
                   "value":{"quantity":"2","unit":"item","sales_unit":"pack"},"source_ids":["same-source-name"],"alternatives":[]},
                  {"fact_id":"inventory","subject":"Available inventory","kind":"quantity","status":"unknown","value":None,
                   "source_ids":[],"alternatives":[]},
                  {"fact_id":"coupon","subject":"Partially supplied conditional coupon","kind":"coupon","status":"known",
                   "value":coupon(),"source_ids":["same-source-name"],"alternatives":[]}]}

class FactsTests(unittest.TestCase):
 def setUp(self):self.data=sample()
 def validate(self,data=None,**kw):
  args=dict(expected_sku_id="SYN-A",expected_version=1,expected_image_ids=["IMAGE-A"],now=NOW);args.update(kw)
  return c.validate_facts(self.data if data is None else data,**args)
 def test_valid_keeps_unknown_and_no_coupon_calculation(self):
  before=copy.deepcopy(self.data);result=self.validate()
  self.assertTrue(result["valid"]);self.assertFalse(result["facts_verified"]);self.assertEqual(result["coupon_eligibility"],"not_evaluated")
  self.assertEqual(self.data,before);self.assertIsNone(self.data["facts"][2]["value"]);self.assertIn("coupon_conditions_incomplete:coupon",result["warnings"])
 def test_user_supplied_does_not_verify(self):
  self.data["mode"]="user_supplied";self.data["sources"][0]["trust_category"]="user_asserted"
  self.assertFalse(self.validate()["facts_verified"])
 def test_wrong_schema_version_missing_extra(self):
  for change in [{"schema_version":"unknown"},{"version":2},{"unexpected":True}]:
   d=copy.deepcopy(self.data);d.update(change)
   with self.assertRaises(ValueError):self.validate(d)
  del self.data["facts"][0]["subject"]
  with self.assertRaises(ValueError):self.validate()
 def test_wrong_sku_image_order(self):
  for kw in [{"expected_sku_id":"OTHER"},{"expected_image_ids":["OTHER"]},{"expected_version":True}]:
   with self.assertRaises(ValueError):self.validate(**kw)
 def test_expired_and_future_pack(self):
  for field,value in [("expires_at",NOW),("as_of","2026-01-04T00:00:00Z"),("as_of","2026-01-01")]:
   d=copy.deepcopy(self.data);d[field]=value
   with self.assertRaises(ValueError):self.validate(d)
 def test_source_expiry_null_is_unknown(self):
  self.assertIn("source_expiry_unknown:same-source-name",self.validate()["warnings"])
  self.data["sources"][0]["expires_at"]=NOW
  with self.assertRaises(ValueError):self.validate()
 def test_source_wrong_sku_version_future(self):
  for field,value in [("applicable_sku_ids",["OTHER"]),("version",True),("as_of","2026-01-02T00:00:00Z")]:
   d=copy.deepcopy(self.data);d["sources"][0][field]=value
   with self.assertRaises(ValueError):self.validate(d)
  with self.assertRaises(ValueError):self.validate(expected_source_versions={"same-source-name":2})
 def test_unknown_no_defaults(self):
  for value in [0,False,{"quantity":"0","unit":"item","sales_unit":"pack"}]:
   d=copy.deepcopy(self.data);d["facts"][2]["value"]=value
   with self.assertRaises(ValueError):self.validate(d)
 def test_conflict_preserved(self):
  f=self.data["facts"][0];f.update(status="conflicting",value=None,alternatives=[
    {"value":money(1000),"source_ids":["same-source-name"]},{"value":money(1200),"source_ids":["same-source-name"]}])
  self.assertTrue(self.validate()["valid"]);self.assertIsNone(f["value"])
  f["alternatives"][1]["value"]=money(1000)
  with self.assertRaises(ValueError):self.validate()
 def test_money_bool_float_negative(self):
  for value in [True,1.5,-1,float("nan"),float("inf")]:
   d=copy.deepcopy(self.data);d["facts"][0]["value"]["minor"]=value
   with self.assertRaises(ValueError):self.validate(d)
 def test_quantity_nan_infinity_exponent_zero(self):
  for value in ["NaN","Infinity","1e3","0","-1",True]:
   d=copy.deepcopy(self.data);d["facts"][1]["value"]["quantity"]=value
   with self.assertRaises(ValueError):self.validate(d)
 def test_quantity_basis_distinct_required(self):
  del self.data["facts"][1]["value"]["sales_unit"]
  with self.assertRaises(ValueError):self.validate()
 def test_coupon_negative_cross_currency_scale_excess(self):
  for key,value in [("minor",-1),("minor",1001),("currency","USD"),("scale",0),("minor",True)]:
   d=copy.deepcopy(self.data);d["facts"][3]["value"]["discount"][key]=value
   with self.assertRaises(ValueError):self.validate(d)
 def test_coupon_incomplete_not_claim_complete(self):
  self.data["facts"][3]["value"]["conditions_complete"]=True
  with self.assertRaises(ValueError):self.validate()
 def test_coupon_expired_warn_not_apply(self):
  self.data["facts"][3]["value"].update(valid_from="2025-01-01T00:00:00Z",valid_until="2026-01-01T00:00:00Z",conditions_complete=True)
  result=self.validate();self.assertIn("coupon_expired:coupon",result["warnings"]);self.assertEqual(result["coupon_eligibility"],"not_evaluated")
 def test_missing_unknown_duplicate_source(self):
  self.data["facts"][0]["source_ids"]=["OTHER"]
  with self.assertRaises(ValueError):self.validate()
  self.data=sample();self.data["sources"]*=2
  with self.assertRaises(ValueError):self.validate()
 def test_json_duplicate_nonfinite_bom(self):
  for raw in [b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',b'\xef\xbb\xbf{}']:
   with self.assertRaises(ValueError):c.parse_bytes(raw)

class BundleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.a=self.root/"a.json";self.b=self.root/"b.json"
  self.write(self.a,sample());self.write(self.b,sample("SYN-B","IMAGE-B"))
 def tearDown(self):self.tmp.cleanup()
 def write(self,path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf8")
 def test_single_exact_bytes(self):
  task=c.build_task(self.a,"Read only").encode("utf8")
  self.assertEqual(c.extract_task_facts(task),self.a.read_bytes())
 def test_multiple_skus_independent_same_source_names(self):
  task=c.build_task([self.a,self.b],"Read only").encode("utf8")
  bundle=c.extract_task_bundle(task);self.assertEqual(len(bundle["entries"]),2)
  self.assertEqual(c.extract_task_facts(task,"SYN-B"),self.b.read_bytes())
  with self.assertRaises(ValueError):c.extract_task_facts(task)
  self.assertEqual(len(c.bundle_index([self.a,self.b])),2)
 def test_duplicate_pack_sku_or_image_rejected(self):
  for key in ["pack_id","sku_id","image_ids"]:
   other=sample("SYN-B","IMAGE-B");other[key]=sample()[key];self.write(self.b,other)
   with self.assertRaises(ValueError):c.build_task([self.a,self.b],"Read only")
 def test_cross_pack_source_not_borrowed(self):
  other=sample("SYN-B","IMAGE-B");other["sources"]=[]
  with self.assertRaises(ValueError):c.validate_facts(other,expected_sku_id="SYN-B",expected_version=1,expected_image_ids=["IMAGE-B"],now=NOW)
 def test_reserved_markers_in_text_or_instructions(self):
  for token in [c.BEGIN,c.END,c.HASH]:
   with self.assertRaises(ValueError):c.build_task(self.a,"Read "+token)
   d=sample();d["facts"][0]["subject"]=token;self.write(self.a,d)
   with self.assertRaises(ValueError):c.build_task(self.a,"Read")
   self.write(self.a,sample())
 def test_task_limit_includes_bundle_and_instructions(self):
  with self.assertRaises(ValueError):c.build_task(self.a,"x"*(256*1024))
 def test_duplicate_or_mutated_block(self):
  task=c.build_task(self.a,"Read only")
  for changed in [task+task,task.replace(c.BEGIN,c.BEGIN+"\n"+c.BEGIN),task.replace("Synthetic pack list price","Changed fact")]:
   with self.assertRaises(ValueError):c.extract_task_bundle(changed.encode("utf8"))
 def test_bundle_hash_metadata_and_readable_mismatch(self):
  base=c.make_bundle([self.a,self.b])
  for key,value in [("raw_facts_sha256","0"*64),("sku_id","OTHER"),("version",True)]:
   bundle=copy.deepcopy(base);bundle["entries"][0][key]=value
   with self.assertRaises(ValueError):c._check_bundle(bundle)
  bundle=copy.deepcopy(base);bundle["entries"][0]["facts"]["sku_id"]="OTHER"
  with self.assertRaises(ValueError):c._check_bundle(bundle)

 def test_readable_type_confusion_rejected(self):
  cases=[(["version"],True),(["version"],1.0),(["sources",0,"version"],True),(["facts",0,"value","minor"],True),(["facts",0,"value","minor"],1000.0),(["facts",0,"value","scale"],2.0),(["facts",3,"value","conditions_complete"],0)]
  for path,value in cases:
   with self.subTest(path=path,value=value):
    bundle=c.make_bundle([self.a]);obj=bundle["entries"][0]["facts"]
    for key in path[:-1]:obj=obj[key]
    obj[path[-1]]=value
    with self.assertRaises(ValueError):c._check_bundle(bundle)
 def test_raw_type_confusion_rejected(self):
  bundle=c.make_bundle([self.a]);row=bundle["entries"][0]
  bad=copy.deepcopy(row["facts"]);bad["version"]=True
  raw=json.dumps(bad).encode("utf8");row["raw_facts_base64"]=base64.b64encode(raw).decode();row["raw_facts_sha256"]=c.digest(raw)
  with self.assertRaises(ValueError):c._check_bundle(bundle)
 def test_canonical_type_sensitive(self):
  self.assertNotEqual(c._canonical({"v":[1]}),c._canonical({"v":[True]}))
  self.assertNotEqual(c._canonical({"v":[1]}),c._canonical({"v":[1.0]}))
 def test_standalone_helper_exact_bytes(self):
  import shop_facts_task_helper as helper
  for paths in [self.a,[self.a,self.b]]:
   self.assertEqual(c.build_task(paths,"Read only\n中文" ).encode("utf8"),helper.build_task(paths,"Read only\n中文").encode("utf8"))
   self.assertEqual(c.bundle_index(paths),helper.bundle_index(paths))

class HandoffTests(BundleTests):
 def setUp(self):
  super().setUp();self.run=self.root/"run";self.run.mkdir();self.hand=self.root/"handoff.json"
  task=c.build_task([self.a,self.b],"Read only").encode("utf8");(self.run/"task.txt").write_bytes(task)
  images=json.dumps([{"image_id":"IMAGE-A"},{"image_id":"IMAGE-B"}]).encode("utf8");(self.run/"images.json").write_bytes(images)
  contract={"items":[{"path":"task.txt","sha256":c.digest(task)},{"path":"images.json","sha256":c.digest(images)}]}
  self.write(self.run/"contract.json",contract)
  report={"schema_version":"1.2","issues":[{"issue_id":"ISS-A","image_id":"IMAGE-A","severity":"P1","resolution_status":"unknown","fact_verification_status":"unverified"},{"issue_id":"ISS-B","image_id":"IMAGE-B","severity":"P0","resolution_status":"open","fact_verification_status":"unverified"}],"rechecks":[{"previous_issue_id":"OLD-A","image_id":"IMAGE-A","resolution_status":"partial","fact_verification_status":"unverified"}]}
  self.write(self.run/"issues.json",report);self.write(self.run/"result.json",{"role":"douyin-shop-ops","artifacts":{"issues.json":c.digest((self.run/"issues.json").read_bytes())}})
  self.handoff={"schema_version":"shop_handoff/1.0","handoff_id":"SYN-H","sender_role":"douyin-shop-ops","recipient_role":"douyin-selection","scope":"offline_review","release_decision":"not_authorized","sku_id":"SYN-A","facts_version":1,"facts_sha256":c.digest(self.a.read_bytes()),"run_id":"SYN-RUN","contract_sha256":c.digest((self.run/"contract.json").read_bytes()),"receipt_sha256":c.digest((self.run/"result.json").read_bytes())}
  self.write(self.hand,self.handoff)
  self.kw=dict(expected_run_id="SYN-RUN",expected_contract_sha256=self.handoff["contract_sha256"],expected_receipt_sha256=self.handoff["receipt_sha256"],expected_facts_sha256=self.handoff["facts_sha256"],expected_handoff_sha256=c.digest(self.hand.read_bytes()),expected_sku_id="SYN-A",expected_facts_version=1,expected_image_ids=["IMAGE-A"],expected_recipient_role="douyin-selection",expected_bundle_index=c.bundle_index([self.a,self.b]),now=NOW)
 def receive(self,**changes):
  kw=dict(self.kw);kw.update(changes)
  return c.receive_handoff(self.hand,self.a,self.run,**kw)
 def test_p0_rejects_real_incomplete_run(self):
  self.assertFalse(self.receive()["accepted"])
 def test_mocked_positive_calls_p0_and_keeps_facts(self):
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":True}) as accept:
   result=self.receive();self.assertTrue(result["accepted"],result);accept.assert_called_once_with(self.run,"SYN-RUN",self.kw["expected_contract_sha256"],self.kw["expected_receipt_sha256"])
   self.assertEqual([x["issue_id"] for x in result["scoped_issues"]],["ISS-A"]);self.assertEqual(result["scoped_rechecks"][0]["resolution_status"],"partial");self.assertEqual(result["scoped_issues"][0]["fact_verification_status"],"unverified")
   self.assertFalse(result["facts_verified"]);self.assertIsNone(result["facts"]["facts"][2]["value"]);self.assertEqual(result["release_decision"],"not_authorized")
 def test_caller_cannot_override_p0(self):
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":False,"errors":["blocked"]}):
   self.assertFalse(self.receive()["accepted"])
 def test_tampered_handoff_even_if_repin_rejects_extra_accepted(self):
  self.handoff["accepted"]=True;self.write(self.hand,self.handoff)
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":True}):
   self.assertFalse(self.receive()["accepted"])
   self.assertFalse(self.receive(expected_handoff_sha256=c.digest(self.hand.read_bytes()))["accepted"])
 def test_wrong_destination_and_pin(self):
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":True}):
   for changes in [{"expected_recipient_role":"tiktok-selection"},{"expected_facts_sha256":"0"*64},{"expected_sku_id":"OTHER"},{"expected_facts_version":2},{"expected_image_ids":["IMAGE-B"]},{"expected_bundle_index":[]}]:
    self.assertFalse(self.receive(**changes)["accepted"])
 def test_report_changed_after_acceptance(self):
  (self.run/"issues.json").write_text("{}",encoding="utf8")
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":True}):self.assertFalse(self.receive()["accepted"])
 def test_task_changed_after_acceptance(self):
  (self.run/"task.txt").write_text("fake",encoding="utf8")
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":True}):self.assertFalse(self.receive()["accepted"])
 def test_expired_sibling_pack_rejected(self):
  # Even this SKU cannot consume a bundle whose other SKU pack is stale.
  with patch.object(c.shop_run_acceptance,"accept",return_value={"accepted":True}):
   self.assertFalse(self.receive(now="2026-01-04T00:00:00Z")["accepted"])

def load_tests(loader,tests,pattern):
 suites=[]
 for cls in (FactsTests,BundleTests,HandoffTests):
  suites.extend(cls(name) for name in cls.__dict__ if name.startswith("test_"))
 return unittest.TestSuite(suites)
if __name__=="__main__":unittest.main(verbosity=2)
