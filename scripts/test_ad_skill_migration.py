"""Advertising review rules must survive the existing migration generator."""
import unittest
from pathlib import Path
import migrate_codex_skills as m

class AdMigrationTests(unittest.TestCase):
    def test_review_rebuild_and_idempotence(self):
        for role in ("douyin-ad-buyer", "tiktok-ad-buyer"):
            source=(m.ROOT/f"runtime-agents/{role}/skills/{role}-playbook/SKILL.md").read_text(encoding="utf-8")
            with self.subTest(role=role):
                self.assertIn("二次审查修订：可复现决策契约",source)
                self.assertEqual(m.adapted(source,role),source)
                self.assertEqual(m.adapted(m.adapted(source,role),role),source)
    def test_four_ad_mirrors(self):
        for role in ("douyin-ad-buyer", "tiktok-ad-buyer"):
            files=[m.ROOT/f"{prefix}/{role}-playbook/SKILL.md" for prefix in (f"runtime-agents/{role}/skills",f"agents/{role}/skills","skills",".agents/skills")]
            with self.subTest(role=role):
                self.assertTrue(all(f.read_bytes()==files[0].read_bytes() for f in files))
if __name__=="__main__": unittest.main()
