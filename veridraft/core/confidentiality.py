"""Confidentiality gate (ADR-0007) — the second conjunction gate.

Reuses CAW-02 boundary/visibility semantics VERBATIM (Veridraft never re-derives labels;
it consumes effective labels carried in the import envelope and re-asserts them at its
own egress boundary). Evaluated at two points:

  1. Ingest classification (at gate): artifact effective boundary/visibility =
     lattice-max over selected claims → assigns the confidentiality TRACK. Routes only.
  2. Egress decision (at the sink): decide(artifact, audience) — total, side-effect-free,
     default-deny — PLUS a redaction re-sweep over every string the engine emitted.
     Egress is the load-bearing gate; a single redaction hit aborts publication.

Three inherited invariants: monotone propagation (no laundering), generated-text-is-not-
evidence (carried by the evidence gate), and fail-closed default-deny.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import (
    Audience,
    Boundary,
    Claim,
    ConfidentialityTrack,
    Visibility,
)

# The redaction ruleset is a VENDORED, version-pinned copy of CAW-02 semantics
# (ADR-0007 decision 6) — no shared runtime substrate. Bump this when the vendored
# copy is refreshed; the version is stamped on every egress decision for audit.
RULESET_VERSION = "caw02-redaction-vendored-v0"


@dataclass
class EffectiveLabels:
    boundary: Boundary
    visibility: Visibility
    track: ConfidentialityTrack


def classify(claims: list[Claim]) -> EffectiveLabels:
    """Ingest classification: lattice-max over selected claims → track. Fail-closed."""
    boundary = Boundary.max([c.boundary for c in claims])
    visibility = Visibility.effective([c.visibility for c in claims])
    track = (
        ConfidentialityTrack.PUBLIC_SOURCE_ASSISTED
        if boundary is Boundary.PUBLIC and visibility is Visibility.TEAM
        else ConfidentialityTrack.INTERNAL_REVIEW_REQUIRED
    )
    return EffectiveLabels(boundary, visibility, track)


@dataclass
class Decision:
    allow: bool
    reason: str


def decide(boundary: Boundary, visibility: Visibility, audience: Audience) -> Decision:
    """Total, side-effect-free, default-deny egress allow-list (ADR-0007 §2.2).

    - private ⇒ never allow (any audience).
    - public audience ⇒ allow iff boundary==public AND visibility==team.
    - internal audience ⇒ allow up to boundary==internal.
    - counsel audience ⇒ allow up to confidential (privileged) — still redaction-swept.
    - anything unrecognized ⇒ block.
    """
    if visibility is Visibility.PRIVATE:
        return Decision(False, "effective visibility is private — never exportable")
    if audience is Audience.PUBLIC:
        if boundary is Boundary.PUBLIC:
            return Decision(True, "public sink allowed: boundary=public, visibility=team")
        return Decision(False, f"public sink blocked: boundary={boundary.value} > public")
    if audience is Audience.INTERNAL:
        if boundary.rank <= Boundary.INTERNAL.rank:
            return Decision(True, f"internal sink allowed: boundary={boundary.value}")
        return Decision(False, f"internal sink blocked: boundary={boundary.value} > internal")
    if audience is Audience.COUNSEL:
        return Decision(True, "counsel (privileged) allowed up to confidential")
    return Decision(False, f"unrecognized audience {audience!r} — default-deny")


# --- redaction ruleset ------------------------------------------------------

@dataclass
class Ruleset:
    """Vendored redaction ruleset. Term lists + PII regexes. Deterministic, offline."""

    version: str = RULESET_VERSION
    codenames: tuple[str, ...] = ("Falcon", "Meteor", "Halberd")
    customers: tuple[str, ...] = ("AcmeCorp", "Initech")
    fabs: tuple[str, ...] = ("Fab-7", "Fab-12")
    extra_terms: tuple[str, ...] = ()

    @classmethod
    def load(cls, config: dict | None = None) -> "Ruleset":
        config = config or {}
        return cls(
            codenames=tuple(config.get("codenames", cls.codenames)),
            customers=tuple(config.get("customers", cls.customers)),
            fabs=tuple(config.get("fabs", cls.fabs)),
            extra_terms=tuple(config.get("extra_terms", ())),
        )

    def _term_patterns(self) -> list[tuple[str, re.Pattern]]:
        pats: list[tuple[str, re.Pattern]] = []
        groups = {
            "codename": self.codenames,
            "customer": self.customers,
            "fab": self.fabs,
            "term": self.extra_terms,
        }
        for kind, terms in groups.items():
            for t in terms:
                if not t:
                    continue
                # Substring match (case-insensitive), NOT word-bounded, and tolerant of a single
                # injected separator between characters ("Fal con", "Fal-con") so a codename cannot
                # be laundered by splitting it. The text is NFKC-normalized in scan() first so
                # full-width look-alikes are folded. Fail-closed — over-blocking is recoverable.
                pat = r"[\s._/\\\-]{0,2}".join(re.escape(ch) for ch in t)
                pats.append((kind, re.compile(pat, re.IGNORECASE)))
        return pats

    _PII = {
        "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        # A phone-shaped number. Boundaries exclude identifier/digit context. We match only
        # forms carrying a phone-specific SIGNAL — an explicit '+' (E.164), a parenthesized
        # area code, or two real separators — so a DOI / arXiv id / '068431-1189'-style
        # identifier is not caught. A bare separator-less 10-digit run is deliberately NOT
        # matched: it is indistinguishable from a numeric id and redaction is fail-closed, so a
        # false positive would wrongly block a legitimate publish.
        "phone": re.compile(
            r"(?<![\w.\d])(?:"
            r"\+\d{1,3}[ .\-]?\d{2,4}[ .\-]?\d{3,4}[ .\-]?\d{2,4}"   # E.164 / +country (has '+')
            r"|\(\d{2,4}\)[ .]?\d{3,4}[ .\-]?\d{3,4}"               # parenthesized area code
            r"|\d{2,4}[ .\-]\d{3,4}[ .\-]\d{3,4}"                   # three groups, two separators
            r")(?![\w.\d])"),
    }

    def scan(self, text: str) -> list[dict]:
        import unicodedata
        text = unicodedata.normalize("NFKC", text or "")   # fold full-width / compatibility forms
        text = re.sub("[\u200b\u200c\u200d\u00ad\ufeff]", "", text)   # strip zero-width / soft hyphen
        hits: list[dict] = []
        for kind, pat in self._term_patterns():
            for m in pat.finditer(text):
                hits.append({"type": kind, "span": m.group(0), "at": m.start()})
        for kind, pat in self._PII.items():
            for m in pat.finditer(text):
                hits.append({"type": kind, "span": m.group(0), "at": m.start()})
        return hits

    def redact(self, text: str) -> str:
        out = text
        for _, pat in self._term_patterns():
            out = pat.sub("[REDACTED]", out)
        for pat in self._PII.values():
            out = pat.sub("[REDACTED]", out)
        return out


def scan_strings(strings: list[str], ruleset: Ruleset | None = None) -> list[dict]:
    """Egress re-sweep over every string the engine emitted. Any hit aborts publication."""
    rs = ruleset or Ruleset()
    hits: list[dict] = []
    for i, s in enumerate(strings):
        if not s:
            continue
        for h in rs.scan(s):
            hits.append({**h, "field": i})
    return hits
