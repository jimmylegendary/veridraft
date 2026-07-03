# Veridraft Operator Guide — install, principles, usage, and model selection

Veridraft (Paper & Patent Writing Harness) turns evidence-gated claims + real results into
papers/patents by wrapping an AI writing engine (PaperOrchestra) and an AI reviewer with
governance that the models do not provide. This guide covers what to install, how it
works, how to use it, and — most importantly for a company deployment — **which AI model
to use**.

---

## 1. Principles (why it behaves the way it does)

- **Evidence gate.** A claim can be drafted only if it carries *admissible* evidence — a
  typed, resolvable ref to a concrete artifact (`caw02_evidence`, `caw01_result`,
  `source_artifact` = a repo file/design-doc/test-result at a commit). **Generated text is
  never evidence** — the one invariant no config can relax.
- **No fabrication.** The tool **requires real experimental results** to draft a paper
  (`require_results`); it will refuse rather than invent an evaluation. Numbers in the
  paper are verbatim from the experimental log; the writing engine is forbidden to invent
  them; the egress gate re-scans the output.
- **Governance is model-free.** The gate, ledger, confidentiality `decide()` + redaction
  re-sweep, patent-first interlock, lifecycle audit, and the deterministic paper gates
  (citation/orphan, anti-leakage) are **pure Python — no LLM**. This is the key property
  for model choice (§4): the guarantees hold no matter which model generates the prose.
- **Ports & adapters.** Sources, the writing engine, patent engine, sinks, and novelty are
  swappable adapters behind typed ports; the core depends only on the ports.
- **Confidentiality.** CAW-02 `boundary`(public⊂internal⊂confidential)×`visibility`(team|
  private) labels, fail-closed; two-point enforcement (ingest classify + egress
  `decide()`+redaction re-sweep). A public sink is hard-blocked for non-public content.
- **AI reviewer.** A venue-specific simulated peer review + autoraters produce a quality
  score, verdict, ranked weaknesses, and improvement guidance — captured as a hash-chained
  audited artifact (`veridraft reviews`).

---

## 2. Installation requirements

**Required**
- **Python ≥ 3.10.** The harness core runs on the **standard library only** — no pip
  packages needed to gate, assemble, publish, review-capture, or audit.

**Optional (graceful degradation — the core degrades cleanly if these are absent)**

| Capability | Needs | If missing |
|---|---|---|
| Real LaTeX PDF | `tectonic` **or** `pdflatex`+`latexmk`+`bibtex` (TeX Live) | falls back to a built-in dependency-free PDF writer |
| Real figures/plots | `matplotlib`, `numpy` (the plotting agent creates a venv if pip is PEP-668 locked) | plotting step cannot render |
| Egress PDF scan | `pdftotext` (poppler) **or** `pypdf` | egress **fails closed** on an unscannable PDF deliverable |
| Defense-in-depth invariants | `cue` binary | CUE vet is skipped (Python gate stays authoritative) |
| Literature verification | network → Semantic Scholar / OpenAlex | lit-review step cannot verify citations |
| Local AI-text RISK detector (`binoculars-local`) | a **separate** CPU venv with `torch`+`transformers` (see below) | `readiness` runs without a detector band (OFF by default) |
| **The AI engine (writing + review + aggregation)** | an LLM — see **§4** | those steps cannot run; the model-free core still works |

Install (optional editable): `pip install -e .` (no runtime deps). Or just run
`python -m veridraft ...` from `impl/`.

**Optional AI-text RISK detector (local, CPU-only, no LLM API).** The `binoculars-local`
detector keeps the harness core stdlib-pure by putting the heavy deps in a **separate
venv** and shelling out to `veridraft/scripts/binoculars_score.py`. Set it up once
(respecting PEP-668 — never system pip):

```bash
python3 -m venv ~/.veridraft-detector-venv
~/.veridraft-detector-venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu transformers \
  || ~/.veridraft-detector-venv/bin/pip install torch transformers   # fallback if index lacks your Python tag
```

