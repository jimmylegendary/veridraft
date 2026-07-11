"""Patent-idea memo — schema + deterministic completeness gate (the pre-drafting artifact).

Before a full application (claims, spec), a strong practice writes an INVENTION-DISCLOSURE / idea
memo: the core inventive concept in plain language, concept figures, and a per-statutory-requirement
patentability CASE (basis + grounding + open items) — NOT claims, NOT a granted-patent conclusion.
A human/attorney reviews it to decide whether to file.

This module owns:
  - the required section list + the verbatim BOUNDARY_STATEMENT (the honest limit of an idea memo);
  - the patentability_case.json shape (utility §101 / novelty §102 / non-obviousness §103 /
    enablement+written-description §112);
  - skeleton generators for the offline/degraded path (all sections as TODO placeholders);
  - completeness(): a deterministic gate — all sections present, boundary verbatim, all four
    requirements with a non-empty basis + grounding, and §102/§103 keeping a non-empty open_items
    (an idea memo must NOT self-assert novelty or non-obviousness).
"""
from __future__ import annotations

import re

# The 12 required H2 sections (order matters for rendering, not for the check).
REQUIRED_SECTIONS = [
    "Field & Problem",
    "Inventive Concept (Plain Language)",
    "How It Works",
    "Concept Figures",
    "Distinctions Over Known Approaches",
    "Advantages",
    "Enablement Detail",
    "Limitations & Future Work",
    "Reduction to Practice & Disclosure Record",
    "Inventors",
    "Patentability Case (Summary)",
    "Boundary Statement",
]

REQUIREMENT_KEYS = ["utility_101", "novelty_102", "nonobviousness_103", "enablement_wd_112"]
_STATUTES = {"utility_101": "35 U.S.C. 101", "novelty_102": "35 U.S.C. 102",
             "nonobviousness_103": "35 U.S.C. 103", "enablement_wd_112": "35 U.S.C. 112"}
# §102 and §103 can NEVER be self-asserted as settled → their open_items must stay non-empty.
_MUST_DEFER = {"novelty_102", "nonobviousness_103"}

BOUNDARY_STATEMENT = (
    "This memo argues a patentability CASE from available evidence. It does not assert novelty, "
    "non-obviousness, or patentability as concluded fact. A professional prior-art search and review "
    "by a licensed patent attorney/agent remain mandatory before any filing decision."
)
_PROF_SEARCH_ITEM = ("A professional prior-art search and a licensed patent attorney/agent opinion "
                     "remain MANDATORY before filing — this memo does not establish this requirement.")


def _has_section(md: str, title: str) -> bool:
    # match an H2 heading whose text starts with the section title (tolerant of trailing words)
    pat = re.compile(r"^##\s+" + re.escape(title.split(" (")[0]), re.M)
    return bool(pat.search(md or ""))


