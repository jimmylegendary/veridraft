#!/usr/bin/env python3
"""Deterministic marketing gate (P1) — the honesty floor for the marketing-revised paper version.

The marketing flow revises a finished paper into a discoverability-optimized SECOND version. Ordinary
marketing inflates; an academic over-claim is fatal. So this gate is model-independent and works by
DIFF: anything the marketing version ADDS beyond the original manuscript (a statistic not in the
paper, a superlative the paper never earned) is the violation. Reframing/reordering/sharpening is
fine; inventing or inflating is not.

- Added STAT (a number-with-unit/%/×/-fold in the marketing text whose value is absent from the
  original paper AND the experimental log) → BLOCKING. No fabricated or rounded-up numbers.
- Added HYPE (a superlative/absolute present in the marketing text but NOT in the original) →
  ADVISORY (a human confirms the paper substantiates it, or softens it).
No LLM: pure regex/set diff over the two .tex bodies + the source numbers.
"""
from __future__ import annotations

import re

_HYPE = [
    r"first[- ]?(?:ever|to|method|tool|system|framework|approach|work|study|paper|technique)\b",
    r"world'?s (?:first|best|only)", r"revolution(?:ary|ize[sd]?)",
    r"breakthrough", r"unprecedented", r"paradigm[- ]shift\w*", r"game[- ]?chang\w+", r"ground[- ]?breaking",
    r"prov(?:e|es|en|ed)\b", r"guarantee[sd]?", r"best[- ]in[- ]class", r"state[- ]of[- ]the[- ]art",
    r"\bSOTA\b", r"dramatic(?:ally)?", r"vastly", r"orders of magnitude", r"massive(?:ly)?",
    r"perfect(?:ly)?", r"never before", r"only (?:tool|method|system|work|approach|framework)",
    r"solv(?:e|es|ed) (?:the )?\w+ (?:problem|challenge)", r"eliminat(?:e|es|ed) (?:all|every)",
    r"cutting[- ]edge", r"world[- ]?class", r"the definitive",
]
_HYPE_RE = re.compile("|".join(f"(?:{h})" for h in _HYPE), re.I)

# a STATISTIC: a number immediately trailed by a unit/%/multiplier, or a bare decimal (e.g. 3.2)
_STAT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:%|percent|x\b|×|-?fold|ms|µs|us\b|ns\b|s\b|GB|MB|TB|KB|FLOPs?|"
    r"tokens?/s|times(?: faster| slower| smaller| larger| higher| lower)?|speedups?)"
    r"|\b\d+\.\d+\b", re.I)


def _prose(tex: str) -> str:
    """Best-effort strip of LaTeX so we lint the human-readable text, not command names."""
    tex = re.sub(r"(?<!\\)%.*", "", tex)                       # comments (a real % is escaped as \%)
    tex = re.sub(r"\\([%&#_$])", r"\1", tex)                   # LaTeX escapes → plain, so 99\% reads as 99%
    tex = re.sub(r"\\(?:label|ref|cite[a-z]*|includegraphics|bibliography|usepackage|documentclass)"
                 r"\s*(?:\[[^\]]*\])?\s*\{[^}]*\}", " ", tex)  # keys/refs (don't lint their contents)
    tex = re.sub(r"\\[a-zA-Z@]+\s*(?:\[[^\]]*\])?", " ", tex)  # remaining control sequences
    tex = re.sub(r"[{}]", " ", tex)
    return tex


def _norm_stat(s: str) -> str:
    m = re.search(r"\d+(?:\.\d+)?", s)
    return m.group(0).rstrip("0").rstrip(".") if m and "." in m.group(0) else (m.group(0) if m else s)


def _stats(text: str) -> set[str]:
    return {_norm_stat(m.group(0)) for m in _STAT_RE.finditer(text)}


def _hype(text: str) -> list[str]:
    return [m.group(0).lower() for m in _HYPE_RE.finditer(text)]


def lint_marketing(marketing_tex: str, original_tex: str, source_numbers: str = "") -> dict:
    """Return {'blocking': [...], 'advisory': [...]} for the marketing version vs the original."""
    mk, og = _prose(marketing_tex), _prose(original_tex)
    src_stats = _stats(og) | _stats(_prose(source_numbers))
    blocking, advisory = [], []
    for st in sorted(_stats(mk) - src_stats):
        blocking.append(f"added statistic '{st}' is not in the paper or the experimental log — "
                        "no number may appear in marketing that the evidence does not support")
    og_hype = set(_hype(og))
    seen = set()
    for h in _hype(mk):
        if h not in og_hype and h not in seen:
            seen.add(h)
            advisory.append(f"added superlative '{h}' not present in the original paper — confirm the "
                            "results substantiate it, or soften it")
    return {"blocking": blocking, "advisory": advisory}


def metadata_kit(title: str, abstract: str, keywords: list[str], url: str = "") -> dict:
    """Deterministic SEO/GEO metadata from the (already gated) title/abstract — no invention."""
    jsonld = {
        "@context": "https://schema.org", "@type": "ScholarlyArticle",
        "headline": title, "name": title, "abstract": abstract,
        "keywords": keywords, "url": url,
    }
    meta_tags = ([f'<meta name="citation_title" content="{title}">']
                 + [f'<meta name="citation_keywords" content="{k}">' for k in keywords]
                 + [f'<meta name="DC.title" content="{title}">',
                    f'<meta name="description" content="{abstract[:300]}">'])
    return {"json_ld": jsonld, "meta_tags": meta_tags, "keywords": keywords}
