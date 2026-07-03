"""Evidence-completeness gate (ADR-0003).

The Python gate here is AUTHORITATIVE — it is what structurally blocks the writing
engine. The optional CUE schema (schema/ledger.cue) is defense-in-depth: if the
`cue` binary is installed we also `cue vet` a snapshot so the hard invariants are
declared in two places. The OSS-foundation research is explicit that the real
protection is the code, not the declaration.

The one invariant no gate profile can relax: generated text is NEVER evidence.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .models import (
    Claim,
    ClaimGateResult,
    ClaimType,
    EvidenceKind,
    GateProfile,
    GateReport,
    GateStatus,
    InterlockStatus,
)

CUE_SCHEMA = Path(__file__).parent.parent / "schema" / "ledger.cue"

# Canonical patent-first floor, INDEPENDENT of any gate profile: a P3 (future-device /
# patent-sensitive) claim is disclosure-blocked for paper egress until a human releases it.
# A profile's `patent_sensitive_types` may ADD to this, never remove it — so selecting the
# us-utility-patent profile (empty list) cannot silently un-hold a P3 claim at egress.
PATENT_SENSITIVE_DEFAULT = frozenset({ClaimType.P3})


# Minimum RESOLVABLE shape per admissible evidence kind. Every admissible kind must carry a
# typed, resolvable pointer — a source_artifact needs `<path>@<commit>` (or a repo/URL scheme),
# a caw01_result a `caw01://` ref, a caw02_evidence a `caw02://` ref — so free prose relabeled as
# evidence of ANY kind is rejected (not just source_artifact). Content resolution (does the commit
# contain the file / does the id exist) is the Source/CAW-01/CAW-02 adapter's job — documented seam.
# Each pattern is FULLY ANCHORED (^…$) so the WHOLE ref must be a structured pointer — free prose
# that merely CONTAINS an '@<hex>' run no longer passes (relabel attack).
_REF_SHAPE = {
    EvidenceKind.SOURCE_ARTIFACT: re.compile(
        r"^(?:github|repo|https?|git)://\S+$|^[\w./@\-]+@[0-9a-fA-F]{7,40}$", re.I),
    EvidenceKind.CAW01_RESULT: re.compile(r"^caw01://\S+$", re.I),
    EvidenceKind.CAW02_EVIDENCE: re.compile(r"^caw02://\S+$", re.I),
}


def _ref_shape_ok(kind: EvidenceKind, ref: str) -> bool:
    pat = _REF_SHAPE.get(kind)
    return True if pat is None else bool(pat.search(ref))


def disclosure_blocked(claim: Claim) -> bool:
    """True iff disclosing this claim in a PAPER requires a human interlock release —
    used at publish() egress, profile-independent (patent-first invariant)."""
    if claim.interlock_status is InterlockStatus.RELEASED:
        return False
    return claim.type in PATENT_SENSITIVE_DEFAULT or claim.interlock_status is InterlockStatus.HELD


def evaluate_claim(claim: Claim, profile: GateProfile,
                   valid_result_ids: set[str] | None = None) -> ClaimGateResult:
    reasons: list[str] = []

    # 1. Reject inadmissible evidence explicitly (the hard invariant).
    for e in claim.evidence:
        if e.kind in (EvidenceKind.GENERATED_TEXT, EvidenceKind.PROSE_NOTE):
            reasons.append(
                f"evidence {e.id!r} ({e.kind.value}) rejected: generated/prose text is "
                f"never evidence (ADR-0003)"
            )
        elif not e.ref.strip():
            reasons.append(
                f"evidence {e.id!r} ({e.kind.value}) rejected: empty/unresolvable ref"
            )
        elif not _ref_shape_ok(e.kind, e.ref):
            # Resolution seam: a ref must at least have the RESOLVABLE SHAPE of its kind — a
            # source_artifact needs a `<path>@<commit>` pointer, not free prose relabeled as
            # evidence. (Full content resolution — does that commit contain the file — is the
            # SourceAdapter's job; this shape gate stops the trivial relabel attack.)
            reasons.append(
                f"evidence {e.id!r} ({e.kind.value}) rejected: ref {e.ref!r} is not a resolvable "
                f"{e.kind.value} pointer (source_artifact needs '<path>@<commit>')")

    admissible = claim.admissible_evidence()

    # 2. Minimum admissible evidence per claim type. Hard floor of 1: no config profile can
    #    admit a claim with ZERO admissible evidence (the "no claim without evidence" invariant).
    min_required = max(1, profile.min_evidence_by_type.get(claim.type.value, 1))
    if len(admissible) < min_required:
        reasons.append(
            f"insufficient evidence: {len(admissible)} admissible < "
            f"{min_required} required for {claim.type.value}"
        )

    # 3. Optional: require a CAW-01 result ref (traceable numbers) that actually RESOLVES to a
    #    declared result in the bundle — not merely a non-empty (possibly ghost) list.
    if profile.require_result_ref_by_type.get(claim.type.value, False):
        if not claim.result_refs:
            reasons.append(
                f"{claim.type.value} requires a CAW-01 result ref for traceable numbers, none present")
        elif valid_result_ids is not None:
            ghost = [r for r in claim.result_refs if r not in valid_result_ids]
            if ghost:
                reasons.append(
                    f"{claim.type.value} result_refs do not resolve to a declared result: {ghost}")

    # 4. Patent-first interlock (UC-3): a patent-sensitive claim is default-denied for
    #    paper drafting while its interlock is HELD; a human `release_interlock` (i.e.
    #    the patent has been filed/cleared) lets it through on the next gate.
    if claim.type.value in profile.patent_sensitive_types:
        if claim.interlock_status is not InterlockStatus.RELEASED:
            reasons.append(
                f"patent-first interlock HELD (patent-sensitive {claim.type.value}): "
                f"release the interlock before paper drafting"
            )

    status = GateStatus.PASSED if not reasons else GateStatus.BLOCKED
    return ClaimGateResult(
        claim_id=claim.claim_id,
        type=claim.type.value,
        status=status,
        admissible_evidence=len(admissible),
        reasons=reasons,
    )


def run_gate(claims: list[Claim], profile: GateProfile, bundle_id: str,
             valid_result_ids: set[str] | None = None) -> GateReport:
    report = GateReport(profile=profile.name, bundle_id=bundle_id)
    for c in claims:
        report.results.append(evaluate_claim(c, profile, valid_result_ids))
    return report


# --- optional CUE defense-in-depth -----------------------------------------

def cue_available() -> bool:
    return shutil.which("cue") is not None


def cue_vet_snapshot(claims: list[Claim]) -> tuple[bool, str]:
    """If `cue` is installed, vet a ledger snapshot against schema/ledger.cue.

    Returns (ok, detail). Never authoritative — the Python gate is. When `cue` is
    absent this is a no-op reporting `skipped`.
    """
    if not cue_available():
        return True, "skipped (cue binary not installed)"
    if not CUE_SCHEMA.exists():
        return True, f"skipped (schema not found at {CUE_SCHEMA})"

    snapshot = {
        "claims": [
            {
                "claim_id": c.claim_id,
                "type": c.type.value,
                "evidence": [
                    {"id": e.id, "kind": e.kind.value, "ref": e.ref} for e in c.evidence
                ],
            }
            for c in claims
        ]
    }
    with tempfile.TemporaryDirectory() as td:
        data_path = Path(td) / "snapshot.json"
        data_path.write_text(json.dumps(snapshot), encoding="utf-8")
        try:
            proc = subprocess.run(
                ["cue", "vet", str(CUE_SCHEMA), str(data_path)],
                capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError) as e:  # pragma: no cover
            return True, f"skipped (cue invocation failed: {e})"
    ok = proc.returncode == 0
    detail = "cue vet passed" if ok else f"cue vet FAILED: {proc.stderr.strip() or proc.stdout.strip()}"
    return ok, detail
