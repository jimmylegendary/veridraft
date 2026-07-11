#!/usr/bin/env python3
"""Portable patent-drafting runner — backend-agnostic (sibling of run.py for papers).

Drafts a US-utility-patent application over ANY configured backend (self-hosted OpenAI-compatible
API, openclaw, claude-code, codex) from an assembled invention workspace, so Veridraft's
`patent-llm` PatentEngine can be turnkey with the same connection config as the paper engine.

    python3 patent_run.py --config backend.json --workspace <ws> [--probe]

The workspace must contain inputs/{invention.md, claims.json} (Veridraft's `assemble_patent`
produces exactly this). Output contract (consumed by the patent-llm adapter):
    <ws>/final/patent.tex, <ws>/final/patent.pdf, <ws>/final/open_items.json

Governance note: this NEVER files. It drafts; Veridraft's `patent-review` lints + a human/attorney
gate decide filing-readiness. The drafting prompt bakes the readiness requirements (101 terminal
effect, dispositive delta in the independent, method/system/CRM completeness, §112 definiteness).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_info
import patent_figures
import providers
import run as po_run   # figure staging


def log(m: str) -> None:
    print(f"[patent-run] {m}", flush=True)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


_PROMPT = """Draft a US utility-patent APPLICATION from the assembled invention disclosure. This is a
DRAFT, never a filing. Read the inputs in {ws}/inputs/ (invention.md, claims.json, results.json,
patentability.json) and write these files under {ws}/final/:
  - patent.tex  : a USPTO-style LaTeX document — Field, Background, Summary, Detailed Description
    (numbered [0001]… paragraphs, antecedent basis, multiple embodiments + fallbacks), a Brief
    Description of the Drawings, CLAIMS, and an Abstract (<=150 words). Compile-safe LaTeX.
  - open_items.json : a JSON array of every gap a human/attorney MUST resolve.

