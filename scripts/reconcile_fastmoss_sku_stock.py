"""Pure offline reconciliation of returned FastMoss SKU stock reports.

No provider calls, credentials, persistence hooks, price decisions or purchase
authorizations.  Provider data is preserved; calculated inventory is a separate
view of the *returned* rows, not a statement about complete warehouse stock.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "fastmoss-returned-stock-reconciliation/2"
MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_VARIANTS = 10000
MAX_DIMENSIONS = 128
MAX_PROPERTIES_PER_SKU = 64
MAX_VALUES_PER_DIMENSION = 20000
MAX_JSON_NODES = 500000
MAX_JSON_DEPTH = 40
MAX_STRING_LENGTH = 100000


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _validate_json(value: Any) -> None:
    """Reject non-JSON / unbounded structures before copying or traversal."""
    pending = [(value, 0)]
    visited = 0
    while pending:
        node, depth = pending.pop()
        visited += 1
        if visited > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError("INVALID_INPUT: JSON resource limit exceeded")
        if node is None or type(node) in (bool, int):
            continue
        if type(node) is float:
            if not math.isfinite(node):
                raise ValueError("INVALID_INPUT: non-finite JSON number")
            continue
        if type(node) is str:
            if len(node) > MAX_STRING_LENGTH:
                raise ValueError("INVALID_INPUT: string length limit exceeded")
            continue
        if type(node) is list:
            pending.extend((item, depth + 1) for item in node)
            continue
        if type(node) is dict:
            if any(type(key) is not str for key in node):
                raise ValueError("INVALID_INPUT: object keys must be strings")
            pending.extend((key, depth + 1) for key in node)
            pending.extend((item, depth + 1) for item in node.values())
            continue
        raise ValueError("INVALID_INPUT: value is not a JSON value")


def _identifier(value: Any) -> bool:
    # Do not stringify rounded JSON floats, trim names or normalize long IDs.
    return type(value) is str and bool(value) and value == value.strip() and len(value) <= 256


def _name(value: Any) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _stock(value: Any) -> bool:
    # bool is an int subclass.  Integral floats and numeric strings remain unknown.
    return type(value) is int and value >= 0


def _stock_state(row: dict) -> tuple[int | None, str]:
    if "stock_units" not in row:
        return None, "missing_stock_units"
    value = row["stock_units"]
    if value is None:
        return None, "null_stock_units"
    if not _stock(value):
        return None, "invalid_stock_units_type_or_range"
    return value, "known"


def reconcile(payload: dict) -> dict:
    """Return a deterministic derived view without modifying ``payload``.

    Malformed roots, missing product/list identity and resource-limit breaches
    raise ValueError.  Malformed individual SKU/property rows are retained and
    reported as incomplete, never silently coerced to zero or discarded as safe.
    """
    if type(payload) is not dict:
        raise ValueError("INVALID_INPUT: root must be an object")
    _validate_json(payload)
    if not _identifier(payload.get("product_id")):
        raise ValueError("INVALID_INPUT: product_id must be an exact nonempty string")
    variants = payload.get("sku_variants")
    if type(variants) is not list or len(variants) > MAX_VARIANTS:
        raise ValueError("INVALID_INPUT: sku_variants must be a bounded list")
    raw_dimensions = payload.get("sku_dimensions", [])
    if type(raw_dimensions) is not list or len(raw_dimensions) > MAX_DIMENSIONS:
        raise ValueError("INVALID_INPUT: sku_dimensions must be a bounded list")
    product_id = payload["product_id"]
    issues: list[dict] = []
    conflicts: list[dict] = []
    registry: dict[str, dict] = {}

    def issue(code: str, **evidence: Any) -> None:
        issues.append({"code": code, **evidence})

    def dimension(did: str) -> dict:
        return registry.setdefault(did, {"names": set(), "values": {}, "source_indexes": []})

    def value_metadata(did: str, vid: str) -> set:
        return dimension(did)["values"].setdefault(vid, set())

    # Provider dimension metadata informs exact identity resolution only; no
    # distribution cell is used to populate the calculated inventory view.
    for di, raw_dimension in enumerate(raw_dimensions):
        if type(raw_dimension) is not dict or not _identifier(raw_dimension.get("dimension_id")):
            issue("invalid_source_dimension_identity", dimension_index=di)
            continue
        did = raw_dimension["dimension_id"]
        meta = dimension(did)
        meta["source_indexes"].append(di)
        if _name(raw_dimension.get("dimension_name")):
            meta["names"].add(raw_dimension["dimension_name"])
        values = raw_dimension.get("values", [])
        if type(values) is not list or len(values) > MAX_VALUES_PER_DIMENSION:
            raise ValueError("INVALID_INPUT: dimension values must be a bounded list")
        for vi, raw_value in enumerate(values):
            if type(raw_value) is not dict or not _identifier(raw_value.get("dimension_value_id")):
                issue("invalid_source_dimension_value_identity", dimension_index=di, value_index=vi)
                continue
            labels = value_metadata(did, raw_value["dimension_value_id"])
            if _name(raw_value.get("dimension_value")):
                labels.add(raw_value["dimension_value"])

    buckets: dict[str, list[tuple[int, dict]]] = {}
    invalid_rows: list[int] = []
    for index, row in enumerate(variants):
        if type(row) is not dict or not _identifier(row.get("sku_id")):
            invalid_rows.append(index)
            issue("invalid_sku_identity", row_index=index)
            continue
        buckets.setdefault(row["sku_id"], []).append((index, row))

    skus: list[dict] = []
    sku_index: dict[str, dict] = {}
    exact_duplicate_count = 0
    conflicting_ids: list[str] = []
    for sid, entries in buckets.items():
        signatures: dict[str, list[int]] = {}
        for ri, row in entries:
            signatures.setdefault(_canonical(row), []).append(ri)
        exact_duplicate_count += sum(len(rows) - 1 for rows in signatures.values())
        conflicting = len(signatures) != 1
        row = entries[0][1]
        state: str
        normalized: int | None
        properties: list[dict] = []
        property_mapping_complete = True
        unknown_property_dimension = False
        uncertain_property_dimensions: set[str] = set()
        if conflicting:
            conflicting_ids.append(sid)
            normalized, state = None, "conflicting_duplicate_sku_rows"
            property_mapping_complete = False
            unknown_property_dimension = True
            conflicts.append({"code": state, "sku_id": sid,
                              "row_indexes": [ri for ri, _ in entries]})
        elif "product_id" in row and row["product_id"] != product_id:
            normalized, state = None, "variant_product_id_mismatch"
            property_mapping_complete = False
            unknown_property_dimension = True
            issue(state, sku_id=sid, row_index=entries[0][0],
                  root_product_id=product_id, row_product_id=copy.deepcopy(row["product_id"]))
        else:
            normalized, state = _stock_state(row)
            raw_properties = row.get("properties")
            if type(raw_properties) is not list or len(raw_properties) > MAX_PROPERTIES_PER_SKU:
                if type(raw_properties) is list:
                    raise ValueError("INVALID_INPUT: properties limit exceeded")
                property_mapping_complete = False
                unknown_property_dimension = True
                issue("missing_or_invalid_properties", sku_id=sid)
            else:
                per_dimension: dict[str, dict[str, dict]] = {}
                for pi, prop in enumerate(raw_properties):
                    if (type(prop) is not dict or not _identifier(prop.get("dimension_id"))
                            or not _identifier(prop.get("dimension_value_id"))):
                        property_mapping_complete = False
                        if type(prop) is dict and _identifier(prop.get("dimension_id")):
                            # A valid dimension ID bounds the assignment doubt to
                            # that dimension; do not discard unrelated groupings.
                            uncertain_property_dimensions.add(prop["dimension_id"])
                            dimension(prop["dimension_id"])
                        else:
                            unknown_property_dimension = True
                        issue("invalid_property_identity", sku_id=sid, property_index=pi)
                        continue
                    did, vid = prop["dimension_id"], prop["dimension_value_id"]
                    meta = dimension(did)
                    labels = value_metadata(did, vid)
                    if _name(prop.get("dimension_name")):
                        meta["names"].add(prop["dimension_name"])
                    if _name(prop.get("dimension_value")):
                        labels.add(prop["dimension_value"])
                    per_dimension.setdefault(did, {})[vid] = {"dimension_id": did, "dimension_value_id": vid}
                for did, values in per_dimension.items():
                    if len(values) != 1:
                        property_mapping_complete = False
                        uncertain_property_dimensions.add(did)
                        issue("multiple_values_for_one_sku_dimension", sku_id=sid,
                              dimension_id=did, dimension_value_ids=sorted(values))
                    else:
                        properties.append(next(iter(values.values())))
            if normalized is None:
                issue(state, sku_id=sid, row_indexes=[ri for ri, _ in entries])
        item = {"sku_id": sid,
                "source_row_indexes": [ri for ri, _ in entries],
                "source_rows": [copy.deepcopy(raw) for _, raw in entries],
                "stock_units": normalized,
                "stock_state": state,
                "properties": sorted(properties, key=lambda prop: (prop["dimension_id"], prop["dimension_value_id"])),
                "property_mapping_complete": property_mapping_complete,
                "unknown_property_dimension": unknown_property_dimension,
                "uncertain_property_dimension_ids": sorted(uncertain_property_dimensions)}
        skus.append(item)
        sku_index[sid] = item

    known_skus = [item for item in skus if item["stock_units"] is not None]
    # An empty search/report is not proof of an empty catalogue or zero stock.
    returned_complete = bool(variants) and not invalid_rows and len(known_skus) == len(skus)
    if not variants:
        issue("empty_returned_list_is_unproven_not_zero_stock")
    known_subtotal = sum(item["stock_units"] for item in known_skus)
    computable_total = known_subtotal if returned_complete else None

    # Inventory allocation is keyed by dimension_id/value_id, never label alone.
    members: dict[tuple[str, str], list[str]] = {}
    assigned_by_dimension: dict[str, set[str]] = {}
    for item in skus:
        for prop in item["properties"]:
            members.setdefault((prop["dimension_id"], prop["dimension_value_id"]), []).append(item["sku_id"])
            assigned_by_dimension.setdefault(prop["dimension_id"], set()).add(item["sku_id"])

    def stock_of_ids(ids: list[str]) -> dict:
        selected = [sku_index[sid] for sid in ids]
        known = [item for item in selected if item["stock_units"] is not None]
        subtotal = sum(item["stock_units"] for item in known)
        complete = bool(selected) and len(known) == len(selected)
        return {"sku_ids": sorted(ids), "sku_count": len(ids),
                "known_stock_sku_count": len(known),
                "unknown_stock_sku_ids": sorted(item["sku_id"] for item in selected if item["stock_units"] is None),
                "known_stock_subtotal": subtotal,
                "computable_stock_units": subtotal if complete else None,
                "complete_for_returned_group": complete,
                "provenance": "derived_from_returned_sku_reports"}

    derived_groups: dict[tuple[str, str], dict] = {}
    uncertain_by_dimension = {
        did: {item["sku_id"] for item in skus
              if item["unknown_property_dimension"] or did in item["uncertain_property_dimension_ids"]}
        for did in registry}
    for (did, vid), ids in members.items():
        assigned_to_dimension = assigned_by_dimension[did]
        unallocated_ids = sorted((set(sku_index) - assigned_to_dimension) | uncertain_by_dimension[did])
        assignment_complete = not invalid_rows and not unallocated_ids
        group_stock = stock_of_ids(ids)
        member_stock_complete = group_stock["complete_for_returned_group"]
        if not assignment_complete:
            group_stock["computable_stock_units"] = None
            group_stock["complete_for_returned_group"] = False
        derived_groups[(did, vid)] = {
            "dimension_id": did, "dimension_value_id": vid,
            "dimension_names": sorted(registry[did]["names"]),
            "dimension_value_labels": sorted(registry[did]["values"][vid]),
            **group_stock,
            "complete_for_assigned_members": member_stock_complete,
            "assignment_complete_for_returned_dimension": assignment_complete,
            "unknown_assignment_sku_ids": unallocated_ids,
            "invalid_sku_row_indexes": list(invalid_rows)}

    def unresolved(reason: str, **extra: Any) -> dict:
        return {"mapping_status": "UNRESOLVED", "reason": reason,
                "derived_stock_units": None, **extra}

    def resolve_reference(reference: Any, fixed_dimension_id: str | None = None) -> dict:
        if type(reference) is not dict or not reference:
            return unresolved("missing_or_invalid_reference")
        if "product_id" in reference and reference["product_id"] != product_id:
            return unresolved("reference_product_id_mismatch")
        if "sku_id" in reference:
            sid = reference["sku_id"]
            if not _identifier(sid):
                return unresolved("invalid_reference_sku_id")
            if sid not in sku_index:
                return unresolved("reference_sku_not_in_returned_list", sku_id=sid)
            item = sku_index[sid]
            identity_fields = ("dimension_id", "dimension_name", "dimension_value_id", "dimension_value")
            has_extra_identity = fixed_dimension_id is not None or any(key in reference for key in identity_fields)
            validated_dimension = None
            if has_extra_identity:
                for key in ("dimension_id", "dimension_value_id"):
                    if key in reference and not _identifier(reference[key]):
                        return unresolved("invalid_supplied_sku_reference_identity", sku_id=sid, field=key)
                for key in ("dimension_name", "dimension_value"):
                    if key in reference and not _name(reference[key]):
                        return unresolved("invalid_supplied_sku_reference_identity", sku_id=sid, field=key)
                did = fixed_dimension_id
                if "dimension_id" in reference:
                    if did is not None and reference["dimension_id"] != did:
                        return unresolved("sku_reference_fixed_dimension_conflict", sku_id=sid)
                    did = reference["dimension_id"]
                    if did not in registry:
                        return unresolved("unknown_supplied_sku_reference_dimension", sku_id=sid)
                if "dimension_name" in reference:
                    possible_dids = [key for key, meta in registry.items()
                                     if reference["dimension_name"] in meta["names"]]
                    if did is not None:
                        if did not in possible_dids:
                            return unresolved("sku_reference_dimension_name_conflict", sku_id=sid)
                    elif len(possible_dids) == 1:
                        did = possible_dids[0]
                    else:
                        return unresolved("ambiguous_or_unknown_sku_reference_dimension_name", sku_id=sid,
                                          matching_dimension_ids=sorted(possible_dids))
                candidates = []
                for prop in item["properties"]:
                    prop_did, prop_vid = prop["dimension_id"], prop["dimension_value_id"]
                    if did is not None and prop_did != did:
                        continue
                    if "dimension_value_id" in reference and reference["dimension_value_id"] != prop_vid:
                        continue
                    if ("dimension_value" in reference
                            and reference["dimension_value"] not in registry[prop_did]["values"][prop_vid]):
                        continue
                    candidates.append(prop)
                if len(candidates) != 1:
                    return unresolved("sku_reference_identity_conflict_or_unresolvable", sku_id=sid,
                                      matching_property_count=len(candidates))
                validated_dimension = candidates[0]
                selected_did = validated_dimension["dimension_id"]
                if (item["unknown_property_dimension"]
                        or selected_did in item["uncertain_property_dimension_ids"]):
                    return unresolved("sku_reference_property_assignment_uncertain", sku_id=sid,
                                      dimension_id=selected_did)
            selected = stock_of_ids([sid])
            return {"mapping_status": "RESOLVED", "mapping_basis": "exact_sku_id",
                    "derived_stock_units": selected["computable_stock_units"],
                    "all_supplied_identity_fields_checked": True,
                    "validated_reference_dimension": copy.deepcopy(validated_dimension),
                    "represents_unique_returned_sku": True, **selected}
        explicit_did = reference.get("dimension_id")
        dname = reference.get("dimension_name")
        if fixed_dimension_id is not None:
            if "dimension_id" in reference and explicit_did != fixed_dimension_id:
                return unresolved("reference_dimension_id_mismatch")
            did = fixed_dimension_id
        elif "dimension_id" in reference:
            if not _identifier(explicit_did) or explicit_did not in registry:
                return unresolved("unknown_or_invalid_reference_dimension_id")
            did = explicit_did
        else:
            if not _name(dname):
                return unresolved("missing_exact_dimension_name")
            possible_dids = [key for key, meta in registry.items() if dname in meta["names"]]
            if len(possible_dids) != 1:
                return unresolved("ambiguous_or_unknown_dimension_name", matching_dimension_ids=sorted(possible_dids))
            did = possible_dids[0]
        if dname is not None and (not _name(dname) or dname not in registry[did]["names"]):
            return unresolved("dimension_name_identity_conflict", dimension_id=did)
        label = reference.get("dimension_value")
        if "dimension_value_id" in reference:
            vid = reference["dimension_value_id"]
            if not _identifier(vid) or vid not in registry[did]["values"]:
                return unresolved("unknown_or_invalid_reference_value_id", dimension_id=did)
            if label is not None and (not _name(label) or label not in registry[did]["values"][vid]):
                return unresolved("value_label_identity_conflict", dimension_id=did, dimension_value_id=vid)
        else:
            if not _name(label):
                return unresolved("missing_exact_dimension_value_label", dimension_id=did)
            possible_vids = [key for key, labels in registry[did]["values"].items() if label in labels]
            if len(possible_vids) != 1:
                return unresolved("ambiguous_or_unknown_value_label", dimension_id=did,
                                  matching_dimension_value_ids=sorted(possible_vids))
            vid = possible_vids[0]
        group = derived_groups.get((did, vid))
        if group is None:
            return unresolved("value_not_allocatable_from_returned_skus", dimension_id=did, dimension_value_id=vid)
        return {**stock_of_ids(group["sku_ids"]),
                "mapping_status": "RESOLVED", "mapping_basis": "exact_dimension_and_unique_value_identity",
                "dimension_id": did, "dimension_value_id": vid,
                "derived_stock_units": group["computable_stock_units"],
                "computable_stock_units": group["computable_stock_units"],
                "complete_for_returned_group": group["complete_for_returned_group"],
                "complete_for_assigned_members": group["complete_for_assigned_members"],
                "assignment_complete_for_returned_dimension": group["assignment_complete_for_returned_dimension"],
                "unknown_assignment_sku_ids": group["unknown_assignment_sku_ids"],
                "represents_unique_returned_sku": group["sku_count"] == 1,
                "invalid_sku_row_indexes": list(invalid_rows)}

    def compare_stock(raw: dict, mapping: dict, location: str) -> dict:
        present = "stock_units" in raw
        source = raw.get("stock_units")
        derived = mapping.get("derived_stock_units")
        if mapping["mapping_status"] != "RESOLVED":
            comparison = "UNRESOLVED_MAPPING"
        elif derived is None:
            comparison = "INCOMPLETE_RETURNED_GROUP"
        elif not present or not _stock(source):
            comparison = "UNKNOWN_SOURCE_STOCK"
        elif source == derived:
            comparison = "MATCH"
        else:
            comparison = "CONFLICT"
            conflicts.append({"code": "source_stock_differs_from_returned_sku_group",
                              "location": location, "source_stock_units": source,
                              "derived_stock_units": derived, "sku_ids": mapping["sku_ids"],
                              "dimension_id": mapping.get("dimension_id"),
                              "dimension_value_id": mapping.get("dimension_value_id"),
                              "source_was_zero": source == 0})
        return {"source_stock_present": present, "source_stock_units": copy.deepcopy(source),
                "stock_comparison": comparison}

    output_dimensions: list[dict] = []
    breakdown_mappings: list[tuple[str, str, dict, dict]] = []
    for did, meta in sorted(registry.items()):
        groups = [group for (group_did, _), group in sorted(derived_groups.items()) if group_did == did]
        allocated_ids = {sid for group in groups for sid in group["sku_ids"]}
        unallocated = sorted(set(sku_index) - allocated_ids)
        uncertain_assignments = sorted(uncertain_by_dimension[did])
        allocation_complete = returned_complete and not unallocated and not uncertain_assignments
        allocated_subtotal = sum(group["known_stock_subtotal"] for group in groups)
        source_summaries: list[dict] = []
        for di in meta["source_indexes"]:
            raw_dimension = raw_dimensions[di]
            summary = raw_dimension.get("distribution_summary")
            if type(summary) is not dict:
                issue("missing_or_invalid_distribution_summary", dimension_index=di)
                source_summaries.append({"source_dimension_index": di, "raw": copy.deepcopy(summary),
                                         "total_comparison": "UNKNOWN_SOURCE_TOTAL", "breakdown_comparisons": []})
                continue
            source_total = summary.get("total_stock_units")
            if not _stock(source_total):
                total_comparison = "UNKNOWN_SOURCE_TOTAL"
            elif not allocation_complete:
                total_comparison = "INCOMPLETE_RETURNED_DIMENSION"
            elif source_total == computable_total:
                total_comparison = "MATCH_RETURNED_LIST_TOTAL"
            else:
                total_comparison = "DIFFERS_FROM_RETURNED_LIST_TOTAL"
                conflicts.append({"code": "source_total_differs_from_returned_list_total",
                                  "dimension_id": did, "source_dimension_index": di,
                                  "source_total_stock_units": source_total,
                                  "derived_returned_list_total": computable_total,
                                  "note": "A difference can also mean the provider returned an incomplete catalogue."})
            raw_breakdown = summary.get("breakdown")
            comparisons: list[dict] = []
            if type(raw_breakdown) is not list:
                issue("missing_or_invalid_source_breakdown", dimension_index=di)
            else:
                if len(raw_breakdown) > MAX_VALUES_PER_DIMENSION:
                    raise ValueError("INVALID_INPUT: breakdown row limit exceeded")
                for bi, raw in enumerate(raw_breakdown):
                    if type(raw) is not dict:
                        comparisons.append({"source_breakdown_index": bi,
                                            **unresolved("invalid_source_breakdown_row")})
                        continue
                    # 'Other' in a Top-N distribution is not a SKU identity.
                    if raw.get("dimension_value") == "Other" and "dimension_value_id" not in raw and "sku_id" not in raw:
                        mapping = unresolved("source_other_residual_not_a_returned_sku_group")
                    else:
                        mapping = resolve_reference(raw, fixed_dimension_id=did)
                    result = {"source_breakdown_index": bi, **mapping,
                              **compare_stock(raw, mapping, f"sku_dimensions[{di}].distribution_summary.breakdown[{bi}]")}
                    comparisons.append(result)
                    if mapping["mapping_status"] == "RESOLVED":
                        breakdown_mappings.append((did, mapping.get("dimension_value_id", ""), raw, result))
            source_summaries.append({"source_dimension_index": di, "raw": copy.deepcopy(summary),
                                     "total_comparison": total_comparison,
                                     "breakdown_comparisons": comparisons})
        output_dimensions.append({"dimension_id": did, "dimension_names": sorted(meta["names"]),
                                  "groups": groups, "grouping": "full_returned_stock_groups_without_other",
                                  "known_allocated_stock_subtotal": allocated_subtotal,
                                  "computable_returned_dimension_total": computable_total if allocation_complete else None,
                                  "complete_for_returned_rows": allocation_complete,
                                  "unallocated_sku_ids": unallocated,
                                  "uncertain_assignment_sku_ids": uncertain_assignments,
                                  "invalid_sku_row_indexes": list(invalid_rows),
                                  "source_summaries": source_summaries,
                                  "do_not_add_totals_across_dimensions": True})

    raw_top = payload.get("top_sku_by_units_sold")
    top_mapping = resolve_reference(raw_top)
    source_top = {"raw": copy.deepcopy(raw_top), **top_mapping,
                  "sales_ranking_recomputed": False,
                  "interpretation": "Source-selected sales Top reference; calculated stock may represent multiple returned SKUs, not an independently verified sales ranking."}
    if type(raw_top) is dict:
        source_top.update(compare_stock(raw_top, top_mapping, "top_sku_by_units_sold"))
    if top_mapping["mapping_status"] == "RESOLVED" and type(raw_top) is dict:
        for did, vid, raw, comparison in breakdown_mappings:
            # Compare only the same exact resolved group, never merely a name.
            if (top_mapping.get("dimension_id"), top_mapping.get("dimension_value_id")) != (did, vid):
                continue
            for metric in ("gmv", "units_sold"):
                top_number, breakdown_number = raw_top.get(metric), raw.get(metric)
                if (type(top_number) in (int, float) and type(breakdown_number) in (int, float)
                        and top_number != breakdown_number):
                    conflicts.append({"code": "source_cross_view_metric_mismatch",
                                      "metric": metric, "dimension_id": did, "dimension_value_id": vid,
                                      "source_top_value": top_number, "source_breakdown_value": breakdown_number,
                                      "source_breakdown_index": comparison["source_breakdown_index"],
                                      "derived_metric_value": None})

    return {"schema_version": SCHEMA_VERSION,
            "status": "RECONCILED_OFFLINE" if returned_complete else "INCOMPLETE_OFFLINE",
            "product_id": product_id,
            "provenance": "derived_from_returned_sku_reports",
            "source": {"provider_payload": copy.deepcopy(payload),
                       "canonical_payload_sha256": hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
                       "provider_values_modified": False},
            "returned_list": {"input_row_count": len(variants),
                              "unique_valid_sku_count": len(skus),
                              "known_stock_sku_count": len(known_skus),
                              "unknown_stock_sku_count": len(skus) - len(known_skus),
                              "invalid_sku_row_indexes": invalid_rows,
                              "exact_duplicate_row_count": exact_duplicate_count,
                              "conflicting_duplicate_sku_ids": sorted(conflicting_ids),
                              "known_stock_subtotal": known_subtotal,
                              "computable_total_stock_units": computable_total,
                              "complete_for_returned_rows": returned_complete,
                              "complete_catalogue_verified": False,
                              "scope": "Only the returned product-specific unique SKU rows; not all product/warehouse availability."},
            "skus": skus,
            "dimensions": output_dimensions,
            "source_top": source_top,
            "conflicts": conflicts,
            "issues": issues,
            "interpretation_limits": [
                "Never fill mixed-metric source breakdown cells or splice derived groups into source Other.",
                "Known subtotal is not a complete total when any returned identity or stock is unknown/conflicting.",
                "Dimensions are alternate allocations of the same SKUs; do not sum their totals.",
                "No missing price, units_sold, GMV, currency or statistical window is derived from stock.",
                "Sparse Top-N assembly is an inference only; the provider implementation is unverified.",
                "Stock-unit semantics, caps, complete catalogue and real warehouse availability remain unverified."],
            "operational_use": {"offline_display_derived_stock_allowed": True,
                                "warehouse_stock_verified": False,
                                "procurement_authorized": False,
                                "replenishment_authorized": False,
                                "price_or_commerce_release_authorized": False}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Raw single-product SKU JSON payload (not a tool envelope)")
    parser.add_argument("--output", required=True, type=Path, help="New output file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        with args.input.open("rb") as source:
            raw = source.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("INVALID_INPUT: input file size limit exceeded")
        payload = json.loads(raw.decode("utf-8-sig"))
        result = reconcile(payload)
        result["file_provenance"] = {"input_sha256": hashlib.sha256(raw).hexdigest(),
                                     "input_bytes": len(raw)}
        encoded = (json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        # Exclusive creation is atomic; a concurrent/previous result is preserved.
        with args.output.open("xb") as destination:
            destination.write(encoded)
    except (ValueError, OSError, RecursionError) as exc:
        print(json.dumps({"status": "ERROR", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status": "WRITTEN", "output": str(args.output),
                      "output_bytes": len(encoded), "output_sha256": hashlib.sha256(encoded).hexdigest(),
                      "input_sha256": hashlib.sha256(raw).hexdigest()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
