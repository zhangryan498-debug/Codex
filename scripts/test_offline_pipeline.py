import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import offline_pipeline as pipeline
import verify_reports as gates


class OfflineTests(unittest.TestCase):
    def known_audit(self):
        return {"checks": [{"编号": key, "结果": "不通过"} for key in pipeline.KNOWN_EMPTY],
                "datasets": {ds: {"rows": 0} for ds in pipeline.KNOWN_EMPTY.values()}}

    def test_known_gaps_are_not_an_unqualified_pass(self):
        self.assertEqual(pipeline.audit_status(self.known_audit(), 1), "known_data_limits")
        self.assertEqual(pipeline.audit_status(self.known_audit(), 0), "failed")

    def test_unexpected_or_missing_audit_fails(self):
        data = self.known_audit()
        data["checks"].append({"编号": "new-error", "结果": "不通过"})
        self.assertEqual(pipeline.audit_status(data, 1), "failed")
        self.assertEqual(pipeline.audit_status({}, 0), "failed")

    def test_known_gap_with_existing_data_is_not_ignored(self):
        data = self.known_audit()
        data["datasets"]["P1_explore_post"]["rows"] = 1
        self.assertEqual(pipeline.audit_status(data, 1), "failed")

    def test_idempotence_includes_progress_report_and_deletions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "docs/reports").mkdir(parents=True)
            progress = root / "docs/progress.md"
            progress.write_text("first", encoding="utf-8")
            with patch.object(gates, "ROOT", root):
                first = gates.artifact_hashes()
                progress.write_text("second", encoding="utf-8")
                self.assertEqual(gates.changed_artifacts(first, gates.artifact_hashes()), ["docs/progress.md"])
                progress.unlink()
                self.assertEqual(gates.changed_artifacts(first, gates.artifact_hashes()), ["docs/progress.md"])

    def test_network_is_blocked_in_python_child(self):
        env = {**os.environ, "PYTHONPATH": str(pipeline.ROOT / "scripts/offline_guard"), "PYTHONUTF8": "1"}
        proc = subprocess.run([sys.executable, "-c",
                               "import socket; socket.create_connection(('127.0.0.1', 9))"],
                              env=env, capture_output=True, encoding="utf-8", timeout=10)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("network access is disabled", proc.stderr)


if __name__ == "__main__":
    unittest.main()
