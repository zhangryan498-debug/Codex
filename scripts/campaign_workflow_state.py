"""Compare offline workflow outputs against independently verified ledger facts.

This is an output-equivalence check, not a price calculator or a live approval.
Missing source facts are never replaced by zero. Only redundant zero outputs in
two explicitly reconciled replay shapes may be omitted. Unknown extensions fail.
"""
from copy import deepcopy

POLICY_VERSION = "workflow-replay-equivalence-v1"


def strict_equal(left, right):
    """JSON equality with exact scalar types (False is not integer zero)."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            strict_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _integer(value):
    return type(value) is int and value >= 0


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _unique(rows, keys):
    if not isinstance(rows, list) or not rows:
        return None
    result = {}
    for row in rows:
        if not isinstance(row, dict) or any(not _nonempty(row.get(k)) for k in keys):
            return None
        identity = tuple(row[k] for k in keys)
        if identity in result and not strict_equal(result[identity], row):
            return None
        result[identity] = row
    return list(result.values())


def _matches(expected, values):
    return all(key in expected and strict_equal(expected[key], value)
               for key, value in values.items())


def _reconciled_replay(expected, workflow):
    """Derive narrow equivalences from explicit same-key committed ledgers."""
    if not isinstance(workflow, dict):
        return set(), None
    shapes = [key for key in ("existing_ledger", "existing_fee_ledger",
                              "existing_processed_refund_ids") if key in workflow]
    reserve = workflow.get("current_reserved_minor", 0)
    if len(shapes) != 1 or not _integer(reserve) or reserve != 0:
        return set(), None
    if any(k in workflow and workflow[k] != "committed" for k in ("status", "terminal_status")):
        return set(), None
    if any(k in expected and expected[k] != "committed" for k in ("status", "terminal_status")):
        return set(), None
    common = {"new_commit_count": 0, "new_charge_count": 0, "new_voucher_usage": 0}
    if shapes[0] == "existing_ledger":
        rows = _unique(workflow["existing_ledger"], ("event_id",))
        retries = workflow.get("incoming_retries")
        request, idem = workflow.get("request_id"), workflow.get("idempotency_key")
        initial = workflow.get("budget_initial_minor")
        if (not rows or not isinstance(retries, list) or not retries
                or not _nonempty(request) or not _nonempty(idem) or not _integer(initial)):
            return set(), None
        if any(row.get("status") != "committed"
               or row.get("request_id") != request or row.get("idempotency_key") != idem
               or not _integer(row.get("seller_promo_debit_minor"))
               or not _integer(row.get("voucher_usage_increment")) for row in rows):
            return set(), None
        if any(not isinstance(row, dict) or not _nonempty(row.get("event_id"))
               or row.get("request_id") != request or row.get("idempotency_key") != idem
               for row in retries):
            return set(), None
        debit = sum(row["seller_promo_debit_minor"] for row in rows)
        values = dict(common, committed_minor=debit,
                      voucher_usage=sum(row["voucher_usage_increment"] for row in rows),
                      budget_remaining_minor=initial-debit, reserved_minor=0,
                      unique_commit_count=len(rows), replayed_requests=len(retries))
        if initial < debit or not _matches(expected, values):
            return set(), None
        return {"reserved_minor"}, "committed"
    if shapes[0] == "existing_fee_ledger":
        rows = _unique(workflow["existing_fee_ledger"], ("event_id", "fee_type"))
        callbacks = workflow.get("incoming_callbacks")
        event = workflow.get("success_event_id")
        initial = workflow.get("fee_budget_initial_minor")
        if (not rows or not isinstance(callbacks, list) or not callbacks
                or not _nonempty(event) or not _integer(initial)
                or workflow.get("fee_budget_scope") != "basic_mdp_only"):
            return set(), None
        if any(row.get("event_id") != event or row.get("fee_type") != "basic_mdp"
               or row.get("status") != "committed" or not _integer(row.get("debit_minor"))
               for row in rows):
            return set(), None
        if any(not isinstance(row, dict) or row.get("event_id") != event for row in callbacks):
            return set(), None
        debit = sum(row["debit_minor"] for row in rows)
        values = dict(common, mdp_fee_debit_minor=debit,
                      fee_budget_remaining_minor=initial-debit,
                      unique_fee_count=len(rows), ignored_duplicate_callbacks=len(callbacks))
        if initial < debit or not _matches(expected, values):
            return set(), None
        return {"new_commit_count", "new_voucher_usage"}, "committed"
    return set(), None


def compare_workflow_state(expected, observed, workflow=None):
    """Return diagnostics and an explicit canonical output only on equivalence.

    ``expected`` must be a trusted independently calculated output, and workflow
    must be the immutable original input. Callers retain and hash raw output.
    This function never mutates either input and never approves a price.
    """
    errors, omitted, auxiliary = [], [], []
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        errors.append("state.object_required")
    else:
        optional, terminal = _reconciled_replay(expected, workflow)
        for key, value in expected.items():
            if key not in observed:
                if key in optional and type(value) is int and value == 0:
                    omitted.append(key)
                else:
                    errors.append("state." + str(key) + ".missing")
            elif not strict_equal(value, observed[key]):
                errors.append("state." + str(key) + ".mismatch")
        for key, value in observed.items():
            if key in expected:
                continue
            if key in ("status", "terminal_status") and terminal is not None and strict_equal(value, terminal):
                auxiliary.append(key)
            else:
                errors.append("state." + str(key) + ".unverified_extra")
    return {"policy_version": POLICY_VERSION, "equivalent": not errors,
            "errors": errors, "omitted_proven_zero_fields": sorted(omitted),
            "checked_auxiliary_fields": sorted(auxiliary),
            "canonical_state": deepcopy(expected) if not errors else None}
