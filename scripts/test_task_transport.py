"""Task transport regression using synthetic strings only; no blind-test inputs."""
import contextlib, hashlib, io, json, subprocess, tempfile, unittest
from pathlib import Path
import run_codex_role as m

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT/"scripts/run_codex_role.ps1"

class TaskTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name)
        self.file=self.base/"task \u4e2d\u6587.txt"
    def tearDown(self): self.tmp.cleanup()
    def test_read_preserves_crlf_quotes(self):
        value=' {"key":"quoted \\"value\\"","path":"C:\\\\data\\\\"}\r\n中文\U0001f600\r\n  '
        self.file.write_bytes(value.encode("utf-8"))
        self.assertEqual(m.read_task(task_file=self.file),value)
    def test_utf8_bom(self):
        self.file.write_bytes(b"\xef\xbb\xbf"+'中文\r\n'.encode("utf-8"))
        self.assertEqual(m.read_task(task_file=self.file),'中文\r\n')
    def test_bad_utf8(self):
        self.file.write_bytes(b"\xff")
        with self.assertRaises(UnicodeDecodeError):m.read_task(task_file=self.file)
    def test_exclusive_and_empty(self):
        for kwargs in [{},{"task":"x","task_file":"x"},{"task":" \r\n"}]:
            with self.assertRaises(ValueError):m.read_task(**kwargs)
    def invoke(self,value,mode):
        self.file.write_bytes(value.encode("utf-8"))
        ps=self.base/"invoke.ps1"
        quote=lambda s:str(s).replace("'","''")
        if mode=="task":
            body="$payload=[IO.File]::ReadAllText('"+quote(self.file)+"',[Text.Encoding]::UTF8)\n& '"+quote(WRAPPER)+"' -Role douyin-shop-ops -Task $payload -ValidateOnly\nexit $LASTEXITCODE\n"
        else:
            body="Set-Location -LiteralPath '"+quote(self.base)+"'\n& '"+quote(WRAPPER)+"' -Role douyin-shop-ops -TaskFile '"+quote(self.file.name)+"' -ValidateOnly\nexit $LASTEXITCODE\n"
        ps.write_bytes(b"\xef\xbb\xbf"+body.encode("utf-8"))
        r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(ps)],capture_output=True,timeout=30)
        self.assertEqual(r.returncode,0,r.stderr.decode("utf-8",errors="replace"))
        obj=json.loads(r.stdout.decode("utf-8-sig"))
        self.assertEqual(obj["task_sha256"],hashlib.sha256(value.encode("utf-8")).hexdigest())
        self.assertEqual(obj["task_utf8_bytes"],len(value.encode("utf-8")))
        self.assertFalse(obj["model_called"])
        self.assertEqual(self.file.read_bytes(),value.encode("utf-8"))
    def test_original_task_lossless_payloads(self):
        payloads=['{"nested":{"quote":"a \\"b\\"","number":42}}',
                  'backslash quote \\" and trailing slash\\',
                  'line1\r\nline2\nline3\r\n  ',
                  '中文 空格 \U0001f600 引号"保持"',
                  '--begins-with-option-like-text',
                  ('long 中文 "x" \\\\ \r\n'*4000)]
        for value in payloads:
            with self.subTest(length=len(value)):self.invoke(value,"task")
    def test_taskfile_lossless_relative(self):
        self.invoke('{"x":"a\\\\b"}\r\n中文\U0001f600\n  ',"file")
    def test_ps_rejects_both_and_neither(self):
        self.file.write_text("x",encoding="utf-8")
        for extra in [[],["-Task","x","-TaskFile",str(self.file)]]:
            r=subprocess.run(["powershell","-NoProfile","-ExecutionPolicy","Bypass","-File",str(WRAPPER),
                              "-Role","douyin-shop-ops","-ValidateOnly",*extra],capture_output=True,timeout=30)
            self.assertNotEqual(r.returncode,0)

if __name__=="__main__":
    unittest.main(verbosity=2)

