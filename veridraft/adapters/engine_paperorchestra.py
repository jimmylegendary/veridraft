"""v1 default WritingEngineAdapter: `paperorchestra` (ADR-0002).

Wraps PaperOrchestra as a SUBPROCESS over the assembled `workspace/` (the harness
core depends only on the port, never on PO directly). PaperOrchestra is the heavy
5-agent pipeline (LLM + LaTeX); this adapter does NOT reimplement it — it invokes a
configured runner command and captures the output contract
(`final/paper.tex`, `final/paper.pdf`, scores, `citation_pool.json`).

Because PO needs an LLM + LaTeX environment, this adapter is real but not the
zero-dependency slice default (config selects `minimal-latex` for the offline slice).
Configure `po_command` to a runner that takes the workspace path, e.g.:

    [adapters.writing_engine.paperorchestra]
    po_command = ["paper-orchestra", "run", "--workspace", "{workspace}"]
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core.models import EngineOutput
from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity


@register(port="writing_engine", id="paperorchestra")
class PaperOrchestraEngineAdapter:
    capabilities = AdapterCapabilities(
        port="writing_engine",
        id="paperorchestra",
        version="0.1.0",
        provides=("paper_tex", "paper_pdf", "scores", "citation_pool"),
        features=frozenset({"llm", "latex", "full-pipeline"}),
        requires_config=("po_command",),
        maturity=Maturity.V1,
    )

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.po_command = self.config.get("po_command")

    def health(self) -> HealthStatus:
        if not self.po_command:
            return HealthStatus.unhealthy(
                "paperorchestra needs `po_command` config (a runner over the workspace); "
                "use engine `minimal-latex` for the offline slice"
            )
        return HealthStatus.healthy("paperorchestra runner configured")

    def draft(self, workspace: str) -> EngineOutput:
        if not self.po_command:
            raise RuntimeError("paperorchestra: `po_command` not configured")
        cmd = [
            part.replace("{workspace}", workspace) for part in self.po_command
        ]
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"paperorchestra runner failed (exit {proc.returncode}): "
                f"{proc.stderr.strip()[:500]}"
            )

        final = Path(workspace) / "final"
        tex = final / "paper.tex"
        pdf = final / "paper.pdf"
        scores_path = final / "scores.json"
        citation_pool = Path(workspace) / "citation_pool.json"
        scores = json.loads(scores_path.read_text()) if scores_path.exists() else {}

        return EngineOutput(
            engine_adapter="paperorchestra",
            workspace_path=workspace,
            paper_tex_path=str(tex) if tex.exists() else None,
            paper_pdf_path=str(pdf) if pdf.exists() else None,
            scores=scores,
            figures=[],
            provenance={"citation_pool": str(citation_pool) if citation_pool.exists() else None},
        )
