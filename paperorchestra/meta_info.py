#!/usr/bin/env python3
"""Meta-info schema + forced elicitation — the ask-the-user step before any drafting starts.

A paper/patent needs meta-information that ONLY the human can supply (venue, double-blind status,
authors; inventors, assignee, jurisdictions, prior public disclosure). The pipeline used to assume
or silently default these — producing e.g. a non-anonymized draft for a double-blind venue, or a
patent draft with no inventor/jurisdiction record. Now the runners BLOCK before drafting unless
`inputs/meta.json` satisfies the schema, and emit MACHINE-READABLE questions so the driving agent
must ask the user (already-supplied info passes without questions). Derivable fields (working title,
keywords, page limit from the guidelines) never block — the model fills them, grounded in inputs.

    python3 meta_info.py --workspace <ws> --kind paper|patent [--questions]

Exit codes: 0 = meta complete · 3 = questions pending (written to <ws>/meta.questions.json).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ask=True → only a human can answer; drafting is BLOCKED until inputs/meta.json supplies it.
# ask=False → derivable; the drafting prompt derives it (grounded in inputs) when absent.
PAPER_FIELDS = [
    {"key": "paper_type", "ask": True, "example": "research", "choices": ["research", "survey"],
     "question": "Is this a RESEARCH paper (your own experiments) or a SURVEY/STUDY paper (reviewing others' work)?",
     "why": "A survey grounds and discusses OTHER works' figures, so it must capture reference figures "
            "from the cited PDFs before drafting — a required pre-writing step research papers skip."},
    {"key": "venue", "ask": True, "example": "MLSys 2027 / IEEE Micro / arXiv-only",
     "question": "Which venue is this paper targeting (conference/journal/workshop/arXiv)?",
     "why": "Venue drives formatting, tone, page budget, and the AI-disclosure policy check."},
    {"key": "anonymize", "ask": True, "type": bool, "example": True,
     "question": "Is the submission double-blind (must the draft omit author names/affiliations)?",
     "why": "A non-anonymized draft submitted to a double-blind venue is a desk reject."},
    {"key": "authors", "ask": True, "type": list, "example": [{"name": "J. Lee", "affiliation": "X Lab", "orcid": ""}],
     "question": "Who are the authors, in order (name, affiliation, ORCID if any)?",
     "why": "Authorship cannot be invented; needed for the (non-anonymous or camera-ready) header."},
    {"key": "audience", "ask": True, "example": "systems researchers who build ML serving infra",
     "question": "Who is the primary audience (who should find this paper useful)?",
     "why": "Audience shapes what is explained vs assumed, and the framing of contributions."},
    {"key": "deadline", "ask": True, "optional": True, "example": "2026-09-01",
     "question": "Submission deadline, if any?",
     "why": "Recorded in provenance; no schedule pressure is applied to content."},
    {"key": "working_title", "ask": False, "derive": "inputs/idea.md"},
    {"key": "keywords", "ask": False, "derive": "inputs/idea.md + gated claims"},
    {"key": "page_limit", "ask": False, "derive": "inputs/conference_guidelines.md"},
]

PATENT_FIELDS = [
    {"key": "inventors", "ask": True, "type": list, "example": ["Jimmy Lee"],
     "question": "Who are the inventors (legal names, all actual contributors to the claims)?",
     "why": "Inventorship is a legal fact — mis-listing invalidates a patent; it cannot be derived."},
    {"key": "assignee", "ask": True, "example": "ACME Corp / self",
     "question": "Who is the assignee (owner) of the patent — employer, company, or the inventor(s)?",
     "why": "Ownership must be recorded before filing; employment agreements often decide it."},
    {"key": "jurisdictions", "ask": True, "type": list, "example": ["US", "KR", "PCT"],
     "question": "Which jurisdictions do you intend to file in (US / KR / EP / PCT ...)?",
     "why": "Claim style and formal requirements differ per office; drives the draft's conventions."},
    {"key": "filing_type", "ask": True, "example": "provisional",
     "choices": ["provisional", "nonprovisional"],
     "question": "Provisional first, or straight to a non-provisional application?",
     "why": "A provisional relaxes claim formality but sets the priority-date strategy."},
    {"key": "known_prior_art", "ask": True, "example": "none known / arXiv:2311.09735, US10123456",
     "question": "Any prior art you already know of ('none known' is a valid answer)?",
     "why": "Known art must be disclosed (IDS duty in the US); hiding it can render the patent unenforceable."},
    {"key": "public_disclosure", "ask": True, "example": "none / 2026-05-01 arXiv preprint",
     "question": "Has the invention been publicly disclosed anywhere (paper, talk, repo)? When?",
     "why": "Public disclosure starts grace-period clocks (12mo US/KR; NO grace in EP) — legally decisive."},
    {"key": "invention_title", "ask": False, "derive": "inputs/invention.md"},
    {"key": "key_embodiments", "ask": False, "derive": "inputs/claims.json"},
]

_FIELDS = {"paper": PAPER_FIELDS, "patent": PATENT_FIELDS}


def _present(meta: dict, f: dict) -> bool:
    v = meta.get(f["key"])
    if v is None or v == "" or v == []:
        return False
    want = f.get("type")
    if want is bool and not isinstance(v, bool):
        return False
    if want is list and not isinstance(v, list):
        return False
    if f.get("choices"):
        # case-insensitive + paper_type synonyms (study/review == survey), matching what the
        # question advertises ("SURVEY/STUDY") and what preflight accepts — else a valid answer
        # loops the meta gate forever while preflight already treats it as a survey.
        norm = str(v).strip().lower()
        if f["key"] == "paper_type" and norm in ("study", "review"):
            norm = "survey"
        if norm not in [c.lower() for c in f["choices"]]:
            return False
    return True


def load_meta(ws: Path) -> dict:
    p = ws / "inputs" / "meta.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def check_meta(ws: Path, kind: str) -> dict:
    meta = load_meta(ws)
    missing = [f for f in _FIELDS[kind]
               if f["ask"] and not f.get("optional") and not _present(meta, f)]
    derivable = [f["key"] for f in _FIELDS[kind] if not f["ask"] and not _present(meta, f)]
    return {"missing": missing, "derivable_missing": derivable, "meta": meta}


def questions(missing: list[dict]) -> list[dict]:
    return [{"key": f["key"], "question": f["question"], "why": f["why"],
             "example": f["example"], **({"choices": f["choices"]} if f.get("choices") else {})}
            for f in missing]


def enforce(ws: Path, kind: str, log=print) -> None:
    """Block drafting until the human-only meta fields are supplied. Writes <ws>/meta.questions.json
    and prints a machine-readable block so the DRIVING AGENT must ask the user, then exits 3."""
    chk = check_meta(ws, kind)
    if not chk["missing"]:
        return
    qs = questions(chk["missing"])
    (ws / "meta.questions.json").write_text(json.dumps(qs, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[meta] {kind} meta-info incomplete — {len(qs)} question(s) the USER must answer.")
    log("[meta] ASK THE USER these questions, write the answers to "
        f"{ws}/inputs/meta.json (key → answer), then re-run:")
    log("META-QUESTIONS-BEGIN")
    log(json.dumps(qs, indent=2, ensure_ascii=False))
    log("META-QUESTIONS-END")
    raise SystemExit(3)


def prompt_block(ws: Path, kind: str) -> str:
    """Meta as drafting instructions — collected info must actually SHAPE the output."""
    meta = load_meta(ws)
    if not meta:
        return ""
    lines = [f"\n{kind.upper()} META (obey exactly):"]
    for k, v in meta.items():
        if k.startswith("_") or v in (None, "", []):
            continue
        lines.append(f"- {k}: {json.dumps(v, ensure_ascii=False)}")
    if kind == "paper":
        if meta.get("anonymize"):
            lines.append("- DOUBLE-BLIND: omit ALL author names/affiliations/acknowledgments and any "
                         "self-identifying phrasing ('our prior work [x]' → 'prior work [x]').")
        elif meta.get("authors"):
            lines.append("- Use the author list above verbatim in the header; never invent authors.")
    if kind == "patent":
        lines.append("- Record inventors/assignee exactly as given; note public_disclosure and "
                     "known_prior_art in open_items (IDS / grace-period review for the attorney).")
    derivable = check_meta(ws, kind)["derivable_missing"]
    if derivable:
        lines.append(f"- Derive (grounded in inputs, no invention): {', '.join(derivable)}.")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="meta-info")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--kind", required=True, choices=["paper", "patent"])
    ap.add_argument("--questions", action="store_true", help="print pending questions, don't gate")
    args = ap.parse_args(argv)
    ws = Path(os.path.expanduser(args.workspace)).resolve()
    if args.questions:
        print(json.dumps(questions(check_meta(ws, args.kind)["missing"]), indent=2, ensure_ascii=False))
        return 0
    try:
        enforce(ws, args.kind)
    except SystemExit as e:
        return int(e.code or 0)
    print(f"[meta] {args.kind} meta-info complete ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
