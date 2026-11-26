"""P0 acceptance adversarial contracts; synthetic only, no real model."""
import contextlib,copy,io,json,sys,unittest,subprocess
from pathlib import Path
from unittest.mock import patch,MagicMock
from test_codex_role_entry import EntryTests
import run_codex_role as m
import shop_run_acceptance as a
class AcceptTests(EntryTests):
 def completed(self,version="1.0",restricted=False):
  if version=="1.0":self.assertEqual(self.invoke("read_success",True),0)
  else:
   self.setup_role();old=self.root/"old.json";old.write_text(json.dumps(self.report),encoding="utf8")
   report=copy.deepcopy(self.report);report.update(schema_version=version,previous_report_sha256=a.sha(old),rechecks=[])
   def fake(cmd,**kw):
    out=Path(cmd[cmd.index("-o")+1]);out.write_text(json.dumps(report),encoding="utf8")
    kw["stdout"].write('{"type":"turn.completed"}\n')
    kw["stderr"].write('2026-11-10T12:00:00Z INFO log: codex_otel.log_only: event.name="codex.tool_result" tool_name=view_image success=true arguments='+json.dumps({"path":str(self.png)})+' output=image\n')
    class P:returncode=0
    return P()
   with patch.object(m,"find_codex",return_value=sys.executable),patch.object(m,"run_process_tree",side_effect=fake),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    self.assertEqual(m.execute("douyin-shop-ops","fixture",values=[str(self.png)],root=self.root,recheck_from=str(old),recheck_version=version,restricted_tools=restricted),0)
  self.run=max((self.root/"work/codex-runs").iterdir(),key=lambda p:p.name)
  self.receipt=json.loads((self.run/"result.json").read_text())
  self.pins=(self.receipt["run_id"],a.sha(self.run/"contract.json"),a.sha(self.run/"result.json"))
 def verdict(self):return a.accept(self.run,*self.pins)
 def test_initial_full_accept(self):
  self.completed();self.assertTrue(self.verdict()["accepted"])
 def test_recheck11_full_accept(self):
  self.completed("1.1");self.assertTrue(self.verdict()["accepted"])
 def test_recheck12_full_accept(self):
  self.completed("1.2");self.assertTrue(self.verdict()["accepted"])
 def test_each_output_tamper(self):
  self.completed()
  for name in ["answer.raw.json","issues.json","answer.md","image-read-proof.json","input-integrity.json","schema-validation.json","events.jsonl","stderr.log"]:
   p=self.run/name;old=p.read_bytes();p.write_bytes(old+b" ");self.assertFalse(self.verdict()["accepted"],name);p.write_bytes(old)
 def test_each_contract_input_tamper(self):
  self.completed();contract=json.loads((self.run/"contract.json").read_text())
  for row in contract["items"]:
   p=self.run/row["path"];old=p.read_bytes();p.write_bytes(old+b" ");self.assertFalse(self.verdict()["accepted"],row["path"]);p.write_bytes(old)
 def test_history_snapshot_tamper(self):
  self.completed("1.2");p=self.run/"previous-issues.json";p.write_bytes(p.read_bytes()+b" ");self.assertFalse(self.verdict()["accepted"])
 def test_timeout_and_cancel_fail_closed(self):
  for exc,kind in [(subprocess.TimeoutExpired("synthetic",1),"timeout"),(KeyboardInterrupt(),"cancelled")]:
   self.setup_role()
   with patch.object(m,"find_codex",return_value=sys.executable),patch.object(m,"run_process_tree",side_effect=exc),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
    self.assertEqual(m.execute("douyin-shop-ops","fixture",values=[str(self.png)],root=self.root),1)
   run=max((self.root/"work/codex-runs").iterdir(),key=lambda p:p.name);result=json.loads((run/"result.json").read_text());self.assertEqual(result["failure_kind"],kind);self.assertFalse(result["acceptance_ready"]);self.assertFalse((run/"issues.json").exists())
 def test_restricted_command_is_bound(self):
  self.completed("1.2",restricted=True);cmd=json.loads((self.run/"command.json").read_text())
  for item in ["--ignore-user-config",'web_search="disabled"',"shell_tool","unified_exec","hooks"]:self.assertIn(item,cmd)
  self.assertTrue(self.verdict()["accepted"])
 def test_quarantine_failure_still_rejected(self):
  original=Path.replace
  def fault(path,target):
   if path.name in ("issues.json","answer.md"):raise OSError("synthetic quarantine failure")
   return original(path,target)
  with patch.object(a,"bind_outputs",side_effect=OSError("bind failure")),patch.object(Path,"replace",fault):
   self.assertEqual(self.invoke("read_success",True),1)
  run=next((self.root/"work/codex-runs").iterdir());receipt=json.loads((run/"result.json").read_text());self.assertTrue(receipt["quarantine_errors"])
  self.assertFalse(a.accept(run,receipt["run_id"],a.sha(run/"contract.json"),a.sha(run/"result.json"))["accepted"])
 def test_final_receipt_write_failure_rejected(self):
  original=m.write_json
  def fault(path,data):
   if path.name=="result.pending.json":raise OSError("synthetic receipt failure")
   return original(path,data)
  with patch.object(m,"write_json",side_effect=fault),self.assertRaises(OSError):self.invoke("read_success",True)
  run=next((self.root/"work/codex-runs").iterdir());self.assertFalse((run/"result.json").exists());self.assertFalse(a.accept(run,run.name,a.sha(run/"contract.json"),"0"*64)["accepted"])
 def test_missing_receipt(self):
  self.completed();(self.run/"result.json").unlink();self.assertFalse(self.verdict()["accepted"])
 def test_receipt_rewritten(self):
  self.completed();self.receipt["status"]="failed";a.dump(self.run/"result.json",self.receipt);self.assertFalse(self.verdict()["accepted"])
 def test_failed_even_new_receipt_pin(self):
  self.completed();self.receipt.update(status="failed",exit_code=5);a.dump(self.run/"result.json",self.receipt)
  self.assertFalse(a.accept(self.run,self.pins[0],self.pins[1],a.sha(self.run/"result.json"))["accepted"])
 def test_missing_external_pins(self):
  self.completed();self.assertFalse(a.accept(self.run,None,None,None)["accepted"])
 def test_wrong_run_replay(self):
  self.completed();self.assertFalse(a.accept(self.run,"different-run",*self.pins[1:])["accepted"])
 def test_swapped_receipt(self):
  self.completed();first=self.run;oldpins=self.pins;self.completed()
  (first/"result.json").write_bytes((self.run/"result.json").read_bytes());self.assertFalse(a.accept(first,*oldpins)["accepted"])
 def test_wrong_contract_pin(self):
  self.completed();self.assertFalse(a.accept(self.run,self.pins[0],"0"*64,self.pins[2])["accepted"])
 def test_failure_produces_no_consumable_outputs(self):
  self.assertEqual(self.invoke("image_changed",True),6)
  run=next((self.root/"work/codex-runs").iterdir())
  self.assertFalse((run/"issues.json").exists());self.assertFalse((run/"answer.md").exists());self.assertTrue((run/"answer.raw.json").exists())
 def test_render_failure_quarantined(self):
  with patch.object(m,"render_report",side_effect=OSError("synthetic write-stage fault")):
   self.assertEqual(self.invoke("read_success",True),1)
  run=next((self.root/"work/codex-runs").iterdir());self.assertFalse((run/"issues.json").exists());self.assertTrue((run/"failed-diagnostics/issues.json").exists())
 def test_binding_failure_quarantined(self):
  with patch.object(a,"bind_outputs",side_effect=OSError("synthetic bind fault")):
   self.assertEqual(self.invoke("read_success",True),1)
  run=next((self.root/"work/codex-runs").iterdir());self.assertFalse((run/"issues.json").exists());self.assertFalse((run/"answer.md").exists());self.assertTrue((run/"failed-diagnostics/answer.md").exists())
 def test_rule_drift_rejects(self):
  self.completed();contract=json.loads((self.run/"contract.json").read_text());rule=next(x for x in contract["rules"] if Path(x["source"]).is_relative_to(self.root.resolve()));p=Path(rule["source"]);p.write_bytes(p.read_bytes()+b" ");self.assertFalse(self.verdict()["accepted"])
 def test_missing_proof_produces_no_consumable_outputs(self):
  self.assertEqual(self.invoke("success",True),5)
  run=next((self.root/"work/codex-runs").iterdir());self.assertFalse((run/"issues.json").exists())
 def test_structure_only_never_accepts(self):
  self.completed()
  cmd=[sys.executable,str(Path(m.__file__).with_name("validate_shop_image_issues.py")),"--report",str(self.run/"issues.json"),"--manifest",str(self.run/"images.json"),"--mode","structure-only"]
  p=subprocess.run(cmd,capture_output=True,text=True,encoding="utf8");data=json.loads(p.stdout)
  self.assertEqual(p.returncode,0);self.assertTrue(data["schema_valid"]);self.assertFalse(data["accepted"])
 def test_default_cli_requires_pins(self):
  self.completed();p=subprocess.run([sys.executable,str(Path(m.__file__).with_name("validate_shop_image_issues.py")),"--report",str(self.run/"issues.json"),"--manifest",str(self.run/"images.json")],capture_output=True,text=True,encoding="utf8")
  self.assertNotEqual(p.returncode,0);self.assertFalse(json.loads(p.stdout)["accepted"])
 def test_task_resource_bound(self):
  with self.assertRaises(ValueError):m.read_task(task="x"*262145)
 def test_manifest_resource_bound(self):
  p=self.root/"large.json";p.write_bytes(b" "*1048577)
  with self.assertRaises(ValueError):m.resolve_images([],str(p))
 def test_image_pixel_bound(self):
  im=MagicMock();im.format="PNG";im.size=(10000,10000);im.__enter__.return_value=im
  with patch("PIL.Image.open",return_value=im),self.assertRaises(ValueError):m.resolve_images([str(self.png)])
 def test_safe_path_rejects_escape(self):
  with self.assertRaises(ValueError):a.safe_path(self.root,"../escape")
def load_tests(loader,tests,pattern):return unittest.TestSuite(AcceptTests(n) for n in AcceptTests.__dict__ if n.startswith("test_"))
if __name__=="__main__":unittest.main(verbosity=2)
