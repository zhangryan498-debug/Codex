---
name: ecommerce-visual-planning
description: |
  E-commerce main-image and detail-page visual planning skill. Determines the conversion driver and locks a visual style first, allocates main-image tasks and detail-page modules by driver, passes two confirmation gates, then delivers scene descriptions, in-image copy, design notes, and image-generation prompts, closing with a five-dimension self review and a compliance risk list.
  For Taobao, Tmall, JD, Pinduoduo, and Douyin Shop main images, detail pages, selling-point extraction, in-image copy, visual scripts, generation prompts, and advertising-law self checks.
  Triggers: main image, detail page, e-commerce visual, selling points, visual script, in-image copy, click-through rate, conversion rate, generation prompt, advertising law, compliance check, design handoff.
  Do not use for: issuing legal opinions or replacing platform review; fabricating test reports, patents, approval numbers, sales figures, or reviews; manufacturing efficacy claims for products without credentials; automatic listing or publishing; general marketing copy unrelated to visual assets.
license: MIT
---

# E-Commerce Visual Planning and Execution SOP

## Language Rule

Chinese is the default output language. Switch to English or bilingual only when the user explicitly asks. The Chinese file `SKILL.md` remains the runtime entry point.

## When to Use

When the user needs main images, detail pages, selling-point visualization, in-image copy, generation prompts, or advertising-law self checks.

**Do not use for:**

- Final legal opinions, or conclusions replacing a lawyer or platform review
- Fabricating test reports, patents, approval numbers, sales figures, reviews, certifications, or endorsements
- Efficacy claims for medical, pharmaceutical, health-food, or medical-device products without supporting credentials
- Automatic listing, ad placement, or public publishing
- Marketing copy unrelated to visual assets

## Core Principles

1. **Strategy first.** No scene design before the conversion driver is determined.
2. **Main images and detail pages have different jobs.** Main images drive click-through; detail pages drive conversion.
3. **The driver determines the structure.** Task allocation and module selection differ by driver — no fixed template.
4. **Skeleton before copy.** No heavy execution before direction is confirmed.
5. **Compliance is judged against the legal baseline first,** with platform rules layered on top.
6. **Five-dimension independent self review.** Any dimension below 80 is rewritten on its own; every deduction must cite a specific location.

## Workflow

```
Step 1  Gather information, mark gaps         ──┐
Step 2  Determine conversion driver + Style Lock │
Step 3  Visual Strategy Brief  → Gate 1 pause  ──┤ Planning
Step 4  Storyboard             → Gate 2 pause  ──┘
Step 5  Per-image execution output             ──┐
Step 6  Five-dimension self review             ──┤ Execution
Step 7  Compliance risk list + delivery        ──┘
```

## Constraints That Survive Gate-Skipping

The user may ask to skip the confirmation gates. **Skipping a gate skips only the act of waiting for a reply — it skips no constraint.**

Even when the user says "go straight to final" or "skip the confirmation step", all six of the following still apply. Missing any one means the step was not performed:

| # | Constraint | Source |
|---|---|---|
| 1 | Determine the conversion driver before designing | Step 2 |
| 2 | Style Lock complete, all five items | Step 2 |
| 3 | Each frame answers the question its driver table assigns | Step 4 |
| 4 | Per-driver prohibitions (e.g. Rational-Driven KV1 single number) | Step 4 |
| 5 | No two frames answer the same question | Step 4 |
| 6 | Five-dimension self review + compliance risk list | Steps 6–7 |

> Correct behavior when skipping: produce the strategy brief and storyboard **as usual**, simply do not stop for confirmation — continue straight through.
>
> **Wrong behavior: skipping the content of Steps 3–4 and jumping from Step 1 to copy in Step 5.** That discards task allocation, the single-number rule, and de-duplication at once, and the output is guaranteed to be stacked claims with repeated information.

### When the User Names a Deliverable

Users often ask for one thing directly: "just give me the main-image copy", "storyboard only", "I just need the generation prompts".

**Naming a deliverable sets the endpoint — it does not waive what comes before it.** Whatever is named, state what question each of the five frames answers first, then give the requested deliverable.

| User says | Correct response |
|---|---|
| Just give me the copy | State in five lines what each frame answers, then the copy |
| Storyboard only | Output the full five fields per frame; no final copy |
| Just the prompts | Output the allocation first, then the prompts |

It may be compressed to five lines rather than a full storyboard — **but all five questions must be stated**, or there is no way to judge repetition or driver fit.

