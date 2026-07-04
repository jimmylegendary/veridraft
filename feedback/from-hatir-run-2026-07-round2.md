# Veridraft/PaperOrchestra feedback — HATIR run, round 2 (2026-07-04)

Second pass after round-1 fixes shipped (self-verify+self-fix, figure staging, CJK, etc.). Those
worked (figures now distinct, the 215pt table clip gone). New issues below, all verified by rendering
the PDFs + reproducing. The user's standing rule: **the tool must be fixed, not the output** — so this
is structured feedback, not a local patch. Severity: P0 user-visible · P1 pipeline · P2 robustness.

---

## G1 [P0] Body citations render as `[?]` — compile fragility + wrong self-verify signal
**Symptom:** many `[?]` where `\cite{...}` should resolve.
**Reproduction / root cause (three compounding facts):**
1. The paper uses `natbib` + `\bibliography{refs}` + `hyperref`. Resolving `\cite` requires a **bibtex
   pass** (pdflatex → bibtex → pdflatex ×2). `compile_pdf` runs `latexmk`, which *does* invoke bibtex —
   **but only if the whole multi-pass completes.** If any pass errors first (e.g. a missing figure in an
   earlier run, an injected `.tex` issue, a timeout), latexmk stops before/around the bibtex+rerun cycle
   → **no `.bbl`** → every `\cite` becomes `[?]`. (Confirmed: with figures present, latexmk produces
   `paper.bbl` and 0 `[?]`; the earlier run that had missing figures produced `[?]`.) So the citation
   breakage is a **silent side-effect of any compile hiccup**, and there is no guard that the final PDF
   actually resolved its citations.
2. **The self-verify signal is wrong for this.** `_verify_compile` greps the **compile LOG** for
   `Citation .* undefined`. But in a *correct* multi-pass latexmk build the **first pdflatex pass always
   emits those warnings** (before bibtex runs) — I measured **164 "Citation undefined" lines in the log
   of a build whose final PDF has 0 `[?]`.** So the check is a **false positive on every healthy build**
   and, conversely, can miss the real thing. It should judge the **final artifact**, not the log.
3. **self-fix can't fix a compile-pipeline problem via a text edit.** When the defect is "bibtex never
   ran," feeding "unresolved \cite — add the missing bib entry / remove the dangling reference" to the
   backend is wrong: there is nothing to edit; the fix is to *run bibtex*. Worse, an over-eager backend
   may delete `\cite`/`\bibliography` to "resolve" the phantom defect, which would *cause* the very `[?]`
   the user saw.
**Fixes:**
- In `compile_pdf`, run an explicit **`pdflatex → bibtex → pdflatex → pdflatex`** (or `latexmk -pdf
  -bibtex` with a completion check), and after it, **assert `final/paper.bbl` exists and non-empty** when
  the `.tex` has `\bibliography`/`\cite`; fail the compile (not silently ship) otherwise.
- Change `_verify_compile` to detect unresolved refs/cites by **`pdftotext final/paper.pdf` and grep for
  literal `[?]` / `??`** (final-artifact truth), not by grepping the intermediate `.log`.
- In `_self_fix`, **route "unresolved-cite because bibtex didn't run" to a recompile action, not a
  backend text edit**; only route genuine dangling-key/`\label` cases to the backend.

## G2 [P0] Citations should be clickable (hyperref) — free once G1 is fixed, but add a positive check
`hyperref` + `natbib` already make `\cite` a hyperlink to the bibliography entry — **but only when the
citation resolves** (an unresolved `[?]` has no link). So fixing G1 makes them clickable. Recommend the
self-verify additionally **assert the PDF has `/Link` annotations** (e.g. `pdffonts`/`pdfannots` or a
`\hyperref` count) so "clickable cross-references" becomes a checked property, not an assumption.

## G3 [P1] Generated diagram figures: tight spacing, cramped arrows, and a LaTeX-escape leak
Rendered the 9 figures. The **plots** (serving, memory-device DSE, ZigZag, fabric) are clean. The
**diagram** figures have layout defects the user flagged ("화살표 겹침 / 간격 타이트"):
- `fig_hardware_twin_levelgraph.png`: the LevelStack boxes are stacked with **near-zero gap** and the
  arrows are crushed between them; the bottom Mesh-peer `↔` arrow is cramped between two touching boxes.
