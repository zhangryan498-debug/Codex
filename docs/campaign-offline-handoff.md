# Campaign offline handoff contract

## Price-floor acceptance takes priority (2026-11-11)
For a price-floor handoff, use the enforced caller-frozen intake in docs/campaign-price-handoff.md and docs/campaign-price-floor-offline.md. The older field-consistency interface below remains compatible for non-price diagnostics only. Its pass/completed/free-text result is not a price acceptance or live authorization.


## Scope and conclusion
This is a synthetic/offline receiving contract, not merchant, human, campaign-launch or spend approval. Model inference may be online; no business API is authorized by this contract. Use the existing campaign role and templates, not a replacement role.

## Mandatory field gate
For structured campaign handoffs, apply `scripts/campaign_handoff.py` before accepting a recipient acknowledgement. Caller supplies a frozen authority object separately from the packets. Never derive authority from a proposed packet or the model's claim of correctness. Required identity is SKU, integer revision and integer payable cents. Required terms include currency, named timezone, inclusive start, exclusive end, integer list price, canonical coupon rule and shipping. The payload role must match the intended receiver. Missing, null, wrong-type or mismatched values fail; do not infer a missing named timezone from a timestamp offset. Evidence-scope text must be explicit but is not proof of any actual shop configuration.

`validate(authority, packets, receiver)` returns field checks without business actions. `combine(...)` additionally binds a model receipt: final offline acceptance requires BOTH passing fields and model acceptance. A model cannot override a failed gate. Keep model-origin conclusions and system-derived gate conclusions separately visible. The receipt binds input hashes; any mutation requires new validation. Every result remains `release_status=not_authorized`.

In read-only runs, import the module and print JSON from validate; no write permission is required. Where an authorized workflow saves evidence, the CLI supports --input, --receiver and --output. Use the project's .venv Python with -X utf8, not the WindowsApps python shim.

## Business evidence and numeric discipline
Consistency among images, scripts, product sheets and packets does not establish current product facts or current platform terms. Preserve fact_verification_status separately. Unknown costs, stock timestamps, refund reinstatement, stacking or threshold basis stay unknown. User-confirmed facts are only verified within their stated scope. Public sales estimates are not actual store settlement or profit.

Coupon prices must preserve eligible-SKU scope, threshold basis, order of discounts, once versus recurring reductions, usage caps, rounding and time boundaries. Do not double-deduct coupons already in paid revenue. Inventory reservations must preserve units, safety stock, freshness and idempotency. Budget and revision mismatches block handoff.

## Acceptance evidence
Current local evidence: `work/campaign-acceptance-v1/`. The final report is `work/campaign-acceptance-v1/acceptance-report.md` when present. Inspect raw results and distinguish original model failures from field-gate repairs. Never present a generated test plan or configuration migration as a passed live workflow.

## Trusted run binding
Use combine_run with a completed local CLI evidence directory. It verifies task and answer hashes against the recorded result, parses the authority and packets actually included in that run, and rejects stale receipts for any changed input. Model-reported hashes are not trusted. The low-level combine API requires caller-verified binding; do not accept binding from model text. This gate verifies consistency against caller-frozen authority, not the real-world truth, current validity or commercial legality of that authority. Treat structured system errors as the authoritative field diagnostics; retain model prose separately and do not convert its unverified field claims into facts.
