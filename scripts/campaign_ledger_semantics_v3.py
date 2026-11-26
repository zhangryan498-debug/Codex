"""Versioned ledger scopes and contextual citations for synthetic offline reviews.

The caller supplies an independently computed result and the frozen public rule
cards. This layer does not discover live eligibility or invent missing money.
It never turns a previously blocked proposal into ALLOW.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
import re

VERSION = "ledger-scopes-and-rule-trace-v3"
CORE = ("buyer_goods_minor", "platform_reimbursement_minor", "technical_fee_minor",
        "mdp_fee_minor", "other_service_fee_minor", "fees_minor", "listed_costs_minor", "net_minor")
FEE_COMPONENTS = ("technical_fee_minor", "mdp_fee_minor", "other_service_fee_minor")


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _walk(value, path="facts"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield path + "." + key, key, child
            yield from _walk(child, path + "." + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, path + "[" + str(index) + "]")


def required_rule_trace(case, cards):
    """Trace only mechanisms actually present, rather than all platform rules."""
    facts, market = case["facts"], case["market"]
    by_id = {card["id"]: card for card in cards}
    evidence = {}

    def add(rule, paths, purpose):
        if not paths:
            return
        if rule not in by_id:
            raise ValueError("Required rule absent from the frozen snapshot: " + rule)
        entry = evidence.setdefault(rule, {"rule_id": rule, "title": by_id[rule]["title"],
                                          "sources": deepcopy(by_id[rule].get("sources", [])),
                                          "facts_paths": [], "purposes": []})
        entry["facts_paths"] = sorted(set(entry["facts_paths"] + paths))
        entry["purposes"] = sorted(set(entry["purposes"] + [purpose]))

    walked = list(_walk(facts))
    paths = lambda names: [path for path, key, value in walked if key in names]
    if market == "douyin_cn":
        add("DY01", paths({"store"}), "Store admission is separate from subsidy eligibility")
        add("DY02", paths({"platform_voucher", "mixed_voucher", "mixed_cart_voucher", "exclusive_platform_vouchers", "platform_coupon", "seller_voucher"}), "Coupon bearer/funding must be explicit")
        add("DY03", paths({"price_candidates", "seller_product_discount_minor", "seller_product_discount_unit_minor"}), "Single-item discounts are not multiplied together")
        add("DY04", paths({"price_candidates", "seller_voucher", "threshold_basis", "seller_voucher_allocation_per_unit_minor"}), "Priority, discount order and threshold basis")
        add("DY05", paths({"entry_merchant_rate_bps", "merchant_funding_cap_minor", "current_authoritative_synthetic_entry"}), "Mixed-funding entry/version and contribution evidence")
        add("DY06", paths({"mixed_cart_voucher", "merchant_funding_basis", "allocation_contract", "merchant_allocation_weights", "platform_allocation_weights"}), "Funding basis and per-SKU allocation conservation")
        add("DY07", paths({"exclusive_platform_vouchers"}), "Exclusive vouchers cannot be jointly redeemed")
        add("DY08", paths({"promotions", "official_campaign_end_raw", "enabled_registration_modes", "double11_stage"}), "Campaign stage, time and mutually exclusive modes")
        add("DY08", [path for path, key, value in walked if key in ("voucher_subtype", "type") and isinstance(value, str) and "double11" in value], "Double11 candidate cost and eligibility remain campaign-scoped even if another voucher wins")
        add("DY09", paths({"technical_fee", "confirmed_billed_technical_fee_minor", "refund_receipt", "refund_state", "floor"}), "Retained fees and net contribution basis")
    elif market == "tiktok_ph":
        add("PH01", paths({"price_candidates", "selected_seller_flash_sale_price_minor", "selected_seller_flash_sale_unit_price_minor"}), "Product-price priority and flash-sale compatibility")
        add("PH02", paths({"seller_voucher", "regular_voucher", "seller_voucher_candidates", "seller_voucher_allocation_per_unit_minor", "buyer"}), "Seller-voucher eligibility, quantity and threshold")
        add("PH03", paths({"bmsm"}), "BMSM and pre-BMSM voucher threshold")
        add("PH04", paths({"basic_mdv", "confirmed_platform_mdv_reimbursement_per_unit_minor", "coupon"}), "Voucher subtype and allowed/exclusive combinations")
        mdp = facts.get("basic_mdp", {})
        if mdp.get("enrolled") is not False or facts.get("mdp_plus", {}).get("enrolled"):
            add("PH05", paths({"basic_mdp", "mdp_plus"}), "MDP success, calendar and per-physical-unit fee cap")
        add("PH06", paths({"mdp_plus"}), "PLUS joint fee table cannot be replaced by a legacy percentage")
        add("PH07", paths({"price_forecast", "seller_shipping_discount"}), "Forecast is not final payment; shipping basis remains explicit")
    else:
        raise ValueError("Unsupported platform/country")
    return [evidence[key] for key in sorted(evidence)]


def citation_check(reported_ids, trace, cards, market):
    prefix = "DY" if market == "douyin_cn" else "PH"
    valid = {card["id"] for card in cards if card["id"].startswith(prefix)}
    required = {entry["rule_id"] for entry in trace}
    well_typed = isinstance(reported_ids, list) and all(isinstance(x, str) for x in reported_ids)
    reported = set(reported_ids) if well_typed else set()
    return {"required": sorted(required), "reported": sorted(reported),
            "missing": sorted(required-reported), "invalid": sorted(reported-valid),
            "passed": well_typed and len(reported_ids) == len(reported) and bool(reported) and required <= reported <= valid}


def _remove_original_prefix(numbers):
    return {key: deepcopy(value) for key, value in numbers.items() if not key.startswith("original_")}


def _is_invalid(case, result):
    facts = case["facts"]
    reasons = []
    if result.get("ledger_scope", "").startswith("non_executable"):
        reasons.append(result.get("safety_reason", "explicitly invalid proposal"))
    if result.get("decision") == "REJECT" and result.get("floor_status") == "NOT_APPLICABLE":
        reasons.append("no executable current submitted configuration")
    workflow = facts.get("workflow", {})
    if any(event.get("status") == "owner_cancelled" and event.get("authoritative_synthetic_owner_event") is True
           for event in workflow.get("events", [])):
        reasons.append("authoritative cancellation is terminal")
    raw = facts.get("official_campaign_end_raw")
    if isinstance(raw, str):
        match = re.search(r"(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)", raw)
        offset = re.search(r"合成\s*([+-]\d\d:\d\d)", raw)
        if match and offset:
            end = datetime.fromisoformat(match[1]+"T"+match[2]+offset[1])
            if datetime.fromisoformat(case["evaluation_time"]) > end:
                reasons.append("expired at the explicitly declared synthetic campaign clock")
    flash = any(key.startswith("selected_seller_flash_sale") for _, key, _ in _walk(facts))
    if case["market"] == "tiktok_ph" and flash and "cofund_voucher" in facts and facts.get("specific_campaign_override_proof") is None:
        reasons.append("flash and actual cofund voucher prohibited by current frozen rule")
    return sorted(set(reasons))


def normalize(case, independently_calculated, cards):
    """Separate current, historical and hypothetical outputs without recalculating prices."""
    result = deepcopy(independently_calculated)
    facts = case["facts"]
    before_decision = result["decision"]
    prior = deepcopy(result.get("numbers", {}))
    prior_skus = deepcopy(result.get("sku_numbers", {}))
    result["numbers"] = _remove_original_prefix(prior)
    result["sku_numbers"] = {sku: _remove_original_prefix(row) for sku, row in prior_skus.items()}
    scopes = {"current": {"applicability": "applicable", "numbers": result["numbers"],
                           "sku_numbers": result["sku_numbers"]}}
    if "refund_receipt" in facts or "refund_state" in facts:
        scopes["original_pre_refund"] = {
            "numbers": {key.removeprefix("original_"): value for key, value in prior.items() if key.startswith("original_")},
            "sku_numbers": {sku: {key.removeprefix("original_"): value for key, value in row.items() if key.startswith("original_")} for sku, row in prior_skus.items()},
            "use_for_current_floor": False}
    invalid = _is_invalid(case, result)
    if invalid:
        scopes["counterfactual_requested_configuration"] = {
            "numbers": _remove_original_prefix(prior),
            "sku_numbers": {sku: _remove_original_prefix(row) for sku, row in prior_skus.items()},
            "applicability": "arithmetic_only_not_executable", "assumptions": invalid,
            "use_for_current_floor": False}
        empty = {key: None for key in CORE}
        result["numbers"] = deepcopy(empty)
        result["sku_numbers"] = {sku: deepcopy(empty) for sku in prior_skus}
        result["selected_tools"] = []
        result["decision"], result["floor_status"] = "REJECT", "NOT_APPLICABLE"
        scopes["current"] = {"applicability": "not_executable", "numbers": result["numbers"],
                             "sku_numbers": result["sku_numbers"], "reasons": invalid}
    else:
        plus_unknown = facts.get("mdp_plus", {}).get("enrolled") is True and facts["mdp_plus"].get("current_joint_fee_table") is None
        if plus_unknown:
            projection = {"numbers": _remove_original_prefix(prior),
                          "sku_numbers": {sku: _remove_original_prefix(row) for sku, row in prior_skus.items()},
                          "assumptions": ["base plan alone; current PLUS joint fee table unavailable"],
                          "use_for_current_floor": False}
            projection["numbers"]["net_minor"] = prior.get("net_before_plus_minor", prior.get("original_net_minor"))
            for sku, row in projection["sku_numbers"].items():
                row["net_minor"] = prior_skus[sku].get("original_net_minor")
            scopes["base_plan_projection"] = projection
            for row in [result["numbers"], *result["sku_numbers"].values()]:
                row["base_plan_fee_subtotal_minor"] = row.get("fees_minor")
                row["fees_minor"] = row["net_minor"] = None
                row["net_lower_minor"] = row["net_upper_minor"] = None
                row.pop("net_before_plus_minor", None)
                if facts["mdp_plus"].get("current_interaction_with_base_cap") is None:
                    row["mdp_fee_minor"] = None
                components = [row[key] for key in FEE_COMPONENTS if type(row.get(key)) is int]
                row["known_fees_subtotal_minor"] = sum(components) if components else None
            result["decision"], result["floor_status"] = "HOLD", "UNPROVEN"
        unknown_costs = [key.removeprefix("cost_ledger_complete_except_") for key, value in facts.items()
                         if key.startswith("cost_ledger_complete_except_") and value is True and key != "cost_ledger_complete_except_plus"]
        if unknown_costs:
            scopes["known_cost_projection"] = {"numbers": _remove_original_prefix(prior),
                                               "sku_numbers": {sku: _remove_original_prefix(row) for sku, row in prior_skus.items()},
                                               "unknown_components": unknown_costs, "use_for_current_floor": False}
            for row in [result["numbers"], *result["sku_numbers"].values()]:
                row["known_costs_subtotal_minor"] = row.get("listed_costs_minor")
                row["listed_costs_minor"] = None
                row["net_minor"] = None
            shipping = facts.get("seller_shipping_discount", {})
            finite = (set(unknown_costs) == {"shipping"} and type(shipping.get("seller_shipping_cost_if_eligible_minor")) is int
                      and shipping["seller_shipping_cost_if_eligible_minor"] >= 0
                      and type(result["numbers"].get("net_lower_minor")) is int
                      and type(result["numbers"].get("net_upper_minor")) is int)
            if not finite:
                result["decision"], result["floor_status"] = "HOLD", "UNPROVEN"
                for row in [result["numbers"], *result["sku_numbers"].values()]:
                    row["net_lower_minor"] = row["net_upper_minor"] = None
        if result.get("pending_fields"):
            scopes["current"]["unresolved_fields"] = deepcopy(result["pending_fields"])
    trace = required_rule_trace(case, cards)
    result["rule_trace"] = trace
    result["rule_ids"] = [row["rule_id"] for row in trace]
    result["ledger_scopes"] = scopes
    result["semantic_policy"] = VERSION
    result["legacy_calculation_sha256"] = digest(independently_calculated)
    result["current_amount_status"] = amount_status(result)
    result["current_floor_proven"] = result["decision"] == "ALLOW" and result["floor_status"] == "PASS"
    result["scope"] = "synthetic_offline"
    result["release_status"] = "not_authorized"
    if before_decision != "ALLOW" and result["decision"] == "ALLOW":
        raise AssertionError("Semantic normalization must not release a previously blocked proposal")
    errors = validate_scopes(case, result, cards)
    if errors:
        raise ValueError("Invalid semantic result: " + "; ".join(errors))
    return result


def amount_status(result):
    if result["ledger_scopes"]["current"]["applicability"] == "not_executable":
        return "not_executable_current_with_separate_counterfactual"
    numbers = result["numbers"]
    if type(numbers.get("net_minor")) is int:
        return "exact_current_ledger"
    if type(numbers.get("net_lower_minor")) is int and type(numbers.get("net_upper_minor")) is int:
        return "bounded_current_ledger"
    return "partial_known_components_missing_information"


def validate_scopes(case, result, cards):
    errors = []
    current = result.get("ledger_scopes", {}).get("current", {})
    if current.get("numbers") != result.get("numbers") or current.get("sku_numbers") != result.get("sku_numbers"):
        errors.append("current ledger and public numeric fields differ")
    if current.get("applicability") == "not_executable":
        if result.get("decision") != "REJECT" or result.get("selected_tools"):
            errors.append("invalid configuration must remain rejected without effective tools")
        for row in [result.get("numbers", {}), *result.get("sku_numbers", {}).values()]:
            if any(row.get(key) is not None for key in CORE):
                errors.append("hypothetical/current ledger contamination")
    facts = case["facts"]
    for row in [result.get("numbers", {}), *result.get("sku_numbers", {}).values()]:
        for key in CORE:
            value = row.get(key)
            if value is not None and (type(value) is not int or key != "net_minor" and value < 0):
                errors.append("numeric ledger requires exact integers/null: " + key)
    if facts.get("mdp_plus", {}).get("enrolled") is True and facts["mdp_plus"].get("current_joint_fee_table") is None:
        if any(row.get("fees_minor") is not None for row in [result.get("numbers", {}), *result.get("sku_numbers", {}).values()]):
            errors.append("unknown joint total fee presented as a known subtotal")
        if result.get("decision") == "ALLOW":
            errors.append("unknown PLUS joint fee cannot guarantee current floor")
    if any(key.startswith("cost_ledger_complete_except_") and key != "cost_ledger_complete_except_plus" and value is True for key, value in facts.items()):
        if any(row.get("listed_costs_minor") is not None for row in [result.get("numbers", {}), *result.get("sku_numbers", {}).values()]):
            errors.append("unknown total costs presented as a known subtotal")
    if result.get("decision") == "ALLOW":
        floor = facts.get("floor", {})
        if floor.get("scope") == "whole_order":
            checks = [(result.get("numbers", {}), floor.get("amount_minor"))]
        elif floor.get("scope") == "each_sku_total" and set(floor.get("amounts_minor", {})) == set(result.get("sku_numbers", {})):
            checks = [(result["sku_numbers"][sku], amount) for sku, amount in floor["amounts_minor"].items()]
        else:
            checks = []
            errors.append("ALLOW lacks exact floor subject coverage")
        for row, minimum in checks:
            proven = row.get("net_minor")
            if proven is None:
                proven = row.get("net_lower_minor")
            if type(minimum) is not int or minimum < 0 or type(proven) is not int or proven < minimum:
                errors.append("ALLOW lacks a sufficient current lower bound for every subject")
    citation = citation_check(result.get("rule_ids"), required_rule_trace(case, cards), cards, case["market"])
    if not citation["passed"]:
        errors.append("missing, foreign or unknown necessary rule citation")
    return errors
