"""v1 SinkAdapter: write produced artifacts (LaTeX/PDF) to `artifacts/<id>/`.

Large blobs live on the filesystem by path; the ledger stores only the output_ref
(storage-strategy.md). Publishing here is a local file write — the human-gated
publish/file transition stays in the core, not in this adapter (ADR-0001).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from ..core.models import Audience
from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity


@register(port="sink", id="latex-pdf-files")
class LatexPdfSinkAdapter:
    capabilities = AdapterCapabilities(
        port="sink",
        id="latex-pdf-files",
        version="0.1.0",
        provides=("paper_pdf", "paper_tex"),
        features=frozenset({"local-files"}),
        requires_config=(),
        maturity=Maturity.V1,
    )
    # A local file drop is an INTERNAL audience; the core runs decide() against this
    # tier before publish(). A public target must be requested explicitly (and only
    # passes for a public/team artifact).
    audience = Audience.INTERNAL

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        if config and config.get("audience"):
            try:
                self.audience = Audience(config["audience"])
            except ValueError:
                pass

    def health(self) -> HealthStatus:
        return HealthStatus.healthy("latex-pdf-files sink ready")

    def publish(self, artifact_id: str, output_paths: list[str], dest_dir: str) -> str:
        dest = Path(dest_dir) / artifact_id
        dest.mkdir(parents=True, exist_ok=True)
        for p in output_paths:
            src = Path(p)
            if src.exists():
                shutil.copy2(src, dest / src.name)
        return str(dest)
