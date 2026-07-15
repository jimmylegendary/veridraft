#!/usr/bin/env python3
"""Deterministic honesty gate for the NATURALIZE pass — the model-independent floor.

Naturalize is a COPYEDIT that makes AI-drafted prose read like fluent human academic writing
(de-AI-ese, sentence-length variation, concision). It is NOT a "humanizer": it never reads, targets,
or optimizes against an AI-detector score — the goal is the READER's clarity, with the AI-use
disclosure kept fully intact. A copyedit must not change what the paper SAYS, so this gate checks, by
DIFF against the original, that meaning-bearing content is invariant:

  - NUMBER identity   — the multiset of evidence-bearing numbers (with units/%/×/decimals) must be
                        IDENTICAL. A copyedit never changes, adds, or drops a result. → BLOCKING.
  - CITATION identity — the set of \\cite/\\ref/\\eqref/\\label keys must be identical. → BLOCKING.
  - MATH/verbatim freeze — inline/display math, equation envs, \\verb, verbatim/lstlisting must be
                        byte-identical (whitespace-normalized). → BLOCKING.
  - No CLAIM STRENGTHENING — a proof/absolute modal ("proves", "guarantees", "eliminates", "always")
                        present in the naturalized text but NOT the original = epistemic drift.→BLOCKING.
  - Hedge preservation — a large drop in hedges ("may", "suggests", "likely") → ADVISORY (verify a
                        "may reduce" wasn't silently turned into "reduces").
  - Disclosure preservation — an AI-use disclosure sentence in the original must survive. → BLOCKING.
No LLM: pure regex/set diff over the two .tex bodies.
"""
from __future__ import annotations

import re
from collections import Counter

import marketing_lints   # reuse _prose, _STAT_RE, _hype (added-superlative advisory)

# proof / absolute modality — strengthening a claim to one of these is epistemic drift
_STRONG = re.compile(
    r"\b(?:prove[sd]?|proven|guarantee[sd]?|guaranteeing|ensure[sd]?|ensuring|eliminat(?:e|es|ed|ing)|"
    r"always|never|conclusively|definitively|undeniably|invariably|the only\b|optimal(?:ly)?)\b", re.I)
# epistemic hedges — a copyedit may reword but should not systematically delete them
_HEDGE = re.compile(
    r"\b(?:may|might|could|appears?|seems?|suggest(?:s|ed)?|indicat(?:e|es|ed)|tend(?:s|ed)?|"
    r"potential(?:ly)?|possibl[ey]|likely|arguably|relatively|approximately|roughly|generally|"
    r"typically|often|largely|partially|somewhat|associated with)\b", re.I)
# an AI-use disclosure sentence
_DISCLOSURE = re.compile(r"(?:generative AI|large language model|\bLLM\b|AI[- ]assist|AI writing|"
                         r"AI[- ]generated|with the (?:aid|help|assistance) of).{0,80}", re.I)
# math / verbatim spans that must be frozen
_MASK_PATS = [
    r"\$\$.+?\$\$", r"(?<!\\)\$[^$]+?\$", r"\\\[.+?\\\]", r"\\\(.+?\\\)",
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?|eqnarray\*?)\}.*?\\end\{\1\}",
    r"\\verb\|[^|]*\|", r"\\begin\{(verbatim|lstlisting)\}.*?\\end\{\2\}",
]
_MASK_RE = re.compile("|".join(f"(?:{p})" for p in _MASK_PATS), re.S)
_KEY_RE = re.compile(r"\\(?:cite[a-z]*|ref|eqref|autoref|cref|Cref|label)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")


def _numbers(text: str) -> Counter:
    prose = marketing_lints._prose(text)
    return Counter(marketing_lints._norm_stat(m.group(0)) for m in marketing_lints._STAT_RE.finditer(prose))


def _keys(text: str) -> Counter:
    keys: list[str] = []
    for m in _KEY_RE.finditer(text):
        keys += [k.strip() for k in m.group(1).split(",") if k.strip()]
    return Counter(keys)


def _masks(text: str) -> Counter:
    return Counter(re.sub(r"\s+", " ", m.group(0)).strip() for m in _MASK_RE.finditer(text))