def completeness(idea_md: str, case: dict, priorart: dict | None = None) -> dict:
    """Deterministic gate over the idea memo + case. {passed, failures, warnings}."""
    failures, warnings = [], []
    md = idea_md or ""
    for sec in REQUIRED_SECTIONS:
        if not _has_section(md, sec):
            failures.append(f"missing section: ## {sec}")
    if BOUNDARY_STATEMENT not in re.sub(r"\s+", " ", md):
        failures.append("the verbatim Boundary Statement is missing (an idea memo must state its limit)")

    reqs = {r.get("requirement"): r for r in (case.get("requirements") or []) if isinstance(r, dict)} \
        if isinstance(case, dict) else {}
    for key in REQUIREMENT_KEYS:
        r = reqs.get(key)
        if not r:
            failures.append(f"patentability_case missing requirement: {key}")
            continue
        if not (r.get("basis") or "").strip():
            failures.append(f"{key}: empty `basis` (state the evidence/argument)")
        if not (r.get("grounded_in") or []):
            failures.append(f"{key}: empty `grounded_in` (point at a section/figure/prior-art/artifact)")
        if key in _MUST_DEFER and not (r.get("open_items") or []):
            failures.append(f"{key}: `open_items` must be non-empty — an idea memo cannot settle "
                            f"{'novelty' if key=='novelty_102' else 'non-obviousness'} itself")
        # grounded_in anchors: figure:/priorart: pointers should resolve (warn, don't fail)
        for anchor in (r.get("grounded_in") or []):
            if isinstance(anchor, str) and anchor.startswith("figure:"):
                fid = anchor.split(":", 1)[1]
                if fid and fid.lower() not in md.lower():
                    warnings.append(f"{key}: figure anchor {anchor!r} not found in Concept Figures")
            if isinstance(anchor, str) and anchor.startswith("priorart:"):
                rid = anchor.split(":", 1)[1].split("#")[0]
                ids = {str(x.get("id")) for x in ((priorart or {}).get("references") or []) if isinstance(x, dict)}
                if rid and ids and rid not in ids:
                    warnings.append(f"{key}: prior-art anchor {anchor!r} not in priorart.json")

    if not re.search(r"^##\s+Concept Figures", md, re.M) or "TODO" in _section_body(md, "Concept Figures"):
        warnings.append("no concept figure present yet (architecture/flow diagram — not a data plot)")
    return {"passed": not failures, "failures": failures, "warnings": warnings}


def _section_body(md: str, title: str) -> str:
    m = re.search(r"^##\s+" + re.escape(title.split(" (")[0]) + r".*?$(.*?)(?=^##\s|\Z)", md or "",
                  re.M | re.S)
    return m.group(1) if m else ""


# ---- skeleton generators (offline / degraded path) ----------------------------------------------

def skeleton_case(title: str) -> dict:
    def req(key, hint):
        item = ([_PROF_SEARCH_ITEM] if key in _MUST_DEFER else
                ["confirm with counsel"])
        return {"requirement": key, "statute": _STATUTES[key], "basis": "", "grounded_in": [],
                "open_items": item, "hint": hint}
    return {"invention_title": title, "generated_by": "skeleton (no model) — requires completion",
            "requirements": [
                req("utility_101", "concrete process/machine/manufacture with a real-world use; not abstract"),
                req("novelty_102", "per closest reference, which claimed element(s) it lacks; log disclosure dates"),
                req("nonobviousness_103", "why the combination is not obvious to a POSA; secondary considerations"),
                req("enablement_wd_112", "enough detail a POSA could build it; possession shown by code/data/prototype"),
            ]}


def skeleton_markdown(title: str, note: str = "") -> str:
    tag = f" ({note})" if note else ""
    todo = f"> TODO{tag}: complete this section."
    body = {
        "Field & Problem": f"{todo} State the technical field and the specific problem solved.",
        "Inventive Concept (Plain Language)": f"{todo} The core idea in 3-6 sentences, no jargon, no claims.",
        "How It Works": f"{todo} The elements and how they cooperate to solve the problem.",
        "Concept Figures": f"{todo} Architecture/flow/sequence diagram(s) — NOT data plots. Add figure IDs (F1, F2).",
        "Distinctions Over Known Approaches": f"{todo} Closest prior art / status quo; per item, what element it lacks.",
        "Advantages": f"{todo} Technical + economic benefits vs known solutions.",
        "Enablement Detail": f"{todo} Parameters/architecture/protocol so a POSA could build it; possession evidence.",
        "Limitations & Future Work": f"{todo} Candid disadvantages + next 12-18 month directions.",
        "Reduction to Practice & Disclosure Record": f"{todo} Prototype/test status; PUBLIC-DISCLOSURE events WITH DATES.",
        "Inventors": f"{todo} True contributors only (names, contributions, citizenship).",
        "Patentability Case (Summary)": f"{todo} Rendered from patentability_case.json (§101/§102/§103/§112).",
        "Boundary Statement": BOUNDARY_STATEMENT,
    }
    out = [f"# Patent Idea: {title}\n"]
    for sec in REQUIRED_SECTIONS:
        out.append(f"## {sec}\n\n{body[sec]}\n")
    return "\n".join(out)
