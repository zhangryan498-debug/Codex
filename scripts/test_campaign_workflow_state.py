import copy
import unittest
from campaign_workflow_state import compare_workflow_state, strict_equal


def committed_replay():
    workflow = {"request_id": "r", "idempotency_key": "k", "budget_initial_minor": 1000,
                "existing_ledger": [{"event_id": "commit", "request_id": "r", "idempotency_key": "k",
                    "status": "committed", "seller_promo_debit_minor": 120, "voucher_usage_increment": 1}],
                "incoming_retries": [{"event_id": "retry1", "request_id": "r", "idempotency_key": "k"},
                                     {"event_id": "retry2", "request_id": "r", "idempotency_key": "k"}]}
    expected = {"new_commit_count": 0, "new_charge_count": 0, "new_voucher_usage": 0,
                "committed_minor": 120, "voucher_usage": 1, "budget_remaining_minor": 880,
                "reserved_minor": 0, "unique_commit_count": 1, "replayed_requests": 2}
    return expected, workflow


def fee_replay():
    workflow = {"success_event_id": "s", "fee_budget_initial_minor": 1000,
                "fee_budget_scope": "basic_mdp_only",
                "existing_fee_ledger": [{"event_id": "s", "fee_type": "basic_mdp", "debit_minor": 120,
                                         "status": "committed"}],
                "incoming_callbacks": [{"event_id": "s"}, {"event_id": "s"}]}
    expected = {"new_commit_count": 0, "new_charge_count": 0, "new_voucher_usage": 0,
                "mdp_fee_debit_minor": 120, "fee_budget_remaining_minor": 880,
                "unique_fee_count": 1, "ignored_duplicate_callbacks": 2}
    return expected, workflow


