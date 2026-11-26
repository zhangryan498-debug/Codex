import copy
import unittest
import campaign_ledger_semantics_v3 as s

CARDS = [{"id": prefix+str(i).zfill(2), "title": "frozen test rule", "sources": ["test snapshot"]}
         for prefix, count in (("DY", 9), ("PH", 7)) for i in range(1, count+1)]


def fixture(market="douyin_cn"):
    facts = {"sku": {"sku_id": "S", "quantity": 1, "merchant_goods_after_seller_discounts_minor": 3000},
             "costs": {"cogs_minor": 2000}, "cost_ledger_complete": True,
             "floor": {"scope": "each_sku_total", "basis": "net_profit_after_all_listed_costs", "amounts_minor": {"S": 800}}}
    if market == "douyin_cn": facts["technical_fee"] = {"rate_bps": 300}
    else: facts["basic_mdp"] = {"enrolled": True}
    case = {"case_id": "SYNTHETIC", "market": market, "evaluation_time": "2026-11-11T15:00:00+08:00", "facts": facts}
    nums = {"buyer_goods_minor": 3000, "platform_reimbursement_minor": 0, "technical_fee_minor": 90,
            "mdp_fee_minor": 0, "other_service_fee_minor": 0, "fees_minor": 90,
            "listed_costs_minor": 2000, "net_minor": 910, "original_net_minor": 910}
    result = {"case_id": "SYNTHETIC", "decision": "ALLOW", "floor_status": "PASS", "selected_tools": [],
              "numbers": nums, "sku_numbers": {"S": copy.deepcopy(nums)}, "rule_ids": [],
              "pending_fields": [], "state": {}, "safety_reason": "test"}
    return case, result