> This is the SOP's most frequent point of failure: user names the copy → only copy is produced → allocation and de-duplication vanish together → five frames repeat the same selling points.

---

## Step 1: Information Gathering

Never invent missing items — mark them "missing". **Ask at most 3 questions**, only about things that change positioning or compliance boundaries.

| Item | Purpose | If missing |
|---|---|---|
| Product type and credential scope | Determines applicable compliance limits | **Blocking**: high-risk categories get no efficacy content |
| Target platform | Layers platform review rules | Degrade: legal baseline only |
| Audience and usage context | Extracts pain points and reasons to buy | Degrade: generic copy, flag assumptions |
| Selling points, specs, price | Builds Feature→Benefit translation | Degrade: visual direction only |
| Existing assets and competitor references | Establishes the visual starting point | Acceptable |
| Visual style reference | Builds the Style Lock | Acceptable, infer from driver |

## Step 2: Conversion Driver + Style Lock

### The Four Drivers

| Type | Typical categories | What the buyer checks | Visual focus |
|---|---|---|---|
| **Aesthetic-Driven** | Beauty, jewelry, apparel, decor, lighting | Does it look good, does it fit my space | Texture, light, palette, mood |
| **Pain-Point-Driven** | Cleaning, storage, baby, personal care, tools | Will it solve my problem | Problem first, before/after, safety proof |
| **Rational-Driven** | Digital, appliances, 3C, hardware | Are the specs enough, is it worth it | Spec translation, mechanism visuals, numbers |
| **Emotion-Driven** | Fragrance, gift sets, pets, seasonal | Is it giftable, does it feel right | Emotion, ritual, after-state |

**How to decide:** ask what the buyer most wants to confirm before ordering. If two are close, pick a primary and note the secondary; allocate main images by the primary, borrow modules from the secondary for the detail page.

### Style Lock

All five items required:

- **Palette**: primary / secondary / accent — **no more than three colors**
- **Product ratio**: 60%–80% of the main image (Aesthetic-Driven may relax to 50% for atmosphere)
- **Lighting system**: hard / soft / backlit / ambient, with direction and intensity
- **Type system**: heading weight, body weight, numeral face, CJK-Latin pairing rule
- **No-drift list**: random backgrounds, invented logos, style breaks, over-decoration

Once set, every generation prompt must carry the Style Lock. No mid-course changes.

---

## Step 3: Visual Strategy Brief → Gate 1

Output these five, then **pause**:

1. Conversion driver and rationale
2. Style Lock (all five items)
3. Benefit translation table: Feature → Advantage → Benefit → Evidence (evidence only from user-supplied material; otherwise mark "pending")
4. Compliance boundaries: applicable legal limits and platform rules
5. Missing information and current assumptions

**Pause line:**

> This is the visual strategy brief. Please confirm the conversion driver and the Style Lock — if these two are wrong, everything downstream is rework. Once confirmed I will produce the main-image and detail-page storyboard.

## Step 4: Storyboard → Gate 2

**This step produces the director's script only — no final in-image copy.** The point is to surface wrong direction before assets are made.

### Main-Image Allocation by Driver

Each frame must specify: visual task / composition (ratio, angle, eye flow) / primary focal point (exactly one) / color restraint / expressions to avoid.

> **Task names may be reworded per category, but the question each frame answers may not change.**
>
> The third column of each table below is the doubt that frame must remove. The task name is only a label — **the question is the constraint.** Rewording is fine; changing the question means changing the driver, and the logic of the set collapses.
>
> Typical error: Rational-Driven KV3 should answer "how does it compare"; built as "before vs after" it answers a Pain-Point-Driven question instead.

> **Task names are fixed.** Use the names below verbatim. Do not rename, merge, or borrow a task from another driver.
>
> Frequent violation: rewriting Rational-Driven "spec comparison" as "pain-point comparison", or "compatibility" as "material detail". **Renaming the task changes the driver**, and the logic of the whole set collapses.

**Aesthetic-Driven:**

| # | Task | Notes |
|---|---|---|
| KV1 | Texture stopper | Macro or partial close-up; material detail is the selling point; 0–1 line of copy |
| KV2 | Full form | Complete product plus color/size family — confirm "this is the one I want" |
| KV3 | Scene mood | Product in the target environment; environment serves, never dominates |
| KV4 | Craft detail | Seams, plating, stitching, grain — prove quality through detail |
| KV5 | Spec and action | Size, set, colorway, price anchor; numbers over adjectives |

