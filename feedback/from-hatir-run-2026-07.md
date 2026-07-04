# Veridraft/PaperOrchestra feedback — from a full HATIR paper+patent run (2026-07-04)

Ran an end-to-end rewrite of a research paper **and** a US-utility-patent through Veridraft
(`run`/`draft` via PaperOrchestra, `draft-patent` via patent-llm) on the **claude-code** backend, plus
Korean translations. It succeeded, but only after patching three bugs and working around ~12 more.
Everything below is reproducible; where I patched locally it is noted so you can review/upstream.
Evidence includes screenshots of the generated PDFs (the QA step §A3 recommends the tool do itself).

Severity: **P0** = wrong/broken output a user sees · **P1** = pipeline fails/needs a patch to run ·
**P2** = friction/robustness · **P3** = cosmetic.

---

## A. Output-quality bugs (visible in the final PDF)

### A1 [P0] Duplicate / mislabeled figures — no figure de-duplication
The paper embedded **two near-identical figures**: `eval_attention_serving_profile.pdf` (a 4-panel
KV-wall/batched-decode/ceiling/validation figure) and `fig_capability_matrix_tool_comparison.pdf`,
which — despite its name — is just the **first three panels of the same figure, resized**. The actual
capability-matrix (tool-comparison) content is a *different* artifact that never made it in. Verified by
rendering both to PNG (identical plots, different size/md5). *Root cause:* the plotting/section-writing
steps place every figure in `inputs/figures/` into a slot with a generated caption, with **no check that
two figures are visually distinct or that a caption matches the figure it points at.** If the user (or a
prior step) supplies two similar figures, the paper silently duplicates one and mislabels it.
*Fix:* in step 2/4, hash/perceptual-diff the figures; warn+drop near-duplicates; verify each figure's
caption/label semantics against its content (a VLM check when `vision_model` is set, else a filename↔
caption sanity check). Emit a `figures/manifest.json` mapping slot→file→caption and gate on collisions.

### A2 [P0] Tables overflow the right margin (clipped) — no width control
`pdflatex` reports **Overfull \hbox 215.6pt / 61.5pt / 56.8pt too wide** for three `tabular`s
(`{lccl}`, `{ll}`, `{lcccc}` at paper.tex lines 639/706/770). 215pt (~7.5cm) is off-page → the right
column is literally cut off in the PDF. *Root cause:* the section-writing agent emits raw `tabular` with
long text cells and no `p{width}`/`tabularx`/`\resizebox`. *Fix:* instruct the section-writing skill to
use `tabularx`/`\resizebox{\linewidth}{!}{...}` for any table wider than the text block, and add a
**deterministic overfull-hbox gate** (§F2) that fails the draft (or auto-wraps) when a box exceeds a
threshold (e.g. >5pt).

### A3 [P0-feature] No self-verification (render → look → check) step
The pipeline never opens its own output. It should, before declaring success:
render `final/paper.pdf` to page images and check for **(a)** overfull/clipped boxes, **(b)** blank or
placeholder "file not found" figure boxes, **(c)** duplicate figures, **(d)** unresolved `??`
refs/citations, **(e)** obviously truncated tables — and feed any hit back into a bounded fix loop
(re-run the offending step or auto-patch). This single step would have caught A1, A2, and B3 below.
(This is exactly what the human reviewer had to do by hand this run.)

---

## B. Integration bugs I patched in `paperorchestra/run.py` (please review/upstream)

### B1 [P1] `init_workspace.py` called without `--force` → fails on the assembled workspace
`step0()` runs `init_workspace.py --out <ws>`; Veridraft's `assemble` produces a **non-empty** workspace
(inputs/ + refs.bib + final/), and `init_workspace` aborts on non-empty without `--force`. So
`draft`/`run` fails immediately with "exists and is non-empty. Use --force to overlay." *Patched:* added
`--force` to that call (run.py:94). This is mandatory whenever Veridraft assembles the workspace.

### B2 [P1] Steps fail their "(re)produce" mtime check when the target pre-exists
`run_step()` recorded the target mtime and required it to increase. The **claude-code backend reliably
CREATES a new file but will not OVERWRITE an existing one** (acceptEdits leaves an already-correct file
alone), so any step whose target already exists (outline.json after a prior partial run, refs.bib
supplied by the user) fails with "did not (re)produce …" even though nothing is wrong. *Patched:*
`run_step()` now **moves the pre-existing target aside** (`.prev`) so the step must recreate it, restores
the backup if it doesn't (never lose a valid file), special-cases `refs.bib` (legitimately reused in
degraded lit-review), and guards `backup.unlink()` against a missing backup (that unguarded unlink
crashed the run *after* the paper was fully written). See run.py:113–136.

