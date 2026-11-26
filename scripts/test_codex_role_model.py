"""Per-run model routing: no global configuration writes or live model calls."""
import contextlib, io, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import run_codex_role as m

class ModelRoutingTests(unittest.TestCase):
    def invoke(self, **options):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for rel in ("runtime-agents/douyin-ad-buyer/agent.md", ".agents/skills/douyin-ad-buyer-playbook/SKILL.md"):
                f=root/rel; f.parent.mkdir(parents=True,exist_ok=True); f.write_text("Test fixture",encoding="utf-8")
            actual=[]
            def fake(command, **kwargs):
                actual.extend(command)
                Path(command[command.index("-o")+1]).write_text("Test answer",encoding="utf-8")
                kwargs["stdout"].write('{"type":"turn.completed"}\n')
                class Process: returncode=0
                return Process()
            with patch.object(m,"find_codex",return_value="codex"), patch.object(m,"run_process_tree",side_effect=fake), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(m.execute("douyin-ad-buyer","fixture",root=root,**options),0)
            out=Path(actual[actual.index("-o")+1]).parent
            self.assertEqual(json.loads((out/"command.json").read_text(encoding="utf-8")),actual)
            return actual, json.loads((out/"result.json").read_text(encoding="utf-8"))
    def test_default_still_inherits(self):
        cmd,result=self.invoke()
        self.assertNotIn("--model",cmd)
        self.assertFalse(any("model_reasoning_effort" in x for x in cmd))
        self.assertFalse(result["model_override"])
    def test_sol_max_explicit(self):
        cmd,result=self.invoke(model="gpt-6.1-sol",reasoning_effort="max")
        self.assertEqual(cmd[cmd.index("--model")+1],"gpt-6.1-sol")
        self.assertIn('model_reasoning_effort="max"',cmd)
        self.assertTrue(result["model_override"])
        self.assertEqual(result["requested_reasoning_effort"],"max")
    def test_invalid_override(self):
        for model in ("", "gpt-6.1-sol --bad", "gpt\nsol"):
            with self.assertRaises(ValueError): m.model_options(model,"max")
        with self.assertRaises(ValueError): m.model_options("gpt-6.1-sol","invented")
    def test_independent_overrides(self):
        self.assertEqual(m.model_options("gpt-6.1-sol"),["--model","gpt-6.1-sol"])
        self.assertEqual(m.model_options(reasoning_effort="max"),["-c",'model_reasoning_effort="max"'])
if __name__=="__main__": unittest.main()