def _disclosures(text: str) -> set[str]:
    prose = marketing_lints._prose(text)
    return {re.sub(r"\s+", " ", m.group(0)).strip().lower()[:60] for m in _DISCLOSURE.finditer(prose)}


def lint_naturalize(natural_tex: str, original_tex: str) -> dict:
    """{'blocking': [...], 'advisory': [...]} for the naturalized copyedit vs the original."""
    blocking: list[str] = []
    advisory: list[str] = []

    # NUMBER identity (both directions)
    on, nn = _numbers(original_tex), _numbers(natural_tex)
    for st, c in (on - nn).items():
        blocking.append(f"number '{st}' from the paper is missing/changed in the naturalized version "
                        f"({c} occurrence(s)) — a copyedit must not alter results")
    for st, c in (nn - on).items():
        blocking.append(f"number '{st}' appears in the naturalized version but not the paper "
                        f"({c} occurrence(s)) — no number may be introduced")

    # CITATION / ref key identity
    ok_, nk = _keys(original_tex), _keys(natural_tex)
    if ok_ != nk:
        for k in sorted(set(ok_) - set(nk)):
            blocking.append(f"citation/ref key '{k}' was dropped in the naturalized version")
        for k in sorted(set(nk) - set(ok_)):
            blocking.append(f"citation/ref key '{k}' was added in the naturalized version")

    # MATH / verbatim freeze
    om, nm = _masks(original_tex), _masks(natural_tex)
    if om != nm:
        n_changed = sum((om - nm).values()) + sum((nm - om).values())
        blocking.append(f"{n_changed} math/verbatim span(s) changed — a copyedit must leave "
                        "equations, inline math, and code byte-identical")

    # CLAIM STRENGTHENING: a proof/absolute modal added that the original did not use
    os_, ns = set(m.group(0).lower() for m in _STRONG.finditer(marketing_lints._prose(original_tex))), \
        set(m.group(0).lower() for m in _STRONG.finditer(marketing_lints._prose(natural_tex)))
    for s in sorted(ns - os_):
        blocking.append(f"claim strengthened: '{s}' appears in the naturalized version but not the "
                        "paper — a copyedit must not upgrade an epistemic claim")

    # HEDGE preservation (advisory)
    oh = len(_HEDGE.findall(marketing_lints._prose(original_tex)))
    nh = len(_HEDGE.findall(marketing_lints._prose(natural_tex)))
    if oh >= 4 and nh < 0.6 * oh:
        advisory.append(f"hedges dropped from {oh} to {nh} — verify a 'may/suggests/likely' claim was "
                        "not silently turned into an assertion")

    # DISCLOSURE preservation
    lost = _disclosures(original_tex) - _disclosures(natural_tex)
    if lost:
        blocking.append("an AI-use disclosure statement present in the original is missing from the "
                        "naturalized version — the disclosure must never be removed")

    # added superlative (reuse the marketing advisory)
    for h in marketing_lints._hype(marketing_lints._prose(natural_tex)):
        if h not in set(marketing_lints._hype(marketing_lints._prose(original_tex))):
            advisory.append(f"added superlative '{h}' not in the paper — soften or confirm it's earned")
            break

    return {"blocking": blocking, "advisory": advisory}


# ---- AI-use disclosure auto-drafter (the honest counterpart to a "humanizer") -------------------

def draft_disclosure(kind: str = "paper", venue: str = "", tools: list[str] | None = None) -> str:
    """A truthful AI-assistance disclosure the author places in Methods/Acknowledgements. This is what
    an honesty-first tool ships INSTEAD of a detector-evasion humanizer: disclose + take responsibility."""
    tools = tools or ["an AI writing assistant (drafting and copyediting), wrapped by Veridraft"]
    used = "; ".join(tools)
    where = ("the Responsible NLP checklist + Acknowledgements" if re.search(r"acl|naacl|emnlp", venue, re.I)
             else "the paper's methodology/Acknowledgements" if re.search(r"neurips|icml|iclr|cvpr", venue, re.I)
             else "the Acknowledgements")
    return (f"AI-use disclosure (place in {where}): The authors used {used}. All claims, numbers, "
            "citations, and figures were verified by the authors, who take full responsibility for the "
            "content. Generative AI is not an author. (Naturalize is a copyedit for readability; it "
            "does not target or optimize against any AI-detection tool.)")
