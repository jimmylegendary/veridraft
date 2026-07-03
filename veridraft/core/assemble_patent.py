"""Assemble PatentEngine inputs from GATED claims (parallel to assemble.py for papers).

Writes a patent workspace `inputs/` the PatentEngine reads: an invention disclosure,
the typed gated claims (the source of truth for claim drafting), and result refs for
enablement/drawings. Structural block: refuses any claim not PASSED at the gate — the
generated-text-is-never-evidence invariant carries into the patent path.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Claim, ClaimType, GateStatus, ResultRef


class UngatedClaimError(RuntimeError):
    pass


def assemble_patent_inputs(workspace_dir: str, claims: list[Claim], results: list[ResultRef],
                           title: str = "Invention", context: str = "",
                           patentability: dict | None = None) -> dict:
    ungated = [c.claim_id for c in claims if c.gate_status is not GateStatus.PASSED]
    if ungated:
        raise UngatedClaimError(f"cannot assemble patent inputs: ungated claims: {ungated}")
    if not claims:
        raise UngatedClaimError("cannot assemble patent inputs: no gated claims")

    inputs = Path(workspace_dir) / "inputs"
    (inputs / "figures").mkdir(parents=True, exist_ok=True)

    claim_records = [
        {"claim_id": c.claim_id, "type": c.type.value, "statement": c.statement,
         "evidence_count": len(c.admissible_evidence()), "result_refs": list(c.result_refs)}
        for c in claims
    ]
    (inputs / "claims.json").write_text(json.dumps(claim_records, indent=2), encoding="utf-8")
    (inputs / "results.json").write_text(
        json.dumps([{"result_id": r.result_id, "description": r.description, "metrics": r.metrics}
                    for r in results], indent=2), encoding="utf-8")
    (inputs / "patentability.json").write_text(json.dumps(patentability or {}, indent=2), encoding="utf-8")

    method = [c for c in claims if c.type in (ClaimType.P1, ClaimType.P2)]
    proj = [c for c in claims if c.type is ClaimType.P3]
    disclosure = (
        f"# Invention Disclosure: {title}\n\n"
        f"## Technical Field\n\n{context or title}.\n\n"
        f"## Problem / Background\n\n"
        f"Existing approaches leave the following unmet need addressed by this invention.\n\n"
        f"## Summary of the Invention (patentable substrate — P1/P2 method/tool claims)\n\n"
        + ("\n".join(f"- ({c.type.value}) {c.statement}" for c in method) or "- (none)")
        + "\n\n## Projections (P3 — requires enablement review, not independently claimed)\n\n"
        + ("\n".join(f"- ({c.type.value}) {c.statement}" for c in proj) or "- (none)")
        + "\n\n## Enablement support (result refs)\n\n"
        + ("\n".join(f"- {r.result_id}: {r.description}" for r in results) or "- (none)")
        + "\n"
    )
    (inputs / "invention.md").write_text(disclosure, encoding="utf-8")

    return {
        "inputs_dir": str(inputs),
        "claims": [c.claim_id for c in claims],
        "substrate_claims": [c.claim_id for c in method],
        "projection_claims": [c.claim_id for c in proj],
        "title": title,
    }