> ⚠️ Aesthetic-Driven products must **not** use a pain-point before/after as a main image — it drags an aesthetic category down to a functional one.

**Pain-Point-Driven:**

| # | Task | Notes |
|---|---|---|
| KV1 | Problem stopper | The problem in the user's own words; self-explanatory frame |
| KV2 | Solution in action | The key gesture — make the "how" legible |
| KV3 | Before / after | **The core frame.** Same angle, same light; the difference must be self-evident |
| KV4 | Safety proof | Material, certification, suitable users (evidence from user material only) |
| KV5 | Spec and action | Quantity, durability, bundles, CTA |

**Rational-Driven:**

| # | Task | Notes |
|---|---|---|
| KV1 | Spec stopper | Product front plus **exactly one** strongest performance number, set large |
| KV2 | Mechanism visual | Cutaway, airflow/water-path diagram, exploded view |
| KV3 | Spec comparison | Objective table or industry benchmark — **never disparage a competitor brand** |
| KV4 | Compatibility | Ports, dimensions, supported models — kill the "will it work" doubt |
| KV5 | Purchase action | Configuration, warranty, CTA |

> ⚠️ Three prohibitions for Rational-Driven:
>
> - **KV1 carries one number only.** Not a layout preference — a structural precondition for de-duplication. Once KV1 takes all the specs, KV4 and KV5 inevitably collide with it. "Strongest" means a **performance metric** (retention hours, wattage, battery life, precision) — never price, capacity, or bundled extras.
> - **KV3 must not use a time or before/after comparison.** That is a Pain-Point-Driven device. Here the comparison target is **specs against an industry benchmark**, not "before vs after use".
> - **KV4 must not be a material macro shot.** That is Aesthetic-Driven craft detail. This frame answers "will it fit, will it match".

**Emotion-Driven:**

| # | Task | Notes |
|---|---|---|
| KV1 | Mood stopper | Atmosphere first; the product may not dominate; convey the after-state |
| KV2 | Ritual scene | Gifting, holiday, solitude — the target occasion |
| KV3 | Product itself | Fill the information gap; show the real object clearly |
| KV4 | Packaging / unboxing | Gift box, card, unboxing — a decisive moment for emotional categories |
| KV5 | Gifting action | Specs, card service, delivery timing, CTA |

**Category exception: designer toys / blind boxes / figures**

The default Emotion-Driven table is built for fragrance and gift sets; "gifting action" does not apply to blind boxes. Use this instead:

| # | Task | **Question answered** | Notes |
|---|---|---|---|
| KV1 | Mood stopper | Does this character move me? | The IP character's emotional charge; the product need not dominate |
| KV2 | Series overview | How many, and what can I get? | Regular figures laid out plus the secret slot |
| KV3 | Unboxing ritual | What is opening it like? | The hand, the half-open box, the figure emerging — **for a blind box the unboxing is the product** |
| KV4 | Craft and finish | **Is the paintwork worth the price?** | Paint, color separation, flash lines, size in hand |
| KV5 | Odds and action | What are the odds, how do I buy? | Price plus **published draw probability** (a legal duty; see rule library F-1) |

> **Do not swap KV4 for IP credentials or copyright registration.** That belongs to detail-page M7; repeating it in a main image wastes a frame.
> **Blind-box buyers cannot see the actual object — paint precision and flash lines are the core doubt.** No amount of M7 authority substitutes for this frame.

> KV5 here is not merely a CTA. **Draw probability, item distribution, and limited-run quantities must be disclosed** — omitting them is a violation, not a design choice.

### Hard Constraint: De-duplication

**No two of the five main images may answer the same question.**

After the storyboard is written, check each frame once:

> Which single doubt does this frame remove?

Two frames pointing at the same doubt is a duplicate — reject the set and reallocate.

Likewise, **the same value must not be the primary focal point of two frames** — if "316" anchors both KV1 and KV4, those two frames are answering the same question.

> The Narrative coherence dimension checks exactly this: list the five doubts and see whether any two are the same.

### Detail-Page Modules by Driver

Modules must advance along attention → desire → trust → action.

| Module | Function |
|---|---|
| M1 Hook | Within 3 seconds, "this is for me" — not a bare product shot |
| M2 Core benefits | Top 2–3 reasons; features translated into benefits |
| M3 Material and touch | Cold/soft/translucent/crisp, with matching light |
| M4 Mechanism | Draw *why* it works |
| M5 Scene coverage | 2–3 concrete scenes |
| M6 Before / after | Same-condition comparison |
| M7 Trust evidence | Reports, patents, credentials — **user-supplied material only** |
| M8 Spec table | Full specs, 100% consistent with the rest of the page |
| M9 Objective benchmark | Public industry reference, never "we win, they lose" |
| M10 FAQ | Reverse-engineered from buyer doubts |
| M11 CTA and disclaimers | Price, guarantees, disclaimer placement |

