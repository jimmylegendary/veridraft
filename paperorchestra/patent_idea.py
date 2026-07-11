#!/usr/bin/env python3
"""Patent-IDEA runner — the pre-drafting memo (backend-agnostic; a sibling of patent_run.py).

BEFORE a full application (claims, spec), draft an INVENTION-DISCLOSURE / idea memo: the core
inventive concept in plain language (NO claims), a per-statutory-requirement patentability CASE
(§101/§102/§103/§112, each grounded in the disclosure + the prior-art distinctions), and CONCEPT
figures (architecture/flow — not data plots) that pass the same overlap/spacing image gate as the
patent drawings. A human/attorney reviews this to decide whether to file.

    python3 patent_idea.py --config backend.json --workspace <ws> [--probe]

Output: <ws>/idea/patent_idea.md, <ws>/idea/patentability_case.json, <ws>/idea/figures/*.png,
        <ws>/idea/idea.verify.json (deterministic completeness gate).

Deterministic guarantees (model cannot override): every required section must be present, the
verbatim Boundary Statement must be there, and §102/§103 must keep a non-empty open_items — an idea
memo argues a CASE; it never asserts novelty/patentability as settled.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # repo root → veridraft package
import patent_figures
import providers

try:
    from veridraft.core import patent_idea as idea_core
except Exception:   # noqa: BLE001
    idea_core = None


def log(m: str) -> None:
    print(f"[patent-idea] {m}", flush=True)


def _prompt(ws: Path) -> str:
    from veridraft.core import patent_idea as ic
    sections = "\n".join(f"  ## {s}" for s in ic.REQUIRED_SECTIONS)
    return f"""Draft a PATENT-IDEA memo (an invention disclosure) for the invention in {ws}/inputs/
(read invention.md, claims.json, results.json, and priorart.json if present). This is the
pre-drafting memo — NOT the application, NOT claims. A human/attorney will review it.

Write {ws}/idea/patent_idea.md with EXACTLY these H2 sections (in this order):
{sections}
Rules for the memo:
- Section "Inventive Concept (Plain Language)": the core idea in 3-6 sentences, no jargon, NO claims.
- Ground every statement in the disclosure/results/prior-art you were given; do NOT invent numbers.
- "Concept Figures": 1-3 ARCHITECTURE/FLOW diagrams (NOT data plots) rendered to {ws}/idea/figures/
  (F1.png, F2.png, ...); reference each by its ID (F1, F2) in the text.
{patent_figures.PATENT_FIGURE_RULES}
- "Boundary Statement": reproduce this text VERBATIM as the whole section body:
  "{ic.BOUNDARY_STATEMENT}"

Also write {ws}/idea/patentability_case.json:
{{
  "invention_title": "...",
  "requirements": [
    {{"requirement":"utility_101","statute":"35 U.S.C. 101","basis":"why it is a concrete process/machine/manufacture with real-world use, not abstract","grounded_in":["idea:Inventive Concept","figure:F1"],"open_items":["confirm no abstract-idea exposure with counsel"]}},
    {{"requirement":"novelty_102","statute":"35 U.S.C. 102","basis":"per closest reference, which claimed element(s) it lacks","grounded_in":["priorart:R1"],"open_items":["PROFESSIONAL prior-art search required","verify no >1yr public-disclosure bar"]}},
    {{"requirement":"nonobviousness_103","statute":"35 U.S.C. 103","basis":"why the combination is not obvious to a POSA (secondary considerations)","grounded_in":["idea:Distinctions Over Known Approaches"],"open_items":["substantiate each secondary consideration with evidence"]}},
    {{"requirement":"enablement_wd_112","statute":"35 U.S.C. 112","basis":"enough detail a POSA could build it; possession shown by code/data/prototype","grounded_in":["idea:Enablement Detail"],"open_items":["confirm the disclosed scope is fully supported"]}}
  ]
}}
Every requirement needs a NON-EMPTY basis and grounded_in; §102 and §103 MUST keep a non-empty
open_items (you cannot settle novelty/non-obviousness here). Confirm both files exist, then stop."""


def draft(cfg: dict, ws: Path) -> int:
    inputs = ws / "inputs"
    if not (inputs / "invention.md").exists() or not (inputs / "claims.json").exists():
        raise SystemExit(f"missing inputs/{{invention.md,claims.json}} in {ws} — run assemble_patent first")
    idea_dir = ws / "idea"
    idea_dir.mkdir(parents=True, exist_ok=True)
    md_path, case_path = idea_dir / "patent_idea.md", idea_dir / "patentability_case.json"
    if md_path.exists() and md_path.stat().st_size > 200 and not cfg.get("_force"):
        log("existing idea/patent_idea.md found — idempotent resume (--force to redraft)")
    else:
        log(f"drafting patent-idea via backend={cfg.get('backend')}")
        try:
            providers.dispatch(cfg, _prompt(ws), str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        except Exception as e:   # noqa: BLE001 — degrade to a skeleton, never crash
            log(f"backend failed ({e}); writing a skeleton memo (needs completion)")
    # degraded fallback if the backend produced nothing
    title = _title(inputs / "invention.md")
    if not md_path.exists() and idea_core is not None:
        md_path.write_text(idea_core.skeleton_markdown(title, "backend produced nothing"), encoding="utf-8")
    if not case_path.exists() and idea_core is not None:
        case_path.write_text(json.dumps(idea_core.skeleton_case(title), indent=2), encoding="utf-8")

    # concept figures → same overlap/spacing image gate as the patent drawings
    figs = idea_dir / "figures"
    if figs.is_dir() and any(figs.iterdir()):
        remaining = patent_figures.check_and_fix(cfg, ws, figs,
                                                 max_iters=int(cfg.get("figure_fix_max_iters", 2)))
        (idea_dir / "figures.verify.json").write_text(json.dumps(remaining, indent=2), encoding="utf-8")
        for r in remaining:
            log(f"  ⚠ concept figure: {r}")
    else:
        log("no concept figures yet (an idea memo should include an architecture/flow diagram)")

    # deterministic completeness gate
    if idea_core is not None:
        try:
            case = json.loads(case_path.read_text(encoding="utf-8")) if case_path.exists() else {}
        except ValueError:
            case = {}
        pa = None
        pj = inputs / "priorart.json"
        if pj.exists():
            try:
                pa = json.loads(pj.read_text(encoding="utf-8"))
            except ValueError:
                pa = None
        report = idea_core.completeness(md_path.read_text(encoding="utf-8") if md_path.exists() else "",
                                        case, pa)
        (idea_dir / "idea.verify.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                   encoding="utf-8")
        log(f"completeness: {'PASS' if report['passed'] else 'INCOMPLETE'} "
            f"({len(report['failures'])} failure(s), {len(report['warnings'])} warning(s))")
        for f in report["failures"]:
            log(f"  ✗ {f}")
    log("patent-idea memo ready; a professional search + attorney review remain mandatory before filing")
    return 0


def _title(inv: Path) -> str:
    if inv.exists():
        for line in inv.read_text(encoding="utf-8").splitlines():
            if line.startswith("# "):
                return line[2:].replace("Invention Disclosure:", "").strip()
    return "Invention"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="patent-idea")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"could not read/parse --config {args.config}: {e}")
    cfg["_force"] = args.force
    ok, detail = providers.probe(cfg)
    log(f"backend probe: {'OK' if ok else 'FAIL'} — {detail}")
    if args.probe:
        return 0 if ok else 1
    if not ok:
        raise SystemExit(detail)
    return draft(cfg, Path(os.path.expanduser(args.workspace)).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