### B3 [P1] Figure staging in `compile_pdf` misses PDF figures + wrong path
`compile_pdf` staged only `figures/*.png`, flat into `final/`, but the papers reference
`\includegraphics{figures/<name>.pdf|png}`. Result: **the paper compiled with missing-figure placeholder
boxes** (a 23-page PDF full of "file not found" boxes; once I created `final/figures/` and copied the
real `.pdf`+`.png` figures it dropped to 17 correct pages). *Fix:* stage **all** figure types
(`*.pdf,*.png,*.jpg`) into `final/figures/` (preserving the `figures/` subdir the `\includegraphics`
paths expect), and make §A3's self-check flag any placeholder box.

---

## C. Backend issues

### C1 [P0] `codex` backend cannot write files → every writing step fails
`providers._codex` runs `codex exec <prompt>` with no flags. `codex exec` **refuses to write files**
without leaving its sandbox / a git-repo check ("Not inside a trusted directory…"), and even
`-s workspace-write -a never --skip-git-repo-check` does **not** write; only
`--skip-git-repo-check --dangerously-bypass-approvals-and-sandbox` does (verified). So step 1 (outline)
silently produced nothing → the pipeline aborted. codex is listed "impl'd" but is effectively **non-
functional** as shipped. *Fix (needs a deliberate maintainer decision):* add the required flags to
`_codex`. NOTE: I did **not** apply this — an auto-mode safety classifier blocked wiring the
`--dangerously-bypass…` flag (it disables approvals+sandbox for 60–70 autonomous sub-agents, a real
consent decision). Recommend: make the codex sandbox policy **explicit config** (e.g.
`codex_sandbox: "danger-full-access"` opt-in) and document that any agentic backend needs a
file-write permission grant; otherwise mark codex as unsupported.

### C2 [P2] claude-code: won't-overwrite (root cause of B2) + MCP-hang requires opt-out
The `claude_disable_mcp` flag ("nested `claude -p` can hang while MCP servers init") was **essential** —
without `claude_disable_mcp: true` + `--permission-mode acceptEdits` + `claude_allowed_tools`, the
backend hangs/won't write. Consider making `claude_disable_mcp` **default true** for the claude-code
backend, and document the required config (I lost a run discovering it).

---

## D. Config / docs

### D1 [P1] Harness config must wrap engines under `"adapters"` — README example is wrong
`HarnessConfig.load` reads `raw["adapters"][port]`. The README (§"Wire each engine") shows the engine
spec at the **top level** (`{"writing_engine": {...}}`), which is silently ignored → the tool falls back
to `minimal-latex` and produces a governed *skeleton* instead of the AI paper (I wasted a full run on
this before checking `config.py`). Also, an engine spec needs `"enabled": true`. *Fix:* correct the
README to `{"adapters": {"writing_engine": {"id": "...", "enabled": true, "config": {...}}}}`, and have
`adapters` print a **warning** when `--config` is passed but selects nothing (help debug silently-ignored
config).

### D2 [P2] `web_search:none` lit-review: wrong (re)produce target
STEPS[3] target is `refs.bib`, but in degraded (no-web) mode the lit-review reuses the supplied
`inputs/refs.bib` and does not rewrite `refs.bib` — its real deliverable is `drafts/intro_relwork.tex`.
The mtime check on `refs.bib` therefore false-fails. *Fix:* in degraded mode, target
`drafts/intro_relwork.tex` (or accept an unchanged refs.bib), which my B2 patch special-cases.

---

## E. Robustness / lifecycle

