"""Synthetic-only recheck contracts; no blind inputs."""
import copy,json,tempfile,unittest,subprocess,sys
from pathlib import Path
import run_codex_role as m
import shop_image_recheck as r
class RecheckTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.p=Path(self.tmp.name)/"prior.json"
  self.images=[dict(image_id="img-01",file="current.png")]
  self.old=dict(schema_version="1.0",summary="synthetic",release_decision="not_authorized",images=[dict(image_id="img-01",image_file="old.png",read_status="read",observation="old",uncertainties=[])],issues=[dict(issue_id="old-1",image_id="img-01",image_file="old.png",original_text="old",basis="basis",severity="P1",action="review",repair_copy="proposal",retest_status="pending")])
  self.p.write_text(json.dumps(self.old),encoding="utf8");self.previous=r.load_previous(self.p,self.images,m.validate_report)
  self.report=copy.deepcopy(self.old);self.report.update(schema_version="1.1",previous_report_sha256=self.previous["sha256"],issues=[],rechecks=[dict(previous_issue_id="old-1",image_id="img-01",image_file="current.png",current_text="current",evidence="current visible evidence",basis="comparison",status="closed",action="offline close only")])
  self.report["images"][0]["image_file"]="current.png"
 def tearDown(self):self.tmp.cleanup()
 def validate(self):return r.validate_recheck(self.report,self.images,self.previous,m.validate_report)
 def test_closed_open_unknown(self):
  for status in ["closed","open","unknown"]:
   self.report["rechecks"][0]["status"]=status;self.assertEqual(self.validate(),[])
 def test_missing(self):
  self.report["rechecks"]=[];self.assertTrue(self.validate())
 def test_duplicate(self):
  self.report["rechecks"]*=2;self.assertTrue(self.validate())
 def test_unknown_old_id(self):
  self.report["rechecks"][0]["previous_issue_id"]="invented";self.assertTrue(self.validate())
 def test_bad_image(self):
  self.report["rechecks"][0]["image_id"]="img-02";self.assertTrue(self.validate())
 def test_bad_filename(self):
  self.report["rechecks"][0]["image_file"]="other.png";self.assertTrue(self.validate())
 def test_closed_unread(self):
  self.report["images"][0]["read_status"]="unreadable";self.assertTrue(self.validate())
 def test_empty_evidence(self):
  self.report["rechecks"][0]["evidence"]=" ";self.assertTrue(self.validate())
 def test_wrong_hash(self):
  self.report["previous_report_sha256"]="0"*64;self.assertTrue(self.validate())
 def test_release_forbidden(self):
  self.report["release_decision"]="approved";self.assertTrue(self.validate())
 def test_new_issue_cannot_close(self):
  row=copy.deepcopy(self.old["issues"][0]);row.update(issue_id="new-1",image_file="current.png",retest_status="closed");self.report["issues"]=[row];self.assertTrue(self.validate())
 def test_new_issue_no_reuse(self):
  row=copy.deepcopy(self.old["issues"][0]);row["image_file"]="current.png";self.report["issues"]=[row];self.assertTrue(self.validate())
 def test_order_binding(self):
  with self.assertRaises(ValueError):r.load_previous(self.p,[dict(image_id="img-02",file="current.png")],m.validate_report)
 def test_first_review_rejects_v11(self):self.assertTrue(m.validate_report(self.report,self.images))
 def test_standalone_rejects_no_proof(self):
  report=Path(self.tmp.name)/"report.json";manifest=Path(self.tmp.name)/"images.json"
  report.write_text(json.dumps(self.report),encoding="utf8");manifest.write_text(json.dumps(self.images),encoding="utf8")
  proc=subprocess.run([sys.executable,str(Path(m.__file__).with_name("validate_shop_image_issues.py")),"--report",str(report),"--manifest",str(manifest),"--previous",str(self.p)],capture_output=True,text=True)
  self.assertNotEqual(proc.returncode,0);self.assertFalse(json.loads(proc.stdout)["valid"])
 def test_previous_unchanged(self):
  before=self.p.read_bytes();r.prompt(self.previous);self.validate();self.assertEqual(before,self.p.read_bytes())
if __name__=="__main__":unittest.main(verbosity=2)
