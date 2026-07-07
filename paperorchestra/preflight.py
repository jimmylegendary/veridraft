#!/usr/bin/env python3
"""Preflight — input-sufficiency gate + auto-remediation, so a thin-context project cannot
silently produce a hollow paper/patent.

Before drafting, every input dimension is scored deterministically and classified:
  OK          — sufficient, proceed.
  AUTOFILL    — insufficient but DERIVABLE from artifacts → --remediate dispatches the backend to
                fill it, grounded (literature research via verified refs; code documentation from
                the source repo; idea densification from existing notes). Derived ≠ invented.
  NEEDS-HUMAN — insufficient and NOT derivable (experimental numbers, meta facts) → BLOCKING.
                Numbers are NEVER auto-filled: "generated text is never evidence."

    python3 preflight.py --config backend.json --workspace <ws> [--kind paper] [--remediate] [--strict]

Exit codes: 0 = all OK · 4 = auto-fillable gaps remain (run --remediate) · 5 = needs-human gaps.
Report: <ws>/preflight.json — the driving agent relays NEEDS-HUMAN items to the user verbatim.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import meta_info
import providers

OK, AUTOFILL, HUMAN = "OK", "AUTOFILL", "NEEDS-HUMAN"


def log(m: str) -> None:
    print(f"[preflight] {m}", flush=True)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def _count_numbers(text: str) -> int:
    return len(re.findall(r"\b\d+(?:\.\d+)?\s?(?:%|ms|µs|us|ns|s\b|GB|MB|KB|x\b|×|tokens?/s|FLOPs?)"
                          r"|\b\d+\.\d+\b", text, re.I))


def _bib_entries(text: str) -> int:
    return len(re.findall(r"^@\w+\s*\{", text, re.M))


def _repo_doc_coverage(repo: Path) -> tuple[float, str]:
    """Rough documentation coverage of a source repo: README + docs/ + docstring density."""
    if not repo.exists():
        return 0.0, "source repo path does not exist"
    score, notes = 0.0, []
    if any((repo / n).exists() for n in ("README.md", "README.rst", "readme.md")):
        score += 0.4
    else:
        notes.append("no README")
    if (repo / "docs").is_dir() and any((repo / "docs").rglob("*.md")):
        score += 0.2
    else:
        notes.append("no docs/")
    pys = [p for p in repo.rglob("*.py") if "test" not in p.name and ".venv" not in str(p)][:80]
    if pys:
        with_doc = sum(1 for p in pys if re.search(r'("""|\'\'\')', _read(p)[:2000]))
        score += 0.4 * (with_doc / len(pys))
        if with_doc / len(pys) < 0.5:
            notes.append(f"docstrings in only {with_doc}/{len(pys)} modules")
    return round(score, 2), ("; ".join(notes) or "documented")


def check(ws: Path, kind: str = "paper") -> list[dict]:
    """Deterministic sufficiency report. Each item: {check, status, detail, remedy?}."""
    inputs = ws / "inputs"
    items: list[dict] = []

    def add(check_, status, detail, remedy=None):
        items.append({"check": check_, "status": status, "detail": detail,
                      **({"remedy": remedy} if remedy else {})})

    # 1. idea density — derivable from notes/repo, so AUTOFILL when thin
    idea = _read(inputs / "idea.md")
    words = len(idea.split())
    if words >= 150:
        add("idea", OK, f"{words} words")
    else:
        add("idea", AUTOFILL, f"idea.md has only {words} words — too thin to anchor an outline",
            "densify-idea")

    # 2. experimental numbers — NEVER auto-fillable (evidence rule)
    if kind == "paper":
        exp = _read(inputs / "experimental_log.md")
        n = _count_numbers(exp)
        if n >= 5:
            add("experimental-numbers", OK, f"{n} quantitative values in experimental_log.md")
        else:
            add("experimental-numbers", HUMAN,
                f"experimental_log.md carries {n} quantitative value(s) — a results section cannot "
                "be written from this. Numbers are NEVER auto-filled (generated text is not "
                "evidence): run the experiments or paste real measurements into §2.")

    # 3. literature — derivable (web research with verified citations)
    bib = _bib_entries(_read(ws / "refs.bib")) or _bib_entries(_read(inputs / "refs.bib"))
    if bib >= 8:
        add("literature", OK, f"{bib} bib entries")
    else:
        add("literature", AUTOFILL,
            f"only {bib} bib entries — related-work coverage will be hollow", "litfill")

    # 4. figures or figure-able data
    figs = list((inputs / "figures").glob("*")) if (inputs / "figures").is_dir() else []
    if figs:
        add("figures", OK, f"{len(figs)} pre-supplied figure(s)")
    elif kind == "paper" and _count_numbers(_read(inputs / "experimental_log.md")) >= 5:
        add("figures", OK, "none supplied, but §2 numbers allow the plotting step to generate them")
    else:
        add("figures", AUTOFILL, "no figures and no plottable data — diagram figures can be derived "
            "from the idea/architecture", "diagramfill")

    # 5. meta info — human-only (delegates to the meta gate)
    missing = meta_info.check_meta(ws, kind)["missing"]
    if missing:
        add("meta-info", HUMAN, f"{len(missing)} human-only meta field(s) unanswered "
            f"({', '.join(f['key'] for f in missing)}) — the meta gate will ask the user")
    else:
        add("meta-info", OK, "meta.json complete")

    # 6. source-repo documentation — derivable from the code itself
    meta = meta_info.load_meta(ws)
    repo = meta.get("source_repo")
    if repo:
        cov, note = _repo_doc_coverage(Path(os.path.expanduser(repo)))
        if cov >= 0.6:
            add("code-documentation", OK, f"coverage ~{cov} ({note})")
        else:
            add("code-documentation", AUTOFILL,
                f"coverage ~{cov} ({note}) — the writing model will guess architecture it can't read",
                "docfill")

    # 7. contract files
    for req in (("template.tex",), ("conference_guidelines.md",)) if kind == "paper" else ():
        f = req[0]
        add(f, OK if (inputs / f).exists() else HUMAN,
            "present" if (inputs / f).exists() else f"inputs/{f} missing — supply the venue's file")
    return items


