"""v0 WritingEngineAdapter: `minimal-latex`.

A deterministic, offline, no-LLM engine used to prove the `gated claim → PDF` slice
end-to-end without the full PaperOrchestra stack. It reads the assembled
`workspace/inputs/` and writes `workspace/final/{paper.tex, paper.pdf}`:

  - paper.tex  — a valid LaTeX article (real renderers can compile it).
  - paper.pdf  — compiled with `tectonic`/`pdflatex` if either is on PATH, else via
                 the dependency-free builtin PDF writer (core.pdf).

This is one adapter behind the WritingEngine port; `paperorchestra` is the other,
selected by config. Swapping engines does not touch the core (ADR-0002/0005).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..core.models import EngineOutput
from ..core.pdf import write_text_pdf
from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity


@register(port="writing_engine", id="minimal-latex")
class MinimalLatexEngineAdapter:
    capabilities = AdapterCapabilities(
        port="writing_engine",
        id="minimal-latex",
        version="0.1.0",
        provides=("paper_tex", "paper_pdf"),
        features=frozenset({"deterministic", "offline", "no-llm"}),
        requires_config=(),
        maturity=Maturity.V1,
    )

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def health(self) -> HealthStatus:
        return HealthStatus.healthy(f"minimal-latex ready (renderer: {_detect_renderer()})")

    def draft(self, workspace: str) -> EngineOutput:
        ws = Path(workspace)
        inputs = ws / "inputs"
        idea = _read(inputs / "idea.md")
        exp_log = _read(inputs / "experimental_log.md")
        template = _read(inputs / "template.tex")

        title = _first_title(idea) or "Veridraft Draft"
        final = ws / "final"
        final.mkdir(parents=True, exist_ok=True)

        paper_tex = _build_tex(template, title, idea, exp_log)
        tex_path = final / "paper.tex"
        tex_path.write_text(paper_tex, encoding="utf-8")

        pdf_path = final / "paper.pdf"
        renderer = _render_pdf(tex_path, final, title, idea, exp_log, pdf_path)

        return EngineOutput(
            engine_adapter="minimal-latex",
            workspace_path=str(ws),
            paper_tex_path=str(tex_path),
            paper_pdf_path=str(pdf_path),
            scores={"renderer": renderer},
            figures=[],
            provenance={"renderer": renderer, "inputs": ["idea.md", "experimental_log.md", "template.tex"]},
        )


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _first_title(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def _tex_escape(s: str) -> str:
    repl = {
        "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
        "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(repl.get(ch, ch) for ch in s)


def _build_tex(template: str, title: str, idea: str, exp_log: str) -> str:
    """Insert a governed body into the template's document body.

    The assembled markdown is embedded verbatim (robust against LaTeX specials) so
    the .tex is always valid regardless of content.
    """
    body_lines = [
        r"\section*{" + _tex_escape(title) + "}",
        r"\paragraph{Provenance.} Assembled by the Veridraft harness from evidence-gated claims.",
        "",
        r"\section*{Idea (assembled from gated claims)}",
        r"\begin{verbatim}",
        idea.strip() or "(empty)",
        r"\end{verbatim}",
        "",
        r"\section*{Experimental Log}",
        r"\begin{verbatim}",
        exp_log.strip() or "(empty)",
        r"\end{verbatim}",
    ]
    body = "\n".join(body_lines)

    if template.strip() and r"\end{document}" in template:
        return template.replace(r"\end{document}", body + "\n" + r"\end{document}")
    # Fallback minimal article if no usable template.
    return (
        "\\documentclass{article}\n\\usepackage[margin=1in]{geometry}\n"
        "\\begin{document}\n" + body + "\n\\end{document}\n"
    )


def _detect_renderer() -> str:
    if shutil.which("tectonic"):
        return "tectonic"
    if shutil.which("pdflatex"):
        return "pdflatex"
    return "builtin"


def _render_pdf(tex_path: Path, out_dir: Path, title: str, idea: str,
                exp_log: str, pdf_path: Path) -> str:
    renderer = _detect_renderer()
    try:
        if renderer == "tectonic":
            proc = subprocess.run(
                ["tectonic", str(tex_path), "--outdir", str(out_dir), "--keep-logs"],
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode == 0 and pdf_path.exists():
                return "tectonic"
        elif renderer == "pdflatex":
            proc = subprocess.run(
                ["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                 "-output-directory", str(out_dir), str(tex_path)],
                capture_output=True, text=True, timeout=180,
            )
            if proc.returncode == 0 and pdf_path.exists():
                return "pdflatex"
    except (OSError, subprocess.SubprocessError):
        pass

    # Dependency-free fallback: always produces a valid PDF.
    lines: list[str] = []
    for block in (idea, exp_log):
        for ln in block.splitlines():
            lines.append(ln.lstrip("#").rstrip())
        lines.append("")
    write_text_pdf(str(pdf_path), title, lines)
    return "builtin"
