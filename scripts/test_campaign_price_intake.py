"""New caller/entry regression, all synthetic; model processes are mocked."""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import campaign_price_handoff as g
import campaign_price_intake as intake
import run_codex_role as entry
from test_campaign_price_handoff import fixture,resync,model

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.file=self.root/"caller price input.json"
        self.taskfile=self.root/"task.txt"
        self.taskfile.write_text("Synthetic caller review only.",encoding="utf-8")
        self.f=fixture()
        self.bundle=self.envelope()
        for role in g.ROUTES:
            for rel in (f"runtime-agents/{role}/agent.md",f".agents/skills/{role}-playbook/SKILL.md"):
                p=self.root/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text("Synthetic test role",encoding="utf-8")
        self.save()
    def tearDown(self):
        self.tmp.cleanup()
    def envelope(self,f=None):
        f=f or self.f
        c={k:copy.deepcopy(f[k]) for k in ("authority","packet","source_input","calculation")}
        c["case_id"]="NEW-CALLER-1"
        return {"schema_version":"1.0","scope":"synthetic_offline","release_status":"not_authorized",
                "receiver_role":f["receiver"],"cases":[c]}
    def save(self):
        self.file.write_bytes(intake.json_bytes(self.bundle))
    def prepare(self,**kw):
        args=dict(role=self.bundle["receiver_role"],task="Synthetic caller task.",sandbox="read-only",
                  input_file=self.file,evaluation_time=self.f["evaluation_time"])
        args.update(kw);return intake.prepare(**args)
    def receipt(self,status="accepted"):
        rows=[]
        for c in self.bundle["cases"]:
            row=model(dict(c,receiver=self.bundle["receiver_role"]),status)
            row["case_id"]=c["case_id"]
            row.pop("scope");row.pop("release_status")
            rows.append(row)
        return {"scope":"synthetic_offline","release_status":"not_authorized","receipts":rows}
    def invoke(self,mode="valid",status="accepted"):
        self.save()
        actual=[]
        def fake(command,**kwargs):
            actual.extend(command)
            out=Path(command[command.index("-o")+1]).parent
            answer=self.receipt(status)
            if mode=="missing_decision":
                answer["receipts"][0].pop("offline_receipt")
            if mode=="duplicate_receipt":
                answer["receipts"].append(copy.deepcopy(answer["receipts"][0]))
            if mode=="scope_conflict":
                answer["receipts"][0]["release_status"]="authorized"
            if mode=="wrong_receiver":
                answer["receipts"][0]["receiver_role"]="tiktok-shop-ops"
            raw=intake.json_bytes(answer)
            if mode=="prose":
                raw=b"accepted; everything is safe"
            if mode=="duplicate_json":
                raw=raw.replace(b'"scope": "synthetic_offline"',b'"scope": "bad", "scope": "synthetic_offline"',1)
            if mode!="missing_answer":
                (out/"answer.md").write_bytes(raw)
            kwargs["stdout"].write('{"type":"'+("turn.started" if mode=="no_completion" else "turn.completed")+'"}\n')
            if mode=="origin_changed":
                self.file.write_bytes(self.file.read_bytes()+b" ")
            if mode=="snapshot_changed":
                (out/"price-intake-input.json").write_bytes(b"{}")
            if mode=="contract_changed":
                (out/"price-intake-contract.json").write_bytes(b"{}")
            if mode=="task_changed":
                (out/"task.txt").write_bytes(b"changed")
            if mode=="prompt_changed":
                (out/"prompt.txt").write_bytes(b"changed")
            if mode=="source_changed":
                (self.root/f"runtime-agents/{self.bundle['receiver_role']}/agent.md").write_text("changed")
            class Process:
                returncode=7 if mode=="nonzero" else 0
            return Process()
        with patch.object(entry,"find_codex",return_value=sys.executable),patch.object(entry,"run_process_tree",side_effect=fake),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            rc=entry.execute(self.bundle["receiver_role"],"Synthetic caller task.",root=self.root,
                             price_handoff_file=self.file,price_evaluation_time=self.f["evaluation_time"])
        out=Path(actual[actual.index("-o")+1]).parent
        return rc,out,intake.parse_bytes((out/"result.json").read_bytes()),actual
    def test_normal_tasks_have_no_price_acceptance(self):
        self.assertIsNone(intake.prepare("douyin-shop-ops","plain text"))
    def test_reserved_markers_cannot_bypass_typed_lane(self):
        for marker in intake.RESERVED:
            with self.subTest(marker=marker),self.assertRaises(ValueError):
                intake.prepare("douyin-shop-ops","\n"+marker+"\n{}")
    def test_options_required_together(self):
        for kw in ({"input_file":None},{"evaluation_time":None}):
            with self.assertRaises(ValueError):self.prepare(**kw)
    def test_host_rejects_caller_embedded_markers(self):
        with self.assertRaises(ValueError):self.prepare(task="PRICE_HANDOFF_CASES\n[]")
    def test_only_supported_readonly_role(self):
        for kw in ({"sandbox":"workspace-write"},{"role":"douyin-ad-buyer"},{"role":"tiktok-campaign-ops"},{"incompatible":True}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.prepare(**kw)
    def test_exact_envelope_fields_and_scope(self):
        for key,val in (("scope","live"),("release_status","authorized"),("receiver_role","tiktok-shop-ops"),("schema_version","2")):
            original=copy.deepcopy(self.bundle)
            self.bundle[key]=val;self.save()
            with self.assertRaises(ValueError):self.prepare(role=self.f["receiver"])
            self.bundle=original
        self.bundle["model_verified"]=True;self.save()
        with self.assertRaises(ValueError):self.prepare()
    def test_duplicate_case_or_packet_rejected(self):
        self.bundle["cases"]*=2;self.save()
        with self.assertRaises(ValueError):self.prepare()
        self.bundle["cases"][1]=copy.deepcopy(self.bundle["cases"][1])
        self.bundle["cases"][1]["case_id"]="different";self.save()
        with self.assertRaises(ValueError):self.prepare()
    def test_extra_case_fields_rejected(self):
        self.bundle["cases"][0]["receiver"]="douyin-shop-ops";self.save()
        with self.assertRaises(ValueError):self.prepare()
    def test_model_output_directory_not_input(self):
        p=self.root/"work/codex-runs/fake/caller.json";p.parent.mkdir(parents=True);p.write_bytes(self.file.read_bytes())
        with self.assertRaises(ValueError):self.prepare(input_file=p)
    def test_duplicate_json_keys_rejected(self):
        self.file.write_bytes(self.file.read_bytes().replace(b'"scope": "synthetic_offline"',b'"scope": "x", "scope": "synthetic_offline"',1))
        with self.assertRaises(ValueError):self.prepare()
    def test_oversize_bundle_rejected(self):
        self.file.write_bytes(b" "*262145)
        with self.assertRaises(ValueError):self.prepare()
    def test_both_routes_preflight(self):
        for role in g.ROUTES:
            self.f=fixture(role);self.bundle=self.envelope();self.save()
            c=self.prepare()
            self.assertEqual(c["preflight"][0]["system_receipt"],"accepted")
            self.assertEqual(c["cases"][0]["receiver"],role)
    def test_unknown_cost_does_not_become_safe(self):
        self.bundle["cases"][0]["authority"]["unknowns"]=["refund"]
        self.save();self.assertEqual(self.prepare()["preflight"][0]["system_receipt"],"rejected")
    def test_entry_valid_price_receipt_and_raw_preservation(self):
        rc,out,result,command=self.invoke()
        self.assertEqual(rc,0);self.assertTrue(result["price_handoff_accepted"])
        self.assertFalse(result["acceptance_ready"])
        self.assertEqual(result["price_accepted_count"],1)
        checked=intake.parse_bytes((out/"price-intake-receipt.json").read_bytes())
        self.assertEqual(checked["records"][0]["raw_model_receipt"],self.receipt()["receipts"][0])
        self.assertEqual(checked["release_status"],"not_authorized")
        self.assertEqual(result["price_intake_receipt_sha256"],intake.sha_bytes((out/"price-intake-receipt.json").read_bytes()))
        self.assertNotIn("--ignore-user-config",command)
        self.assertNotIn("--model",command)
        self.assertIn('web_search="disabled"',command)
        self.assertIn("apps",command)
    def test_actual_ph_entry(self):
        self.f=fixture("tiktok-shop-ops");self.bundle=self.envelope()
        rc,out,result,_=self.invoke()
        self.assertEqual(rc,0);self.assertEqual(result["role"],"tiktok-shop-ops")
        self.assertTrue(result["price_handoff_accepted"])
    def test_model_acceptance_cannot_override_floor_failure(self):
        self.bundle["cases"][0]["authority"]["floor_cents"]=1201
        rc,out,result,_=self.invoke()
        self.assertEqual(rc,0);self.assertFalse(result["price_handoff_accepted"])
        checked=intake.parse_bytes((out/"price-intake-receipt.json").read_bytes())
        self.assertTrue(checked["records"][0]["model_gate_conflict"])
        self.assertEqual(checked["records"][0]["system_receipt"],"rejected")
    def test_model_reject_also_blocks(self):
        rc,out,result,_=self.invoke(status="rejected")
        self.assertEqual(rc,0);self.assertFalse(result["price_handoff_accepted"])
    def test_failure_modes_never_publish_price_acceptance(self):
        for mode in ("prose","duplicate_json","missing_decision","duplicate_receipt","scope_conflict","wrong_receiver",
                     "missing_answer","no_completion","nonzero","origin_changed","snapshot_changed",
                     "contract_changed","task_changed","prompt_changed","source_changed"):
            with self.subTest(mode=mode):
                rc,out,result,_=self.invoke(mode)
                self.assertNotEqual(rc,0)
                self.assertEqual(result["status"],"failed")
                self.assertEqual(result["price_intake_status"],"failed")
                self.assertFalse(result["price_handoff_accepted"])
                self.assertFalse((out/"price-intake-receipt.json").exists())
                self.assertTrue((out/"error.txt").is_file())
    def test_finish_requires_actual_role(self):
        _,out,result,_=self.invoke()
        context=self.prepare()
        intake.freeze(context,out,[self.root/"runtime-agents/douyin-shop-ops/agent.md"],
                      prompt_bytes=(out/"prompt.txt").read_bytes())
        result["role"]="tiktok-shop-ops"
        with self.assertRaises(ValueError):intake.finish(context,out,result)
    def test_source_read_freeze_race_fails_before_model(self):
        actual_freeze=intake.freeze
        def race(context,run_dir,paths,**kwargs):
            (self.root/"runtime-agents/douyin-shop-ops/agent.md").write_text("NEW SOURCE")
            return actual_freeze(context,run_dir,paths,**kwargs)
        with patch.object(intake,"freeze",side_effect=race),patch.object(entry,"find_codex") as cli,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            rc=entry.execute("douyin-shop-ops","Synthetic caller task.",root=self.root,
                price_handoff_file=self.file,price_evaluation_time=self.f["evaluation_time"])
        self.assertNotEqual(rc,0);cli.assert_not_called()
        runs=list((self.root/"work/codex-runs").iterdir())
        self.assertEqual(len(runs),1)
        result=intake.parse_bytes((runs[0]/"result.json").read_bytes())
        self.assertEqual(result["price_intake_status"],"failed")
        self.assertFalse((runs[0]/"price-intake-receipt.json").exists())

    def test_downstream_fresh_time_and_expiration(self):
        _,out,_,_=self.invoke()
        inside=intake.revalidate_run(self.file,"douyin-shop-ops","2026-11-11T13:00:00+08:00",out)
        self.assertEqual(inside["records"][0]["system_receipt"],"accepted")
        self.assertIn("no_new_model_run",inside["intake_kind"])
        expired=intake.revalidate_run(self.file,"douyin-shop-ops","2026-11-12T00:00:00+08:00",out)
        self.assertEqual(expired["records"][0]["system_receipt"],"rejected")
    def test_downstream_no_new_input_or_rule_reuses_old_receipt(self):
        for change in ("price","rule","fee"):
            self.bundle=self.envelope();_,out,_,_=self.invoke()
            c=self.bundle["cases"][0]
            c["source_input"][change]="new version"
            c["calculation"]["input_sha256"]=g.digest(c["source_input"])
            c["authority"]["input_sha256"]=g.digest(c["source_input"])
            c["packet"]["input_sha256"]=g.digest(c["source_input"])
            if change=="rule":
                c["calculation"]["rule_versions"]={"SYN-LOCAL":"2"}
                c["authority"]["rule_versions"]={"SYN-LOCAL":"2"}
            resync(c);self.save()
            with self.subTest(change=change),self.assertRaises(ValueError):
                intake.revalidate_run(self.file,"douyin-shop-ops",self.f["evaluation_time"],out)
    def test_downstream_receipt_tampering_and_wrong_role_rejected(self):
        _,out,_,_=self.invoke()
        with self.assertRaises(ValueError):intake.revalidate_run(self.file,"tiktok-shop-ops",self.f["evaluation_time"],out)
        (out/"price-intake-receipt.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):intake.revalidate_run(self.file,"douyin-shop-ops",self.f["evaluation_time"],out)
    def test_validate_only_python_price_no_writes_or_model(self):
        command=[sys.executable,"-B","-X","utf8",str(Path(entry.__file__).resolve()),
            "--role","douyin-shop-ops","--task-file",str(self.taskfile),
            "--price-handoff-file",str(self.file),"--price-evaluation-time",self.f["evaluation_time"],"--validate-only"]
        done=subprocess.run(command,capture_output=True,timeout=30)
        self.assertEqual(done.returncode,0,done.stderr.decode("utf-8",errors="replace"))
        result=json.loads(done.stdout.decode("utf-8-sig"))
        self.assertTrue(result["price_intake_requested"])
        self.assertFalse(result["model_called"])
        self.assertEqual(result["price_preflight_rejected"],0)
    def test_validate_only_powershell_price_paths(self):
        command=["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",
            str(Path(entry.__file__).with_suffix(".ps1").resolve()),"-Role","douyin-shop-ops",
            "-TaskFile",str(self.taskfile),"-PriceHandoffFile",str(self.file),
            "-PriceEvaluationTime",self.f["evaluation_time"],"-ValidateOnly"]
        done=subprocess.run(command,capture_output=True,timeout=30)
        self.assertEqual(done.returncode,0,done.stderr.decode("utf-8",errors="replace"))
        result=json.loads(done.stdout.decode("utf-8-sig"))
        self.assertTrue(result["price_intake_requested"]);self.assertFalse(result["model_called"])

if __name__=="__main__":
    unittest.main(verbosity=2)
