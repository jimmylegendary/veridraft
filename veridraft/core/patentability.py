"""Patentability screen (ADR-0004 §3) — the pre-draft gate for the patent path.

Reuses the shared novelty front + patent-specific tests. NOT legal advice; the human/
attorney gate downstream is mandatory. Claim typing maps directly: P1/P2 method/tool
claims are the patentable substrate; P3 future-device claims are projections and get
flagged `requires-enablement-review` rather than treated as independently patentable.

A `no-go` verdict blocks drafting exactly as a failed evidence gate does.
"""
from __future__ import annotations

from .models import Claim, ClaimType, PatentabilityVerdict


def screen(claims: list[Claim], prior_art: list[dict] | None = None) -> dict:
    prior_art = prior_art or []
    substrate = [c for c in claims if c.type in (ClaimType.P1, ClaimType.P2)]
    projections = [c for c in claims if c.type is ClaimType.P3]

    notes: list[str] = []
    per_claim: dict[str, str] = {}
    design_doc_only = []
    for c in substrate:
        # every element must trace to admissible evidence (enablement/written description)
        if c.admissible_evidence():
            per_claim[c.claim_id] = "patentable-substrate: evidence-backed (enablement support present)"
            if not c.result_refs:
                design_doc_only.append(c.claim_id)
        else:
            per_claim[c.claim_id] = "weak: no admissible evidence for enablement"
    for c in projections:
        per_claim[c.claim_id] = ("requires-enablement-review (P3 future-device): a projection, not "
                                 "independently patentable until reduced to practice / constructively enabled")

    # verdict
    if not substrate:
        verdict = PatentabilityVerdict.NO_GO
        notes.append("no P1/P2 method/tool claims — no patentable substrate (only projections/none).")
    elif any("weak" in v for v in per_claim.values()):
        verdict = PatentabilityVerdict.WEAK
        notes.append("some substrate claims lack enablement evidence; strengthen before filing.")
    else:
        verdict = PatentabilityVerdict.RECOMMEND

    # 102/103 novelty: harness only surfaces prior-art to DISTINGUISH; no single ref may
    # disclose all elements. With no prior-art adapter wired, this is advisory.
    if prior_art:
        notes.append(f"{len(prior_art)} prior-art ref(s) to distinguish (102/103): ensure ≥1 "
                     f"claim element is absent from every single reference.")
    else:
        notes.append("no prior-art adapter wired: 102/103 novelty is UNSCREENED — a human/prior-art "
                     "search must confirm before filing (open_item).")
    notes.append("101 eligibility (esp. software/AI abstract-idea) is a LEGAL determination — deferred to counsel.")
    if design_doc_only:
        notes.append(f"design-doc-only substrate (no result ref): {design_doc_only} — patentable on "
                     f"written-description enablement (no reduction-to-practice required), but counsel "
                     f"should confirm the spec fully enables each such claim.")

    return {
        "verdict": verdict.value,
        "substrate_claims": [c.claim_id for c in substrate],
        "projection_claims": [c.claim_id for c in projections],
        "per_claim": per_claim,
        "rationale": notes,
    }
