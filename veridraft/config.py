"""Harness configuration + gate-profile loading.

Config is JSON (stdlib only) so the slice needs no third-party TOML lib. A default
config wires the v1 slice adapters; an optional `veridraft.config.json` overrides it.
Gate profiles live in `veridraft/profiles/<name>.json` and are config-selected
(ADR-0003 option D): a new venue/jurisdiction is a new profile, not a core change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .core.models import GateProfile

PROFILES_DIR = Path(__file__).parent / "profiles"
VENUES_PATH = Path(__file__).parent / "venues.json"

DEFAULT_ADAPTERS: dict = {
    "source": {"id": "caw02-bundle", "enabled": True, "config": {}},
    "writing_engine": {"id": "minimal-latex", "enabled": True, "config": {}},
    "patent_engine": {"id": "minimal-patent", "enabled": True, "config": {}},
    "sink": {"id": "latex-pdf-files", "enabled": True, "config": {}},
    "novelty": {"id": "novelty-stub", "enabled": False, "config": {}},
    # Optional local AI-text RISK detector (Binoculars). OFF by default: readiness
    # works without it. Enable + provide the torch/transformers venv to attach a
    # false-flag RISK band (never a verdict) to the readiness report. See README.
    "detector": {"id": "binoculars-local", "enabled": False, "config": {}},
}


@dataclass
class HarnessConfig:
    adapters: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_ADAPTERS)))
    gate_profile: str = "neurips-paper"
    data_dir: str = ".veridraft"

    @classmethod
    def load(cls, path: str | None = None) -> "HarnessConfig":
        cfg = cls()
        if path and Path(path).exists():
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            merged = json.loads(json.dumps(DEFAULT_ADAPTERS))
            for port, spec in (raw.get("adapters") or {}).items():
                merged.setdefault(port, {}).update(spec)
            cfg.adapters = merged
            cfg.gate_profile = raw.get("gate_profile", cfg.gate_profile)
            cfg.data_dir = raw.get("data_dir", cfg.data_dir)
        return cfg

    def adapter_id(self, port: str) -> str:
        return self.adapters[port]["id"]

    def adapter_config(self, port: str) -> dict:
        return self.adapters[port].get("config", {})


def load_gate_profile(name: str) -> GateProfile:
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        available = ", ".join(p.stem for p in PROFILES_DIR.glob("*.json")) or "(none)"
        raise FileNotFoundError(
            f"gate profile {name!r} not found in {PROFILES_DIR} (available: {available})"
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    profile = GateProfile(
        name=raw.get("name", name),
        min_evidence_by_type=raw.get("min_evidence_by_type", {}),
        require_result_ref_by_type=raw.get("require_result_ref_by_type", {}),
        patent_sensitive_types=raw.get("patent_sensitive_types", ["P3"]),
        non_relaxable=raw.get("non_relaxable", {"generated_text_is_evidence": False}),
        require_results=raw.get("require_results", True),
    )
    _validate_profile_invariants(profile)
    return profile


def load_venues() -> dict:
    """Load the venue registry (rubric/bar per venue) used by the AI reviewer."""
    return json.loads(VENUES_PATH.read_text(encoding="utf-8"))


def load_venue(name: str) -> dict:
    reg = load_venues()
    venue = reg.get("venues", {}).get(name)
    if not venue:
        available = ", ".join(reg.get("venues", {}))
        raise KeyError(f"unknown venue {name!r}; available: {available}")
    return venue


def _validate_profile_invariants(profile: GateProfile) -> None:
    """A profile can never relax the hard invariant that generated text is not evidence."""
    if profile.non_relaxable.get("generated_text_is_evidence", False) is not False:
        raise ValueError(
            f"gate profile {profile.name!r} tries to admit generated text as evidence — "
            f"this invariant is non-relaxable (ADR-0003)"
        )