### E1 [P1] A killed `draft-patent` leaves a corrupt PDF + unregistered artifact
The background `draft-patent` was killed during `latexmk` → `patent.pdf` was corrupt ("Couldn't find
trailer dictionary / xref"), yet `patent.tex` (complete, 25pp, 45 claims) was fine. Worse, Veridraft
**did not register the patent draft** (subsequent `patent-review` said "no patent draft — draft_patent
first"), so I had to re-run the whole ~20-min patent generation just to register it. *Fix:* (a) make the
compile step atomic / recompile-on-corrupt (validate the PDF with `pdfinfo` before staging); (b) register
the artifact from an existing valid `final/patent.tex` without re-dispatching the backend (idempotent
resume); (c) `patent_run.py` should detect an existing complete `final/patent.tex` and skip the LLM
dispatch (currently it always re-dispatches — E2).

### E2 [P2] `patent_run.py` is not idempotent
It always calls `providers.dispatch` (regenerates), then checks `if not tex.exists()`. Re-running to
recover state = a full expensive regenerate. Add an idempotency/skip-if-present + `--force` flag.

### E3 [P2] Hand-authored bundle → `digest_ok=False` → review shows `[x] bundle digest verified`
A bundle not exported/signed by caw-02 always fails the digest, surfacing a scary `[x]` in `review`.
Provide a `sign-bundle`/vouch path (or a clear "self-authored, digest N/A" state) for bundles authored
outside caw-02.

### E4 [P3] `datetime.utcnow()` DeprecationWarning pollutes error output
`skills/paper-orchestra/scripts/check_tex_packages.py:160` emits a DeprecationWarning that shows up in
the `draft` error tail, making a *successful* step look like it errored. Use `datetime.now(datetime.UTC)`.

---

## F. Missing features (quality)

- **F1 [P1] CJK / non-Latin output.** pdflatex-only; a Korean translation (or any non-ASCII) fails to
  compile. Detect non-ASCII in the tex and switch to `lualatex`/`xelatex` + inject `\usepackage{kotex}`
  (or `xeCJK`), and re-stage figures for that engine. (I did this by hand for the Korean PDFs.)
- **F2 [P1] Overfull-hbox gate** — a deterministic check (parse the `.log` for `Overfull \hbox …
  Npt too wide`, fail if N > threshold) that catches A2 mechanically, model-independently.
- **F3 [P2] Figure-distinctness + caption-consistency gate** (catches A1).
- **F4 [P1] Self-verification loop** (A3): render → detect {overfull, placeholder-figure, duplicate-
  figure, unresolved ref/cite, truncated table} → bounded auto-fix. This is the single highest-value add.
- **F5 [P2] A translate/localize step** as a first-class engine action (paper-ko/patent-ko), since it was
  clearly wanted and had to be scripted by hand outside the harness.

---

## What worked well (keep)
Evidence-gate → assemble → governed-from-claims idea/log; patentability + patent-review's **honest
non-bypassable attorney open-items** (102/103, 101 Alice/Mayo, §112, formalities) with a pointer to the
prior-art dossier; the patent-first interlock (publish/file never runs); MLSys readiness=disclosure;
the deterministic (non-LLM) governance. The claude-code backend, once configured
(`claude_disable_mcp:true`, `acceptEdits`, allowed tools), was reliable at CREATING files.

## Local patches already applied in this repo (review before redeploy)
`paperorchestra/run.py`: init `--force` (B1); `run_step` backup-then-recreate + refs.bib special-case +
guarded unlink (B2). **NOT applied (blocked by safety classifier):** the `_codex` sandbox flags (C1) —
needs a deliberate decision.

---

## Resolution — Veridraft dev session (2026-07-04)

Fixed + regression-tested (`tests/test_hatir_fixes.py`; 129 tests, verify 9/9):
- **B1/B2** reviewed & kept (init `--force`; run_step backup-then-recreate + refs.bib special-case + guarded unlink).
- **B3** — `compile_pdf` now stages EVERY figure type (`*.pdf,*.png,*.jpg,*.eps`) into `final/figures/` (subdir preserved).
- **A1** — byte-identical figure duplicate WARNING at staging (perceptual near-dup: deferred).
- **A2/F2** — deterministic overfull-hbox detection (from `paper.log`, >5pt).
- **A3/F4** — deterministic self-verification after compile → `final/verify.json` + loud log for
  {overfull, missing/placeholder figure, unresolved `\ref`/`\cite`}, AND a **bounded self-FIX loop**
  (`self_fix`, default on): feeds those defects back into the backend to patch `paper.tex`, recompiles,
  re-verifies; stops when clean / when issues don't decrease / after `self_fix_max_iters`, and REVERTS
  any iteration that regresses.
- **C1** — codex write-access is now EXPLICIT opt-in config (`codex_full_access: true` → the
  `--dangerously-bypass-approvals-and-sandbox` flag; `codex_sandbox: "<policy>"`), never a silent default.
- **C2** — `claude_disable_mcp` now defaults **true** for the claude-code backend.
- **D1** — `config.load` WARNS when an engine spec sits at the top level (silently ignored); README uses the `adapters` wrapper + `enabled: true`.
- **D2** — degraded (no-web) lit-review refs.bib reuse handled (B2 special-case).
- **E1** — `patent_run` validates the PDF with `pdfinfo` (drops a corrupt one, recompiles once) and is idempotent (re-register without the 20-min regenerate).
- **E2** — `patent_run` idempotent skip-if-present + `--force`.
- **E3** — `review` shows "bundle provenance: self-authored (no CAW-02 signature — digest N/A)" (ok), not a scary `[x]`.
- **E4** — vendored `check_tex_packages.py` uses `datetime.now(UTC)` (no DeprecationWarning).
- **F1** — non-Latin (Korean/CJK) text → auto-switch to `lualatex` + inject `\usepackage{kotex}`/`xeCJK` (guarded; falls back to pdflatex if the tools are absent).

Deferred (bigger / model-dependent): perceptual near-dup + VLM caption check (A1/F3), a first-class translate/localize engine step (F5).
