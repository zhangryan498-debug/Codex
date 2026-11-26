"""Finite synthetic session replay. No connector, network, model or business I/O.

Content semantics are frozen supplied role answers; original modules validate
their quotes/rules and notice/remediation state. Price proof is revalidated from
the actual original caller and completed host, never from an input safe flag.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "scripts"))
from live_price_evidence_v2 import PriceVerifier
from live_window_review_v6 import ledger, MONEY
from live_compliance_review_v2 import review as compliance_review
from ph_live_cpl_v1 import assess as assess_cpl

VERSION = "live-session-review-v4"
SCOPE = {"scope": "synthetic_offline", "release_status": "not_authorized", "execution_authorized": False}
ALIASES = ("A", "B")
FINANCIAL_TYPES = {"order", "cash_receipt", "costs", "fulfill", "cancel", "refund", "return_received", "settled", "inventory_snapshot"}
CONTENT_TYPES = {"script", "observation"}
COST_FIELDS = ("fees_minor", "costs_minor", "ad_spend_minor")
RECAP_AMOUNTS = ("placed_gmv_minor", "confirmed_cash_minor", "refunds_minor", "pending_order_value_minor", "known_costs_subtotal_minor", "total_costs_minor", "settled_net_profit_minor")


def canonical(x):
    return json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(x):
    return hashlib.sha256(canonical(x).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text(x):
    return isinstance(x, str) and bool(x.strip())


def uint(x):
    return type(x) is int and x >= 0


def positive(x):
    return type(x) is int and x > 0


def instant(x):
    if not isinstance(x, str):
        raise ValueError("offset timestamp required")
    d = datetime.fromisoformat(x)
    if d.utcoffset() is None:
        raise ValueError("offset timestamp required")
    return d


def dependencies():
    paths = [Path(__file__)] + [PROJECT / "scripts" / name for name in (
        "live_price_evidence_v2.py", "campaign_price_intake.py", "campaign_price_handoff.py",
        "live_window_review_v6.py", "ad_decision_contract.py", "live_compliance_review_v1.py",
        "live_compliance_review_v2.py", "ph_live_cpl_v1.py")]
    return {str(path): sha(path) for path in paths}


def segment_map(value):
    if not isinstance(value, list):
        raise ValueError("accumulated_segments must be the frozen list of segment answers")
    by = {}
    for item in value:
        if not isinstance(item, dict) or not text(item.get("event_id")):
            raise ValueError("segment identity missing")
        key = item["event_id"]
        if key in by and digest(by[key]) != digest(item):
            raise ValueError("frozen semantic answer changed: " + key)
        by[key] = deepcopy(item)
    return by


def empty_recap():
    return {**{key: 0 for key in RECAP_AMOUNTS}, "settled_net_profit_minor": None, "performance_state": "not_observed"}


def project_uncertain_financial_evidence(checkpoint, uncertain):
    """Label retained candidate branches without altering any operation decision.

    This is called only AFTER the ordinary replay has computed its checkpoint.
    The predicate is the exact existing top-level canonical-null predicate.
    No-order, partial costs, known oversell, prices and ordinary restrictions do
    not on their own activate this projection.
    """
    if not uncertain:
        return checkpoint
    original = {key: deepcopy(checkpoint[key]) for key in (
        "order_book", "per_sku_financial_evidence", "inventory_components", "financial_integrity")}
    nested = []

    def unproven_finance(value):
        # Preserve source values solely in the separately excluded candidate
        # object. The current canonical status does not assert a zero amount,
        # an empty book, settled performance, or a proved absence of risks.
        return {"proven": False, "reason": "canonical_data_uncertainty",
                "scope": "canonical_unproven", "observed_performance": "partial",
                "unknown_cost_order_ids": None, "oversold_skus": None,
                "candidate_branch_retained": True}

    def scrub(node, path=()):
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key in ("candidate_branch_evidence", "known_validated_prefix", "segments"):
                    continue
                target = path + (key,)
                if key in ("financial_evidence", "financial_integrity") and isinstance(value, dict):
                    nested.append({"path": list(target), "original_value": deepcopy(value)})
                    node[key] = unproven_finance(value)
                elif key == "financial_proven":
                    nested.append({"path": list(target), "original_value": deepcopy(value)})
                    node[key] = False
                    node["financial_evidence_scope"] = "current_checkpoint_canonical_unproven"
                else:
                    scrub(value, target)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                scrub(value, path + (index,))

    scrub(checkpoint)
    checkpoint["order_book"] = None
    checkpoint["order_book_status"] = {"scope": "canonical_unproven", "proven": False,
                                       "performance_state": "partial", "order_count": None,
                                       "candidate_branch_retained": True}
    checkpoint["per_sku_financial_evidence"] = {
        sku: {"scope": "canonical_unproven", "proven": False,
              "settled_order_ids": None, "pending_order_ids": None,
              "confirmed_cash_minor": None, "pending_order_value_minor": None,
              "settled_ledger": None, "performance_state": "partial",
              "candidate_branch_retained": True}
        for sku in ALIASES
    }
    checkpoint["inventory_components"] = {
        sku: {"scope": "canonical_unproven", "proven": False,
              "on_hand": None, "reserved": None, "safety_stock": None,
              "candidate_branch_retained": True}
        for sku in ALIASES
    }
    checkpoint["canonical_evidence_status"] = {
        "proven": False, "scope": "canonical_unproven", "performance_state": "partial",
        "predicate": "cursor != required_cursor or bool(financial_errors)",
        "journal_prefix_complete": checkpoint["cursor"] == checkpoint["required_cursor"],
        "financial_error_count": len(checkpoint["financial_errors"]),
        "known_prefix_evidence_is_separate": True,
    }
    checkpoint["candidate_branch_evidence"] = {
        "scope": "noncanonical_candidate", "excluded_from_canonical": True, "proven": False,
        "reason": "Previously applied branch is retained for audit, not a current canonical order book or settled ledger.",
        "candidate_semantics": "known_prefix_only_not_whole_session" if not checkpoint["financial_errors"] else "unresolved_candidate_branch",
        "applied_cursor": checkpoint["cursor"],
        "known_validated_prefix_cursor": checkpoint["known_validated_prefix"]["through_seq"],
        **original, "nested_financial_contexts": nested,
    }
    return checkpoint


def order_summary(orders):
    result = empty_recap()
    if not orders:
        return result, {"A": None, "B": None}
    values = list(orders.values())
    result.update(
        placed_gmv_minor=sum(o["buyer_goods_minor"] for o in values),
        confirmed_cash_minor=sum(o["receipts"] for o in values),
        refunds_minor=sum(o["refunds"] for o in values),
        pending_order_value_minor=sum(max(o["buyer_goods_minor"] - o["receipts"], 0) for o in values if o["cancelled"] != o["quantity"]),
        known_costs_subtotal_minor=sum(o[key] for o in values for key in COST_FIELDS if o[key] is not None),
        performance_state="partial",
    )
    complete = all(o["cost_complete"] and all(o[k] is not None for k in COST_FIELDS) for o in values)
    result["total_costs_minor"] = result["known_costs_subtotal_minor"] if complete else None
    by_sku = {}
    for sku in ALIASES:
        scoped = [o for o in values if o["sku"] == sku]
        mature = [o for o in scoped if o["settled"] and o["cost_complete"]]
        components = {k: 0 for k in MONEY}
        for o in mature:
            for key, field in (("buyer_paid_minor", "receipts"), ("refunds_minor", "refunds"),
                               ("fee_credits_minor", "fee_credits"), ("recoveries_minor", "recoveries")):
                components[key] += o[field]
            for key in COST_FIELDS:
                components[key] += o[key]
        by_sku[sku] = {
            "settled_order_ids": [o["order_id"] for o in mature],
            "pending_order_ids": [o["order_id"] for o in scoped if not o["settled"]],
            "confirmed_cash_minor": sum(o["receipts"] for o in scoped),
            "pending_order_value_minor": sum(max(o["buyer_goods_minor"] - o["receipts"], 0) for o in scoped if o["cancelled"] != o["quantity"]),
            "settled_ledger": ledger(components) if mature else None,
            "performance_state": "not_observed" if not scoped else "settled" if len(mature) == len(scoped) else "partial",
        }
    if complete and all(o["settled"] for o in values):
        components = {k: 0 for k in MONEY}
        for o in values:
            components["buyer_paid_minor"] += o["receipts"]
            components["refunds_minor"] += o["refunds"]
            components["fee_credits_minor"] += o["fee_credits"]
            components["recoveries_minor"] += o["recoveries"]
            for key in COST_FIELDS:
                components[key] += o[key]
        result["settled_net_profit_minor"] = ledger(components)["post_ad_profit_minor"]
        result["performance_state"] = "settled"
    return result, by_sku


class Replay:
    def __init__(self, case, segments, catalog, pack):
        self.case, self.segments, self.catalog, self.pack = deepcopy(case), segment_map(segments), deepcopy(catalog), deepcopy(pack)
        self.market = (case.get("platform"), case.get("country"), case.get("currency"))
        if self.market not in (("douyin", "CN", "CNY"), ("tiktok", "PH", "PHP")):
            raise ValueError("unsupported exact market")
        if not text(case.get("case_id")) or not text(case.get("session_id")):
            raise ValueError("case/session identity required")
        if not isinstance(case.get("steps"), list) or not case["steps"]:
            raise ValueError("nonempty progressive steps required")
        self.price_verifier = PriceVerifier(catalog)
        self.errors, self.financial_errors, self.conflicts, self.journal, self.ids = [], [], {}, {}, {}
        self.stock, self.quotes, self.orders, self.scripts = {}, deepcopy(case.get("initial_prices", {})), {}, {}
        self.phase, self.active, self.lineup = "planned", "A", []
        self.cursor, self.required, self.duplicates, self.transport_proven = 0, 0, 0, True
        self.history, self.history_segments, self.bridge_trace = [], {}, []
        self.snapshots, self.checkpoints, self.seen_requests, self.seen_checkpoints = {}, [], set(), set()
        self.latest_preview, self.segment_seen = {}, set()
        self.terminal, self.baseline = False, {"sku": "A", "state": "not_observed", "reason": "initial_empty_order_book"}
        facts = case.get("product_facts")
        if not isinstance(facts, dict) or set(facts) != set(ALIASES) or any(not facts[s] for s in ALIASES):
            self.error("product_facts_missing")
        initial = case.get("initial_inventory")
        for sku in ALIASES:
            row = initial.get(sku) if isinstance(initial, dict) else None
            if not isinstance(row, dict) or any(not uint(row.get(k)) for k in ("on_hand", "reserved", "safety_stock")) or row.get("reserved") != 0 or row.get("on_hand", -1) < row.get("reserved", 0):
                self.stock[sku] = None
                self.error("initial_inventory_invalid:" + sku, financial=True)
            else:
                self.stock[sku] = deepcopy(row)
            if not isinstance(self.quotes.get(sku), dict):
                self.quotes[sku] = {}
        self.snapshots[0] = self.financial_snapshot()

    def error(self, reason, financial=False, seq=None):
        if reason not in self.errors:
            self.errors.append(reason)
        if financial:
            self.financial_errors.append({"reason": reason, "seq": seq or 0})

    def financial_snapshot(self):
        return {"orders": deepcopy(self.orders), "stock": deepcopy(self.stock)}

    def price(self, sku):
        quote = self.quotes.get(sku, {})
        entry = self.catalog.get(quote.get("price_ref"))
        if sku not in ALIASES or not isinstance(entry, dict):
            return {"proven": False, "reason": "price_reference_missing"}
        expected = ("CN-LS-" if self.market[0] == "douyin" else "PH-LS-") + sku
        try:
            if entry.get("sku") != expected or type(entry.get("quantity")) is not int or entry["quantity"] != 1:
                return {"proven": False, "reason": "price_catalog_concrete_sku_or_quantity_mismatch"}
            call = {"platform": self.market[0], "country": self.market[1], "currency": self.market[2],
                    "target_sku": expected, "quantity": 1, "price_revision": quote.get("revision"),
                    "price_ref": quote.get("price_ref"), "evaluation_time": self.step["as_of"]}
            proof = self.price_verifier(call)
            if not proof.get("proven"):
                return proof
            # PriceVerifier validates caller SHA/host/packet identity. Bind the
            # displayed buyer amount to that same original trusted caller.
            source = json.loads(Path(entry["input_file"]).read_text(encoding="utf-8-sig"))
            rows = [r for r in source["cases"] if r["packet"]["packet_id"] == entry["packet_id"]]
            buyer = rows[0]["source_input"].get("buyer_goods_minor") if len(rows) == 1 else None
            if not uint(buyer) or type(quote.get("advertised_buyer_minor")) is not int or quote["advertised_buyer_minor"] != buyer or entry.get("buyer_goods_minor") != buyer:
                return {**proof, "proven": False, "reason": "advertised_buyer_amount_not_original_caller"}
            return {**proof, "buyer_goods_minor": buyer, "logical_sku": sku, "source_catalog_entry_sha256": digest(entry)}
        except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
            return {"proven": False, "reason": "original_price_proof_error:" + type(exc).__name__}

    def finance_proof(self, sku):
        unknown = [o["order_id"] for o in self.orders.values() if not o["cost_complete"] or any(o[k] is None for k in COST_FIELDS)]
        oversold = [s for s, row in self.stock.items() if row is not None and row["on_hand"] - row["reserved"] - row["safety_stock"] < 0]
        return {"proven": not self.errors and not unknown and not oversold,
                "reason": "synthetic_cost_and_inventory_integrity" if not self.errors and not unknown and not oversold else "unknown_costs_or_integrity_or_oversell",
                "unknown_cost_order_ids": unknown, "oversold_skus": oversold,
                "observed_performance": "not_observed" if not self.orders else "settled" if all(o["settled"] for o in self.orders.values()) else "partial"}

    def add_history(self, raw, event_type=None, key=None, history=None, hsegments=None, sku=None):
        history = self.history if history is None else history
        hsegments = self.history_segments if hsegments is None else hsegments
        event = deepcopy(raw)
        source_id = event["event_id"]
        event["event_id"] = key or source_id
        event["type"] = event_type or event["type"]
        event["at_second"], event["sequence"] = len(history) + 1, len(history) + 1
        event.pop("seq", None)
        history.append(event)
        if event["type"] == "content":
            segment = deepcopy(self.segments.get(source_id, {}))
            segment["event_id"] = event["event_id"]
            claim_review = self.price_claim_review(raw)
            segment["system_price_claim_review"] = claim_review
            segment["original_model_classification"] = segment.get("classification")
            segment["original_model_segment_sha256"] = digest(self.segments.get(source_id, {}))
            if not claim_review["proven"] and segment.get("classification") not in ("prohibited", "risk_review", "evidence_missing"):
                # Derived SYSTEM evidence gap, never a rewrite of the frozen
                # original role answer or a claim that the model was correct.
                segment.update(classification="evidence_missing", possible_enforcement=[], enforcement_certain=False,
                               reason="System-bound on-air price/speech evidence gap: " + ";".join(claim_review["errors"]))
            hsegments[event["event_id"]] = segment
        return event

    def price_claim_review(self, event):
        original = self.segments.get(event.get("event_id"), {})
        errors, reviewed = [], []
        if original.get("price_claims_complete") is not True:
            errors.append("price_claim_extraction_not_explicitly_complete")
        claims = original.get("price_claims")
        if not isinstance(claims, list):
            claims = []
            errors.append("price_claim_list_missing_or_invalid")
        channels = event.get("channels")
        channels = channels if isinstance(channels, dict) else {}
        proof = None
        for index, claim in enumerate(claims):
            why = []
            comparison_status = "not_a_current_quote"
            if not isinstance(claim, dict) or set(claim) != {"channel", "quote", "buyer_goods_minor", "kind"}:
                why.append("price_claim_schema_invalid")
            else:
                channel = claim.get("channel")
                source = channels.get(channel, {}) if isinstance(channel, str) else {}
                quoted = claim.get("quote")
                if channel not in ("speech", "captions", "visual") or not isinstance(source, dict) or not text(quoted) or not isinstance(source.get("text"), str) or quoted not in source["text"]:
                    why.append("price_claim_quote_not_original_channel")
                amount = claim.get("buyer_goods_minor")
                if amount is not None and not uint(amount):
                    why.append("price_claim_amount_not_minor_integer_or_null")
                kind = claim.get("kind")
                if kind not in ("current", "historical", "negated", "uncertain"):
                    why.append("price_claim_kind_invalid")
                elif kind == "uncertain":
                    why.append("price_claim_meaning_uncertain")
                elif kind == "current":
                    if not uint(amount):
                        why.append("current_price_claim_amount_unknown")
                        comparison_status = "claim_amount_unknown"
                    proof = self.price(event.get("sku")) if proof is None else proof
                    if proof.get("proven") is not True:
                        why.append("current_price_evidence_unproven")
                        comparison_status = "price_evidence_unproven_no_proved_numeric_comparison"
                    elif uint(amount) and amount != proof.get("buyer_goods_minor"):
                        why.append("current_price_claim_differs_from_bound_current_quote")
                        comparison_status = "differs_from_proven_current_quote"
                    elif uint(amount):
                        comparison_status = "matches_proven_current_quote"
            reviewed.append({"claim_index": index, "claim": deepcopy(claim), "errors": why,
                             "comparison_status": comparison_status})
            errors.extend(f"claim[{index}]:" + error for error in why)
        return {"proven": not errors, "errors": errors, "claims": reviewed,
                "current_price_evidence": deepcopy(proof), "source_event_id": event.get("event_id"),
                "source_event_sha256": digest(event), "original_segment_sha256": digest(original),
                "original_model_classification": original.get("classification"),
                "scope": "bounded_synthetic_price_assertion_protocol_not_complete_natural_language_detection"}

    def compliance(self, history=None, hsegments=None):
        history = self.history if history is None else history
        hsegments = self.history_segments if hsegments is None else hsegments
        adapted = {"case_id": self.case["case_id"], "session_id": self.case["session_id"],
                   "platform": self.market[0], "country": self.market[1], "currency": self.market[2],
                   "initial_sku": "A", "product_facts": self.case.get("product_facts", {}), "events": history}
        model = {"case_id": adapted["case_id"], **SCOPE, "explanation": "Trusted replay adapter; semantic segment bytes remain separately hash-bound.",
                 "segments": list(hsegments.values()), "final_recommendation": "eligible_offline"}
        result = compliance_review(adapted, model, self.pack, lambda c, s: self.price(s), lambda c, s: self.finance_proof(s))
        result["adapter_notice"] = "Final recommendation in adapter is not a model global prediction; use system state and segment validation, not adapter model agreement."
        return result

    def preview(self, event):
        sku = event.get("sku")
        if sku not in ALIASES:
            return {"safe": False, "reason": "preview_sku_missing"}
        # Original compliance validator's prebound initial alias is A. A preview
        # is isolated, with an explicit identity-only alias map; no price,
        # financial values or on-air history are inherited or cleared.
        raw = deepcopy(event)
        for field in ("sku", "shown_sku", "linked_sku"):
            if raw.get(field) == sku:
                raw[field] = "A"
            elif text(raw.get(field)):
                raw[field] = "foreign-original-alias:" + raw[field]
        raw.update(type="content", at_second=1, sequence=1)
        adapted = {"case_id": self.case["case_id"] + ":preview:" + event["event_id"], "session_id": self.case["session_id"],
                   "platform": self.market[0], "country": self.market[1], "currency": self.market[2], "initial_sku": "A",
                   "product_facts": {"A": deepcopy(self.case.get("product_facts", {}).get(sku))}, "events": [raw]}
        model = {"case_id": adapted["case_id"], **SCOPE, "explanation": "Isolated preview semantic validation.",
                 "segments": [deepcopy(self.segments.get(event["event_id"], {}))], "final_recommendation": "eligible_offline"}
        result = compliance_review(adapted, model, self.pack, lambda c, s: {"proven": True, "reason": "preview_semantics_only_price_checked_separately"}, lambda c, s: {"proven": True, "reason": "preview_semantics_only_finance_checked_separately"})
        price_speech = self.price_claim_review(event)
        safe = result.get("final_recommendation") == "eligible_offline" and result.get("model_evidence_binding_valid") is True and price_speech["proven"]
        return {"safe": safe, "source_sku": sku, "alias_map": {sku: "A"}, "source_event_sha256": digest(event), "source_segment_sha256": digest(self.segments.get(event["event_id"])), "price_speech_evidence": price_speech, "system_result": result}

    def cpl(self):
        if self.market[0] != "tiktok":
            return {"live_effect": "none", "applied": False}
        context = self.step.get("cpl_context")
        if not isinstance(context, dict):
            return {"live_effect": "hold_evidence", "data_errors": ["current_PH_CPL_context_missing"]}
        try:
            fresh = instant(context.get("evaluation_time")) == instant(self.step["as_of"])
        except (ValueError, TypeError):
            fresh = False
        if not fresh:
            return {"live_effect": "hold_evidence", "data_errors": ["current_PH_CPL_clock_mismatch"]}
        return assess_cpl({"platform": "tiktok", "country": "PH", "currency": "PHP", "cpl_context": context}, self.pack)

    def general_reasons(self, include_content=True):
        reasons = list(self.errors)
        if self.cursor != self.required:
            reasons.append("journal_prefix_incomplete")
        if self.step.get("connection") not in ("connected", "reconnected") or not self.transport_proven:
            reasons.append("transport_not_resynchronized")
        financial = self.finance_proof(self.active)
        if not financial["proven"]:
            reasons.append(financial["reason"])
        if self.market[0] == "tiktok":
            controls = self.step.get("ph_controls")
            if not isinstance(controls, dict) or controls.get("site") != "PH" or controls.get("fulfillment_state") != "safe" or controls.get("cashflow_state") != "safe":
                reasons.append("PH_fulfillment_or_cashflow_unknown_or_unsafe")
            if self.cpl().get("live_effect") != "none":
                reasons.append("PH_LIVE_rights_not_proven")
        if include_content and self.history:
            prior = self.compliance()
            # An eligible temporary candidate may provide the fresh clean scene
            # after a valid lift. It cannot erase any previous unresolved issue,
            # malformed semantic finding, or active notice. Old SKU price is
            # independently replaced by the target's exact price on a switch.
            if prior.get("unresolved_issues") or prior.get("active_notices") or prior.get("data_errors") or prior.get("terminal_session"):
                reasons.append("existing_content_or_notice:" + prior.get("final_recommendation", "hold_evidence"))
        if self.terminal:
            reasons.append("terminated_session_never_reopens")
        return sorted(set(reasons))

    def sku_reasons(self, sku, script):
        reasons = []
        if sku not in ALIASES or not self.case.get("product_facts", {}).get(sku):
            return ["target_product_identity_unknown"]
        row = self.stock.get(sku)
        if row is None or row["on_hand"] - row["reserved"] - row["safety_stock"] <= 0:
            reasons.append("target_stock_not_available")
        if not self.price(sku).get("proven"):
            reasons.append("target_price_not_proven")
        if not isinstance(script, dict) or script.get("sku") != sku or not self.preview(script).get("safe"):
            reasons.append("target_script_not_complete_and_safe")
        return reasons

    def ingest(self, event):
        if not isinstance(event, dict) or not text(event.get("event_id")) or not positive(event.get("seq")):
            self.error("arrival_identity_or_sequence_invalid", financial=True)
            return
        key, seq = event["event_id"], event["seq"]
        if event.get("session_id") != self.case["session_id"]:
            self.error("foreign_session:" + key, financial=True, seq=seq)
            return
        siblings = []
        if seq in self.journal and digest(self.journal[seq]) != digest(event):
            siblings = [self.journal[seq], event]
        if key in self.ids and digest(self.ids[key]) != digest(event):
            siblings.extend([self.ids[key], event])
        if siblings:
            old = self.conflicts.setdefault(key, [])
            for item in siblings:
                if digest(item) not in {digest(x) for x in old}:
                    old.append(deepcopy(item))
            self.error("journal_conflict:" + key, financial=True, seq=min(x["seq"] for x in siblings))
        elif seq in self.journal:
            self.duplicates += 1
        else:
            self.journal[seq] = deepcopy(event)
            self.ids[key] = deepcopy(event)

    def apply(self, event):
        typ, key, seq = event.get("type"), event["event_id"], event["seq"]
        if typ == "script":
            if event.get("sku") not in ALIASES:
                raise ValueError("script_sku_invalid")
            self.scripts[key] = deepcopy(event)
            self.latest_preview[event["sku"]] = key
            return
        if typ == "observation":
            self.add_history(event, "content")
            return
        if typ in ("notice", "remediation", "telemetry_gap"):
            self.add_history(event, "gap" if typ == "telemetry_gap" else typ)
            result = self.compliance()
            if result.get("terminal_session"):
                self.terminal = True
                if self.phase != "closed":
                    self.phase = "terminated"
            return
        if typ == "resync":
            if event.get("authenticated") is not True or event.get("covered_through_seq") != seq - 1 or type(event.get("covered_through_seq")) is not int:
                raise ValueError("resync_not_authenticated_or_complete")
            self.transport_proven = True
            return
        if typ == "activity_price":
            if event.get("sku") not in ALIASES or not text(event.get("price_ref")) or not uint(event.get("revision")) or not uint(event.get("advertised_buyer_minor")):
                raise ValueError("activity_price_schema_invalid")
            self.quotes[event["sku"]] = {k: event[k] for k in ("price_ref", "revision", "advertised_buyer_minor")}
            return
        if typ == "inventory_snapshot":
            sku = event.get("sku")
            if sku not in ALIASES or any(not uint(event.get(k)) for k in ("on_hand", "reserved", "safety_stock")) or event["on_hand"] < event["reserved"]:
                raise ValueError("inventory_snapshot_schema_invalid")
            reserved = sum(o["quantity"] - o["cancelled"] - o["fulfilled"] for o in self.orders.values() if o["sku"] == sku)
            if event["reserved"] != reserved:
                raise ValueError("inventory_reserved_disagrees_with_order_book")
            self.stock[sku] = {k: event[k] for k in ("on_hand", "reserved", "safety_stock")}
            return
        if typ not in FINANCIAL_TYPES:
            raise ValueError("unsupported_event_type")
        oid = event.get("order_id")
        if not text(oid):
            raise ValueError("order_identity_missing")
        if typ == "order":
            sku = event.get("sku")
            if oid in self.orders or sku not in ALIASES or not positive(event.get("quantity")) or not uint(event.get("buyer_goods_minor")) or event.get("payment_method") not in ("prepaid", "cod") or self.stock.get(sku) is None:
                raise ValueError("order_schema_or_identity_invalid")
            proof = self.price(sku)
            if not proof.get("proven") or event["buyer_goods_minor"] != proof.get("buyer_goods_minor", -1) * event["quantity"]:
                raise ValueError("order_price_not_bound_to_current_quote")
            self.orders[oid] = {
                "order_id": oid, "sku": sku, "quantity": event["quantity"], "buyer_goods_minor": event["buyer_goods_minor"],
                "payment_method": event["payment_method"], "fulfilled": 0, "cancelled": 0, "returned": 0,
                "receipts": 0, "refunds": 0, "fee_credits": 0, "recoveries": 0,
                "fees_minor": None, "costs_minor": None, "ad_spend_minor": None,
                "cost_complete": False, "settled": False, "source_event_ids": [key],
            }
            self.stock[sku]["reserved"] += event["quantity"]
            return
        if oid not in self.orders:
            raise ValueError("unknown_order_reference")
        o = self.orders[oid]
        if typ == "cash_receipt":
            basis = "confirmed_paid" if self.market[0] == "douyin" else "confirmed_collected"
            if not uint(event.get("amount_minor")) or event.get("basis") != basis or o["receipts"] + event["amount_minor"] > o["buyer_goods_minor"] or o["settled"] and o["cancelled"] == o["quantity"] and event["amount_minor"]:
                raise ValueError("cash_receipt_not_confirmed_or_exceeds_order")
            o["receipts"] += event["amount_minor"]
        elif typ == "costs":
            if type(event.get("complete")) is not bool or any(event.get(k) is not None and not uint(event.get(k)) for k in COST_FIELDS) or any(k not in event for k in COST_FIELDS):
                raise ValueError("cost_components_invalid")
            if event["complete"] and any(event[k] is None for k in COST_FIELDS):
                raise ValueError("cost_complete_flag_has_unknown_amount")
            if any(o[k] is not None and (event[k] is None or event[k] < o[k]) for k in COST_FIELDS):
                raise ValueError("known_cumulative_cost_decreased_or_removed")
            if o["cost_complete"] and not event["complete"]:
                raise ValueError("complete_cost_evidence_retracted")
            for k in COST_FIELDS:
                o[k] = event[k]
            o["cost_complete"] = event["complete"]
        elif typ in ("fulfill", "cancel", "return_received"):
            qty = event.get("quantity")
            if not positive(qty):
                raise ValueError("quantity_invalid")
            if typ == "return_received":
                if type(event.get("restockable")) is not bool or o["returned"] + qty > o["fulfilled"]:
                    raise ValueError("return_not_bound_to_fulfilled_units")
                o["returned"] += qty
                if event["restockable"]:
                    self.stock[o["sku"]]["on_hand"] += qty
            else:
                if qty > o["quantity"] - o["cancelled"] - o["fulfilled"]:
                    raise ValueError("fulfill_or_cancel_exceeds_unfulfilled")
                if typ == "fulfill":
                    if self.stock[o["sku"]]["on_hand"] < qty:
                        raise ValueError("physical_fulfillment_exceeds_on_hand")
                    o["fulfilled"] += qty
                    self.stock[o["sku"]]["on_hand"] -= qty
                else:
                    o["cancelled"] += qty
                self.stock[o["sku"]]["reserved"] -= qty
        elif typ == "refund":
            if any(not uint(event.get(k)) for k in ("amount_minor", "fee_credit_minor", "recovery_minor")):
                raise ValueError("refund_component_invalid")
            positive_fee_unproven = event["fee_credit_minor"] > 0 and (o["fees_minor"] is None or o["fee_credits"] + event["fee_credit_minor"] > o["fees_minor"])
            positive_recovery_unproven = event["recovery_minor"] > 0 and (o["costs_minor"] is None or o["recoveries"] + event["recovery_minor"] > o["costs_minor"])
            if o["refunds"] + event["amount_minor"] > o["receipts"] or positive_fee_unproven or positive_recovery_unproven:
                raise ValueError("refund_or_credits_exceed_known_source")
            o["refunds"] += event["amount_minor"]
            o["fee_credits"] += event["fee_credit_minor"]
            o["recoveries"] += event["recovery_minor"]
        elif typ == "settled":
            if not o["cost_complete"] or any(o[k] is None for k in COST_FIELDS) or not (o["receipts"] == o["buyer_goods_minor"] or o["cancelled"] == o["quantity"] and o["receipts"] == 0):
                raise ValueError("settlement_not_financially_complete")
            o["settled"] = True
        o["source_event_ids"].append(key)

    def request(self, request):
        if not isinstance(request, dict) or not text(request.get("request_id")):
            self.error("request_identity_missing")
            return {"request_id": request.get("request_id") if isinstance(request, dict) else None, "decision": "hold", "reason": "request_identity_missing"}
        key, action = request["request_id"], request.get("action")
        if key in self.seen_requests:
            self.error("duplicate_request_identity:" + key)
            return {"request_id": key, "decision": "hold", "reason": "duplicate_request_identity"}
        self.seen_requests.add(key)
        reasons, extra = [], {}
        if action in ("recap", "close"):
            if action == "close":
                self.phase = "closed"
            return {"request_id": key, "decision": "allow", "reason": "safe_stop_or_read_only_reconciliation"}
        if action == "pause":
            if self.phase in ("open", "live"):
                self.phase = "paused"
                return {"request_id": key, "decision": "allow", "reason": "safe_pause"}
            return {"request_id": key, "decision": "hold", "reason": "pause_requires_open_or_live"}
        if self.phase in ("closed", "terminated") or self.terminal:
            return {"request_id": key, "decision": "hold", "reason": "closed_or_terminated_session_never_reopens"}
        if action == "schedule":
            global_reasons = self.general_reasons()
            candidates, candidate_evidence = [], {}
            for sku in ALIASES:
                script = self.scripts.get(self.latest_preview.get(sku))
                why = global_reasons + self.sku_reasons(sku, script)
                candidate_evidence[sku] = sorted(set(why))
                if not why:
                    candidates.append(sku)
            self.lineup = candidates
            return {"request_id": key, "decision": "allow" if candidates else "hold", "reason": "safe_lineup_subset" if candidates else "no_proven_lineup_candidate", "candidate_reasons": candidate_evidence}
        sku = request.get("sku", self.active)
        script = self.scripts.get(request.get("script_id"))
        if action == "open":
            if self.phase != "planned" or sku != "A" or "A" not in self.lineup:
                reasons.append("open_requires_planned_scheduled_A")
        elif action == "pitch":
            if self.phase not in ("open", "live") or sku != self.active:
                reasons.append("pitch_requires_active_open_or_live_SKU")
        elif action == "switch":
            if self.phase not in ("open", "live") or sku == self.active or sku not in self.lineup:
                reasons.append("switch_requires_different_scheduled_target")
        elif action == "resume":
            if self.phase != "paused" or sku != self.active:
                reasons.append("resume_requires_paused_current_SKU")
        else:
            return {"request_id": key, "decision": "hold", "reason": "unsupported_request_no_incremental_spend_authority"}
        reasons += self.general_reasons()
        reasons += self.sku_reasons(sku, script)
        candidate_history, candidate_segments = deepcopy(self.history), deepcopy(self.history_segments)
        if isinstance(script, dict) and sku in ALIASES:
            if action == "switch":
                self.add_history({"event_id": "switch:" + key, "session_id": self.case["session_id"], "type": "switch", "from_sku": self.active, "to_sku": sku}, history=candidate_history, hsegments=candidate_segments)
            activation = self.add_history(script, "content", key="activate:" + key + ":" + script["event_id"], history=candidate_history, hsegments=candidate_segments)
            candidate_result = self.compliance(candidate_history, candidate_segments)
            extra["candidate_compliance"] = candidate_result
            if candidate_result.get("final_recommendation") != "eligible_offline" or candidate_result.get("model_evidence_binding_valid") is not True:
                reasons.append("candidate_on_air_history_not_safe")
        else:
            activation = None
        if reasons:
            return {"request_id": key, "decision": "hold", "reason": ";".join(sorted(set(reasons))), **extra}
        self.history, self.history_segments = candidate_history, candidate_segments
        self.bridge_trace.append({"request_id": key, "source_script_id": script["event_id"], "source_script_sha256": digest(script), "source_segment_sha256": digest(self.segments[script["event_id"]]), "activated_event_id": activation["event_id"], "mapping_kind": "identity_only_new_on_air_occurrence", "raw_answer_changed": False})
        if action == "switch":
            self.active = sku
            self.baseline = {"sku": sku, "state": "not_observed", "reason": "new_target_performance_baseline_reset", "request_id": key, "inherited_old_sku_roi": False}
        self.phase = "open" if action == "open" else "live"
        return {"request_id": key, "decision": "allow", "reason": "all_current_offline_gates_passed", **extra}

    def run(self):
        prior_asof = None
        for step in self.case["steps"]:
            self.step = step
            if not isinstance(step, dict) or not text(step.get("checkpoint_id")) or step["checkpoint_id"] in self.seen_checkpoints:
                raise ValueError("checkpoint_identity_invalid_or_repeated")
            self.seen_checkpoints.add(step["checkpoint_id"])
            asof = instant(step.get("as_of"))
            if prior_asof is not None and asof < prior_asof:
                self.error("checkpoint_clock_reversed")
            prior_asof = asof
            if not uint(step.get("through_seq")) or step["through_seq"] < self.required:
                self.error("journal_required_cursor_reversed_or_invalid", financial=True, seq=self.cursor + 1)
            else:
                self.required = step["through_seq"]
            if step.get("connection") not in ("connected", "disconnected", "reconnected"):
                self.error("connection_state_missing")
                self.transport_proven = False
            if step.get("connection") == "disconnected":
                self.transport_proven = False
            arrivals = step.get("arrivals")
            if not isinstance(arrivals, list):
                raise ValueError("checkpoint_arrivals_must_be_list")
            for event in arrivals:
                self.ingest(event)
                if isinstance(event, dict) and event.get("type") in CONTENT_TYPES and text(event.get("event_id")):
                    self.segment_seen.add(event["event_id"])
            conflict_seqs = {item["seq"] for rows in self.conflicts.values() for item in rows}
            while self.cursor < self.required and self.cursor + 1 in self.journal and self.cursor + 1 not in conflict_seqs:
                next_seq = self.cursor + 1
                event = self.journal[next_seq]
                try:
                    self.apply(event)
                except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
                    self.error("invalid_event:" + event["event_id"] + ":" + str(exc), financial=True, seq=next_seq)
                self.cursor = next_seq
                self.snapshots[self.cursor] = self.financial_snapshot()
            # A semantic answer for hidden future data or a missing first answer
            # is an evidence error, never a default safe classification.
            missing_segments = self.segment_seen - set(self.segments)
            if missing_segments:
                self.error("semantic_answers_missing:" + ",".join(sorted(missing_segments)))
            requests = step.get("requests")
            if not isinstance(requests, list):
                raise ValueError("checkpoint_requests_must_be_list")
            results = [self.request(request) for request in requests]
            recap, by_sku = order_summary(self.orders)
            uncertain = self.cursor != self.required or bool(self.financial_errors)
            cutoffs = [x["seq"] - 1 for x in self.financial_errors if x["seq"] > 0]
            known_cursor = min([self.cursor] + cutoffs) if cutoffs else self.cursor
            known_cursor = max(0, known_cursor)
            known = self.snapshots.get(known_cursor, self.snapshots[0])
            known_recap, _ = order_summary(known["orders"])
            available = {sku: None if self.stock[sku] is None else self.stock[sku]["on_hand"] - self.stock[sku]["reserved"] - self.stock[sku]["safety_stock"] for sku in ALIASES}
            if uncertain:
                recap = {**{k: None for k in RECAP_AMOUNTS}, "performance_state": "partial"}
                available = {sku: None for sku in ALIASES}
            elif self.errors:
                recap["settled_net_profit_minor"] = None
                recap["performance_state"] = "partial"
            checkpoint = {
                "case_id": self.case["case_id"], "checkpoint_id": step["checkpoint_id"], "version": VERSION, **SCOPE,
                "phase": self.phase, "active_sku": self.active, "scheduled_skus": deepcopy(self.lineup),
                "inventory": available, "recap": recap, "request_results": results,
                "as_of": step["as_of"], "cursor": self.cursor, "required_cursor": self.required,
                "journal_complete": self.cursor == self.required, "transport_proven": self.transport_proven,
                "errors": list(self.errors), "financial_errors": deepcopy(self.financial_errors),
                "price_evidence": {sku: self.price(sku) for sku in ALIASES},
                "financial_integrity": self.finance_proof(self.active), "per_sku_financial_evidence": by_sku,
                "cpl_evidence": self.cpl(), "compliance_evidence": self.compliance() if self.history else None,
                "price_speech_preview_evidence": {sku: self.price_claim_review(self.scripts[self.latest_preview[sku]]) for sku in ALIASES if sku in self.latest_preview},
                "terminal_session": self.terminal, "target_baseline": deepcopy(self.baseline),
                "order_book": deepcopy(self.orders), "inventory_components": deepcopy(self.stock),
                "conflicting_journal_evidence": deepcopy(self.conflicts), "duplicate_event_count": self.duplicates,
                "known_validated_prefix": {"scope": "known_prefix_only", "through_seq": known_cursor, "recap": known_recap, "inventory_components": deepcopy(known["stock"])},
                "buffered_sequences": sorted(seq for seq in self.journal if seq > self.cursor),
                "semantic_source_hashes": {key: digest(self.segments[key]) for key in sorted(self.segment_seen) if key in self.segments},
                "bridge_trace": deepcopy(self.bridge_trace), "real_business_actions": 0,
            }
            self.checkpoints.append(project_uncertain_financial_evidence(checkpoint, uncertain))
        if set(self.segments) - self.segment_seen:
            # Extra semantic findings indicate future leakage or detached input.
            raise ValueError("semantic_answers_for_unrevealed_content")
        return {"case_id": self.case["case_id"], "version": VERSION, **SCOPE, "checkpoints": self.checkpoints,
                "last_checkpoint": self.checkpoints[-1], "input_sha256": digest(self.case),
                "segments_sha256": digest(list(self.segments.values())), "catalog_sha256": digest(self.catalog),
                "rule_pack_sha256": digest(self.pack), "journal_event_hashes": {str(seq): digest(event) for seq, event in sorted(self.journal.items())},
                "real_business_actions": 0}


def review_prefix(case_prefix, accumulated_segments, catalog, rule_pack):
    """Replay only the supplied prefix; caller must bind original role answers."""
    return Replay(case_prefix, accumulated_segments, catalog, rule_pack).run()


def checkpoint_payload(case_prefix, accumulated_segments, catalog, rule_pack):
    result = review_prefix(case_prefix, accumulated_segments, catalog, rule_pack)
    payload = {
        "schema": "live-session-persisted-checkpoint-v1", **SCOPE,
        "case_prefix": deepcopy(case_prefix), "accumulated_segments": deepcopy(accumulated_segments),
        "case_prefix_sha256": digest(case_prefix), "segments_sha256": digest(accumulated_segments),
        "catalog_sha256": digest(catalog), "rule_pack_sha256": digest(rule_pack),
        "dependencies": dependencies(), "cursor": result["last_checkpoint"]["cursor"],
        "checkpoint_id": result["last_checkpoint"]["checkpoint_id"], "result": result,
        "result_sha256": digest(result),
    }
    return {**payload, "checkpoint_payload_sha256": digest(payload)}


def save_checkpoint(path, case_prefix, accumulated_segments, catalog, rule_pack):
    payload = checkpoint_payload(case_prefix, accumulated_segments, catalog, rule_pack)
    with Path(path).open("x", encoding="utf-8", newline="\n") as out:
        json.dump(payload, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
    return {"path": str(Path(path).resolve()), "sha256": sha(path), "cursor": payload["cursor"], "checkpoint_id": payload["checkpoint_id"]}


def verify_checkpoint(path, expected_case_prefix, accumulated_segments, catalog, rule_pack):
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    expected = checkpoint_payload(expected_case_prefix, accumulated_segments, catalog, rule_pack)
    if digest(payload) != digest(expected):
        raise ValueError("persisted checkpoint corrupt, stale, detached, or source changed")
    return {"valid": True, "checkpoint_id": payload["checkpoint_id"], "cursor": payload["cursor"],
            "checkpoint_sha256": sha(path), "replayed_result_sha256": payload["result_sha256"],
            "new_model_calls": 0, **SCOPE}


def resume_checkpoint(path, new_case_prefix, accumulated_segments, catalog, rule_pack):
    """Validate the persisted old prefix before a fresh-process replay extension."""
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    old = payload.get("case_prefix")
    if not isinstance(old, dict) or not isinstance(old.get("steps"), list):
        raise ValueError("persisted old prefix missing")
    n = len(old["steps"])
    proposed_old = deepcopy(new_case_prefix)
    proposed_old["steps"] = proposed_old["steps"][:n]
    if digest(old) != digest(proposed_old) or len(new_case_prefix["steps"]) <= n:
        raise ValueError("resume extension changes or does not extend persisted prefix")
    incoming = segment_map(accumulated_segments)
    old_segments = payload.get("accumulated_segments")
    if not isinstance(old_segments, list) or any(incoming.get(s.get("event_id")) != s for s in old_segments):
        raise ValueError("resume semantic history changed")
    verification = verify_checkpoint(path, old, old_segments, catalog, rule_pack)
    result = review_prefix(new_case_prefix, accumulated_segments, catalog, rule_pack)
    result["resume_verification"] = verification
    return result
