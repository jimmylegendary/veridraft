# Veridraft

**Evidence-gated authoring for research papers & patents.** Veridraft turns *verified claims +
admissible evidence + real results* into a paper or a US-utility-patent draft — and refuses to
let anything ship that it cannot ground. It wraps an AI writing engine (PaperOrchestra) and an
AI reviewer with the governance the model does **not** provide: an evidence gate, a patent-first
interlock, fail-closed confidentiality, submission-readiness, and a tamper-evident audit ledger.

> **No claim ships without a warrant.** Generated text is never evidence; every drafted claim
> traces to a typed, resolvable artifact, and the tool would rather refuse than fabricate.

The governance is **model-free** — the gate, ledger, confidentiality decision, redaction re-sweep,
patent-first interlock, lifecycle audit, and the readiness gates are pure Python with no LLM. So
choosing a model is a *quality/cost* decision, not a *safety* one: the guarantees hold no matter
which model writes the prose.

---

## Why it exists

An AI writing engine will happily write a confident, well-formatted paper around claims it made
up, cite work it didn't verify, disclose a patentable invention before you file, or leak an
internal codename into a public PDF. Veridraft is the **harness around the engine** that makes
those failures structurally impossible — or at least loud, audited, and fail-closed.

## What it guarantees

| Gate | Guarantee |
|---|---|
| **Evidence gate** | A claim drafts only if it carries *admissible* evidence — a typed, resolvable ref (`source_artifact = path@commit`, `caw01://result`, `caw02://evidence`). **Generated/prose text is never evidence** — the one invariant no config can relax. `require_results` refuses to draft an evaluation from an empty or valueless result set. |
| **Patent-first interlock** | A patent-sensitive (future-device) claim is HELD by default; a paper cannot disclose it without a human `release-interlock` (after filing). The hold is durable — it survives a bundle re-import, a claim relabel, and a dropped-claim re-import (fail-closed). |
| **Confidentiality (egress)** | `boundary`(public⊂internal⊂confidential) × `visibility`(team\|private), fail-closed. Two-point enforcement: ingest classify + egress `decide()` + a redaction re-sweep over the actual PDF text. An image-only/empty-text PDF is refused (can't verify it); the audience is clamped to the sink's tier. |
| **Submission-readiness** | Maps a venue's LLM policy → the required AI-use **disclosure** text; **hard-blocks** publish to venues that *prohibit* AI-generated text until a human signs off — and the sign-off is bound to the exact draft, so a re-draft can't reuse it. Never helps evade an AI ban. |
| **AI reviewer** | A venue-specific simulated peer review + autoraters → quality score, verdict, ranked weaknesses, improvement guidance. Stub/placeholder reviews are rejected. |
| **Audit ledger** | Hash-chained lifecycle events (JSON-encoded, HEAD-anchored) + a state-consistency check, so tampering with a state, truncating the log, or deleting the anchor is detectable. |

## Patent path

A separate `PatentEngine` (not the paper engine) drafts a structured application: a
**patentability screen** (102/103/101/enablement; a future-device claim → requires-enablement-review;
a no-go blocks drafting) → independent **method / system / CRM** claims + a dependent ladder + a
specification with antecedent basis + abstract + `open_items` → a USPTO-style PDF. `needs_human =
True` is fixed: **Veridraft never files.** Deterministic `minimal-patent` (zero-dep) or
backend-driven `patent-llm`.

---

## Quickstart (zero dependencies)

The core runs on the **Python standard library only** — no pip, no LaTeX, no LLM, no network:

```bash
git clone https://github.com/jimmylegendary/veridraft && cd veridraft
python -m veridraft run examples/bundle_demo/bundle.json \
  --template examples/bundle_demo/template.tex \
  --guidelines examples/bundle_demo/conference_guidelines.md --audience public
bash verify.sh          # compile + full test suite + acceptance scenarios (all green)
```

## The op-manifest (same governed ops behind CLI / API / MCP)

```
import-bundle → gate → assemble → draft → review → readiness → publish        # paper
              gate → patentability → draft-patent → patent-review              # patent
release-interlock (human) · reviews · events · status · adapters · venues
```

Real paper from a repo (the "code + design docs → paper" case): aggregate the repo into a
governed bundle (claims backed by `source_artifact` evidence, only real numbers), gate it, assemble
inputs, run the **PaperOrchestra** pipeline over `workspace/inputs/`, then `publish` (the egress
gate scans the produced PDF). See [`docs/GUIDE.md`](docs/GUIDE.md).

## Choosing / connecting an AI model

Only three parts use an LLM — repo→bundle aggregation, the PaperOrchestra writing pipeline, and
the AI reviewer; everything else is model-free Python. The portable runner in
[`paperorchestra/`](paperorchestra/README.md) drives **any backend from one config** — a
self-hosted OpenAI-compatible endpoint (`base_url`/`api_key`/`model`), `openclaw`, `claude-code`,
or `codex` — so the same skills work with in-house OSS models or a Claude subscription. Governance
is model-free, so the choice is quality/cost. Full guidance (Claude Max tiering vs OSS self-host,
optional local Binoculars AI-text RISK detector) in [`docs/GUIDE.md`](docs/GUIDE.md).

## Architecture

Hexagonal **ports & adapters**: `source`, `writing_engine`, `patent_engine`, `sink`, `novelty`,
`detector` are swappable adapters behind typed ports, selected by config; the core depends only on
the ports. A documented stub is registered + discoverable but refuses to run while marked `stub`.

## Assurance

The governance layer was hardened over **7 rounds of adversarial multi-agent review** ("does a bad
artifact slip through?"). Each confirmed fail-open, crash, or security issue was closed with a
regression test that pins its exploit; the load-bearing invariants (evidence admissibility,
patent-first interlock, fail-closed confidentiality, ledger integrity, `-no-shell-escape`) are
robust, while the natural-language claim/prose lints are labelled **advisory heuristics**, not
adversarial-proof. `tests/` holds every repro.

```bash
python -m unittest discover -s tests    # 123 tests
```

## Status & license

v1 vertical slice — the governance spine is complete and hardened; filing, live prior-art search,
and OCR-based image scanning are documented future adapters. **Proprietary — © 2026 Jimmy. All
rights reserved.** (Originated as the CAW-03 product in a private monorepo.)
