"""Prior-art search analysis + honesty invariants (feeds the patentability screen).

A machine-assisted prior-art search (patents via Google Patents web search, NPL via Semantic
Scholar) produces `inputs/priorart.json`. This module is the DETERMINISTIC layer over that output:

  - analyze():  from the per-claim-element disclosure map, DERIVE §102 anticipation risks (a single
                reference disclosing EVERY element of a claim) and §103 obviousness combinations
                (a minimal set of references jointly covering all elements) — never trusting the
                model to flag them; the math is done here.
  - enforce_honesty(): the CARDINAL rule of prior-art search — absence of found art NEVER proves
                novelty. The verdict is forced to leads-only-non-clearance, a non-removable
                "professional search + attorney" open item is injected, and any "novel / patentable /
                cleared / free to file" conclusion the model emitted is neutralized.
  - validate():  structural + honesty gate ({passed, failures, warnings}).

NOT legal advice. This surfaces prior art to DISTINGUISH and to flag risk; the human/attorney gate
downstream is mandatory and non-bypassable.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MANDATORY_OPEN_ITEM = (
    "A PROFESSIONAL prior-art search and a licensed patent attorney/agent opinion remain MANDATORY "
    "before any filing decision. This machine-assisted search covers only finite databases and "
    "keywords and CANNOT establish novelty — absence of a found reference is not evidence of novelty."
)
NON_CLEARANCE_VERDICT = "leads-only-non-clearance"
# a verdict/conclusion must never imply the invention is clear to file
_FORBIDDEN_VERDICT = re.compile(r"novel|patentab|clear|free to file|no prior art|anticipat\w* none", re.I)
_DISCLOSED = "disclosed"


def load(ws: Path) -> dict | None:
    p = Path(ws) / "inputs" / "priorart.json"
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (ValueError, OSError):
        return None


def _elements(pa: dict) -> dict:
    ce = pa.get("claim_elements") or {}
    return {k: [e for e in (v or []) if isinstance(e, str)] for k, v in ce.items() if isinstance(v, list)}


def analyze(pa: dict) -> dict:
    """Deterministically derive §102 anticipation risks + §103 obviousness combinations."""
    elements = _elements(pa)
    refs = [r for r in (pa.get("references") or []) if isinstance(r, dict)]
    anticipation, obviousness = [], []
    for claim, els in elements.items():
        if not els:
            continue
        # coverage per reference: which of this claim's elements each reference discloses
        cover = {}
        for r in refs:
            disc = (r.get("disclosed_elements") or {}).get(claim, {})
            cover[r.get("id") or r.get("title", "?")] = {
                e for e in els if str(disc.get(e, "")).lower() == _DISCLOSED}
        # §102: one reference discloses EVERY element
        anticipating = set()
        for rid, got in cover.items():
            if set(els) <= got and els:
                anticipating.add(rid)
                anticipation.append({"claim": claim, "reference": rid,
                                     "note": "a single reference appears to disclose every element "
                                             "of this claim — §102 anticipation RISK; distinguish or amend"})
        # §103: greedy minimal set of NON-anticipating references jointly covering all elements
        # (an anticipating ref is a §102 problem, not a combination; exclude it so genuine
        # multi-reference obviousness combinations still surface).
        combo_cover = {rid: got for rid, got in cover.items() if rid not in anticipating}
        need = set(els)
        chosen, guard = [], 0
        while need and guard < len(refs) + 1:
            guard += 1
            best = max(combo_cover.items(), key=lambda kv: len(kv[1] & need), default=(None, set()))
            if not best[0] or not (best[1] & need):
                break
            chosen.append(best[0]); need -= best[1]
        if not need and len(chosen) >= 2:
            obviousness.append({"claim": claim, "references": chosen,
                                "motivation_to_combine": "NOT established by this search",
                                "note": "these references jointly cover all elements — §103 obviousness "
                                        "combination to assess; a rejection needs an articulated "
                                        "motivation-to-combine (KSR), not hindsight"})
    return {"anticipation_102_risks": anticipation, "obviousness_103_combinations": obviousness,
            "references_examined": len(refs), "claims_examined": len(elements)}


def enforce_honesty(pa: dict) -> dict:
    """Force the non-clearance framing. Mutates and returns `pa`."""
    pa["verdict"] = NON_CLEARANCE_VERDICT
    items = [i for i in (pa.get("open_items") or []) if isinstance(i, str)]
    # neutralize any model-emitted conclusion that reads as a clearance
    kept = [i for i in items if not (_FORBIDDEN_VERDICT.search(i) and "risk" not in i.lower()
                                     and "mandatory" not in i.lower())]
    if not any("PROFESSIONAL prior-art search" in i for i in kept):
        kept.insert(0, MANDATORY_OPEN_ITEM)
    pa["open_items"] = kept
    pa.setdefault("generated_by", "machine-assisted search, non-authoritative")
    return pa


def validate(pa: dict) -> dict:
    """Structural + honesty gate. {passed, failures, warnings}."""
    failures, warnings = [], []
    if not isinstance(pa, dict):
        return {"passed": False, "failures": ["priorart.json is not an object"], "warnings": []}
    refs = pa.get("references")
    if not isinstance(refs, list):
        failures.append("missing `references` list")
        refs = []
    if pa.get("verdict") != NON_CLEARANCE_VERDICT:
        failures.append(f"verdict must be {NON_CLEARANCE_VERDICT!r} (a search never establishes novelty)")
    if not any(isinstance(i, str) and "PROFESSIONAL prior-art search" in i for i in (pa.get("open_items") or [])):
        failures.append("the mandatory professional-search + attorney open item is missing")
    for r in refs:
        if not isinstance(r, dict):
            continue
        if not (r.get("id") or r.get("title")):
            warnings.append("a reference has no id/title")
        if not r.get("distinction"):
            warnings.append(f"reference {r.get('id') or r.get('title','?')} has no stated distinction "
                            "over the claim")
    if not refs:
        warnings.append("no references found — this is NOT evidence of novelty; widen the search "
                        "and keep the professional-search open item")
    return {"passed": not failures, "failures": failures, "warnings": warnings}


def summary_notes(pa: dict) -> list[str]:
    """Human-readable notes for the patentability rationale."""
    a = analyze(pa)
    notes = [f"prior-art search examined {a['references_examined']} reference(s) across "
             f"{a['claims_examined']} claim(s) — leads only, NOT a novelty clearance."]
    for risk in a["anticipation_102_risks"]:
        notes.append(f"§102 risk: {risk['reference']} vs {risk['claim']} — {risk['note']}")
    for comb in a["obviousness_103_combinations"]:
        notes.append(f"§103 combination: {comb['claim']} via {comb['references']} — "
                     f"motivation-to-combine {comb['motivation_to_combine']}")
    notes.append(MANDATORY_OPEN_ITEM)
    return notes