# ---- remediation (backend-dispatched, grounded-only) --------------------------------------------

_REMEDIES = {
    "litfill": """The workspace {ws}/inputs is missing literature coverage ({detail}).
Do a REAL literature research pass for the paper described in {ws}/inputs/idea.md:
discover 10-15 directly relevant works (web search if available, else your knowledge cut cautiously),
VERIFY every entry's title/authors/year/venue before adding, and write a valid BibTeX file to
{ws}/refs.bib (merge with any existing entries, no duplicates). NEVER fabricate a reference — if you
cannot verify a work exists, leave it out. Also write {ws}/drafts/lit_notes.md: one line per entry on
why it is relevant. Then stop.""",
    "docfill": """The source repository {repo} is under-documented ({detail}), which starves the
paper of architecture context. READ the actual code and write {ws}/inputs/code_docs.md documenting:
the module map, the core data flow, key design decisions visible in the code, and the main
interfaces — every statement GROUNDED in a file you actually read (cite paths). Do NOT invent
behavior, do NOT include any secret/credential you encounter. Then stop.""",
    "densify-idea": """{ws}/inputs/idea.md is too thin ({detail}) to anchor a paper outline.
Expand it to a dense Idea Summary using ONLY grounded sources: the existing idea.md text,
{ws}/inputs/code_docs.md (if present), the source repo at {repo} (if given), and
{ws}/inputs/experimental_log.md. State the problem, the approach, what exists vs planned, and the
intended contributions. Do NOT invent results, numbers, or capabilities not visible in the sources.
Overwrite {ws}/inputs/idea.md. Then stop.""",
    "diagramfill": """No figures exist for the paper in {ws}/inputs. From idea.md and code_docs.md
(if present), generate 1-3 ARCHITECTURE/FLOW diagram figures (matplotlib/graphviz, per the plotting
skill's diagram rules: real layout engine, min inter-box gap, arrows inset, no LaTeX escapes in
labels) into {ws}/inputs/figures/, plus a captions draft {ws}/inputs/figures/captions_draft.json.
Diagrams only — NEVER a data plot with invented numbers. Then stop.""",
}
# Remediation ORDER matters: document the code first, then densify the idea FROM those docs.
_REMEDY_ORDER = ["docfill", "densify-idea", "litfill", "diagramfill"]


def remediate(cfg: dict, ws: Path, items: list[dict]) -> list[str]:
    """Dispatch the backend once per AUTOFILL gap, in dependency order. Returns remedies run."""
    meta = meta_info.load_meta(ws)
    repo = meta.get("source_repo", "(not given)")
    todo = {i["remedy"]: i for i in items if i["status"] == AUTOFILL and i.get("remedy")}
    ran = []
    for key in _REMEDY_ORDER:
        if key not in todo:
            continue
        log(f"remediate: {key} — {todo[key]['detail']}")
        providers.dispatch(cfg, _REMEDIES[key].format(ws=ws, repo=repo, detail=todo[key]["detail"]),
                           str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        ran.append(key)
    return ran


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="preflight")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--kind", default="paper", choices=["paper", "patent"])
    ap.add_argument("--remediate", action="store_true", help="auto-fill the AUTOFILL gaps via the backend")
    ap.add_argument("--strict", action="store_true", help="exit non-zero on ANY gap (CI mode)")
    args = ap.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"could not read/parse --config {args.config}: {e}")
    ws = Path(os.path.expanduser(args.workspace)).resolve()

    items = check(ws, args.kind)
    if args.remediate and any(i["status"] == AUTOFILL for i in items):
        ok, detail = providers.probe(cfg)
        if not ok:
            raise SystemExit(f"cannot remediate: backend probe failed — {detail}")
        remediate(cfg, ws, items)
        items = check(ws, args.kind)   # re-score: remediation must actually move the needle

    (ws / "preflight.json").write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    icon = {OK: "✅", AUTOFILL: "🔧", HUMAN: "🛑"}
    for i in items:
        log(f"{icon[i['status']]} {i['check']}: {i['detail']}")
    human = [i for i in items if i["status"] == HUMAN]
    autofill = [i for i in items if i["status"] == AUTOFILL]
    if human:
        log(f"{len(human)} NEEDS-HUMAN gap(s) — relay these to the user; drafting on top of them "
            "produces a hollow paper.")
        return 5
    if autofill:
        log(f"{len(autofill)} auto-fillable gap(s) — run with --remediate to fill them (grounded).")
        return 4 if not args.strict else 4
    log("all inputs sufficient ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
