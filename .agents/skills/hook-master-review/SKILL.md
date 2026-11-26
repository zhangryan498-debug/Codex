---
name: hook-master-review
description: Review a short-video opening, first shot, speech, captions and immediate payoff using provided footage or script evidence. Use for hook diagnosis and alternative openings, not full-film technical certification or predicted advertising performance.
---
# Hook Master Review for Codex / D&T

中文任务用中文交付。先给结论与优先修改建议，再给证据。Read [references/review-modules.md](references/review-modules.md) for the eight review stages; no Claude Skill tool or sibling skill discovery is required. Paths are relative to this file. This is a text-only adaptation, with no installer, network API, credentials, package or model dependency. Preserve D&T role ownership and existing scripts; this skill does not authorize publishing, spending or production changes.

## Evidence and scope
- State what was actually reviewed: video/audio, extracted frames plus ASR, a supplied script/storyboard, or an idea. Script descriptions are not watched footage. If modalities are unavailable, continue the supported part, mark the rest unverified; ask only for the missing evidence that blocks the requested verdict.
- Trace each factual claim to supplied evidence. Keep averages, totals, failures and individual cases distinct. Never invent failure counts, causes, timestamps, lengths, experiments, quotations, audience statistics or observed frames. Distinguish user-confirmed fact, supplied claim, observed evidence, inference and missing evidence. Several materials agreeing does not prove their truth.
- Existing clip times require observed or supplied timestamps. Proposed edit timings are labeled proposed, not measured. Duration is unspecified when unknown. New shots are labeled reshoot proposals, never existing footage.
- Simulation of six viewers is a thinking aid, not an audience test, percentage or retention forecast. Never promise views, sales or virality. No current-trend claims without dated evidence. Offline review uses only supplied inputs; do not fetch feeds or paid data.
- Product claims, SKU, price and coupon conditions use the approved fact card; unresolved facts stay pending. A creative pass is not legal or platform clearance. Source niche rules are cautionary examples, not current authoritative law.

## Workflow
1. Capture exact opening speech/text, shot evidence, audience, length, supporting facts and intended action. Separate missing material from a confirmed flaw.
2. Diagnose delay, confusion, irrelevance and disinterest across all available channels together. Cite words/shot IDs; if only speech is available do not fail a possible visual hook unseen.
3. Apply the eight modules as relevant. For review-only tasks prioritize diagnosis and concrete edits; do not force six rewrites. When variants are requested, retain the user's count where supported and D&T's three complete hook variants for a production brief; do not follow the upstream quantity cap.
4. Retain only claims supportable by the facts and assets. If proof is missing, remove the claim or mark a clearly non-publishable placeholder. A creative rewrite must not add an unprovided cause, testimonial or performance statistic.
5. Return: conclusion and scope; prioritized issues with evidence and fix; proposed first shot / spoken hook / on-screen text / immediate payoff; justified alternatives when requested; missing facts and next verification. Separate creative quality, factual grounding, observed technical quality and real business effectiveness. Not tested stays not tested.
6. Recommend an offline comparison or future single-variable test; do not perform posting/advertising. Whole-film identities, lip sync, rights and quality gates remain outside this opening review unless separately inspected.

## Provenance
Adapted from Liam Johnston's MIT Hook Master suite, source commit 287053a825efc992e38653a66fc298925293bf36, inspected 2026-11-11. See [references/provenance.md](references/provenance.md) and LICENSE. Original working example contains inferred specifics not in its brief; it is deliberately not runtime guidance. This adaptation preserves the review logic, not those example facts or Claude installation behavior.
