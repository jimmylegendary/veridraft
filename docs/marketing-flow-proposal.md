# Proposal: Evidence-gated Marketing post-processing (SEO / GEO / amplification)

**Status:** for review. **Tagline fit:** "No *marketing* claim ships without a warrant."

## Why this belongs in Veridraft (and nowhere else)

A paper only matters if it is found, read, and cited. Good research marketers amplify reach through
SEO (Google Scholar / web discoverability), GEO (getting cited by LLM answer engines), plain-language
summaries, social threads, and conference buzz. But academic marketing has an inversion vs ordinary
marketing: **a false or inflated claim is catastrophic** — a reviewer, a journalist, or a replication
kills the paper (and the author's credibility). So the winning move is *aggressive amplification
bounded strictly by the evidence.*

That bound is exactly what Veridraft already enforces for the manuscript. The research we did
confirms the levers of honest amplification are the SAME primitives Veridraft is built on:
- SEO best practice = **accurate** title/abstract, key finding in the first 1–2 sentences, consistent
  terminology, complete metadata — *not* keyword stuffing or writing for the algorithm.
- GEO (KDD'24) shows the biggest visibility lift comes from **statistics addition, quotation/citation,
  and extractable structured passages** — i.e. concrete evidence, which an evidence-gated paper has.

So a marketing stage is not a bolt-on; it is the evidence gate pointed at a new output shape.
**Every other AI marketing tool inflates; Veridraft's would be the one that provably cannot.**

## The flow (a post-processing stage after draft → gate → compile)

`market_run.py` (a runner sibling of `run.py` / `translate_run.py`) takes the compiled paper **and its
gated claim bundle** and emits a **Marketing Kit**, every artifact generated ONLY from gated claims:

1. **SEO metadata** — accurate title variants; a discoverability-tuned abstract (finding first, key
   terms front-loaded, consistent terms); 5–8 keywords; Google Scholar / Highwire / Dublin Core meta
   tags; schema.org `ScholarlyArticle` JSON-LD; ORCID/ROR slots.
2. **GEO / LLM-citability pack** — a structured, extractable "Key Findings" block (exact numbers from
   the gated results), one-sentence quotable claims each tagged with its evidence id, a TL;DR, a FAQ —
   formatted for passage-level extraction/citation by answer engines.
3. **Plain-language summary** — lay significance statement ("why it matters"), jargon-free, every
   sentence still traceable to a gated claim.
4. **Amplification pack** — an X/LinkedIn thread + hook, a blog/project-page draft. The hook may be
   attention-grabbing in FRAMING; it may not be inflated in FACT.
5. **Conference kit** — teaser headline, elevator pitch, hallway one-liner, suggested venues /
   communities / hashtags to share in.

## The load-bearing part: the marketing gate (deterministic, model-independent)

A new `marketing_lints.py` (mirrors `core/lints.py`) runs on every artifact before it can leave:

- **Claim→evidence binding.** Each marketing sentence maps to a gated claim/result; a sentence
  asserting something with no backing gated claim is **BLOCKED** (the evidence gate, reused).
- **Hype / over-claim linter.** Flags absolutes & unbacked comparatives — "first ever", "solves",
  "revolutionary", "proves", "guarantees", "state-of-the-art" (unless the results table supports it),
  bare "outperforms" without the number. Each must be removed, softened, or bound to a specific result.
- **Number fidelity.** Any statistic in the kit must match a gated result verbatim (no rounding-up,
  no cherry-picking a sub-metric the paper doesn't headline).
- **Egress reuse — the big win.** The kit is *external-facing*, so it goes through the existing
  **confidentiality `decide()`** (don't leak a confidential/internal result or codename into a public
  tweet), the **patent-first interlock** (don't publicize a patent-sensitive claim before filing),
  and **submission-readiness** (respect venue embargo / AI-disclosure). These already exist.

Same split as the rest of the product: **copy quality scales with the model; nothing that over-claims
or leaks can ship, on any model.**

## Proposed build order

- **P1 (deterministic scaffold, no model):** `marketing_lints.py` (hype linter + number-fidelity +
  claim→evidence binder) + a `market` op that assembles a metadata/JSON-LD/meta-tags kit from the
  gated bundle deterministically + egress reuse. Testable, ships value with zero model.
- **P2 (model-driven copy):** `market_run.py` over the same backends (thread, plain-language summary,
  GEO pack), each artifact passed through the P1 gate + a self-check loop (like the paper self-fix).
- **P3:** SEO/GEO measurement hooks (keyword coverage report, extractability score), venue-specific
  templates, and a `docs/GUIDE.md` "Localize + Market" section.

## Open questions for review
1. Scope now: P1 only (gate + deterministic kit), or P1+P2 (add the model-driven copy)?
2. Artifact priority — which of the 5 kits matter most to you first?
3. Should the marketing gate be **blocking** (like publish egress) or **advisory** (warn, human ships)?
