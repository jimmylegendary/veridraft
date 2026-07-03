"""Veridraft's own minimal governance domain model (data-model.md).

Veridraft REFERENCES CAW-01 results and CAW-02 claims/evidence by id/URI — it never
duplicates them. These are plain stdlib dataclasses so the v1 slice runs with zero
third-party deps; swapping in Pydantic later (per the OSS-foundation research) is
mechanical because the field shapes are already fixed here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ClaimType(str, Enum):
    """P1/P2 = method/tool claims; P3 = future-device claim (patent-sensitive)."""

    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class EvidenceKind(str, Enum):
    """Only a typed, resolvable ref to a concrete artifact counts as evidence.

    CAW02_EVIDENCE / CAW01_RESULT are real evidence. GENERATED_TEXT and PROSE_NOTE
    are NEVER evidence — the one invariant no gate profile can relax (ADR-0003).
    """

    CAW02_EVIDENCE = "caw02_evidence"
    CAW01_RESULT = "caw01_result"
    # A concrete, resolvable source artifact — a repo file / design doc / test result
    # at a commit. Admissible for the "code + design docs → paper" case (a code file
    # IS a concrete artifact, unlike generated prose).
    SOURCE_ARTIFACT = "source_artifact"
    GENERATED_TEXT = "generated_text"
    PROSE_NOTE = "prose_note"

    def is_admissible(self) -> bool:
        return self in (
            EvidenceKind.CAW02_EVIDENCE,
            EvidenceKind.CAW01_RESULT,
            EvidenceKind.SOURCE_ARTIFACT,
        )


class GateStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    BLOCKED = "blocked"


class Boundary(str, Enum):
    """Ordered lattice inherited verbatim from CAW-02: public ⊂ internal ⊂ confidential.
    "Can it leave the building." Effective boundary = lattice-max over selected claims."""

    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"

    @property
    def rank(self) -> int:
        return {"public": 0, "internal": 1, "confidential": 2}[self.value]

    @classmethod
    def max(cls, values: "list[Boundary]") -> "Boundary":
        # Fail-closed: an empty set is confidential, never public.
        return max(values, key=lambda b: b.rank, default=cls.CONFIDENTIAL)


class Visibility(str, Enum):
    """Unordered axis from CAW-02: team | private. Effective = team iff ALL are team."""

    TEAM = "team"
    PRIVATE = "private"

    @classmethod
    def effective(cls, values: "list[Visibility]") -> "Visibility":
        # Fail-closed: empty or any private ⇒ private.
        if not values or any(v is cls.PRIVATE for v in values):
            return cls.PRIVATE
        return cls.TEAM


class Audience(str, Enum):
    """The target audience a Sink adapter exports to; drives the egress decision."""

    PUBLIC = "public"
    INTERNAL = "internal"
    COUNSEL = "counsel"


class ConfidentialityTrack(str, Enum):
    PUBLIC_SOURCE_ASSISTED = "public-source-assisted"
    INTERNAL_REVIEW_REQUIRED = "internal-review-required"


class BlockedReason(str, Enum):
    """Typed conjunction-gate failure reasons (confidentiality doc §3.2)."""

    EVIDENCE = "EVIDENCE"
    BOUNDARY = "BOUNDARY"
    NOVELTY = "NOVELTY"
    ENGINE = "ENGINE"
    READINESS = "READINESS"
    INTERLOCK = "INTERLOCK"


class PatentabilityVerdict(str, Enum):
    """Pre-draft patentability screen result (ADR-0004 §3). no-go blocks drafting."""

    RECOMMEND = "recommend"
    WEAK = "weak"
    NO_GO = "no-go"


class InterlockStatus(str, Enum):
    """Patent-first interlock state per patent-sensitive claim (UC-3 / data-model
    InterlockState). HELD = default-deny paper drafting until a human releases it
    (i.e. the patent has been filed / cleared); RELEASED = disclosure permitted."""

    NONE = "none"
    HELD = "held"
    RELEASED = "released"


class Lifecycle(str, Enum):
    """Artifact lifecycle board (ADR-0001)."""

    SELECTED = "selected"
    GATED = "gated"
    BLOCKED = "blocked"
    DRAFTING = "drafting"
    DRAFTED = "drafted"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    PUBLISHED_PAPER = "published_paper"
    FILED_PATENT = "filed_patent"


@dataclass
class Evidence:
    id: str
    kind: EvidenceKind
    ref: str = ""          # resolvable id/URI into CAW-02 evidence or CAW-01 result registry
    trust: float | None = None
    note: str = ""

    def is_admissible(self) -> bool:
        """Admissible iff a real evidence kind AND a non-empty resolvable ref."""
        return self.kind.is_admissible() and bool(self.ref.strip())


@dataclass
class ResultRef:
    """A CAW-01 result-registry reference carrying the ground-truth numbers that
    land in experimental_log.md `## 2. Raw Numeric Data`. Referenced by id."""

    result_id: str
    description: str = ""
    metrics: list[dict] = field(default_factory=list)  # [{name, value, unit?}]


@dataclass
class Claim:
    claim_id: str
    type: ClaimType
    statement: str
    evidence: list[Evidence] = field(default_factory=list)
    result_refs: list[str] = field(default_factory=list)  # CAW-01 result ids
    gate_status: GateStatus = GateStatus.PENDING
    # Effective labels computed by CAW-02 and carried in the envelope. Fail-closed:
    # a claim with no/unresolvable label is treated as confidential/private.
    boundary: Boundary = Boundary.CONFIDENTIAL
    visibility: Visibility = Visibility.PRIVATE
    # Patent-first interlock status, loaded from the ledger before gating.
    interlock_status: InterlockStatus = InterlockStatus.NONE

    def admissible_evidence(self) -> list[Evidence]:
        # De-duplicate by (kind, ref): the SAME artifact cited twice is ONE piece of admissible
        # evidence, so it cannot inflate a claim past a min-evidence floor (e.g. P3 needs 2).
        seen, out = set(), []
        for e in self.evidence:
            if e.is_admissible() and (e.kind, e.ref) not in seen:
                seen.add((e.kind, e.ref))
                out.append(e)
        return out


@dataclass
class RawBundle:
    """One import from a SourceAdapter — a projection over a CAW-02 signed bundle."""

    bundle_id: str
    source_adapter: str
    boundary: str = "public_safe"
    claims: list[Claim] = field(default_factory=list)
    results: list[ResultRef] = field(default_factory=list)
    digest: str | None = None
    signature: str | None = None
    provenance_manifest: dict = field(default_factory=dict)


@dataclass
class GateProfile:
    """Type-specific minimum evidence, selected by config (ADR-0003 option D).

    `non_relaxable` invariants can never be flipped by a profile; the gate refuses
    to load a profile that tries to admit generated text as evidence.
    """

    name: str
    min_evidence_by_type: dict[str, int] = field(default_factory=dict)
    require_result_ref_by_type: dict[str, bool] = field(default_factory=dict)
    patent_sensitive_types: list[str] = field(default_factory=lambda: ["P3"])
    non_relaxable: dict = field(
        default_factory=lambda: {"generated_text_is_evidence": False}
    )
    # The tool REQUIRES an evaluation: a paper cannot be drafted from an empty result
    # set — Veridraft demands real experimental results rather than fabricating an
    # evaluation. A functional-only result set (e.g. an e2e suite) satisfies this.
    require_results: bool = True


@dataclass
class ClaimGateResult:
    claim_id: str
    type: str
    status: GateStatus
    admissible_evidence: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class GateReport:
    profile: str
    bundle_id: str
    results: list[ClaimGateResult] = field(default_factory=list)

    @property
    def passed_claim_ids(self) -> list[str]:
        return [r.claim_id for r in self.results if r.status is GateStatus.PASSED]

    @property
    def blocked_claim_ids(self) -> list[str]:
        return [r.claim_id for r in self.results if r.status is GateStatus.BLOCKED]

    @property
    def all_passed(self) -> bool:
        return bool(self.results) and not self.blocked_claim_ids


@dataclass
class EngineOutput:
    """What Veridraft captures from a WritingEngine run (PaperOrchestra output contract)."""

    engine_adapter: str
    workspace_path: str
    paper_tex_path: str | None = None
    paper_pdf_path: str | None = None
    scores: dict = field(default_factory=dict)
    figures: list[dict] = field(default_factory=list)   # figure_id ↔ result_id manifest
    provenance: dict = field(default_factory=dict)


@dataclass
class Artifact:
    id: str
    type: str                       # "paper" | "patent"
    state: Lifecycle
    gated_set_id: str
    confidentiality_track: str = ConfidentialityTrack.INTERNAL_REVIEW_REQUIRED.value
    boundary: str = Boundary.CONFIDENTIAL.value
    visibility: str = Visibility.PRIVATE.value
    engine_run_id: str | None = None
    review_id: str | None = None
    output_ref: str | None = None
