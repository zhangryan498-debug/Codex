"""Deterministic contract tests, not model accuracy or independent blind acceptance."""
import contextlib, copy, io, json, tempfile, unittest, sys
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import run_codex_role as m

class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.png = self.root / "中文 空格.png"
        Image.new("RGB", (12, 12), "white").save(self.png)
        self.rows = m.resolve_images([str(self.png)])
        self.report = {"schema_version":"1.0","summary":"Test only","release_decision":"not_authorized",
            "images":[{"image_id":"img-01","image_file":self.png.name,"read_status":"read",
                       "observation":"A white test image","uncertainties":[]}],"issues":[]}
    def tearDown(self):
        self.tmp.cleanup()
    def test_valid_image_unicode_space(self):
        self.assertEqual(self.rows[0]["dimensions"], [12,12])
        self.assertEqual(self.rows[0]["sha256"], m.sha(self.png))
    def test_manifest_relative(self):
        p=self.root/"inputs.json";p.write_text(json.dumps([self.png.name]),encoding="utf-8")
        self.assertEqual(m.resolve_images([],str(p))[0]["path"],str(self.png.resolve()))
    def test_empty_manifest(self):
        p=self.root/"inputs.json";p.write_text("[]")
        with self.assertRaises(ValueError): m.resolve_images([],str(p))
    def test_blank_path(self):
        with self.assertRaises(ValueError): m.resolve_images([" "])
    def test_missing_path(self):
        with self.assertRaises(FileNotFoundError): m.resolve_images([str(self.root/"missing.png")])
    def test_duplicate(self):
        with self.assertRaises(ValueError): m.resolve_images([str(self.png),str(self.png)])
    def test_extension_and_decode(self):
        p=self.root/"bad.png";p.write_text("not an image")
        with self.assertRaises(Exception): m.resolve_images([str(p)])
        p=self.root/"wrong.jpg";p.write_bytes(self.png.read_bytes())
        with self.assertRaises(ValueError): m.resolve_images([str(p)])
    def test_valid_report(self):
        self.assertEqual(m.validate_report(self.report,self.rows),[])
    def test_missing_required_issue(self):
        self.report["issues"]=[{"issue_id":"x"}]
        self.assertTrue(m.validate_report(self.report,self.rows))
    def test_retest_cannot_pass(self):
        issue={"issue_id":"x","image_id":"img-01","image_file":self.png.name,"original_text":"x","basis":"x",
               "severity":"P1","action":"x","repair_copy":"x","retest_status":"passed"}
        self.report["issues"]=[issue]
        self.assertTrue(m.validate_report(self.report,self.rows))
    def test_coverage_and_reference(self):
        self.report["images"][0]["image_file"]="other.png"
        self.assertTrue(m.validate_report(self.report,self.rows))
        self.report["images"]=[]
        self.assertTrue(m.validate_report(self.report,self.rows))
    def test_no_release(self):
        self.report["release_decision"]="approved"
        self.assertTrue(m.validate_report(self.report,self.rows))
    def test_proof_not_self_report(self):
        self.assertFalse(m.image_proof('I saw every image',self.rows,"test")["all_images_covered"])
        line='2026-11-10T12:00:00Z INFO log: codex_otel.log_only: event.name="codex.tool_result" tool_name=view_image success=true arguments='+json.dumps({"path":str(self.png),"detail":"original"})+' output=<image omitted>'
        self.assertTrue(m.image_proof(line,self.rows,"test")["all_images_covered"])
    def test_forged_keyword_line_not_proof(self):
        line='untrusted output: view_image success=true arguments='+json.dumps({"path":str(self.png)})
        self.assertFalse(m.image_proof(line,self.rows,"test")["all_images_covered"])
    def test_other_tool_output_not_proof(self):
        line='2026-11-10T12:00:00Z INFO log: codex_otel.log_only: event.name="codex.tool_result" tool_name=exec_command success=true arguments='+json.dumps({"path":str(self.png),"text":"view_image"})
        self.assertFalse(m.image_proof(line,self.rows,"test")["all_images_covered"])
    def test_same_count_other_image_proof_rejected(self):
        proof={"run_id":"test","manifest_sha256":m.manifest_digest(self.rows),
               "all_images_covered":True,"covered_count":1,
               "successful_calls":[{"path":str(self.root/"unrelated.png"),"success":True}]}
        self.assertTrue(m.validate_proof(proof,self.rows,"test"))
    def test_other_run_proof_rejected(self):
        proof={"run_id":"other","manifest_sha256":m.manifest_digest(self.rows),
               "successful_calls":[{"path":str(self.png),"success":True}]}
        self.assertTrue(m.validate_proof(proof,self.rows,"test"))
    def test_other_manifest_hash_rejected(self):
        proof={"run_id":"test","manifest_sha256":"bad",
               "successful_calls":[{"path":str(self.png),"success":True}]}
        self.assertTrue(m.validate_proof(proof,self.rows,"test"))
    def test_missing_cli_is_failure(self):
        self.setup_role()
        with patch.object(m,"find_codex",side_effect=FileNotFoundError("missing")),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(m.execute("douyin-shop-ops","fixture",root=self.root),1)
    def setup_role(self):
        for p in ["runtime-agents/douyin-shop-ops/agent.md",".agents/skills/douyin-shop-ops-playbook/SKILL.md"]:
            f=self.root/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_text("Test fixture")
    def invoke(self, mode, images=False):
        self.setup_role()
        def fake(cmd, **kwargs):
            args=cmd; answer=Path(args[args.index("-o")+1])
            if images:
                self.assertEqual(args[-3], "--output-schema")
                self.assertEqual(args[-1], "-")
            kwargs["stdout"].write(json.dumps({"type":"turn.completed" if mode != "no_completion" else "turn.started"})+"\n")
            if mode != "missing_answer":
                answer.write_text(json.dumps(self.report) if images else "Test fixture answer",encoding="utf-8")
            if mode in ("read_success", "image_changed"):
                kwargs["stderr"].write('2026-11-10T12:00:00Z INFO log: codex_otel.log_only: event.name="codex.tool_result" tool_name=view_image success=true arguments='+json.dumps({"path":str(self.png),"detail":"original"})+' output=image\n')
            if mode == "image_changed":
                Image.new("RGB",(12,12),"red").save(self.png)
            class P: returncode=7 if mode=="nonzero" else 0
            return P()
        with patch.object(m,"find_codex",return_value=sys.executable),patch.object(m,"run_process_tree",side_effect=fake),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return m.execute("douyin-shop-ops","fixture","read-only",[str(self.png)] if images else [],root=self.root)
    def test_legacy_success(self): self.assertEqual(self.invoke("success"),0)
    def test_native_failure_propagates(self): self.assertEqual(self.invoke("nonzero"),7)
    def test_missing_completion_fails(self): self.assertEqual(self.invoke("no_completion"),3)
    def test_missing_answer_fails(self): self.assertEqual(self.invoke("missing_answer"),3)
    def test_missing_image_read_proof_fails(self): self.assertEqual(self.invoke("success",True),5)
    def test_image_contract_success(self): self.assertEqual(self.invoke("read_success",True),0)
    def test_input_mutation_fails(self): self.assertEqual(self.invoke("image_changed",True),6)
    def test_reject_write_image_mode(self):
        self.setup_role()
        with self.assertRaises(ValueError):
            m.execute("douyin-shop-ops","fixture","workspace-write",[str(self.png)],root=self.root)

if __name__=="__main__":
    unittest.main(verbosity=2)