| Driver | Required | Optional | Usually omit |
|---|---|---|---|
| Aesthetic-Driven | M1 M2 M3 M5 M10 M11 | M7 M8 | M4 M6 M9 |
| Pain-Point-Driven | M1 M2 M6 M7 M10 M11 | M3 M5 M9 | M4 |
| Rational-Driven | M1 M2 M4 M8 M9 M10 M11 | M6 M7 | M3 |
| Emotion-Driven | M1 M2 M3 M5 M11 | M7 M10 | M4 M6 M8 M9 |

Keep the total between 6 and 9 modules. More than 9 and buyers stop scrolling; fewer than 6 and trust is under-built.

**Pause line:**

> This is the skeleton — no final copy yet. Please confirm the main-image task allocation and the detail-page module order. Changing it now is one line of text; changing it after the assets exist is a redo. Once confirmed I will produce scenes, copy, and design notes.

---

## Step 5: Execution Output

After Gate 2, deliver **four items per image**:

1. **Scene description**: element layout, product ratio, lighting direction, background, eye path
2. **In-image copy**: benefit-translated. Main image **≤5 lines, ≤6 CJK characters per line**; detail-page module **≤6 lines**
3. **Design notes**: primary focal point, eye-guidance path, color rationale, disclaimer placement
4. **Generation prompt**: Style Lock + subject + composition + material + lighting + background + negative constraints

## Step 6: Five-Dimension Independent Self Review

Each dimension scored 0–100 independently. **Any dimension below 80 is rewritten on its own — a high score elsewhere never offsets it.**

**Every deduction must cite a specific location** (e.g. "KV3 in-image copy, line 2"). A score without a location is not a review; treat it as not performed.

| Dimension | Basis | Heavy deductions |
|---|---|---|
| **Compliance** | Legal baseline line by line, platform rules on top; **imagery is reviewed like copy, and assets are checked for being real** | Absolute superlatives, fabricated evidence, medical implication, food efficacy claims, unsupported claims made by the image itself, craft claims riding on AI imagery |
| **Benefit translation** | Are features translated into perceivable benefits | Spec-sheet language, unexplained parameters, function without benefit |
| **Primary focal point** | Product ratio, single selling point, copy line count, eye flow | Subject too small, stacked claims, overlong copy, unclear focal point |
| **Material expression** | Does light match material; is the main-image / detail-page split clean | Missing tactile description, mixed logic, style drift |
| **Narrative coherence** | List the doubt each frame removes — any duplicates; does the page follow the purchase path | Two frames on one doubt, jumpy order, missing trust evidence |

> **What the score is:** a structured self check, not an external audit. The compliance score **is not a legal opinion**; platform pre-review and credential verification are still required before listing.

## Step 7: Compliance Risk List

Separately from the score, output a **deterministic risk list** — no scoring, item by item.

**Copy and imagery both go through it.** Translate each frame into a declarative sentence and review that sentence by the standards applied to copy — whatever the image says, treat it as written. See rule library Layer 0, item 6.

### Asset Authenticity: AI Imagery Cannot Carry Craft Claims

AI-generated and CG-rendered images are not the physical product. Using them for visuals is fine, but **the parts that "look well made" cannot be turned into selling points** — if the manufactured item falls short, that is a mismatch with the description (Advertising Law Art. 28).

| AI / render is fine | Must be a real photo |
|---|---|
| Mood, atmosphere, scene | **Craft close-ups** (color separation, edges, seams, stitching) |
| Concept, world-building | **Material feel** (gloss, grain, texture) |
| Series composition | **Size reference** (against a hand, a desk, a familiar object) |
| Packaging preview | **Color fidelity** (buyers compare on arrival) |

High-risk categories: **designer toys** (paint fidelity — the top complaint driver), apparel (drape, true color), food (portion, plating), furniture (grain, finish).

Do not put these on an AI image without a physical sample: *fine color separation, clean edges, multi-layer paint, rich color, premium finish, high fidelity*. Fix by (1) reshooting with a sample, (2) dropping the craft copy and keeping it purely atmospheric, or (3) labeling the frame "concept image, actual product may vary".

