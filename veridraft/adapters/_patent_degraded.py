"""Deterministic degraded fallbacks for the patent engines (used when no LLM/command is available).

These keep the pre-draft steps whole in offline / no-backend mode: an HONEST empty prior-art record
(carrying the mandatory professional-search open item, never a clearance) and a skeleton patent-idea
memo (all required sections present as explicit TODO placeholders + the verbatim boundary statement).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core import patent_idea as idea_core
from ..core import priorart as priorart_core


def _write_degraded_priorart(workspace: str, why: str) -> str:
    ws = Path(workspace)
    (ws / "inputs").mkdir(parents=True, exist_ok=True)
    pa = {"search": {"sources": [], "queries": [], "date_note": f"no search performed ({why})"},
          "claim_elements": {}, "references": [], "open_items": []}
    priorart_core.enforce_honesty(pa)
    pa["analysis"] = priorart_core.analyze(pa)
    out = ws / "inputs" / "priorart.json"
    out.write_text(json.dumps(pa, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(out)


def _write_skeleton_idea(workspace: str, title: str, why: str) -> str:
    """A skeleton patent-idea memo + case: required sections present as TODOs, boundary verbatim."""
    ws = Path(workspace)
    idea_dir = ws / "idea"
    idea_dir.mkdir(parents=True, exist_ok=True)
    md = idea_core.skeleton_markdown(title, note=why)
    (idea_dir / "patent_idea.md").write_text(md, encoding="utf-8")
    case = idea_core.skeleton_case(title)
    (idea_dir / "patentability_case.json").write_text(json.dumps(case, indent=2, ensure_ascii=False),
                                                      encoding="utf-8")
    return str(idea_dir / "patent_idea.md")
