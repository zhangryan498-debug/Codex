# E-Commerce Visual Planning Skill

Turns product information into a main-image and detail-page package a designer can start from: scene descriptions, in-image copy, design notes, generation prompts, and a compliance risk list.

[中文](README.md)

---

## The Problem

Ask an AI to "write main-image copy" and you usually get three things: stacked selling points, copy-pasted spec sheets, and superlatives that will not pass platform review.

This skill adds three constraints:

1. **Determine the conversion driver before designing anything.** Cosmetics and power drills do not share a main-image logic.
2. **Two confirmation gates.** One after the strategy brief, one after the storyboard — wrong direction gets caught while it is still one line of text.
3. **Compliance judged against the legal baseline,** with each rule labeled by source: statute, derived practice, or unverified.

## How It Differs

| | Common approach | This skill |
|---|---|---|
| Main-image tasks | One fixed 5-frame template | **Allocated by driver** — four drivers, four allocations |
| Detail-page modules | All 9, always | **Trimmed by driver**, kept to 6–9 |
| Self review | One overall score | Five independent dimensions, **any below 80 is rewritten alone** |
| Deductions | Score only | **Must cite a location**, or the review is void |
| Compliance | Keyword blocklist | Layered rule library with **source labels** |

## Install

```bash
npx skills add forest-USTC/ecommerce-visual-planning-skill
```

Or drop it into your skills directory:

```bash
git clone https://github.com/forest-USTC/ecommerce-visual-planning-skill.git \
  ~/.claude/skills/ecommerce-visual-planning
```

Works with Claude Code, Codex, Cursor, and other agents supporting custom skills.

## Usage

```
Make me a main-image and detail-page set. Product is X, platform is Taobao.
```

It asks for the critical missing information first (at most 3 questions), then runs the full flow.

**You will hit two pauses** — by design:

- First: confirm the conversion driver and Style Lock
- Second: confirm main-image allocation and detail-page module order

To skip them, say "use your judgment, go straight to final."

> **Note on language:** default output is Chinese, since the compliance rules target Chinese e-commerce platforms. Ask for English or bilingual explicitly if you need it.

## Output

```
1. Strategy recap (driver / Style Lock / compliance boundaries / assumptions)
2. Five main images  — each: scene + in-image copy + design notes + prompt
3. Detail-page modules — each: scene + in-image copy + design notes + prompt
4. Five-dimension self review (with deduction locations and fixes)
5. Compliance risk list (match + risk type + basis + suggested fix)
6. Information still needed
```

## Compliance Library

[references/compliance-rules.md](references/compliance-rules.md), layered by category:

- **Layer 0, universal**: superlatives, evidence requirements, competitor comparison, medical implication, effect hedging
- **Category layers**: ordinary food (strictest) / health food / cosmetics / fitness equipment / electronics

Each rule carries a source label:

| Label | Meaning |
|---|---|
| 【法】 | Written into statute |
| 【推】 | Derived practice |
| 【验】 | Platform detail requiring verification — **not treated as fact until verified** |

Platform-specific rules are mostly 【验】 today. The right way to fill them in is with **your own rejection cases**, not guesswork.

## Boundaries

This skill does **not** do the following, and should not be pushed to:

- Issue legal opinions or replace platform review
- Fabricate test reports, patents, approval numbers, sales figures, reviews, or certifications
- Manufacture efficacy claims for products without credentials
- List or advertise products automatically

**The compliance score in the self review is a structured self check, not a legal opinion.** Platform pre-review and credential verification are still required.

## Verify

```bash
python3 tools/verify_skill.py
```

Checks file completeness, frontmatter, all four drivers, and that both gate pause lines survive.

## License

MIT