Enable it via `adapters.detector = {"id":"binoculars-local","enabled":true}` (see the
README section *Optional: local AI-text RISK detector*). It emits a false-flag **RISK**
band (never a verdict), uses a **locally recalibrated** threshold
(`veridraft/detector_calibration.json`, because a Qwen-0.5B pair is far weaker than the
paper's Falcon-7B), and never sends a manuscript off-box. Not installed here?
`readiness` still works — it just reports no detector band.

---

## 3. Usage

The op-manifest (same governed ops behind CLI / API / MCP):

```
import_bundle → run_gate → assemble_inputs → draft → run_review → publish
                              ↑ requires results        ↑ egress gate (confidentiality + redaction)
release_interlock (human)   ai-review --venue V   reviews   events   status   adapters   venues
```

Offline slice (zero deps, deterministic — proves the governance spine):
```bash
python -m veridraft run examples/bundle_demo/bundle.json \
  --template examples/bundle_demo/template.tex \
  --guidelines examples/bundle_demo/conference_guidelines.md --audience public
bash verify.sh          # compile + full test suite + acceptance scenarios (all green)
```

Real paper from a repo (the "code + design docs → paper" case):
1. Aggregate the repo into a governed bundle (claims backed by `source_artifact` evidence,
   results = only real repo numbers). *(LLM step — the aggregator.)*
2. `import-bundle` → `gate` → `assemble` (the tool refuses if there are no results).
3. Run the **PaperOrchestra** pipeline over the assembled `workspace/inputs/` *(LLM step)*.
4. `publish` — the egress gate scans the produced PDF, then publishes.
5. `ai-review --venue <V>` → `reviews <bundle>` for the quality assessment + guidance.

Config: `--config veridraft.config.json` selects a **gate profile** (`neurips-paper`,
`systems-paper` — venue-shaped evidence thresholds) and adapters. `veridraft venues` lists the
**AI-reviewer target venues** (rubric/bar per venue), grouped by domain.

---

## 4. Choosing an AI model  ← read this

**Only three parts use an LLM:** (i) repo→bundle aggregation, (ii) the PaperOrchestra
writing pipeline (~60–70 model calls/paper), (iii) the AI reviewer (~5 agents/review).
Everything else — every governance guarantee — is model-free Python. So model choice is a
**quality/cost** decision, not a **safety** one: the honesty guarantees hold regardless.

Steps ranked by how much model strength matters:

| Step | Reasoning demand | Notes |
|---|---|---|
| Section writing, meta-review, outline, aggregation | **high** | long-horizon reasoning; use the strongest model |
| Literature review (Semantic Scholar verification) | medium | wall-clock bound by 1 QPS rate limit, not by model cost |
| Content refinement (peer-review loop) | medium–high | judgment quality matters |
| Plotting code, input validation, formatting | low | mechanical; a cheap/fast model is fine |

### Scenario A — Claude CLI, $100/month per person

This is the **highest-quality** path (the writing + reviewer are Claude Code skills, so
they run natively). `$100/mo` ≈ the **Claude Max (5×)** subscription, which is usage-limited
rather than per-token billed.

- **Model tiering to stay in budget:** use **Opus 4.8** (`claude-opus-4-8`) for the
  high-reasoning steps (outline, section-writing, content-refinement, meta-review,
  aggregation); use **Sonnet 5** (`claude-sonnet-5`) for lit-review + most agents; use
  **Haiku 4.5** (`claude-haiku-4-5-20251001`) for mechanical steps (validation, formatting,
  plotting code). This keeps the expensive tier on the ~5–8 calls that actually need it.
- **Throughput expectation:** a full paper is ~60–70 calls / ~40 min wall-time (lit review
  dominates the clock, not the cost). On a Max plan, budget roughly **a few full papers +
  several reviews per person per month**; run heavy pipelines off-peak and **resume**
  cached workspace steps instead of re-running.
- **If it's $100/mo of metered API instead of a subscription:** put Opus only on the
  quality-critical calls and Sonnet/Haiku everywhere else — the tier split above is what
  keeps a metered cap viable. (For exact current pricing/limits use the `/claude-api`
  reference; model tiers/prices move.)

### Scenario B — Open-source, self-hosted models

The harness core runs **unchanged** (no LLM). Serve an OSS model behind an
OpenAI-compatible endpoint (**vLLM / SGLang / TGI / Ollama**) and point the LLM steps at it.

- **Recommended models (verify current SOTA at deploy time):** for the high-reasoning
  steps use a strong reasoning/coding model — **DeepSeek-V3 / DeepSeek-R1**,
  **Qwen2.5-Coder-72B / Qwen3**, **Llama-3.3-70B**, or **Mistral-Large**. Use a smaller
  instruct model (8–34B) for the mechanical steps.
- **Novelty/lit layer without a frontier model:** the OSS-foundation research already
  picked **PaperQA2** (Apache-2.0) + **OpenAlex/pyalex** for related-work; PaperQA2 runs on
  local models (Ollama) + local **sentence-transformers** embeddings — no external API.
- **Quality caveat — and why it's safe anyway:** OSS models are weaker at long-horizon
  multi-agent writing, strict no-fabrication adherence, and citation-verification
  discipline, so expect lower paper quality and **more gate failures**. That is by design:
  the **deterministic, model-free gates** (evidence gate, `require_results`, citation/orphan
  gate, anti-leakage, confidentiality egress re-sweep) **catch the slips a weaker model
  makes** — they never trust the model. A weaker model produces a weaker (or rejected)
  paper; it cannot produce a *dishonest* one.

### Recommendation matrix

| Step | Scenario A (Claude, $100/mo) | Scenario B (OSS self-host) |
|---|---|---|
| Aggregation, outline, section-writing, meta-review | **Opus 4.8** | DeepSeek-R1 / Qwen3 / Llama-3.3-70B |
| Lit review, refinement, per-reviewer | **Sonnet 5** | DeepSeek-V3 / Qwen2.5-72B (+ PaperQA2 local) |
| Plotting, validation, formatting | **Haiku 4.5** | 8–34B instruct model |
| Every governance gate | **none (Python)** | **none (Python)** |

**Bottom line:** pick the model your budget/policy allows — Claude Max on the strong steps,
or a self-hosted 70B-class model. The guardrails don't move. If the model is weaker, you
get a weaker paper that the gates keep honest; you never get a fabricated evaluation.
