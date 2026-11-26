"""Synthetic Windows Job lifecycle tests; no network, business data, or model."""
import ctypes,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import shop_process_guard as g

def is_running(pid):
    kernel=ctypes.WinDLL("kernel32",use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    kernel.GetExitCodeProcess.restype=ctypes.c_int
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return False
    try:
        code=ctypes.c_ulong()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)):raise ctypes.WinError()
        return code.value==259
    finally:kernel.CloseHandle(handle)

@unittest.skipUnless(os.name=="nt","Windows Job Object tests")
class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def tree(self,normal_exit=False):
        grand="import os,time,pathlib;pathlib.Path(%r).write_text(str(os.getpid()));time.sleep(60)" % str(self.root/"grand.pid")
        child=("import os,time,pathlib,subprocess,sys;"
               "pathlib.Path(%r).write_text(str(os.getpid()));"
               "subprocess.Popen([sys.executable,'-c',%r]);time.sleep(60)") % (str(self.root/"child.pid"),grand)
        parent=("import os,time,pathlib,subprocess,sys;"
                "pathlib.Path(%r).write_text(str(os.getpid()));"
                "subprocess.Popen([sys.executable,'-c',%r]);time.sleep(%s)") % (
                    str(self.root/"parent.pid"),child,"0.8" if normal_exit else "60")
        return [sys.executable,"-c",parent]

    def assert_tree_dead(self):
        markers=[self.root/(x+".pid") for x in ["parent","child","grand"]]
        self.assertTrue(all(p.exists() for p in markers),"Synthetic descendants must actually have started")
        pids=[int(p.read_text()) for p in markers]
        until=time.monotonic()+5
        while time.monotonic()<until and any(is_running(pid) for pid in pids):
            time.sleep(.03)
        self.assertFalse(any(is_running(pid) for pid in pids),pids)

    def test_success_utf8_stdin(self):
        payload='opaque JSON: {"x":"quoted"}\\ tail\r\n中文'
        result=g.run_process_tree([sys.executable,"-c","import sys;sys.stdout.write(sys.stdin.read());sys.stderr.write('note')"],
                                  input=payload,text=True,encoding="utf8",errors="strict",
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=5)
        self.assertEqual(result.returncode,0)
        expected=subprocess.run([sys.executable,"-c","import sys;sys.stdout.write(sys.stdin.read())"],input=payload,text=True,encoding="utf8",stdout=subprocess.PIPE,check=True)
        self.assertEqual(result.stdout,expected.stdout)
        self.assertEqual(result.stderr,"note")

    def test_binary_stdin_exact(self):
        payload=b'opaque \"quote\"\\ tail\r\n'
        result=g.run_process_tree([sys.executable,"-c","import sys;sys.stdout.buffer.write(sys.stdin.buffer.read())"],input=payload,stdout=subprocess.PIPE,timeout=5)
        self.assertEqual(result.stdout,payload)

    def test_nonzero_preserved(self):
        result=g.run_process_tree([sys.executable,"-c","raise SystemExit(7)"],timeout=5)
        self.assertEqual(result.returncode,7)

    def test_timeout_entire_tree(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            g.run_process_tree(self.tree(),timeout=1.5,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.assert_tree_dead()

    def test_normal_parent_exit_cleans_descendants(self):
        result=g.run_process_tree(self.tree(True),timeout=5,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.assertEqual(result.returncode,0)
        self.assert_tree_dead()

    def test_cancellation_entire_tree(self):
        def cancel(process,*args,**kwargs):
            until=time.monotonic()+5
            while time.monotonic()<until and not (self.root/"grand.pid").exists():time.sleep(.02)
            raise KeyboardInterrupt("synthetic cancellation")
        with patch.object(subprocess.Popen,"communicate",cancel):
            with self.assertRaises(KeyboardInterrupt):g.run_process_tree(self.tree(),timeout=10)
        self.assert_tree_dead()

    def test_unrelated_process_survives(self):
        unrelated=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                g.run_process_tree(self.tree(),timeout=1.5)
            self.assertIsNone(unrelated.poll())
            self.assert_tree_dead()
        finally:
            unrelated.kill();unrelated.wait(timeout=5)

    def test_assign_failure_fail_closed(self):
        marker=self.root/"started"
        command=[sys.executable,"-c","from pathlib import Path;Path(%r).write_text('bad')" % str(marker)]
        with patch.object(g._Job,"assign_and_resume",side_effect=OSError("synthetic policy denial")):
            with self.assertRaises(OSError):g.run_process_tree(command,timeout=5)
        self.assertFalse(marker.exists())

    def test_bad_command_and_timeout(self):
        for timeout in [0,-1,float("inf"),float("nan")]:
            with self.assertRaises(ValueError):g.run_process_tree([sys.executable],timeout=timeout)
        with self.assertRaises(ValueError):g.run_process_tree("shell text")
        with self.assertRaises(FileNotFoundError):g.run_process_tree(["missing-synthetic-program.exe"],timeout=2)

if __name__=="__main__":unittest.main(verbosity=2)