class WorkflowEquivalenceTests(unittest.TestCase):
    def test_exact_outputs_and_no_workflow_remain_compatible(self):
        self.assertTrue(compare_workflow_state({}, {})["equivalent"])
        self.assertTrue(compare_workflow_state({"new_charge_count": 0}, {"new_charge_count": 0})["equivalent"])

    def test_committed_replay_equivalence_and_no_mutation(self):
        expected, workflow = committed_replay()
        observed = dict(expected, terminal_status="committed")
        del observed["reserved_minor"]
        before = copy.deepcopy((expected, observed, workflow))
        result = compare_workflow_state(expected, observed, workflow)
        self.assertTrue(result["equivalent"])
        self.assertEqual(result["omitted_proven_zero_fields"], ["reserved_minor"])
        self.assertEqual(result["checked_auxiliary_fields"], ["terminal_status"])
        self.assertEqual((expected, observed, workflow), before)
        self.assertIsNot(result["canonical_state"], expected)

    def test_fee_replay_can_omit_inapplicable_counters(self):
        expected, workflow = fee_replay()
        observed = {k: v for k, v in expected.items() if k not in ("new_commit_count", "new_voucher_usage")}
        result = compare_workflow_state(expected, observed, workflow)
        self.assertTrue(result["equivalent"])
        self.assertEqual(result["omitted_proven_zero_fields"], ["new_commit_count", "new_voucher_usage"])

    def test_missing_nonzero_or_financial_fields_fail(self):
        for factory in (committed_replay, fee_replay):
            expected, workflow = factory()
            optional = {"reserved_minor"} if factory is committed_replay else {"new_commit_count", "new_voucher_usage"}
            for field in set(expected) - optional:
                with self.subTest(workflow=factory.__name__, field=field):
                    observed = dict(expected)
                    del observed[field]
                    self.assertFalse(compare_workflow_state(expected, observed, workflow)["equivalent"])

    def test_every_changed_numeric_output_fails(self):
        for factory in (committed_replay, fee_replay):
            expected, workflow = factory()
            for field in expected:
                with self.subTest(workflow=factory.__name__, field=field):
                    observed = dict(expected)
                    observed[field] += 1
                    self.assertFalse(compare_workflow_state(expected, observed, workflow)["equivalent"])

    def test_null_bool_float_and_string_zero_never_equal_or_default(self):
        expected, workflow = committed_replay()
        for field in expected:
            for invalid in (None, False, float(expected[field]), str(expected[field])):
                with self.subTest(field=field, value=invalid):
                    observed = dict(expected)
                    observed[field] = invalid
                    self.assertFalse(compare_workflow_state(expected, observed, workflow)["equivalent"])

    def test_unverified_extra_fields_and_wrong_terminal_fail(self):
        expected, workflow = committed_replay()
        for extra in ({"new_debit": 0}, {"terminal_status": "cancelled"}, {"status": "prepared"},
                      {"terminal_status": "committed", "status": "cancelled"}):
            with self.subTest(extra=extra):
                self.assertFalse(compare_workflow_state(expected, dict(expected, **extra), workflow)["equivalent"])

    def test_missing_or_unknown_source_is_not_zero(self):
        expected, workflow = committed_replay()
        observed = dict(expected)
        del observed["reserved_minor"]
        for broken in (None, {}, {**workflow, "budget_initial_minor": None},
                       {**workflow, "current_reserved_minor": 1},
                       {**workflow, "current_reserved_minor": False},
                       {**workflow, "current_reserved_minor": None},
                       {**workflow, "status": "unknown"}):
            with self.subTest(source=broken):
                self.assertFalse(compare_workflow_state(expected, observed, broken)["equivalent"])

    def test_uncommitted_new_key_or_new_event_cannot_normalize(self):
        for factory in (committed_replay, fee_replay):
            expected, workflow = factory()
            observed = dict(expected)
            del observed["reserved_minor" if factory is committed_replay else "new_commit_count"]
            for mutation in ("prepared", "new_identity", "cancelled", "conflicting_duplicate"):
                broken = copy.deepcopy(workflow)
                ledger = "existing_ledger" if factory is committed_replay else "existing_fee_ledger"
                if mutation == "prepared":
                    broken[ledger][0]["status"] = "prepared"
                elif mutation == "new_identity":
                    if factory is committed_replay:
                        broken["incoming_retries"][0]["idempotency_key"] = "new"
                    else:
                        broken["incoming_callbacks"][0]["event_id"] = "new"
                elif mutation == "cancelled":
                    broken["terminal_status"] = "cancelled"
                else:
                    row = dict(broken[ledger][0])
                    row["seller_promo_debit_minor" if factory is committed_replay else "debit_minor"] += 1
                    broken[ledger].append(row)
                with self.subTest(workflow=factory.__name__, mutation=mutation):
                    self.assertFalse(compare_workflow_state(expected, observed, broken)["equivalent"])

    def test_ledger_and_expected_budget_must_reconcile(self):
        for factory in (committed_replay, fee_replay):
            expected, workflow = factory()
            observed = dict(expected)
            del observed["reserved_minor" if factory is committed_replay else "new_commit_count"]
            budget = "budget_initial_minor" if factory is committed_replay else "fee_budget_initial_minor"
            for invalid in (None, False, 1, workflow[budget] + 1):
                with self.subTest(workflow=factory.__name__, budget=invalid):
                    self.assertFalse(compare_workflow_state(expected, observed, {**workflow, budget: invalid})["equivalent"])

    def test_mixed_workflow_and_nonzero_optional_counter_fail(self):
        expected, workflow = fee_replay()
        observed = dict(expected)
        del observed["new_commit_count"]
        self.assertFalse(compare_workflow_state(expected, observed, {**workflow, "existing_ledger": []})["equivalent"])
        expected["new_commit_count"] = 1
        self.assertFalse(compare_workflow_state(expected, observed, workflow)["equivalent"])

    def test_recursive_exact_types_do_not_use_python_bool_equality(self):
        self.assertFalse(strict_equal({"x": [0]}, {"x": [False]}))
        self.assertFalse(strict_equal({"x": 0}, {"x": 0.0}))

    def test_exact_duplicate_ledger_is_idempotent_but_not_an_extra_debit(self):
        for factory in (committed_replay, fee_replay):
            expected, workflow = factory()
            ledger = "existing_ledger" if factory is committed_replay else "existing_fee_ledger"
            workflow[ledger].append(copy.deepcopy(workflow[ledger][0]))
            observed = dict(expected)
            del observed["reserved_minor" if factory is committed_replay else "new_commit_count"]
            self.assertTrue(compare_workflow_state(expected, observed, workflow)["equivalent"])

    def test_conflicting_terminal_assertions_do_not_normalize(self):
        expected, workflow = committed_replay()
        expected["status"] = "cancelled"
        observed = dict(expected, terminal_status="committed")
        self.assertFalse(compare_workflow_state(expected, observed, workflow)["equivalent"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
