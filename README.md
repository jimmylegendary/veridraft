# Veridraft

**Evidence-gated authoring for research papers & patents.** Veridraft turns *verified claims +
admissible evidence + real results* into a paper or a US-utility-patent draft — and refuses to
let anything ship that it cannot ground. It is the **governance harness** — an evidence gate, a
patent-first interlock, fail-closed confidentiality, submission-readiness, an AI reviewer, and a
tamper-evident audit ledger — **that wraps a writing engine**; it does not reimplement the AI
author. See **[What's in the box vs what you connect](#whats-in-the-box-vs-what-you-connect)**.

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

## What's in the box vs what you connect

Veridraft is the **governance + wiring**, not the AI author. Document generation comes in two tiers:

| Tier | Engine (in this repo) | Produces | Needs |
|---|---|---|---|
| **Self-contained (default)** | `minimal-latex` (paper), `minimal-patent` (patent) | A real, openable, **deterministically templated** PDF assembled from your gated claims — a valid document *skeleton* (patent: independent method/system/CRM claims + a dependent ladder + spec + abstract), fully governed. Not AI-authored prose. | **Nothing** — Python stdlib only, offline. |
| **AI-quality (opt-in)** | `paperorchestra` (paper) + the vendored **PaperOrchestra skills in [`skills/`](skills/)** (MIT); `patent-llm` (patent) + `paperorchestra/patent_run.py` | Full AI-written manuscripts (PaperOrchestra's 5-agent pipeline: outline → plotting → verified lit-review → section-writing → refinement) / an AI-drafted patent application. | Only an **LLM backend** you configure — a self-hosted OSS API (`base_url`/`api_key`/`model`), `claude-code`, `openclaw`, or `codex`. The PaperOrchestra pipeline is now **vendored** (no separate install); the *model* is what you connect. |

So: **`python -m veridraft run …` / `draft-patent` work out of the box and emit a governed PDF**;
the *high-quality AI writing* is the vendored PaperOrchestra pipeline driven over a model you
connect. Every governance guarantee below holds identically for both tiers — the harness is what
this project is. See [Third-party notices](NOTICE) for the vendored MIT components.

## Paper pipeline: deterministic contract vs model-dependent quality

The design principle: **which steps run, in what order, with what I/O contract and gates, is
deterministic** (guaranteed regardless of model); **only the content quality of each step scales
with the connected model/agent.**

| Step / feature | Deterministic (always runs · contract · gate) | Model-dependent (quality) |
|---|---|---|
| 0. Input validation | idea.md · experimental_log.md · template · guidelines required; strict experimental-log structure; TeX-package probe | — |
| 1. Outline | exactly 1 call; output must be a **schema-valid `outline.json`** (invalid ⇒ pipeline HALTS); carries plotting/lit/section plans | outline structure / framing |
| 2. Plotting (2‖3) | per-figure; **real matplotlib render** (not hallucinated); numbers ONLY from experimental_log §2; fixed VLM critique loop | figure design / clarity (needs a `vision_model`, else degrades) |
| 3. Literature review (2‖3) | web-search discovery → **every citation verified against Semantic Scholar** (title match, cutoff, dedup); unverifiable citations dropped/TODO | discovered-literature relevance & prose (needs `web_search`, else degrades) |
| 4. Section writing | 1 multimodal call; **numbers → booktabs tables verbatim** (no invented numbers); figures spliced; then gates: orphan-citation, latex-sanity, anti-leakage | prose quality of each section |
| 5. Refinement | bounded loop with **strict halt rules** (iter cap; revert on score drop / net sub-axis regression; stop when no new weakness); each iter snapshotted | sharpness of critique / degree of improvement |
| 6. Compile & provenance | latexmk `-no-shell-escape` → PDF; input/output-hash `provenance.json` | — |
| anti-leakage | verbatim leakage-prevention prompt prepended to **every** writing call | — |

Wrapping all of it, **Veridraft's own deterministic layer** (identical for any model): evidence
gate + `require_results`, assemble (only gated claims + real results reach the engine), the runner's
step contract (order · "a step must emit its artifact" · 2‖3 · degrade modes), egress
(`decide()` + redaction re-sweep over the PDF), patent-first interlock, submission-readiness, and
the tamper-evident ledger.

## What it guarantees

| Gate | Guarantee |
|---|---|
| **Evidence gate** | A claim drafts only if it carries *admissible* evidence — a typed, resolvable ref (`source_artifact = path@commit`, `caw01://result`, `caw02://evidence`). **Generated/prose text is never evidence** — the one invariant no config can relax. `require_results` refuses to draft an evaluation from an empty or valueless result set. |
| **Patent-first interlock** | A patent-sensitive (future-device) claim is HELD by default; a paper cannot disclose it without a human `release-interlock` (after filing). The hold is durable — it survives a bundle re-import, a claim relabel, and a dropped-claim re-import (fail-closed). |
| **Confidentiality (egress)** | `boundary`(public⊂internal⊂confidential) × `visibility`(team\|private), fail-closed. Two-point enforcement: ingest classify + egress `decide()` + a redaction re-sweep over the actual PDF text. An image-only/empty-text PDF is refused (can't verify it); the audience is clamped to the sink's tier. |
| **Submission-readiness** | Maps a venue's LLM policy → the required AI-use **disclosure** text; **hard-blocks** publish to venues that *prohibit* AI-generated text until a human signs off — and the sign-off is bound to the exact draft, so a re-draft can't reuse it. Never helps evade an AI ban. |
| **Review governance** | A deterministic review-readiness checklist (claims gated, PDF present, labels) + a venue-rubric registry (`venues.json`) + a review-record contract with **stub/placeholder rejection**. The AI peer-review *panel* itself is an external LLM engine you drive (like the writing engine); the harness records, validates, and audits its output. |
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

## Connecting an AI model (papers **and** patents)

Only the LLM steps need a model; everything else is model-free Python. Both the paper runner
(`paperorchestra/run.py`) and the patent runner (`paperorchestra/patent_run.py`) drive **the same
four backends from one `backend.json`** (see [`paperorchestra/config.example.json`](paperorchestra/config.example.json)):

| `backend` | How it runs | Model source |
|---|---|---|
| `api` | self-hosted OpenAI-compatible endpoint via an OSS agent CLI (`opencode`/`aider`) | `base_url` + `api_key` + `model` (vLLM/Ollama/TGI serving Qwen/Llama/…) |
| `claude-code` | `claude -p` headless | the Claude CLI's model |
| `openclaw` | `openclaw agent --json` | openclaw's provider (e.g. gpt-5.5) |
| `codex` | `codex exec` | codex / OpenAI |

```jsonc
// backend.json — the same file for papers and patents
{ "backend": "api", "base_url": "http://localhost:8000/v1", "api_key": "env:LLM_API_KEY",
  "model": "Qwen/Qwen2.5-72B-Instruct", "api_driver": "opencode",
  "vision_model": null, "web_search": "none" }   // vision/web optional → those steps degrade
```

Wire each engine to its runner via the harness config (`po_command` / `patent_command`):

```jsonc
"adapters": {                                          // MUST be nested under "adapters" —
  "writing_engine": { "id": "paperorchestra", "enabled": true, "config": { "po_command":
      ["python3","/abs/paperorchestra/run.py","--config","backend.json","--workspace","{workspace}"] } },
  "patent_engine":  { "id": "patent-llm", "enabled": true, "config": { "patent_command":
      ["python3","/abs/paperorchestra/patent_run.py","--config","backend.json","--workspace","{workspace}"] } }
}                                                     // a top-level spec is IGNORED (warns on load)
```

- **Papers:** the vendored PaperOrchestra pipeline runs over your backend (`bash paperorchestra/setup.sh`
  checks TeX / matplotlib / the backend). Vision + web-search are optional; without them the plotting
  critique and lit-review degrade gracefully.
- **Patents:** there is **no external "PaperOrchestra for patents"** — Veridraft's own engines fill
  that gap. `minimal-patent` needs **no model** (deterministic); `patent-llm` drives the same backends
  through `patent_run.py`, and `patent-review` gates the result. Veridraft **never files.**

Governance is model-free, so the model is a quality/cost choice. Full guidance (Claude Max tiering
vs OSS self-host, the optional local Binoculars AI-text RISK detector) in
[`docs/GUIDE.md`](docs/GUIDE.md).

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
