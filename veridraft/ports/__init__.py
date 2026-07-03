"""Veridraft driven ports (the seams the harness core calls out through).

The harness core depends ONLY on these Protocols — never on a concrete adapter
(ADR-0005). An adapter cannot weaken governance: the evidence gate, confidentiality
`decide()`, and the human publish/file transitions live in the core, not here.

Five driven ports:
    SourceAdapter        — where claims/evidence/results come from
    WritingEngineAdapter — drafting (PaperOrchestra is the v1 default adapter)
    PatentEngineAdapter  — patent drafting (separate path)
    SinkAdapter          — where outputs go
    NoveltyAdapter       — related-work + threat signals

Every adapter carries a capability descriptor and a health check so the registry
can run a preflight before a run (ADR-0005 §5). A "future" connector ships as a
documented stub: `maturity="stub"`, methods raise NotImplementedError, and preflight
refuses to run a stub that has been marked active.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from ..core.models import EngineOutput, RawBundle


class Maturity(str, Enum):
    V1 = "v1"
    EXPERIMENTAL = "experimental"
    STUB = "stub"


@dataclass(frozen=True)
class AdapterCapabilities:
    """What an adapter declares it can do — validated by registry preflight."""

    port: str
    id: str
    version: str
    provides: tuple[str, ...] = ()
    features: frozenset[str] = frozenset()
    requires_config: tuple[str, ...] = ()
    maturity: Maturity = Maturity.V1

    def is_stub(self) -> bool:
        return self.maturity is Maturity.STUB


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    detail: str = ""

    @classmethod
    def healthy(cls, detail: str = "") -> "HealthStatus":
        return cls(True, detail)

    @classmethod
    def not_implemented(cls, detail: str = "stub") -> "HealthStatus":
        return cls(False, f"not implemented: {detail}")

    @classmethod
    def unhealthy(cls, detail: str) -> "HealthStatus":
        return cls(False, detail)


@runtime_checkable
class SourceAdapter(Protocol):
    """Where claims/evidence/results come from. v1: a CAW-02 cited bundle on disk."""

    capabilities: AdapterCapabilities

    def import_bundle(self, ref: str) -> RawBundle:
        """Load + return a provenance-tagged bundle. `ref` is a path/URI."""
        ...

    def health(self) -> HealthStatus: ...


@runtime_checkable
class WritingEngineAdapter(Protocol):
    """Drafting. PaperOrchestra is the v1 default; swappable behind this port.

    The core assembles engine-neutral inputs into `workspace/inputs/` and calls
    `draft(workspace)`. The adapter reads that workspace and writes outputs
    (final/paper.tex, final/paper.pdf, scores) back under `workspace/`.
    """

    capabilities: AdapterCapabilities

    def draft(self, workspace: str) -> EngineOutput: ...

    def health(self) -> HealthStatus: ...


@runtime_checkable
class PatentEngineAdapter(Protocol):
    """Patent drafting — a SEPARATE path from paper drafting (stub in the v1 slice)."""

    capabilities: AdapterCapabilities

    def screen(self, claim_ids: list[str]) -> dict: ...

    def draft_patent(self, workspace: str) -> EngineOutput: ...

    def health(self) -> HealthStatus: ...


@runtime_checkable
class SinkAdapter(Protocol):
    """Where outputs go. v1: LaTeX/PDF files under artifacts/."""

    capabilities: AdapterCapabilities

    def publish(self, artifact_id: str, output_paths: list[str], dest_dir: str) -> str: ...

    def health(self) -> HealthStatus: ...


@runtime_checkable
class NoveltyAdapter(Protocol):
    """Related-work + threat signals (stub in the v1 slice)."""

    capabilities: AdapterCapabilities

    def check(self, claim_ids: list[str]) -> list[dict]: ...

    def health(self) -> HealthStatus: ...


@runtime_checkable
class DetectorAdapter(Protocol):
    """AI-text-detection RISK estimator for submission-readiness (ADR-0009).

    Returns a false-flag RISK band, NOT a verdict — detectors false-flag human
    (esp. non-native) writing. The default adapter must be a SELF-HOSTED / local
    detector (e.g. Binoculars) so a confidential manuscript never leaves the box;
    cloud detectors are opt-in, public-boundary only. Never used to evade an AI ban.
    """

    capabilities: AdapterCapabilities

    def detect(self, text: str) -> dict: ...

    def health(self) -> HealthStatus: ...


PORTS: tuple[str, ...] = (
    "source", "writing_engine", "patent_engine", "sink", "novelty", "detector")
