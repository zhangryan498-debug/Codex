import json,tomllib,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1]
ROLES=("douyin-campaign-ops","tiktok-campaign-ops","douyin-shop-ops","tiktok-shop-ops")
class PriceProtocolTests(unittest.TestCase):
    def test_role_and_skill_mirrors(self):
        for role in ROLES:
            with self.subTest(role=role):
                sources=[R/f"runtime-agents/{role}/agent.md",R/f"agents/{role}/agent.md"]
                texts=[p.read_text(encoding="utf-8-sig") for p in sources]
                self.assertEqual(len(set(texts)),1)
                self.assertEqual(texts[0].count("## Offline activity price floor protocol (2026-11-11)"),1)
                self.assertIn("Structured JSON amounts are mandatory ledger data",texts[0])
                paths=[R/f".agents/skills/{role}-playbook/SKILL.md",R/f"skills/{role}-playbook/SKILL.md",R/f"runtime-agents/{role}/skills/{role}-playbook/SKILL.md",R/f"agents/{role}/skills/{role}-playbook/SKILL.md"]
                st=[p.read_text(encoding="utf-8-sig") for p in paths]
                self.assertEqual(len(set(st)),1)
                self.assertEqual(st[0].count("## Offline activity price floor protocol (2026-11-11)"),1)
    def test_native_role_source_binding(self):
        manifest=json.loads((R/".codex/role-manifest.json").read_text(encoding="utf8"))
        for role in ROLES:
            with self.subTest(role=role):
                source=(R/f"runtime-agents/{role}/agent.md").read_text(encoding="utf-8-sig")
                cfg=tomllib.loads((R/f".codex/agents/{role}.toml").read_text(encoding="utf-8-sig"))
                self.assertIn(source,cfg["developer_instructions"])
                self.assertEqual(cfg["name"],role)
                self.assertEqual(sum(x["role"]==role for x in manifest),1)
    def test_protocol_trust_and_scope(self):
        text=(R/"docs/campaign-price-floor-offline.md").read_text(encoding="utf8")
        for item in ["Douyin CN","TikTok PH","not_authorized","not independent evidence","integer minor units","Structured output completeness","hypothetical price","combine_batch_run"]:
            self.assertIn(item,text)

    def test_known_baseline_contract(self):
        text=(R/"docs/campaign-price-floor-offline.md").read_text(encoding="utf8")
        for name in ["fallback_no_activity_net","fallback_flash_only_net","A base_price is not a net amount","actual net remains null"]:
            self.assertIn(name,text)
        for role in ROLES:
            with self.subTest(role=role):
                source=(R/f"runtime-agents/{role}/agent.md").read_text(encoding="utf8")
                self.assertEqual(source.count("### Known baseline and counterfactual branches"),1)
if __name__=="__main__":unittest.main()