- **LaTeX-escape leak into matplotlib text:** the caption reads **`fabric\_reduce`** (literal backslash-
  underscore) — the section-writing/plotting step put a LaTeX-escaped string into a matplotlib label that
  is *not* rendered with `text.usetex`, so the `\_` shows verbatim.
**Fixes (generation-time + verify):**
- Give the plotting skill **explicit layout constraints** for box/arrow diagrams: minimum inter-box
  margin, arrow endpoints inset from box edges (no touching), `tight_layout`/`bbox_inches` with padding,
  and a min figure size per node count. Prefer a real layout engine (graphviz/networkx) over hand-placed
  boxes for graphs.
- **Never emit LaTeX escapes in matplotlib text** unless `usetex=True`; sanitize `\_ \& \% \#` → plain in
  labels/titles (or set `usetex` consistently).
- **The self-verify is log-only and structurally cannot see this** (overlap/tight-spacing/escape-leak are
  *visual*, not in the LaTeX log). See G4.

## G4 [P1] Self-verify is log-only — it needs an actual render-and-LOOK (VLM) pass
Round-1's A3 asked for "render → look → check"; it shipped as a **log-parser** (overfull/missing-figure/
undefined). That catches text-layout defects but is **blind to everything visual**: figure element
overlap, cramped arrows, escape-leaks in figure text, illegible/overlapping labels, a figure that is the
wrong plot, near-duplicate figures (byte-dedup misses resized dups — the exact round-1 A1 bug would
recur). **Add an image-based check:** render each page + each figure to PNG and run a **VLM pass** (when
`vision_model` is set) with a rubric ("any overlapping/cramped elements? text touching/clipped? arrows
crossing boxes? does the figure match its caption? are two figures near-duplicates?"), feeding hits into
the same bounded self-fix loop. Without a vision model, at least add heuristics (figure aspect/whitespace
ratio, duplicate perceptual-hash) — the round-1 A1/F3 items are still DEFERRED and this is where they land.

## G5 [P2] Content review (I read the paper) — content is honest+accurate; one scoping caution
Positive (keep): the manuscript is faithful to the gated claims and **notably honest** — it separates the
single *validated* result (ZigZag byte-exact) from the *exploration-grade* surface, states congestion
needs ASTRA-sim "which we have not yet built or run end-to-end," and gives a real Honest-Limits subsection
(±10% not ±5%/cycle-exact, tensor-free, first-order fabric, dense-attention-only, the launch-floor and
serial-all-to-all as explicitly-bounded calibration choices). No fabricated numbers (a figure even
annotates "no fusion traffic-reduction % is claimed — not in the validated table"). Good governance
outcome.
**One caution for the writing skill (not a bug):** the abstract's "*no prior tool* costs a real captured
workload against a non-existent accelerator …" is the novelty claim, and its nearest threat (Flint —
captures a real workload for non-existent HW, but has *no cost model* and is cluster-tier) must be
**explicitly distinguished in Related Work**, or a reviewer rejects the "only tool" framing. Suggest the
section-writing skill, when a claim is comparative/superlative ("only", "no prior"), **require an explicit
adjacent sentence naming and distinguishing the closest cited neighbor.**

## Still open from round 1
A1/F3 perceptual near-dup + VLM caption-consistency (now folded into G4); F5 first-class translate step
(Korean paper/patent still hand-scripted outside the harness).

## Verification method (as the tool itself should do — G4)
I rendered `paper.pdf` + every figure to PNG and inspected them, `pdftotext | grep '[?]'`, and reran
`latexmk` to reproduce the bibtex-skip. All reproducible.

---

## Resolution — Veridraft dev session (2026-07-04, round 2)

