import copy
import hashlib
import json
import types
import unittest

import replay_campaign_state_repair_v2 as replay


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def aliasing_packer(case, proof, model, sku, binding, provenance):
    versions = {"host_policy": "test-only"}
    source = {"original_proposal": case}
    calculation = {"rule_versions": versions, "proof": proof, "provenance": provenance}
    authority = {"rule_versions": versions, "campaign_review": {"binding": binding}}
    return {"case_id": sku, "source_input": source, "calculation": calculation,
            "authority": authority, "packet": {"payload": authority}}


class ReplayIsolationTests(unittest.TestCase):
    def setUp(self):
        self.old = types.SimpleNamespace(pack=aliasing_packer, gate=types.SimpleNamespace(digest=digest),
                                         proof_summary=lambda value: value)
        self.case = {"facts": {"costs": {"overhead_minor": 11}}}
        self.proof = {"state": {}, "numbers": {"net_minor": 40}}
        self.model = copy.deepcopy(self.proof)
        self.binding = {"task": "a"}
        self.provenance = {"source": "b"}

    def row(self):
        return replay.repaired_pack(self.old, self.case, self.proof, self.model, "SYNTHETIC-A",
                                    self.binding, self.provenance)[0]

    def test_adversarial_row_cannot_mutate_good_row_or_original_source(self):
        good = self.row()
        expected = digest(good)
        bad = self.row()
        bad["source_input"]["original_proposal"]["facts"]["costs"]["overhead_minor"] += 1
        self.assertEqual(digest(good), expected)
        self.assertEqual(self.case["facts"]["costs"]["overhead_minor"], 11)
        replay.ensure_replay_rows_intact([good], [expected], self.old.gate)

    def test_output_graph_does_not_alias_any_caller_input(self):
        before = copy.deepcopy((self.case, self.proof, self.model, self.binding, self.provenance))
        row = self.row()
        row["calculation"]["proof"]["numbers"]["net_minor"] = -1
        row["authority"]["campaign_review"]["raw_model_result_summary"]["numbers"]["net_minor"] = -2
        row["authority"]["campaign_review"]["binding"]["task"] = "changed"
        row["calculation"]["provenance"]["source"] = "changed"
        self.assertEqual((self.case, self.proof, self.model, self.binding, self.provenance), before)

    def test_mutated_persistable_packet_is_detected(self):
        row = self.row()
        expected = digest(row)
        row["source_input"]["original_proposal"]["facts"]["costs"]["overhead_minor"] += 1
        with self.assertRaises(AssertionError):
            replay.ensure_replay_rows_intact([row], [expected], self.old.gate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
