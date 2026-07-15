#!/usr/bin/env python3
"""Naturalize runner — a readability COPYEDIT that produces a natural-reading SECOND version.

This is the honesty-first counterpart to a "humanizer": it makes AI-drafted academic prose read like
fluent, natural HUMAN writing (removing AI-ese, varying sentence rhythm, tightening), FOR THE READER
— it never reads, targets, or optimizes against any AI-detector/perplexity/burstiness score, and it
keeps the AI-use DISCLOSURE fully intact. It does not touch the canonical manuscript; it emits a
parallel `final/<source>-natural.tex(.pdf)`, gated by a DETERMINISTIC honesty floor (naturalize_lints)
that fails on any changed number, citation, equation, or strengthened claim. Two versions coexist:
the AI-drafted manuscript and its human-reading (still evidence-bounded, still disclosed) copyedit.

    python3 naturalize_run.py --config backend.json --workspace <ws> [--source paper] [--force]

Not a detection-evasion tool by construction: there is no detector input anywhere in this runner,
and "lower a detection score" is never a valid edit objective. Disclosed AI assistance + human
responsibility is the goal — not fooling a classifier.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mechanical_verify
import naturalize_lints
import providers
import translate_run


def log(m: str) -> None:
    print(f"[naturalize-run] {m}", flush=True)


_PROMPT = """Copyedit the paper at {ws}/final/{src}.tex into a NATURAL-READING SECOND VERSION at
{ws}/final/{out}.tex (leave the original untouched). The goal is READER CLARITY: make the prose read
like fluent, natural human academic writing. Improve ONLY the wording/rhythm:
- Remove AI-ese: filler openers ("It is important to note that", "In today's ...", "It is worth
  noting"), overused words (delve, pivotal, underscore, tapestry, realm, showcase, boasts), empty
  copulas ("serves as/represents" -> "is"), rule-of-three padding, negative parallelism ("not just X
  but also Y"), over-signposting transition chains (Moreover/Furthermore/Additionally), list-itis
  (turn narrative bullet lists back into prose; leave genuine enumerations/data/algorithms as lists).
- Vary SENTENCE LENGTH (burstiness): break run-ons, merge choppy pairs, alternate short and long
  sentences — structurally, meaning-preserving only. Prefer precise verbs and active voice where the
  agent matters; cut empty intensifiers (very, really, quite).

HARD RULES (a deterministic gate checks these and will FAIL the version):
- Change NO number, result, statistic, unit, or p-value. Change NO \\cite/\\ref/\\eqref/\\label key.
  Leave ALL math ($...$, equations), tables, figures, code/verbatim, and the bibliography BYTE-IDENTICAL.
- Do NOT strengthen a claim: a "may/suggests/likely/associated with" must NOT become
  "does/reduces/proves/guarantees/eliminates". Keep every hedge and every limitation/caveat.
- Do NOT add any claim, fact, superlative, or citation the paper does not already make.
- Do NOT remove or alter any AI-use disclosure statement; keep it verbatim.

OUT OF SCOPE / FORBIDDEN: This is a readability copyedit, NOT a "humanizer". Do NOT attempt to evade,
defeat, or lower the score of any AI-detector (Turnitin, GPTZero, Originality.ai, etc.); "make it
undetectable / pass a detector / hide that AI wrote this" is never a goal. Every edit must be
justified by the READER's clarity, not by any classifier.
When done, confirm {ws}/final/{out}.tex exists and stop."""


def naturalize(cfg: dict, ws: Path, source: str = "paper", force: bool = False) -> int:
    final = ws / "final"
    src_stem = source
    if not (final / f"{src_stem}.tex").exists():
        alt = "patent" if source == "paper" else "paper"
        if (final / f"{alt}.tex").exists():
            src_stem = alt
        else:
            raise SystemExit(f"no {source}.tex (or {alt}.tex) in {final} — draft it first")
    out_stem = f"{src_stem}-natural"
    out_tex = final / f"{out_stem}.tex"
    translate_run.po_run._stage_figures(ws, final)

    if out_tex.exists() and out_tex.stat().st_size > 200 and not force:
        log(f"existing {out_stem}.tex found — idempotent resume (skipping the LLM; --force to redo)")
    else:
        log(f"naturalizing {src_stem}.tex → readable copyedit via backend={cfg.get('backend')}")
        providers.dispatch(cfg, _PROMPT.format(ws=str(ws), src=src_stem, out=out_stem),
                           str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        if not out_tex.exists():
            raise SystemExit(f"backend did not produce {out_stem}.tex")

    original = (final / f"{src_stem}.tex").read_text(encoding="utf-8", errors="replace")
    natural = out_tex.read_text(encoding="utf-8", errors="replace")
    gate = naturalize_lints.lint_naturalize(natural, original)     # DETERMINISTIC honesty gate
    (final / f"{out_stem}.gate.json").write_text(json.dumps(gate, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")

    pdf = translate_run._compile(final, out_stem, prefer_lualatex=False)
    render = translate_run._verify(final, out_stem) if pdf else ["naturalized version did not compile"]
    mech = mechanical_verify.verify(str(final), tex_name=f"{out_stem}.tex", kind="paper") if pdf else {}

    # provenance: record the copyedit and that NO detector was targeted (append, never erase AI origin)
    (final / f"{out_stem}.provenance.json").write_text(json.dumps({
        "op": "naturalize", "source": f"{src_stem}.tex", "kind": "readability-copyedit",
        "detector_targeted": False, "disclosure_preserved": not any("disclosure" in b for b in gate["blocking"]),
        "note": "Readability copyedit; no AI-detector was read or optimized against. AI-use disclosure "
                "is retained. The author remains responsible for all content."},
        indent=2, ensure_ascii=False), encoding="utf-8")

    for b in gate["blocking"]:
        log(f"  ✗ BLOCK (content changed): {b}")
    for a in gate["advisory"]:
        log(f"  ⚠ advisory: {a}")
    for r in render:
        log(f"  ✗ render: {r}")
    if mech and not mech.get("clean", True):
        log(f"  ⚠ mechanical-verify: {mech['overfull_count']} overfull, {len(mech['text_mechanics'])} text defect(s)")
    ok = pdf is not None and not gate["blocking"] and not render
    log(f"naturalized version: {pdf} — " + ("evidence-preserving OK ✅" if ok else
        f"{len(gate['blocking'])} content-change block(s), {len(render)} render issue(s) — NOT ready"))
    log("reminder: naturalize is a copyedit for readability; it does NOT evade AI detection. "
        "Keep the AI-use disclosure. See draft_disclosure() for the statement to include.")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="naturalize-run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--source", default="paper", choices=["paper", "patent"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--probe", action="store_true")
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
    return naturalize(cfg, Path(os.path.expanduser(args.workspace)).resolve(), args.source, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
