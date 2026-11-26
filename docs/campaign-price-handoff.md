# Offline campaign price floor handoff

## Scope and trust boundary

`scripts/campaign_price_handoff.py` is a consistency and safety gate, not a platform pricing engine. It does not discover eligible discounts, choose a platform tool, calculate coupon stacking, or certify a real store. Every result remains `release_status=not_authorized`.

The application must independently freeze the original `source_input`, `authority`, and `calculation`. The calculation must come from trusted deterministic computation or independent review. A model's assertion that its own amount is verified, a model-authored calculation object, or a model-authored binding is not trusted evidence. The accepted method labels and hashes do not authenticate the producer. This trust boundary must be enforced by the caller.

No default fee, subsidy ratio, floor, platform eligibility, or unknown cost is supplied. Missing, unresolved or contradictory inputs fail closed. Integer monetary values are minor units (cents); bool, float, NaN, negative and string values are rejected for monetary fields.

## Contract

`validate(authority, packet, receiver, evaluation_time, source_input, calculation)` returns typed diagnostics, `field_gate` and `system_receipt`.

Supported receiving routes are only `douyin-shop-ops` for `douyin/CN/CNY`, and `tiktok-shop-ops` for `tiktok/PH/PHP`. SKU and revision must match exactly. Authority uses schema 1.1 and explicit `synthetic_offline` evidence scope.

Required authority fields are exported as `AUTH_FIELDS`; required independent-calculation fields as `CALC_FIELDS`. Floor and verified lower bound must share an explicit basis. At equality, acceptance requires `floor_inclusive=true`; below floor is rejected. Seller-cost and fee completeness must both be true, and unknown/conflict lists explicitly empty. Rule IDs map to nonempty version strings. Hashes bind the actual source input and independent calculation object.

The authority carries a named IANA timezone, inclusive start and exclusive end. Timestamp offsets must agree with that zone. The caller supplies the evaluation instant explicitly in that same offset convention; there is no implicit current-time default. This permits reproducible synthetic tests, not replay at a later real execution time. Callers must evaluate again at each downstream use.

Packets contain a nonempty packet ID, receiving role, authority hash, source-input hash and complete authority payload. Missing, additional or changed payload fields fail. This comparison does not establish truth of the authority.

## Actual CLI receiver binding

`task_bundle(...)` emits a stable single-case task section. Place it exactly once inside the actual CLI task; do not repeat its marker strings elsewhere. Have the model return a JSON object with:

- `scope: synthetic_offline`
- `release_status: not_authorized`
- `receiver_role`, `packet_id`, `authority_sha256`, `input_sha256`
- `offline_receipt: accepted` or `rejected`
- optional explanation fields, which never override typed diagnostics

`combine_run(..., run_dir)` checks completed status, successful exit, turn completion, task/answer raw-byte hashes and run ID. It parses and compares the actual frozen task inputs before combining the model receipt with the deterministic gate. Model acceptance cannot override a failed gate. Rejected model receipts also block final acceptance.

For multiple independent handoffs in one CLI invocation, use `batch_task_bundle(cases)`, then `combine_batch_run(cases, run_dir)`. Cases contain unique `case_id` plus the six validate arguments. The bundle adds trusted hashes for the model to copy, not infer. Model output is a `scope/release_status/receipts` object; every receipt adds its `case_id`. Duplicate, missing, extra or stale coverage is rejected.

The lower-level `combine(..., binding)` is only for callers that already independently verified the binding. Local run files and hashes are not signatures and do not protect against an attacker rewriting the entire trusted evidence set.

## Regression and integration

Run the new unit suite with the project Python:

```powershell
.\.venv\Scripts\python.exe -B -X utf8 -m unittest discover -s scripts -p test_campaign_price_handoff.py -v
```

Public synthetic receiver fixtures live in `work/campaign-floor-handoff-integration-inputs-v1/`. They are separate, unblinded integration examples, not part of any independently authored blind pricing score. Their amounts and fees are explicit synthetic assumptions, not platform rules. The files do not authorize live shop access, publication or spending.

Existing campaign roles, Skills, original handoff gate and entrypoint were not modified by this module's creation. Actual receiver-model integration remains a separate test.


## Identity and floor scope correction in schema 1.1

The v0 implementation passed 22 new unit methods but independent code review found that calculation evidence could be reused for a changed SKU or revision after rehashing authority. Those tests did not cover the replay gap and are not a safety acceptance. Original code, inputs, hashes and logs are preserved in work/campaign-price-gate-v0-review-20261111/.

Schema 1.1 binds platform, country, currency, SKU, revision, floor_scope and floor_basis identically across original source input, independent calculation and authority. Source input must be a nonempty object with those identity fields. Empty/null evidence and missing or mismatched subjects fail even when their hashes have been recomputed.