Fixed + regression-tested (`tests/test_hatir_fixes.py`, 137 tests / verify 9/9); verified G1/G2 with a
real natbib+hyperref+bibtex build (healthy build → 0 issues, `.bbl` produced, `/Link` confirmed):
- **G1 [P0]** — `_verify_compile` judges the FINAL PDF (`pdftotext` → literal `[?]`/`??`), not the
  multi-pass LOG (which false-positived on every build); `compile_pdf` asserts a non-empty `paper.bbl`
  when the paper cites and runs an explicit `pdflatex→bibtex→pdflatex×2` recovery if latexmk skipped
  bibtex; `_self_fix` routes a bibtex-skip to a RECOMPILE (never a backend edit) and the fix prompt
  forbids deleting `\cite`/`\bibliography`.
- **G2 [P0]** — clickable cross-refs are a CHECKED property (hyperref loaded + citations resolved),
  with `/Link` annotation confirmation via a mutool-decompress fallback for compressed PDFs.
- **G3 [P1]** — plotting skill (`diagram-patterns.md`): mandatory box/arrow layout constraints (real
  layout engine / min gap / arrow inset / tight-layout+pad / canvas sized to node count) and a hard
  "NEVER put LaTeX escapes in matplotlib text; sanitize `\_ \& \% \#`" rule (the `fabric\_reduce` leak).
- **G4 [P1]** — self-verify is no longer log-only: a deterministic **perceptual near-duplicate**
  figure check (8×8 average-hash, rasterizes pdf/eps) catches resized dups a byte hash misses (folds
  in round-1 A1/F3), plus a **VLM render-and-LOOK** pass (advisory) when `vision_model` is set.
- **G5 [P2]** — section-writing skill (`prompt.md`): a comparative/superlative claim ("only", "no
  prior") now REQUIRES an adjacent sentence naming and distinguishing the closest cited neighbor.

**F5 multilingual translate — DONE:** `paperorchestra/translate_run.py` localizes a paper OR patent
into any language over the same backend (`--lang ko|Korean|한국어|de|…`), idiomatic + technical terms
kept in English + LaTeX/keys/numbers preserved; CJK → lualatex+kotex auto, self-verified, idempotent.

Still deferred: auto-FIXING visual figure defects (regenerating figures) beyond detection.

---

## Self-adversarial-review (workflow, 2026-07-04) — 10 CONFIRMED findings, all fixed

An adversarial multi-agent review of the round-2 fixes themselves surfaced regressions the fixes
introduced; all fixed + regression-tested (143 tests, verify 9/9; validated with real latexmk):
- **[HIGH] pdftotext-absent false-negative + false "resolved"** — `_verify_compile` no longer scans
  pdftotext for `[?]`/`??`; it now trusts the FINAL-pass end-of-run summary `There were undefined
  references/citations` (reliable, works without poppler-utils, immune to prose `[?]`/`??`). pdftotext
  only enriches the message. G2 "clickable" is based on that authoritative signal, not an empty scan.
- **[HIGH] `_self_fix` count-only revert lost a good paper** — `compile_pdf` now returns None on a
  non-zero latexmk exit (real LaTeX error), and `_self_fix` reverts to the pre-fix `paper.tex` when the
  recompile FAILS (not just when the issue count rises), so a compile-breaking edit can't destroy a
  shippable paper.
- **inline `\begin{thebibliography}` false BIBTEX-SKIP** — a new `_uses_bibtex` gates the `.bbl` check
  and the recovery pass on an EXTERNAL `\bibliography`/`\addbibresource` only (a manual bib needs no `.bbl`).
- **bibtex-skip `[?]` symptom routed to the backend** — `_self_fix` now excludes the co-occurring
  unresolved-cite symptom (not only the token) from editable when a bibtex-skip is present (recompile,
  never a backend edit).
- **prose `[?]`/`??` false-positives** — removed with the summary-based signal above.
- **EPS excluded from near-dup** — `_fig_ahash` rasterizes eps via mutool/gs (pdftoppm is PDF-only).
- **blank/sparse figure aHash collision** — near-uniform figures (stdev < 5) are excluded from near-dup.
- **G5 skill contradiction** — the distinguishing clause now belongs where the claim is authored
  (abstract/intro), not in the verbatim-preserved Related Work.
(2 findings were rate-limited before verification; 1 near-dup finding downgraded to PLAUSIBLE.)
