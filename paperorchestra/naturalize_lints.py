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
# a RESULT/claim verb — the payload of an empirical claim
_CLAIM_VERB = re.compile(
    r"\b(?:reduc\w+|increas\w+|improv\w+|decreas\w+|lower\w*|rais\w+|boost\w+|outperform\w*|achiev\w+|"
    r"yield\w+|enabl\w+|prevent\w+|caus\w+|surpass\w*|accelerat\w+|speed\w*|outpace\w*|beat\w+|scal\w+|"
    r"cut\w*|shrink\w*|halv\w+|doubl\w+|eliminat\w+)\b", re.I)
_EPISTEMIC = re.compile(
    r"\b(?:may|might|could|would|suggest\w*|indicat\w*|appears?|seems?|potential\w*|possibl[ey]|"
    r"likely|tend(?:s|ed)?)\b", re.I)


def _claim_balance(prose: str) -> tuple[int, int]:
    """(hedged_claims, bare_claims): each result-verb occurrence, classified by whether an epistemic
    hedge governs it (within a short look-back window). De-hedging turns a hedged claim into a bare
    one, so it shows as hedged↓ AND bare↑ — robust to a verb reword (may reduce → may cut keeps both)
    and to dropping a redundant/non-claim hedge (the verb stays hedged)."""
    hedged = bare = 0
    for m in _CLAIM_VERB.finditer(prose):
        w = prose[max(0, m.start() - 60):m.start()]
        # keep only the CURRENT clause: cut at the nearest preceding clause boundary so a hedge in an
        # adjacent clause ("we suggest X and our method reduces Y") doesn't count as governing the verb
        cut = max(w.rfind(". "), w.rfind("; "), w.rfind(", "), w.rfind(" and "), w.rfind(" but "),
                  w.rfind(" that "), w.rfind(" which "), w.rfind(" while "))
        if cut >= 0:
            w = w[cut + 1:]
        if _EPISTEMIC.search(w):
            hedged += 1
        else:
            bare += 1
    return hedged, bare
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


# Naturalize needs FULL number identity (a copyedit changes NO number), so — unlike the marketing
# added-stat gate — it must also catch BARE integers (500 trials, 87 F1) and comma-grouped numbers
# (1,000×). A dedicated matcher keeps the marketing regex (and its year/index tolerance) untouched.
_NUM_UNIT = (r"(?:%|percent|×|x|-?fold|billion|million|thousand|trillion|hundred|ms|µs|us|ns|GB|MB|"
             r"TB|KB|PB|FLOPs?|tokens?/s|epochs?|params?|parameters)")
_NUM_RE = re.compile(
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?\s?" + _NUM_UNIT + r"?"        # comma-grouped (+opt unit): 1,000x
    + r"|\d+\.\d+\s?" + _NUM_UNIT + r"?"                         # decimal (+opt unit): 12.5%
    + r"|\d+\s?" + _NUM_UNIT                                     # integer + REQUIRED unit: 7 billion
    + r"|\b\d{2,}\b",                                            # bare integer >= 2 digits: 87, 500
    re.I)


def _norm_num(s: str) -> str:
    s = re.sub(r"\s+", "", s).replace(",", "").lower()
    m = re.match(r"(\d+(?:\.\d+)?)(.*)", s)
    if not m:
        return s
    num, unit = m.group(1), m.group(2)
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    return num + unit


def _numbers(text: str) -> Counter:
    prose = marketing_lints._prose(text)
    return Counter(_norm_num(m.group(0)) for m in _NUM_RE.finditer(prose))


def _keys(text: str) -> Counter:
    keys: list[str] = []
    for m in _KEY_RE.finditer(text):
        keys += [k.strip() for k in m.group(1).split(",") if k.strip()]
    return Counter(keys)


_VERBATIM_MARK = re.compile(r"\\verb\||\\begin\{(?:verbatim|lstlisting)\}")


def _masks(text: str) -> Counter:
    out = []
    for m in _MASK_RE.finditer(text):
        span = m.group(0)
        if _VERBATIM_MARK.search(span[:24]):
            out.append("V:" + re.sub(r"\s+", " ", span).strip())   # code: interior spacing is meaningful
        else:
            out.append("M:" + re.sub(r"\s+", "", span))            # math: whitespace-insensitive ($a+b$==$a + b$)
    return Counter(out)


def _disclosure_present(text: str) -> bool:
    """An AI-use disclosure is present if ANY disclosure trigger appears — so rewording the sentence
    (still present) does not read as removed; only a genuine removal blocks."""
    return bool(_DISCLOSURE.search(marketing_lints._prose(text)))


def _strong_key(tok: str) -> str:
    """Collapse a strong-modal surface form to a family key so inflection isn't a false positive."""
    t = tok.lower()
    for stem, key in (("prov", "prove"), ("guarant", "guarantee"), ("ensur", "ensure"),
                      ("eliminat", "eliminate"), ("optim", "optimal"), ("conclusiv", "conclusively"),
                      ("definitiv", "definitively"), ("undeniabl", "undeniably"), ("invariabl", "invariably")):
        if t.startswith(stem):
            return key
    return t


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

    # CLAIM STRENGTHENING: a proof/absolute modal FAMILY added that the original did not use
    os_ = {_strong_key(m.group(0)) for m in _STRONG.finditer(marketing_lints._prose(original_tex))}
    ns = {_strong_key(m.group(0)) for m in _STRONG.finditer(marketing_lints._prose(natural_tex))}
    for s in sorted(ns - os_):
        blocking.append(f"claim strengthened: '{s}' appears in the naturalized version but not the "
                        "paper — a copyedit must not upgrade an epistemic claim")

    # DE-HEDGING: a hedged claim ("may reduce") turned into a bare assertion ("reduces") shows as
    # fewer hedged claims AND more bare claims → BLOCKING (epistemic drift the docstring promises).
    op, npz = marketing_lints._prose(original_tex), marketing_lints._prose(natural_tex)
    ohc, obc = _claim_balance(op)
    nhc, nbc = _claim_balance(npz)
    if nhc < ohc and nbc > obc:
        blocking.append(f"{ohc - nhc} hedged claim(s) were de-hedged (e.g. 'may reduce' → 'reduces') — "
                        "a copyedit must not upgrade a hedged claim into an assertion; reword, don't drop the hedge")

    # overall hedge attrition, incl. filler hedges (advisory only)
    oh, nh = len(_HEDGE.findall(op)), len(_HEDGE.findall(npz))
    if oh >= 5 and nh < 0.6 * oh:
        advisory.append(f"hedges dropped from {oh} to {nh} — verify no 'may/suggests/likely' claim was "
                        "silently firmed up")

    # DISCLOSURE preservation (present→absent only; a reworded-but-present disclosure is fine)
    disclosure_in_original = _disclosure_present(original_tex)
    if disclosure_in_original and not _disclosure_present(natural_tex):
        blocking.append("an AI-use disclosure statement present in the original is missing from the "
                        "naturalized version — the disclosure must never be removed")

    # added superlative (reuse the marketing advisory)
    for h in marketing_lints._hype(marketing_lints._prose(natural_tex)):
        if h not in set(marketing_lints._hype(marketing_lints._prose(original_tex))):
            advisory.append(f"added superlative '{h}' not in the paper — soften or confirm it's earned")
            break

    return {"blocking": blocking, "advisory": advisory,
            "disclosure_in_original": disclosure_in_original}


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
