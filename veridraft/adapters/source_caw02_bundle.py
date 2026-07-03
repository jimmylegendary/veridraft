"""v1 SourceAdapter: import a CAW-02 cited claim+evidence bundle from a JSON file.

This is a projection over CAW-02's exported bundle (ADR-0003 option C). The bundle
shape is the SourceAdapter contract; a future internal-wiki / experiment-server
adapter must produce the SAME shape (typed, resolvable evidence refs) — the gate
never knows which adapter produced a claim.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.models import (
    Boundary,
    Claim,
    ClaimType,
    Evidence,
    EvidenceKind,
    RawBundle,
    ResultRef,
    Visibility,
)
from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity


@register(port="source", id="caw02-bundle")
class Caw02BundleSourceAdapter:
    capabilities = AdapterCapabilities(
        port="source",
        id="caw02-bundle",
        version="0.1.0",
        provides=("claim", "evidence", "result"),
        features=frozenset({"public-safe", "digest-verify"}),
        requires_config=(),
        maturity=Maturity.V1,
    )

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def import_bundle(self, ref: str) -> RawBundle:
        path = Path(ref)
        if not path.exists():
            raise FileNotFoundError(f"CAW-02 bundle not found: {ref}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _parse_bundle(raw)

    def health(self) -> HealthStatus:
        return HealthStatus.healthy("caw02-bundle source ready (file-based)")


def _parse_bundle(raw: dict) -> RawBundle:
    if "bundle_id" not in raw:
        raise ValueError("bundle missing required field 'bundle_id'")

    claims: list[Claim] = []
    for cd in raw.get("claims", []):
        try:
            ctype = ClaimType(cd["type"])
        except (KeyError, ValueError):
            raise ValueError(
                f"claim {cd.get('claim_id')!r} has invalid/missing type "
                f"(expected one of {[t.value for t in ClaimType]})"
            )
        evidence: list[Evidence] = []
        for ed in cd.get("evidence", []):
            try:
                kind = EvidenceKind(ed["kind"])
            except (KeyError, ValueError):
                raise ValueError(
                    f"evidence {ed.get('id')!r} on claim {cd.get('claim_id')!r} has "
                    f"invalid/missing kind (expected one of {[k.value for k in EvidenceKind]})"
                )
            evidence.append(
                Evidence(
                    id=str(ed.get("id", "")),
                    kind=kind,
                    ref=str(ed.get("ref", "")),
                    trust=ed.get("trust"),
                    note=str(ed.get("note", "")),
                )
            )
        claims.append(
            Claim(
                claim_id=str(cd["claim_id"]),
                type=ctype,
                statement=str(cd.get("statement", "")),
                evidence=evidence,
                result_refs=[str(x) for x in cd.get("result_refs", [])],
                boundary=_boundary(cd.get("boundary")),
                visibility=_visibility(cd.get("visibility")),
            )
        )

    results = [
        ResultRef(
            result_id=str(rd["result_id"]),
            description=str(rd.get("description", "")),
            metrics=list(rd.get("metrics", [])),
        )
        for rd in raw.get("results", [])
    ]

    return RawBundle(
        bundle_id=str(raw["bundle_id"]),
        source_adapter="caw02-bundle",
        boundary=str(raw.get("boundary", "public_safe")),
        claims=claims,
        results=results,
        digest=raw.get("digest"),
        signature=raw.get("signature"),
        provenance_manifest=raw.get("provenance_manifest", {}),
    )


def _boundary(value) -> Boundary:
    """Fail-closed: missing/unknown boundary label ⇒ confidential (ADR-0007 invariant 3)."""
    try:
        return Boundary(value) if value is not None else Boundary.CONFIDENTIAL
    except ValueError:
        return Boundary.CONFIDENTIAL


def _visibility(value) -> Visibility:
    """Fail-closed: missing/unknown visibility label ⇒ private."""
    try:
        return Visibility(value) if value is not None else Visibility.PRIVATE
    except ValueError:
        return Visibility.PRIVATE