CLAIM REQUIREMENTS (the review gate checks these — satisfy them):
  - For EACH patentable substrate (each distinct invention in claims.json typed P1/P2), draft a
    METHOD, a SYSTEM, and a non-transitory COMPUTER-READABLE MEDIUM independent claim, plus a
    dependent-claim ladder from broadest to narrowest.
  - Every INDEPENDENT claim must END in a concrete terminal technical effect (e.g. "and emitting a
    machine-readable specification / configuring the accelerator …"), NOT at "computing a cost".
  - Put each invention's DISPOSITIVE novelty delta in its INDEPENDENT claim (not only a dependent).
  - The broadest independent = the smallest independently-novel unit (don't bake a second
    device/design into the base claim).
  - Every claim element must trace to an evidence-gated claim in claims.json. Do NOT invent numbers;
    corroborating results (results.json) go in the SPEC, not as a claim's novelty basis.
  - Define any relative term ("structurally different") with an enumerated closed list (§112(b)).
  - P3 (future-device) claims are projections → do NOT independently claim them; list them in
    open_items as requires-enablement-review.
Also add open_items for: 102/103 prior-art search (UNSCREENED here), 101 eligibility (legal),
§112 antecedent basis / means-plus-function, and jurisdiction formalities.

DRAWINGS: generate 1-3 patent drawings into {ws}/figures/ (fig1.png, fig2.png, ...) illustrating
the claimed system architecture and method flow; add a Brief Description of the Drawings and
\\includegraphics{{figures/figN.png}} floats to patent.tex; recite EVERY reference numeral from the
drawings in the Detailed Description. (An image gate verifies the rendered drawings.)
{drawing_rules}
When done, confirm {ws}/final/patent.tex and {ws}/final/open_items.json exist, then stop."""


def _pdf_ok(pdf: Path) -> bool:
    """True iff the PDF is structurally valid (pdfinfo exit 0). If pdfinfo is absent, assume OK."""
    if not shutil.which("pdfinfo"):
        return pdf.exists()
    try:
        return subprocess.run(["pdfinfo", str(pdf)], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _compile_and_validate(final: Path) -> None:
    """Compile with -no-shell-escape and VALIDATE the PDF — a killed latexmk leaves a corrupt PDF
    ('Couldn't find trailer dictionary'); recompile once, and never stage a corrupt PDF (E1)."""
    for attempt in (1, 2):
        try:
            subprocess.run(["latexmk", "-pdf", "-no-shell-escape", "-interaction=nonstopmode", "patent.tex"],
                           cwd=str(final), capture_output=True, text=True, timeout=240)
        except (OSError, subprocess.SubprocessError):
            log("latexmk unavailable/failed — final/patent.tex left for review"); return
        pdf = final / "patent.pdf"
        if pdf.exists() and _pdf_ok(pdf):
            return
        if pdf.exists():
            pdf.unlink()   # drop a corrupt PDF so nothing downstream stages it
        log(f"compile attempt {attempt}: patent.pdf missing/corrupt" + (" — retrying" if attempt == 1 else ""))


def draft(cfg: dict, ws: Path, force: bool = False) -> int:
    final = ws / "final"
    final.mkdir(parents=True, exist_ok=True)
    tex = final / "patent.tex"
    # E2 idempotent resume: a complete existing draft is NOT re-dispatched (a 20-min regenerate)
    # unless --force. This also cheaply re-registers a run killed during compile (E1).
    if tex.exists() and tex.stat().st_size > 200 and not force:
        log("existing final/patent.tex found — idempotent resume (skipping the LLM; --force to redraft)")
    else:
        inputs = ws / "inputs"
        if not (inputs / "invention.md").exists() or not (inputs / "claims.json").exists():
            raise SystemExit(f"missing inputs/{{invention.md,claims.json}} in {ws} — run assemble_patent first")
        # Legal meta (inventors, assignee, jurisdictions, filing type, prior art, public disclosure)
        # can only come from the human — block and emit questions unless inputs/meta.json has it.
        if cfg.get("require_meta", True):
            meta_info.enforce(ws, "patent", log=log)
        log(f"drafting patent via backend={cfg.get('backend')}")
        providers.dispatch(cfg,
                           _PROMPT.format(ws=str(ws), drawing_rules=patent_figures.PATENT_FIGURE_RULES)
                           + meta_info.prompt_block(ws, "patent"),
                           str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        if not tex.exists():
            raise SystemExit("backend did not produce final/patent.tex (too weak / wrong workspace)")
    if not (final / "open_items.json").exists():
        (final / "open_items.json").write_text("[]", encoding="utf-8")
    # Drawings gate: verify the RENDERED images (monochrome line art, numerals, spacing) and feed
    # defects into a bounded regenerate loop, then stage figures so \includegraphics resolves.
    figs_dir = ws / "figures"
    if figs_dir.is_dir() and any(figs_dir.iterdir()):
        remaining = patent_figures.check_and_fix(cfg, ws, figs_dir,
                                                 max_iters=int(cfg.get("figure_fix_max_iters", 2)))
        (final / "figures.verify.json").write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        for r in remaining:
            log(f"  ⚠ drawing: {r}")
        po_run._stage_figures(ws, final)
    else:
        log("no drawings in figures/ — a filing normally requires drawings (attorney open item)")
    _compile_and_validate(final)
    log("patent draft ready (final/patent.tex); NEVER filed — attorney review required")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="patent-run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--force", action="store_true", help="redraft even if final/patent.tex exists")
    args = ap.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"could not read/parse --config {args.config}: {e}")
    ok, detail = providers.probe(cfg)
    log(f"backend probe: {'OK' if ok else 'FAIL'} — {detail}")
    if args.probe:
        return 0 if ok else 1
    if not ok:
        raise SystemExit(detail)
    return draft(cfg, Path(os.path.expanduser(args.workspace)).resolve(), force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
