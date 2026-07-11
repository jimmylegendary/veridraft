"""PatentEngineAdapter: `patent-llm` — LLM-assisted drafter over a configurable backend.

Higher-quality than the deterministic `minimal-patent`: an LLM drafts (1) independent
claims, (2) a dependent-claim ladder, (3) the specification with antecedent basis, (4)
abstract, (5) drawing refs, from the assembled invention disclosure — then emits
open_items. Like the `paperorchestra` writing engine, it invokes a configured runner
(`patent_command`) so the backend (self-hosted OSS API / openclaw / claude / codex) is a
config choice, not code. `needs_human=True` is fixed; it NEVER files.

Config example (same backend.json as the paper engine — see paperorchestra/config.example.json):
    [adapters.patent_engine.patent-llm]
    patent_command   = ["python3", "/abs/paperorchestra/patent_run.py",      "--config", "backend.json", "--workspace", "{workspace}"]
    priorart_command = ["python3", "/abs/paperorchestra/patent_priorart.py", "--config", "backend.json", "--workspace", "{workspace}"]
    idea_command     = ["python3", "/abs/paperorchestra/patent_idea.py",     "--config", "backend.json", "--workspace", "{workspace}"]
`priorart_command` / `idea_command` are OPTIONAL — without them the pre-draft prior-art search and
idea memo degrade to an honest empty record / a skeleton memo. The deterministic, zero-dependency
alternative for all three is the `minimal-patent` engine (no model needed).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core.models import EngineOutput
from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity
from ._patent_degraded import _write_degraded_priorart, _write_skeleton_idea


@register(port="patent_engine", id="patent-llm")
class PatentLlmEngineAdapter:
    capabilities = AdapterCapabilities(
        port="patent_engine", id="patent-llm", version="0.1.0",
        provides=("patent_spec", "patent_claims", "abstract"),
        features=frozenset({"llm", "needs-human", "backend-configurable"}),
        requires_config=("patent_command",), maturity=Maturity.V1,
    )
    needs_human = True

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.patent_command = self.config.get("patent_command")
        self.priorart_command = self.config.get("priorart_command")   # → patent_priorart.py
        self.idea_command = self.config.get("idea_command")           # → patent_idea.py

    def health(self) -> HealthStatus:
        if not self.patent_command:
            return HealthStatus.unhealthy(
                "patent-llm needs `patent_command` (a backend runner over the workspace); "
                "use engine `minimal-patent` for the offline deterministic draft")
        return HealthStatus.healthy("patent-llm runner configured")

    def screen(self, claim_ids: list[str]) -> dict:
        return {}  # harness owns the patentability screen

    def _run(self, cmd_template, workspace: str, what: str):
        cmd = [part.replace("{workspace}", workspace) for part in cmd_template]
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"patent-llm {what} runner failed (exit {proc.returncode}): "
                               f"{proc.stderr.strip()[:500]}")

    def search_prior_art(self, workspace: str) -> dict:
        """Run the configured prior-art search runner; degrade to an honest empty record if none."""
        if not self.priorart_command:
            _write_degraded_priorart(workspace, "no `priorart_command` configured")
            return {"priorart": str(Path(workspace) / "inputs" / "priorart.json"), "degraded": True}
        self._run(self.priorart_command, workspace, "priorart")
        return {"priorart": str(Path(workspace) / "inputs" / "priorart.json")}

    def draft_idea(self, workspace: str, title: str = "Invention") -> dict:
        """Run the configured patent-idea runner; degrade to a skeleton memo if none configured."""
        if not self.idea_command:
            _write_skeleton_idea(workspace, title, "no `idea_command` configured")
            return {"idea": str(Path(workspace) / "idea" / "patent_idea.md"), "degraded": True}
        self._run(self.idea_command, workspace, "idea")
        return {"idea": str(Path(workspace) / "idea" / "patent_idea.md")}

    def draft_patent(self, workspace: str) -> EngineOutput:
        if not self.patent_command:
            raise RuntimeError("patent-llm: `patent_command` not configured")
        cmd = [part.replace("{workspace}", workspace) for part in self.patent_command]
        proc = subprocess.run(cmd, cwd=workspace, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"patent-llm runner failed (exit {proc.returncode}): "
                               f"{proc.stderr.strip()[:500]}")
        final = Path(workspace) / "final"
        tex, pdf = final / "patent.tex", final / "patent.pdf"
        open_items = []
        oi = final / "open_items.json"
        if oi.exists():
            open_items = json.loads(oi.read_text(encoding="utf-8"))
        return EngineOutput(
            engine_adapter="patent-llm", workspace_path=workspace,
            paper_tex_path=str(tex) if tex.exists() else None,
            paper_pdf_path=str(pdf) if pdf.exists() else None,
            scores={}, provenance={"open_items": open_items, "needs_human": True, "type": "patent"})