floor_scope is explicit: each_sku or order. Supported bases are buyer_goods_per_unit / merchant_net_per_unit for each_sku, and buyer_goods_order / merchant_net_order for order. Arbitrary basis strings or inconsistent scope/basis pairs fail. An order-level pass never establishes a per-SKU floor; each SKU needs its own validated evidence. The gate still does not establish that the trusted calculation actually enumerated every feasible platform discount.

## Batch scope inheritance correction

The verified batch top-level scope and release_status apply to every receipt. Nested receipts may omit those two fields; explicitly conflicting nested values are rejected. No acceptance decision, hash, identity or other missing field is inferred. Missing top-level scope or a missing offline_receipt still fails.

The combiner preserves raw_model_receipt and its hash, records the effective receipt and its separate hash, and lists inherited_scope_fields. The binding includes the complete original answer-byte hash. It never changes the saved model answer. The v1 implementation incorrectly required duplicate nested scope fields despite the task contract. Original v1 code and the real failure are preserved in work/campaign-price-gate-v1-review-20261111/.


## Enforced trusted-caller intake (2026-11-11, offline only)

New structured price acceptances use the dedicated host lane in
`scripts/run_codex_role.py` / `.ps1` and `scripts/campaign_price_intake.py`.
A normal CLI `completed`, positive free text, a model-authored verified flag, or
the legacy field-consistency gate is not a price acceptance. A downstream
consumer must require a completed, hash-bound `price-intake-receipt.json` and
revalidate it at its own explicit evaluation instant. There is no no-gate switch
in this typed lane. Reserved structured price markers supplied without the lane
are rejected. This is not semantic detection of every natural-language task:
ordinary tasks remain compatible but do not acquire price acceptance credentials.

The trusted application/user supplies a UTF-8 JSON file before invoking a model.
Its exact top-level fields are `schema_version: "1.0"`,
`scope: "synthetic_offline"`, `release_status: "not_authorized"`,
`receiver_role`, and `cases`. Each case has exactly `case_id`, `authority`,
`packet`, `source_input`, and `calculation`. All identities and amounts use the
existing schema 1.1 gate. The host inserts receiver and the explicitly supplied
evaluation time; the model cannot choose them. Case IDs and packet IDs are unique.
Model-output files under `codex-runs` are rejected as caller inputs. The caller
must independently verify the input/calculation source before using this lane.
A permissible path, producer string, or hash is not authentication, and the host
cannot prove independent provenance from JSON alone.

Use a caller file, not an answer copied into a file. Example (input validation
only, no model invocation):

```powershell
.\.venv\Scripts\python.exe -B -X utf8 scripts/run_codex_role.py --role douyin-shop-ops --task "Review frozen synthetic handoffs." --price-handoff-file work/campaign-price-intake-20261111/inputs/douyin-shop-ops-caller.json --price-evaluation-time 2026-11-11T12:00:00+08:00 --validate-only
```

Remove `--validate-only` only for an authorized model review. Do not paste a
handcrafted price task bundle: the host creates it exactly once. The equivalent
PowerShell options are `-PriceHandoffFile` and `-PriceEvaluationTime`. The example
does not set a model or fast mode and does not authorize any shop action.

The lane requires a supported shop receiver, read-only sandbox and no mixed
image/recheck/review mode. It freezes caller bytes, generated task, complete prompt,
relevant source/contract hashes and typed preflight diagnostics before inference.
It uses per-run tool-disabling flags without ignoring user model configuration;
these flags are not a claim of physical network isolation. After a completed call,
it verifies the same frozen inputs, source hashes, actual result role, original
task and original answer before independently combining gate and model receipts.
Both must accept; any unknown, conflict, stale identity, missing cost, expired
period or model rejection remains rejected. Duplicate JSON keys and incomplete
model receipts fail closed. Failures preserve diagnostics without publishing an
accepted price receipt. `acceptance_ready` stays false: this lane is never live
business authorization.

`result.json` separately records `price_intake_status`,
`price_intake_contract_sha256`, `price_intake_receipt_sha256`,
`price_accepted_count`, `price_rejected_count`, and `price_handoff_accepted`
(the last is true only when every case is offline accepted). Execution exit zero
can mean a successfully recorded batch of rejected proposals; read the typed
per-case results, never infer acceptance from the process exit code.

At each downstream use, call `campaign_price_intake.revalidate_run` with the
unchanged original caller file, intended role, new explicit timestamp and run
directory. The standalone CLI exposes the same check via `--input`, `--receiver`,
`--evaluation-time`, `--run-dir`, and `--output` (a new evidence file only).
It verifies the published receipt binding and recomputes the original model/system
combination, then independently checks the floor at the downstream instant.
This is fresh deterministic revalidation using existing model evidence, not a
new model run. New input bytes, rule/fee versions or source code require a new
review, not reuse of an accepted receipt. Receipt/hash files remain local
consistency evidence, not signatures against rewriting the whole trusted set.