> Pairs with Layer 0 item 6: item 6 governs **what the image says**; this one governs **whether the image is real**. Both must pass.

### Source Tiers for Online Verification

Verifying online is allowed, **but the source you cite determines how hard the conclusion can be**.

| Conclusion type | Accepted only from | Always rejected |
|---|---|---|
| Law and regulation | NPC / State Council / SAMR sites, local market-regulation bureaus, official media, Supreme Court gazette | Law-firm marketing posts, self-media explainers, **foreign-agency reprints of Chinese regulations** |
| Platform rules | **The platform's own rule center or seller-backend help center** | **Design-tool blogs (Canva, Chuangkit, etc.)**, agency sites, third-party seller portals, SEO aggregators |
| Testing and certification standards | Standard-issuing bodies, certification body sites | Product pages, supplier marketing material |

Requirements:

- Always **state the source domain** so the user can check it
- Source outside the allowlist → downgrade to "unverified reference" and note "defer to the latest rules in your seller backend"
- **If no official source can be found, say so** — never pad with third-party sites
- Platform rules change often; attach a recency caveat to any verified conclusion

> Frequent violation: citing a design-tool blog post as the authority on "Taobao main-image dimensions". That is second-hand, possibly stale, possibly wrong at the source. **Citing it disguises a 【验】 item as a 【法】 one.**

Full rules in the library, Layer 0, item 8.

| Matched text | Location | Risk type | Basis | Suggested fix |
|---|---|---|---|---|

See `references/compliance-rules.md`.

---

## Handling Missing Material

- **Never fabricate** credentials, test results, sales figures, reviews, endorsements, or certificate numbers
- A "reduced-material brief" is acceptable, with every assumption flagged
- For high-risk categories without credentials, output **visual direction and neutral copy only** — no efficacy statements
- Ask at most 3 questions, prioritizing those that change positioning or compliance

### The Same Applies to Your Own Sources

"Never fabricate" covers not only the user's material but **the evidence you cite**.

When no official source can be found, **say so**:

> I could not find an official source for this. What follows is my judgment — defer to your seller backend or professional advice.

**Do not pad authority with third-party sites.** Citing a law-firm post, a design-tool blog, an agency site, or an SEO aggregator as your basis is the same class of error as inventing a test-report number: **producing a convincing-looking substitute when the real thing is unavailable.**

Cite an official source when one exists (the rule library links them); admit there is none when there isn't. The middle path is not available.

## Failure Modes

| Symptom | Response |
|---|---|
| Scenes generated before the driver is determined | Return to Step 2 |
| User wants to skip the storyboard | Explain it is the anti-rework gate; pause unless the user explicitly says "use your judgment, go straight to final" |
| Copy produced after skipping a gate, with no task allocation or de-duplication table | **Most frequent failure.** Skipping waives the wait, not the output; return to Step 4 and supply them |
| Five main images repeat information | Reject the set and reallocate |
| A KV answers another driver's question | Rebuild against the question column; rewording is fine, changing the question is not |
| Two frames answer the same doubt | Reject the set and reallocate |
| User names the copy, so only copy is produced | **Most frequent failure.** Output the allocation (task name + category) first, then the copy |
| Only the copy was reviewed, not the imagery | Translate each frame into a sentence and review again |
| Detail-page modules stacked arbitrarily | Reorder against the driver table |
| Compliance judged by keyword matching alone | Must state risk type and basis, and must not give a final legal opinion |
| User asks for "bolder, more absolute" | Strengthen the visual, not the claim; name the line that cannot be crossed |
| Self review gives scores without locations | Treat as not performed; redo |

## Pre-Delivery Checklist

- [ ] Product type, platform, and credential scope established?
- [ ] Conversion driver determined with stated rationale?
- [ ] Style Lock complete (palette / ratio / lighting / type / no-drift)?
- [ ] Paused at Gate 1 and waited for confirmation?
- [ ] Paused at Gate 2 and waited for confirmation?
- [ ] Each frame answers the question its driver table assigns?
- [ ] Five frames remove five different doubts, none repeated?
- [ ] Detail-page modules trimmed by driver, total 6–9?
- [ ] Scene, copy, design notes, and prompt given per image?
- [ ] Five-dimension review done, every deduction located?
- [ ] Compliance risk list itemized with basis?
- [ ] Craft / material / size / color claims all backed by the physical product, none riding on AI imagery?
- [ ] Nothing fabricated — credentials, data, sales, reviews, efficacy?

---

*E-commerce Visual Planning SOP v1.0 · Default language: Chinese*
