"""Config-driven adapter registry + capability preflight (ADR-0005 §4/§5).

Adapters register themselves via `@register(port, id)` and declare an
`AdapterCapabilities`. The core selects one adapter per port from config and runs a
preflight BEFORE any run: the adapter must exist, must not be a `stub` while marked
active, must have its `requires_config` satisfied, and must report healthy.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..ports import PORTS, AdapterCapabilities, HealthStatus

_REGISTRY: dict[str, dict[str, type]] = {p: {} for p in PORTS}


def register(port: str, id: str):
    if port not in PORTS:
        raise ValueError(f"unknown port {port!r}; expected one of {PORTS}")

    def _deco(cls):
        caps = getattr(cls, "capabilities", None)
        if not isinstance(caps, AdapterCapabilities):
            raise TypeError(
                f"{cls.__name__} must define a class attribute "
                f"`capabilities: AdapterCapabilities`"
            )
        if caps.port != port or caps.id != id:
            raise ValueError(
                f"{cls.__name__}.capabilities ({caps.port}/{caps.id}) "
                f"does not match @register({port!r}, {id!r})"
            )
        _REGISTRY[port][id] = cls
        return cls

    return _deco


def get_adapter_class(port: str, id: str) -> type:
    try:
        return _REGISTRY[port][id]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY.get(port, {}))) or "(none)"
        raise LookupError(
            f"no adapter {id!r} registered for port {port!r}; known: {known}"
        )


def instantiate(port: str, id: str, config: dict | None = None):
    cls = get_adapter_class(port, id)
    try:
        return cls(config or {})
    except TypeError:
        # adapters that take no config
        return cls()


def list_adapters() -> list[AdapterCapabilities]:
    caps: list[AdapterCapabilities] = []
    for port in PORTS:
        for cls in _REGISTRY[port].values():
            caps.append(cls.capabilities)
    return caps


@dataclass
class PreflightItem:
    port: str
    adapter_id: str
    enabled: bool
    ok: bool
    detail: str


@dataclass
class PreflightReport:
    items: list[PreflightItem]

    @property
    def ok(self) -> bool:
        # Every item here is a REQUIRED port, so all must pass (a disabled required
        # adapter is a failure, not a skip).
        return all(i.ok for i in self.items)

    def failures(self) -> list[PreflightItem]:
        return [i for i in self.items if not i.ok]


def preflight(required_ports: list[str], adapter_cfg: dict) -> PreflightReport:
    """Validate wiring for the ports a run needs.

    `adapter_cfg` maps port -> {id, enabled, config}. A stub adapter that is
    enabled is refused with a message pointing at the file to implement.
    """
    items: list[PreflightItem] = []
    for port in required_ports:
        spec = adapter_cfg.get(port)
        if not spec:
            items.append(PreflightItem(port, "(unset)", True, False,
                                       f"no adapter configured for required port {port!r}"))
            continue
        adapter_id = spec.get("id", "(unset)")
        enabled = bool(spec.get("enabled", True))
        if not enabled:
            items.append(PreflightItem(
                port, adapter_id, enabled, False,
                f"required port {port!r} adapter {adapter_id!r} is disabled in config"))
            continue
        try:
            cls = get_adapter_class(port, adapter_id)
        except LookupError as e:
            items.append(PreflightItem(port, adapter_id, enabled, False, str(e)))
            continue

        caps: AdapterCapabilities = cls.capabilities
        if enabled and caps.is_stub():
            items.append(PreflightItem(
                port, adapter_id, enabled, False,
                f"adapter {adapter_id!r} is a documented STUB and cannot run while "
                f"active — implement {cls.__module__}.{cls.__name__} or disable it"))
            continue

        missing = [k for k in caps.requires_config if k not in spec.get("config", {})]
        if enabled and missing:
            items.append(PreflightItem(
                port, adapter_id, enabled, False,
                f"missing required config keys for {adapter_id!r}: {missing}"))
            continue

        health: HealthStatus = instantiate(port, adapter_id, spec.get("config")).health()
        items.append(PreflightItem(port, adapter_id, enabled, health.ok, health.detail))

    return PreflightReport(items)