class ScopeTests(unittest.TestCase):
    def test_valid_current_ledger_and_inputs_are_preserved(self):
        case, old = fixture()
        before = copy.deepcopy((case, old))
        result = s.normalize(case, old, CARDS)
        self.assertEqual(result["numbers"]["net_minor"], 910)
        self.assertEqual(result["current_amount_status"], "exact_current_ledger")
        self.assertNotIn("original_net_minor", result["numbers"])
        self.assertEqual((case, old), before)

    def assert_invalid(self, case, old):
        result = s.normalize(case, old, CARDS)
        self.assertEqual(result["decision"], "REJECT")
        self.assertEqual(result["floor_status"], "NOT_APPLICABLE")
        for row in [result["numbers"], *result["sku_numbers"].values()]:
            self.assertTrue(all(row[key] is None for key in s.CORE))
        branch = result["ledger_scopes"]["counterfactual_requested_configuration"]
        self.assertEqual(branch["numbers"]["net_minor"], 910)
        self.assertFalse(branch["use_for_current_floor"])
        return result

    def test_cancelled_current_is_null_and_counterfactual_stays_separate(self):
        case, old = fixture()
        case["facts"]["workflow"] = {"events": [{"status": "owner_cancelled", "authoritative_synthetic_owner_event": True}]}
        self.assert_invalid(case, old)

    def test_expired_current_is_null_and_date_is_not_hardcoded(self):
        for year in (2026, 2028):
            case, old = fixture()
            case["evaluation_time"] = str(year)+"-11-12T00:00:00+08:00"
            case["facts"]["official_campaign_end_raw"] = str(year)+"-11-11 23:59:59; 合成+08:00"
            self.assert_invalid(case, old)

    def test_inclusive_end_does_not_expire_early(self):
        case, old = fixture()
        case["evaluation_time"] = "2026-11-11T23:59:59+08:00"
        case["facts"]["official_campaign_end_raw"] = "2026-11-11 23:59:59; 合成+08:00"
        self.assertEqual(s.normalize(case, old, CARDS)["decision"], "ALLOW")

    def test_flash_cofund_illegal_even_if_arithmetic_clears_floor(self):
        case, old = fixture("tiktok_ph")
        case["facts"]["sku"]["selected_seller_flash_sale_price_minor"] = 3000
        case["facts"]["cofund_voucher"] = {"actual_subtype": "cofund_voucher"}
        case["facts"]["specific_campaign_override_proof"] = None
        self.assert_invalid(case, old)

    def test_hypothetical_amount_cannot_be_reintroduced_as_current(self):
        case, old = fixture()
        old.update(decision="REJECT", floor_status="NOT_APPLICABLE")
        result = self.assert_invalid(case, old)
        result["numbers"]["net_minor"] = 910
        self.assertIn("hypothetical/current ledger contamination", s.validate_scopes(case, result, CARDS))

    def test_known_plus_subtotal_is_not_unknown_total(self):
        case, old = fixture("tiktok_ph")
        case["facts"]["mdp_plus"] = {"enrolled": True, "current_joint_fee_table": None, "current_interaction_with_base_cap": None}
        case["facts"]["cost_ledger_complete_except_plus"] = True
        old["pending_fields"] = ["mdp_plus.current_joint_fee_table", "unresolved_cost.plus"]
        for row in [old["numbers"], *old["sku_numbers"].values()]:
            row.update(mdp_fee_minor=60, other_service_fee_minor=30, technical_fee_minor=0,
                       fees_minor=90, net_before_plus_minor=910)
        result = s.normalize(case, old, CARDS)
        self.assertEqual(result["decision"], "HOLD")
        for row in [result["numbers"], *result["sku_numbers"].values()]:
            self.assertIsNone(row["fees_minor"])
            self.assertIsNone(row["net_minor"])
            self.assertIsNone(row["net_lower_minor"])
            self.assertEqual(row["base_plan_fee_subtotal_minor"], 90)
        self.assertEqual(result["ledger_scopes"]["base_plan_projection"]["numbers"]["net_minor"], 910)

    def test_shipping_total_unknown_keeps_known_subtotal_and_safe_finite_bound(self):
        case, old = fixture()
        case["facts"]["cost_ledger_complete_except_shipping"] = True
        case["facts"]["seller_shipping_discount"] = {"threshold_basis": None, "seller_shipping_cost_if_eligible_minor": 50}
        for row in [old["numbers"], *old["sku_numbers"].values()]: row.update(net_minor=None, net_lower_minor=860, net_upper_minor=910)
        old["pending_fields"] = ["unresolved_cost.shipping"]
        result = s.normalize(case, old, CARDS)
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["numbers"]["known_costs_subtotal_minor"], 2000)
        self.assertIsNone(result["numbers"]["listed_costs_minor"])
        self.assertEqual(result["numbers"]["net_lower_minor"], 860)

    def test_unknown_unbounded_cost_does_not_receive_a_lower_bound(self):
        case, old = fixture()
        case["facts"]["cost_ledger_complete_except_tax"] = True
        result = s.normalize(case, old, CARDS)
        self.assertEqual(result["decision"], "HOLD")
        self.assertIsNone(result["numbers"]["listed_costs_minor"])
        self.assertIsNone(result["numbers"]["net_lower_minor"])

    def test_pre_refund_snapshot_cannot_replace_current_lifecycle_net(self):
        case, old = fixture()
        case["facts"]["refund_receipt"] = {"sku_id": "S", "buyer_goods_refund_minor": 100}
        old["numbers"]["net_minor"] = old["sku_numbers"]["S"]["net_minor"] = 810
        result = s.normalize(case, old, CARDS)
        self.assertEqual(result["numbers"]["net_minor"], 810)
        self.assertEqual(result["ledger_scopes"]["original_pre_refund"]["numbers"]["net_minor"], 910)

    def test_bool_float_string_and_missing_lower_bound_do_not_authorize(self):
        for value in (False, 910.0, "910", None, 799):
            case, old = fixture()
            old["numbers"]["net_minor"] = old["sku_numbers"]["S"]["net_minor"] = value
            with self.subTest(value=value), self.assertRaises(ValueError): s.normalize(case, old, CARDS)

    def test_order_surplus_does_not_cover_a_failing_sku(self):
        case, old = fixture()
        case["facts"]["floor"]["amounts_minor"]["B"] = 500
        old["sku_numbers"]["B"] = dict(old["sku_numbers"]["S"], net_minor=499)
        old["numbers"]["net_minor"] = 1409
        with self.assertRaises(ValueError): s.normalize(case, old, CARDS)

    def test_required_rule_added_for_nonwinning_campaign_candidate(self):
        case, old = fixture()
        case["facts"]["exclusive_platform_vouchers"] = [{"voucher_subtype": "synthetic_double11_consumer"}]
        result = s.normalize(case, old, CARDS)
        self.assertIn("DY08", result["rule_ids"])

    def test_allocation_and_flash_unknown_still_cite_relevant_rules(self):
        case, old = fixture()
        case["facts"]["allocation_contract"] = {"tie_break_rule": None}
        self.assertIn("DY06", s.normalize(case, old, CARDS)["rule_ids"])
        case, old = fixture("tiktok_ph")
        case["facts"]["sku"]["selected_seller_flash_sale_unit_price_minor"] = 3000
        case["facts"]["coupon"] = {"actual_voucher_subtype": None}
        self.assertIn("PH01", s.normalize(case, old, CARDS)["rule_ids"])

    def test_fee_only_case_does_not_require_irrelevant_voucher_rules(self):
        case, old = fixture("tiktok_ph")
        self.assertEqual(s.normalize(case, old, CARDS)["rule_ids"], ["PH05"])

    def test_missing_foreign_duplicate_and_unknown_citations_fail(self):
        case, old = fixture()
        good = s.normalize(case, old, CARDS)
        for ids in ([], ["PH05"], ["DY09", "DY09"], ["DY99"]):
            bad = copy.deepcopy(good); bad["rule_ids"] = ids
            with self.subTest(ids=ids): self.assertIn("missing, foreign or unknown necessary rule citation", s.validate_scopes(case, bad, CARDS))


if __name__ == "__main__": unittest.main(verbosity=2)
