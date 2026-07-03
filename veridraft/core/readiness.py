"""Submission-readiness check (ADR-0009, honest framing).

Sits between review and publish. It is NOT an AI-detection-evasion tool. It:
  (b) maps the target venue's LLM policy -> the required disclosure text,
  (d) HARD-GUARDS `prohibited` venues (Science/AAAI): it refuses to run any
      "humanize" pass and default-denies publish, requiring a human sign-off —
      it never helps misrepresent authorship to evade an AI ban,
  (a) optionally attaches a false-flag RISK band from a local DetectorAdapter
      (a self-hosted OSS detector; a RISK proxy, never a verdict).

The substance of the paper is already guaranteed honest by the evidence gate; this
op is about disclosure-compliance + prose-quality risk, not deception.
"""
from __future__ import annotations

from ..config import load_venue, load_venues


def generate_disclosure(venue: dict, tool: str, purpose: str) -> tuple[str, str]:
    """Return (disclosure_text, location) for a venue's required disclosure."""
    templates = load_venues().get("disclosure_templates", {})
    loc = (venue.get("policy") or {}).get("disclosure_location", "acknowledgments")
    tmpl = templates.get(loc) or templates.get("acknowledgments", "")
    return tmpl.format(tool=tool, purpose=purpose), loc


def check_readiness(venue_name: str, tool: str, purpose: str,
                    false_flag_risk: dict | None = None) -> dict:
    venue = load_venue(venue_name)
    pol = venue.get("policy") or {}
    policy_class = pol.get("policy_class", "disclosure")
    disclosure_text, loc = generate_disclosure(venue, tool, purpose)
    prohibited = policy_class == "prohibited"

    guidance: list[str] = []
    if policy_class == "allowed":
        guidance.append("LLM-assisted writing is allowed; disclosure optional but recommended.")
    elif policy_class == "disclosure":
        guidance.append(f"Disclose LLM use in the {loc}; add the generated disclosure text.")
    else:  # prohibited
        guidance.append(
            f"{venue_name} PROHIBITS presenting AI-generated text as your own. This tool will "
            f"NOT lower a detector score to evade that — doing so is research misconduct. "
            f"A human must review, take authorship responsibility, and sign off before publish.")
    if pol.get("screening") == "similarity":
        guidance.append(
            f"Venue runs a similarity (plagiarism) check (threshold {pol.get('similarity_threshold')}); "
            f"evidence-gated original wording typically scores low — this is NOT an AI check.")
    if false_flag_risk is None:
        guidance.append(
            "No local detector configured. A low detector score is a RISK proxy, not proof of "
            "human authorship; detectors false-flag human (esp. non-native) writing. Run a "
            "self-hosted OSS detector (e.g. Binoculars) locally for a RISK band; never send a "
            "confidential manuscript to a cloud detector.")
    else:
        band = false_flag_risk.get("band")
        det = false_flag_risk.get("detector", "detector")
        if band == "unavailable":
            guidance.append(
                f"Local detector ({det}) is enabled but unavailable "
                f"({false_flag_risk.get('note', 'see note')}). No RISK band produced.")
        else:
            fpr = false_flag_risk.get("measured_false_positive_rate")
            fpr_txt = f", measured false-positive rate {fpr}" if fpr is not None else ""
            guidance.append(
                f"Local detector ({det}) false-flag RISK band: {band} "
                f"(score={false_flag_risk.get('score')}, threshold={false_flag_risk.get('threshold')}"
                f"{fpr_txt}). This is a RISK proxy, NOT a verdict — detectors false-flag human "
                f"(esp. non-native/ESL) writing, and a 0.5B model pair is weaker than the paper's "
                f"Falcon-7B. Never use it to evade an AI-use policy.")

    return {
        "venue": venue_name,
        "policy_class": policy_class,
        "screening": pol.get("screening"),
        "ai_detector_screening": bool(pol.get("ai_detector_screening")),
        "disclosure_required": bool(pol.get("disclosure_required", True)),
        "disclosure_location": loc,
        "disclosure_text": disclosure_text,
        "similarity_threshold": pol.get("similarity_threshold"),
        "false_flag_risk": false_flag_risk,
        "human_signoff_required": prohibited,
        "blocked": prohibited,
        "guidance": guidance,
        "policy_source": pol.get("source"),
    }
