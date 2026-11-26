import unittest
import run_codex_role as r
class OfflineMcpOptionsTests(unittest.TestCase):
    def test_empty_preserves_config(self):self.assertEqual(r.mcp_disable_options(),[])
    def test_flags_are_explicit_process_overrides(self):self.assertEqual(r.mcp_disable_options(['node_repl','fastmoss']),['-c','mcp_servers.node_repl.enabled=false','-c','mcp_servers.fastmoss.enabled=false'])
    def test_bad_identifier_rejected(self):
        for n in ['node repl','a.b','a=1','',None]:
            with self.subTest(name=n),self.assertRaises(ValueError):r.mcp_disable_options([n])
    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):r.mcp_disable_options(['node_repl','node_repl'])
    def test_non_sequence_rejected(self):
        for n in ['node_repl',None,{}]:
            with self.subTest(value=n),self.assertRaises(ValueError):r.mcp_disable_options(n)
if __name__=='__main__':unittest.main()
