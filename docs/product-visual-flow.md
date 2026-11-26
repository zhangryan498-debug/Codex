# Product visual production flow (2026-11-17)

Scope: product main images, long detail pages, posters. Existing approved outputs are immutable baselines. No model, effort, or fast-mode override.

## Required copy gate
Use `python scripts/product_visual_flow.py prepare --task-id <stable-round-id> --state <project-root>/data-pipeline/product-visual/rotation.sqlite --output <new-run>/copy-request.json`.
If the user provides explicit selling copy, supply `--user-copy-file <utf8-file>`: preserve it and do not consume an A/B round.
Otherwise read and execute the exact installed Skill returned by prepare, inspect the product references and emit a real structured receipt. A is ecommerce-detail-page-generator-zh; B is ecommerce-visual-planning-skill. Do not replace Skill execution with an empty acknowledgement or fabricated receipt.
Call `complete` with the same task-id/state, `--receipt <receipt.json>` and a new output path. Required receipt: variant A/B, copy object, provenance.execution_method, provenance.read_sources containing installed Skill SHA256. Then call `copy` with the same task-id/state before layout. Pending rounds block the next generated-copy round; retries reuse the same id; completion advances A/B. The CLI prepares and validates receipts; it does NOT itself run a model. The agent performs the Skill call without changing its selected model.

## Module responsibilities
1. Facts: verified product input, image evidence, explicit copy. Unknown material/dimensions remain unclaimed; simulated facts only in explicitly approved tests with internal provenance.
2. Selling-point Skill: evidence-grounded benefits, priority, titles and body copy. A/B full-round rotation. Layout module must not take over this responsibility.
3. Layout: absorb detail-page-generator theme tokens, readable hierarchy, spacing and section rhythm; preserve module-specific purpose (hero/detail/scene). This is design-method reuse, not installation of its entire app.
4. Element editor: absorb vue-fabric-editor stable object IDs, JSON state, object-level edits and reversible revisions. Preserve the existing short image/text ID registry. Only the requested element changes. Vue editor UI/library is not yet installed.
5. Image generation: preserve product identity and approved unaffected assets; only generate missing or requested replacements.
6. PSD: retain current PSD renderer, native text and text/image/module/background groups; font upload uses existing tested font pipeline. vue-fabric-editor is NOT a replacement PSD exporter.
7. QA: render PNG and PSD, check text overflow, product contact/shadows, layer groups, source hashes and only-target edits. Keep Photoshop application compatibility unverified until actually opened there.

## Existing role entry
Both visual-designer playbooks reference this required gate. Generic run_codex_role loads those playbooks unchanged in mechanism. This is agent-guided orchestration, not an automatic one-command model-to-PSD service. Frozen historical offline replays remain unchanged and must not consume production rotation.
