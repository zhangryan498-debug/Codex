"""Offline, allowlisted JustOne product-search projection. No credential/network I/O.

Join both search representations by (product_id, promotion_id); never by position.
This validates extraction, not source truth, inventory, pricing authority or ROI.
Only explicitly selected fields are retained. Original sources remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re

VERSION = "justone-product-search-summary-v2"
MISSING = object()
NUMBER_KEYS = ("origin", "integer", "decimal", "suffix")
CATEGORY_KEYS = ("first_category", "second_category", "third_category", "fourth_category")
BOUNDARIES = {
    "source_class": "third_party_returned_search_display_fields; merchant_backend_truth_unverified",
    "currency": None,
    "price_origin_unit": None,
    "price_display_unit": None,
    "sale_axis_y_definition": None,
    "month_sale_window_definition": None,
    "sales_unit": None,
    "statistics_timezone": None,
    "scope_equivalence_between_month_sale_and_axis": "UNVERIFIED",
    "title_and_tag_claims": "source_marketing_text_only; not_verified_efficacy_or_D&T_promises",
    "display_price_use": "research_only; not_D&T_floor_or_authorized_price_or_checkout_settlement",
    "regular_price_use": "source_display_reference_only; not_proven_historical_transaction",
    "shop_score_scale": None,
    "shop_score_use": "raw_scale_only; do_not_convert_to_five_stars_or_official_tier",
    "missing_business_fields": ["variant_sku", "actual_stock", "authorization_documents", "GMV", "orders",
                                "impressions", "clicks", "conversions", "ad_spend", "ad_ROI", "ad_incrementality"],
    "forbidden_derivations": ["price_times_month_sale_as_GMV", "price_times_axis_sum_as_GMV",
                              "invisible_funnel_metrics", "sales_correlation_as_ad_incrementality"],
}


def json_type(value):
    if value is MISSING:
        return "missing"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unsupported_python_type"


def get_at(root, pointer):
    """RFC6901 lookup; an absent child is never confused with explicit null."""
    value = root
    if not pointer:
        return value
    for part in pointer.split("/")[1:]:
        token = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            return MISSING
    return value


def evidence(root, pointer):
    value = get_at(root, pointer)
    kind = json_type(value)
    state = "present"
    if value is MISSING:
        state = "missing"
    elif value is None:
        state = "explicit_null"
    elif isinstance(value, str) and value == "":
        state = "empty_string"
    elif isinstance(value, (list, dict)) and len(value) == 0:
        state = "empty_container"
    elif isinstance(value, bool):
        state = "boolean_true" if value else "boolean_false"
    elif isinstance(value, (int, float)) and value == 0:
        state = "numeric_zero"
    result = {"pointer": pointer, "present": value is not MISSING, "json_type": kind, "state": state}
    if value is not MISSING:
        if isinstance(value, float) and not math.isfinite(value):
            result.update(state="nonfinite_number", value_encoding="nonfinite_literal", value=repr(value))
        elif isinstance(value, (list, dict)):
            result["item_count"] = len(value)
            if not value:
                result["value"] = value
        else:
            result["value"] = value
    return result


def container_evidence(root, pointer):
    """Keep shape provenance without copying arbitrary, non-allowlisted children."""
    result = evidence(root, pointer)
    value = result.pop("value", MISSING)
    if isinstance(value, (list, dict)):
        result["item_count"] = len(value)
        if len(value) == 0:
            result["value"] = value
    elif value is not MISSING:
        result["value"] = value
    return result


def scalar_evidence(root, pointer):
    result = evidence(root, pointer)
    if result["json_type"] in {"array", "object"}:
        result.pop("value", None)
        result["state"] = "INVALID_TYPE"
        result["expected_type"] = "scalar"
        result["value_omitted_due_to_invalid_type"] = True
    return result


SOURCE_METADATA_KEYS = (
    "kind", "path", "byte_count", "sha256", "source_is_sanitized_transfer",
    "original_wire_response", "index_sha256", "request_repeated", "recordTime_timezone",
)


def project_source_metadata(source):
    """Caller metadata has its own scalar allowlist; never copy request/auth trees."""
    if source is None:
        return {"kind": "clearly_labelled_synthetic_or_in_memory_input"}
    if not isinstance(source, dict):
        return {"state": "INVALID_TYPE", "json_type": json_type(source),
                "expected_type": "object_of_allowlisted_scalar_metadata", "value_omitted_due_to_invalid_type": True}
    result = {}
    for key in SOURCE_METADATA_KEYS:
        if key not in source:
            continue
        value = source[key]
        if isinstance(value, (list, dict)) or (isinstance(value, float) and not math.isfinite(value)):
            result[key] = {"state": "INVALID_TYPE", "json_type": json_type(value),
                           "metadata_key": key, "expected_type": "finite_scalar_or_null",
                           "value_omitted_due_to_invalid_type": True}
        else:
            result[key] = value
    return result


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def decimal_text(value):
    return format(value, "f") if value is not None else None


def identity(row):
    if not isinstance(row, dict):
        return None
    parts = []
    for key in ("product_id", "promotion_id"):
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        text = str(value)
        if not re.fullmatch(r"[0-9]+", text):
            return None
        parts.append(text)
    return tuple(parts)


def price_diagnostic(root, pointer):
    origin = number(get_at(root, pointer + "/origin"))
    integer = get_at(root, pointer + "/integer")
    fractional = get_at(root, pointer + "/decimal")
    display = None
    if isinstance(integer, str) and re.fullmatch(r"[0-9]+", integer) and isinstance(fractional, str):
        if fractional == "" or re.fullmatch(r"[0-9]+", fractional):
            display = Decimal(integer + ("." + fractional if fractional else ""))
    test_value = origin / Decimal(100) if origin is not None else None
    return {
        "input_pointers": [pointer + "/" + key for key in NUMBER_KEYS],
        "display_numeric_text": decimal_text(display),
        "origin_divided_by_test_divisor_100": decimal_text(test_value),
        "equals_display_at_test_divisor_100": None if origin is None or display is None else test_value == display,
        "unit_or_currency_inference": "NONE; arithmetic_only; divisor_100_is_a_test_not_a_unit_contract",
    }


def axis_diagnostic(root, info_pointer):
    axis_pointer = info_pointer + "/sale_axis"
    raw = get_at(root, axis_pointer)
    points = raw if isinstance(raw, list) else []
    labels, dates, invalid_labels, invalid_y = [], [], [], []
    invalid_label_types = []
    ys = []
    for index, point in enumerate(points):
        label = point.get("x", MISSING) if isinstance(point, dict) else MISSING
        y = point.get("y", MISSING) if isinstance(point, dict) else MISSING
        # A date label is a string. Never copy an unexpected subtree or other
        # type into derived labels; original invalid values remain in source only.
        labels.append(label if isinstance(label, str) else None)
        if not isinstance(label, str):
            invalid_label_types.append({"point_index": index, "pointer": axis_pointer + "/" + str(index) + "/x",
                                        "state": "INVALID_TYPE", "json_type": json_type(label),
                                        "expected_type": "YYYYMMDD_string", "value_omitted_due_to_invalid_type": True})
        try:
            if not isinstance(label, str) or not re.fullmatch(r"[0-9]{8}", label):
                raise ValueError("not an exact YYYYMMDD string")
            parsed = datetime.strptime(label, "%Y%m%d").date()
            dates.append(parsed)
        except ValueError:
            dates.append(None)
            invalid_labels.append(index)
        numeric = number(y)
        if numeric is None:
            invalid_y.append(index)
        ys.append(numeric)
    valid_dates = [d for d in dates if d is not None]
    repeats = sorted(k for k, n in Counter(d.isoformat() for d in valid_dates).items() if n > 1)
    valid = isinstance(raw, list) and bool(points) and not invalid_labels
    strictly_ascending = valid and all(a < b for a, b in zip(dates, dates[1:]))
    contiguous = strictly_ascending and all(b - a == timedelta(days=1) for a, b in zip(dates, dates[1:]))
    missing_dates = []
    if valid_dates:
        seen = set(valid_dates)
        first, last = min(seen), max(seen)
        span = (last - first).days
        if span <= 36600:
            missing_dates = [(first + timedelta(days=i)).isoformat() for i in range(span + 1)
                             if first + timedelta(days=i) not in seen]
    total = sum(ys, Decimal(0)) if isinstance(raw, list) and bool(points) and not invalid_y else None
    month = number(get_at(root, info_pointer + "/month_sale/origin"))
    return {
        "axis_pointer": axis_pointer,
        "point_count": len(points) if isinstance(raw, list) else None,
        "original_date_labels": labels,
        "invalid_date_point_indices": invalid_labels,
        "invalid_date_label_types": invalid_label_types,
        "duplicate_date_labels": repeats,
        "missing_calendar_date_labels": missing_dates,
        "strictly_ascending": bool(strictly_ascending),
        "has_descending_step": any(a is not None and b is not None and b < a for a, b in zip(dates, dates[1:])),
        "date_labels_are_30_consecutive_ascending": bool(contiguous and len(points) == 30),
        "first_parsed_date": valid_dates[0].isoformat() if valid_dates else None,
        "last_parsed_date": valid_dates[-1].isoformat() if valid_dates else None,
        "invalid_or_nonfinite_y_point_indices": invalid_y,
        "all_y_nonnegative": None if invalid_y or not points else all(y >= 0 for y in ys),
        "raw_numeric_comparison": {
            "month_sale_origin_pointer": info_pointer + "/month_sale/origin",
            "axis_y_input_pointers": [axis_pointer + "/" + str(i) + "/y" for i in range(len(points))],
            "axis_y_sum_diagnostic_text": decimal_text(total),
            "month_sale_origin_numeric_text": decimal_text(month),
            "raw_numeric_equal": None if total is None or month is None else total == month,
            "month_minus_axis_sum_diagnostic_text": decimal_text(month - total) if total is not None and month is not None else None,
            "scope_equivalence": "UNVERIFIED",
            "reconciliation": "UNRESOLVED: y semantics, month window, cutoff, unit and timezone unconfirmed; numeric inequality is not proven source error",
            "use": "arithmetic_diagnostic_only; do_not_replace_month_sale_or_infer_GMV_or_full_channel_30_day_sales",
        },
        "window_semantics": "date_labels_only; not_proven_daily_increments_or_full_channel_merchant_truth",
    }


def add_fields(root, destination, prefix, names, label):
    for name in names:
        destination[label + "." + name.replace("/", ".")] = scalar_evidence(root, prefix + "/" + name)


def project_record(root, key, promotion_indices, summary_indices):
    matched = key is not None and len(promotion_indices) == len(summary_indices) == 1
    status = "MATCHED_UNIQUE" if matched else "UNRESOLVED_NO_MERGE"
    result = {
        "join_key": list(key) if key else None,
        "join": {"status": status, "method": "exact_product_id_plus_promotion_id; no_positional_fallback",
                 "promotion_indices": promotion_indices, "summary_indices": summary_indices},
        "fields": {}, "container_states": {}, "sale_axis_points": [],
        "diagnostics": {}, "semantic_state": "DISPLAY_FIELDS_RETAINED; BUSINESS_MEANING_PENDING",
    }
    fields = result["fields"]
    # Do not select any side arbitrarily in an ambiguous/missing join.
    if not matched:
        for branch, indices in (("promotions", promotion_indices), ("summary_promotions", summary_indices)):
            for i in indices:
                add_fields(root, fields, "/data/" + branch + "/" + str(i),
                           ("product_id", "promotion_id"), branch + "_" + str(i))
        return result
    pi, si = promotion_indices[0], summary_indices[0]
    p, s = "/data/promotions/" + str(pi), "/data/summary_promotions/" + str(si)
    b, info = s + "/base_model", s + "/base_model/product_info"
    add_fields(root, fields, p, ("product_id", "promotion_id"), "promotion")
    add_fields(root, fields, p + "/base_info", ("title", "price", "detail_url"), "promotion.base_info")
    add_fields(root, fields, s, ("product_id", "promotion_id"), "summary")
    add_fields(root, fields, info, ("name", "detail_url", "month_sale_text", "product_status/status", "product_status/check_status"), "summary.product")
    for branch, prefix in (("promotion.category", p + "/base_info/category"), ("summary.category", info + "/category")):
        add_fields(root, fields, prefix, ("leaf_layer",), branch)
        for tier in CATEGORY_KEYS:
            add_fields(root, fields, prefix + "/" + tier, ("category_id", "category_name"), branch + "." + tier)
    for label, prefix in (("summary.month_sale", info + "/month_sale"),
                          ("summary.good_ratio", info + "/good_ratio"),
                          ("summary.price", b + "/marketing_info/price_desc/price"),
                          ("summary.regular_price", b + "/marketing_info/price_desc/regular_price")):
        result["container_states"][label] = container_evidence(root, prefix)
        add_fields(root, fields, prefix, NUMBER_KEYS, label)
    add_fields(root, fields, b + "/marketing_info/price_desc", ("price_text",), "summary.marketing")
    add_fields(root, fields, b + "/shop_info", ("shop_id", "shop_name", "shop_score_info/has_data",
               "shop_score_info/shop_score/score", "shop_score_info/shop_score/rating", "shop_score_info/shop_score/percentage",
               "shop_score_info/shop_score/rise_than_7d_ago"), "summary.shop")
    add_fields(root, fields, b + "/extend_info/data_report", ("product_id", "commodity_id", "shop_id", "query"), "summary.report_identity")
    result["container_states"]["summary.base_model"] = container_evidence(root, b)
    result["container_states"]["summary.sale_axis"] = container_evidence(root, info + "/sale_axis")
    axis = get_at(root, info + "/sale_axis")
    if isinstance(axis, list):
        result["sale_axis_points"] = [{"x": scalar_evidence(root, info + "/sale_axis/" + str(i) + "/x"),
                                      "y": scalar_evidence(root, info + "/sale_axis/" + str(i) + "/y")}
                                     for i in range(len(axis))]
    result["diagnostics"] = {"display_price_arithmetic": price_diagnostic(root, b + "/marketing_info/price_desc/price"),
                             "regular_price_arithmetic": price_diagnostic(root, b + "/marketing_info/price_desc/regular_price"),
                             "sale_axis": axis_diagnostic(root, info)}
    return result


def normalize(root, source=None):
    p_raw, s_raw = get_at(root, "/data/promotions"), get_at(root, "/data/summary_promotions")
    errors = []
    if not isinstance(root, dict):
        errors.append("root_is_not_object")
    if not isinstance(get_at(root, "/data"), dict):
        errors.append("data_is_not_object")
    for name, value in (("promotions", p_raw), ("summary_promotions", s_raw)):
        if not isinstance(value, list):
            errors.append(name + "_is_not_array")
    if type(get_at(root, "/code")) is not int or get_at(root, "/code") != 0:
        errors.append("business_code_not_integer_zero")
    ps, ss = p_raw if isinstance(p_raw, list) else [], s_raw if isinstance(s_raw, list) else []
    maps, invalid = [], []
    for name, rows in (("promotions", ps), ("summary_promotions", ss)):
        lookup = defaultdict(list)
        for i, row in enumerate(rows):
            key = identity(row)
            if key is None:
                invalid.append({"branch": name, "index": i, "shape": container_evidence(root, "/data/" + name + "/" + str(i)),
                                "identity_evidence": {k: scalar_evidence(root, "/data/" + name + "/" + str(i) + "/" + k)
                                                      for k in ("product_id", "promotion_id")}})
            else:
                lookup[key].append(i)
        maps.append(lookup)
    pm, sm = maps
    keys = list(pm) + [k for k in sm if k not in pm]
    records = [project_record(root, k, pm.get(k, []), sm.get(k, [])) for k in keys]
    unresolved = [r["join_key"] for r in records if r["join"]["status"] != "MATCHED_UNIQUE"]
    if invalid:
        errors.append("invalid_row_identity_or_shape")
    if unresolved:
        errors.append("nonunique_or_unmatched_identity_join")
    matched = [r for r in records if r["join"]["status"] == "MATCHED_UNIQUE"]
    refs = lambda key: [r["fields"][key] for r in matched]
    numeric_equal = Counter(str(r["diagnostics"]["sale_axis"]["raw_numeric_comparison"]["raw_numeric_equal"]) for r in matched)
    coverage = {
        "promotion_rows": len(ps), "summary_rows": len(ss), "normalized_records": len(records),
        "matched_unique_records": len(matched), "unresolved_join_records": len(unresolved), "invalid_identity_rows": len(invalid),
        "promotion_base_info_price_explicit_null": sum(x["state"] == "explicit_null" for x in refs("promotion.base_info.price")),
        "summary_display_price_origin_present": sum(x["present"] and number(x.get("value")) is not None for x in refs("summary.price.origin")),
        "summary_regular_price_origin_present": sum(x["present"] and number(x.get("value")) is not None for x in refs("summary.regular_price.origin")),
        "summary_shop_id_present": sum(x["present"] and x["state"] != "explicit_null" for x in refs("summary.shop.shop_id")),
        "summary_month_sale_origin_present": sum(x["present"] and number(x.get("value")) is not None for x in refs("summary.month_sale.origin")),
        "summary_month_sale_numeric_zero": sum(x["state"] == "numeric_zero" for x in refs("summary.month_sale.origin")),
        "summary_month_sale_positive": sum(number(x.get("value")) is not None and number(x.get("value")) > 0 for x in refs("summary.month_sale.origin")),
        "summary_30_contiguous_ascending_date_label_rows": sum(r["diagnostics"]["sale_axis"]["date_labels_are_30_consecutive_ascending"] for r in matched),
        "raw_month_vs_axis_numeric_equal_rows": numeric_equal["True"],
        "raw_month_vs_axis_numeric_unequal_rows": numeric_equal["False"],
        "raw_month_vs_axis_not_computable_rows": numeric_equal["None"],
        "business_scope_equivalence_confirmed_rows": 0,
    }
    return {
        "schema_version": VERSION, "platform": "douyin", "site": "CN", "dataset": "offline_product_search_display_projection",
        "source": project_source_metadata(source),
        "response_evidence": {name: scalar_evidence(root, "/" + name) for name in ("code", "message", "recordTime", "requestId")},
        "query_evidence": {name: scalar_evidence(root, "/requestParams/query/" + name) for name in ("keyword", "page")},
        "collection_shape": {"promotions": container_evidence(root, "/data/promotions"),
                             "summary_promotions": container_evidence(root, "/data/summary_promotions")},
        "technical_validation": {"status": "PROJECTED_WITHOUT_IDENTITY_AMBIGUITY" if not errors else "REJECTED_OR_PARTIAL_NO_AMBIGUOUS_MERGE",
                                 "errors": errors, "meaning": "technical_extraction_only; not_business_approval"},
        "coverage": coverage, "boundaries": BOUNDARIES.copy(), "records": records, "invalid_rows": invalid,
        "operations": {"api_requests": 0, "credential_accesses": 0, "source_writes": 0, "business_actions": 0},
    }


def strict_json(raw):
    def invalid(constant):
        raise ValueError("nonstandard JSON number: " + constant)
    return json.loads(raw.decode("utf-8-sig"), parse_constant=invalid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest.lower() != args.expected_sha256.lower():
        raise SystemExit("source_integrity_mismatch; no_projection_written")
    result = normalize(strict_json(raw), {"path": str(args.source.resolve()), "byte_count": len(raw), "sha256": digest,
                                       "request_repeated": False, "recordTime_timezone": "UNSPECIFIED_IN_SOURCE"})
    data = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as handle:
        handle.write(data)
    after = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if after != digest:
        raise SystemExit("source_changed_during_projection; investigate")
    print(json.dumps({"coverage": result["coverage"], "technical_validation": result["technical_validation"],
                      "output_sha256": hashlib.sha256(data).hexdigest(), "source_unchanged": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
