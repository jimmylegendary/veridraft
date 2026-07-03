"""Input assembly — governed bundle → PaperOrchestra input tuple (ADR-0002 §5).

Produces `workspace/inputs/{idea.md, experimental_log.md, template.tex,
conference_guidelines.md, figures/}` from GATED claims + CAW-01 result refs.

Structural block (ADR-0003 §6): this function REFUSES to assemble a claim whose
gate_status is not PASSED. There is no code path from an ungated claim to engine
inputs — the gate is not advisory.
"""
from __future__ import annotations

from pathlib import Path

from .models import Claim, ClaimType, GateStatus, ResultRef


class UngatedClaimError(RuntimeError):
    """Raised if assembly is attempted with a claim that did not pass the gate."""


def assemble_inputs(
    workspace_dir: str,
    claims: list[Claim],
    results: list[ResultRef],
    template_tex: str,
    guidelines_md: str,
    title: str = "Veridraft Draft",
    figure_paths: list[str] | None = None,
    context: str = "",
) -> dict:
    ungated = [c.claim_id for c in claims if c.gate_status is not GateStatus.PASSED]
    if ungated:
        raise UngatedClaimError(
            f"cannot assemble inputs: claims did not pass the gate: {ungated}"
        )
    if not claims:
        raise UngatedClaimError("cannot assemble inputs: no gated claims provided")

    inputs = Path(workspace_dir) / "inputs"
    figs = inputs / "figures"
    figs.mkdir(parents=True, exist_ok=True)

    idea = _build_idea(title, claims, context)
    exp_log, cited_results = _build_experimental_log(claims, results)

    (inputs / "idea.md").write_text(idea, encoding="utf-8")
    (inputs / "experimental_log.md").write_text(exp_log, encoding="utf-8")
    (inputs / "template.tex").write_text(template_tex, encoding="utf-8")
    (inputs / "conference_guidelines.md").write_text(guidelines_md, encoding="utf-8")

    for p in figure_paths or []:
        src = Path(p)
        if src.exists():
            (figs / src.name).write_bytes(src.read_bytes())

    return {
        "inputs_dir": str(inputs),
        "idea": str(inputs / "idea.md"),
        "experimental_log": str(inputs / "experimental_log.md"),
        "template": str(inputs / "template.tex"),
        "guidelines": str(inputs / "conference_guidelines.md"),
        "figures_dir": str(figs),
        "provenance": {
            "claim_ids": [c.claim_id for c in claims],
            "result_ids": cited_results,
        },
    }


def _build_idea(title: str, claims: list[Claim], context: str = "") -> str:
    methods = [c for c in claims if c.type in (ClaimType.P1, ClaimType.P2)]
    p1 = [c for c in methods if c.type is ClaimType.P1]
    bullets = "\n".join(f"- ({c.type.value}) {c.statement}" for c in methods) or "- (none)"
    hypotheses = "\n".join(f"- {c.statement}" for c in p1) or \
        "- The gated claims hold under the reported experimental conditions."
    topic = context.strip() or title
    return (
        f"# {title}\n\n"
        f"## Problem Statement\n\n"
        f"This paper investigates {topic}. It is assembled from evidence-gated claims: "
        f"every claim below passed Veridraft's evidence gate and traces to concrete experimental "
        f"results, so each quantitative statement in the paper is backed by data (generated "
        f"text is never treated as evidence).\n\n"
        f"## Core Hypothesis\n\n{hypotheses}\n\n"
        f"## Proposed Methodology\n\n{bullets}\n\n"
        f"## Expected Contribution\n\n"
        f"A defensible, provenance-carrying account of the above claims — each reported number "
        f"traceable to a result id — suitable for submission after review.\n"
    )


def _build_experimental_log(
    claims: list[Claim], results: list[ResultRef]
) -> tuple[str, list[str]]:
    """Strict 3-section log. Section 2 numbers are sourced from CAW-01 result refs;
    every number is traceable to a result_id."""
    by_id = {r.result_id: r for r in results}
    cited_ids: list[str] = []
    for c in claims:
        for rid in c.result_refs:
            if rid in by_id and rid not in cited_ids:
                cited_ids.append(rid)

    setup_lines = "\n".join(
        f"- Claim {c.claim_id} ({c.type.value}) is backed by "
        f"{len(c.admissible_evidence())} admissible evidence item(s)."
        for c in claims
    )

    data_blocks: list[str] = []
    for rid in cited_ids:
        r = by_id[rid]
        header = f"Result `{rid}`" + (f" — {r.description}" if r.description else "")
        rows = "\n".join(
            f"| {m.get('name','')} | {m.get('value','')} | {m.get('unit','')} |"
            for m in r.metrics
        ) or "| (no metrics) | | |"
        data_blocks.append(
            f"{header}\n\n"
            f"| Metric | Value | Unit |\n| --- | --- | --- |\n{rows}\n"
        )
    data_section = "\n\n".join(data_blocks) or "(no CAW-01 result refs supplied)"

    observations = "\n".join(
        f"- We observed that claim {c.claim_id} held under the reported setup."
        for c in claims
    )

    log = (
        "# Experimental Log\n\n"
        "## 1. Experimental Setup\n\n"
        f"{setup_lines}\n\n"
        "## 2. Raw Numeric Data\n\n"
        f"{data_section}\n\n"
        "## 3. Qualitative Observations\n\n"
        f"{observations}\n"
    )
    return log, cited_ids
