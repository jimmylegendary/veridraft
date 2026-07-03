"""v1 PatentEngineAdapter: `minimal-patent` — deterministic, offline, zero-dep.

Proves the patent path spine (ADR-0004): from GATED claims it assembles a structured
patent application — Title, Field, Background, Summary, Detailed Description (with
antecedent basis + embodiments), CLAIMS (an independent method/apparatus/CRM set +
a dependent ladder), Abstract, drawing refs — and emits `open_items` for every gap a
human/attorney must resolve. `needs_human=True` is fixed; it NEVER files.

It is one adapter behind the PatentEngine port; `patent-llm` (LLM-assisted, higher
quality) is the other, selected by config — swapping does not touch the core.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from ..core.models import EngineOutput
from ..core.pdf import write_text_pdf
from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity


@register(port="patent_engine", id="minimal-patent")
class MinimalPatentEngineAdapter:
    capabilities = AdapterCapabilities(
        port="patent_engine", id="minimal-patent", version="0.1.0",
        provides=("patent_spec", "patent_claims", "abstract"),
        features=frozenset({"deterministic", "offline", "no-llm", "needs-human"}),
        requires_config=(), maturity=Maturity.V1,
    )
    needs_human = True

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def health(self) -> HealthStatus:
        return HealthStatus.healthy("minimal-patent ready (deterministic; human gate mandatory)")

    def screen(self, claim_ids: list[str]) -> dict:
        return {}  # the harness owns the patentability screen (core/patentability.py)

    def draft_patent(self, workspace: str) -> EngineOutput:
        ws = Path(workspace)
        inputs = ws / "inputs"
        # Tolerate malformed inputs (bad JSON / not-a-list / non-dict entries) instead of
        # crashing mid-draft; keep only well-formed entries.
        claims = [c for c in _load_list(inputs / "claims.json")
                  if c.get("type") and isinstance(c.get("statement"), str) and c["statement"].strip()]
        results = _load_list(inputs / "results.json")
        title = _title(_read(inputs / "invention.md")) or "Invention"
        method = [c for c in claims if c.get("type") in ("P1", "P2")]
        proj = [c for c in claims if c.get("type") == "P3"]

        claim_set, open_items = _build_claims(method, proj)
        spec = _build_spec(title, method, results)
        abstract = _build_abstract(title, method)

        final = ws / "final"
        final.mkdir(parents=True, exist_ok=True)
        tex = _build_tex(title, spec, claim_set, abstract)
        tex_path = final / "patent.tex"
        tex_path.write_text(tex, encoding="utf-8")
        pdf_path = final / "patent.pdf"
        renderer = _render(tex_path, final, title, spec, claim_set, abstract, pdf_path)
        (final / "open_items.json").write_text(json.dumps(open_items, indent=2), encoding="utf-8")
        # structured claim-ledger for the deterministic readiness gates (core/lints.py)
        sub = "S1: " + (method[0]["statement"].rstrip(".") if method else "the method")
        ledger = {
            "independent_claims": [{"substrate": sub, "kind": k, "text": t}
                                   for k, t in zip(("method", "system", "medium"), claim_set["independent"])],
            "dependent_claims": [{"substrate": "S1", "kind": "dependent", "text": t}
                                 for t in claim_set["dependent"]],
        }
        (final / "claims.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")

        return EngineOutput(
            engine_adapter="minimal-patent", workspace_path=str(ws),
            paper_tex_path=str(tex_path), paper_pdf_path=str(pdf_path),
            scores={"renderer": renderer, "independent_claims": len(claim_set["independent"]),
                    "dependent_claims": len(claim_set["dependent"]), "open_items": len(open_items)},
            provenance={"open_items": open_items, "needs_human": True,
                        "type": "patent", "substrate_claims": [c.get("claim_id", "?") for c in method]},
        )


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _load_list(p: Path) -> list[dict]:
    """Parse a JSON file to a list of dicts; malformed JSON / non-list / non-dict → dropped."""
    try:
        v = json.loads(_read(p) or "[]")
    except (ValueError, TypeError):
        return []
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _title(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].replace("Invention Disclosure:", "").strip()
    return ""


def _build_claims(method: list[dict], proj: list[dict]) -> tuple[dict, list[str]]:
    open_items: list[str] = []
    if not method:
        open_items.append("No P1/P2 substrate claims — no patentable independent claim can be drafted.")
    els = [c["statement"].rstrip(".") for c in method]
    # P1 (101): if the method ends at computing a cost/estimate/score, it is an abstract idea on
    # a generic computer. Append a terminal practical-application / technical-effect limitation.
    ends_at_cost = bool(els and re.search(r"cost|estimat|score|predict|rank|latency|throughput|perform",
                                          els[-1], re.I))
    terminal = ("; and emitting, based at least in part on a result of the preceding step, a "
                "machine-readable specification of a selected hardware configuration for fabrication "
                "or configuration of the accelerator") if ends_at_cost else ""
    # Independent claim 1 (method): elements from each substrate claim
    steps = "".join(f"\n    {_ordinal(i)} {e};" for i, e in enumerate(els)) or "\n    performing the method;"
    indep = [
        f"1. A computer-implemented method comprising:{steps[:-1]}{terminal}.",
        "2. A system comprising one or more processors and memory storing instructions that, "
        "when executed, cause the system to perform the method of claim 1.",
        "3. A non-transitory computer-readable medium storing instructions that, when executed by "
        "one or more processors, cause the processors to perform the method of claim 1.",
    ]
    # Dependent ladder: one narrowing claim per additional substrate claim + result-backed limitations
    dependent: list[str] = []
    n = 4
    if ends_at_cost:  # P1 101-fallback dependent
        dependent.append(f"{n}. The method of claim 1, wherein emitting the machine-readable "
                         f"specification comprises outputting a register-transfer-level (RTL) or "
                         f"high-level-synthesis (HLS) parameter set usable to configure or fabricate "
                         f"the accelerator.")
        n += 1
    for i, c in enumerate(method[1:], start=1):
        dependent.append(f"{n}. The method of claim 1, wherein {c['statement'].rstrip('.').lower()}.")
        n += 1
        _refs = c.get("result_refs")
        for rr in (_refs if isinstance(_refs, list) else []):
            if not isinstance(rr, str) or not rr.strip():
                continue   # a malformed/empty ref must not fabricate an "evidenced by" dependent
            dependent.append(f"{n}. The method of claim {n-1}, wherein the method achieves a measured "
                             f"improvement evidenced by result {rr}.")
            n += 1
    for c in proj:
        open_items.append(f"P3 projection {c.get('claim_id', '?')} excluded from claims "
                          f"(requires-enablement-review): {c['statement'][:80]}")
    open_items.append("102/103 novelty UNSCREENED: a prior-art search must confirm ≥1 element is absent from "
                      "every single reference before filing.")
    open_items.append("101 eligibility (software/AI abstract-idea) is a legal determination — attorney review.")
    open_items.append("Antecedent basis + means-plus-function (§112) and jurisdiction formalities require review.")
    return {"independent": indep, "dependent": dependent}, open_items


def _ordinal(i: int) -> str:
    return {0: "", 1: "further", 2: "further", 3: "further"}.get(i, "further")


def _build_spec(title: str, method: list[dict], results: list[dict]) -> str:
    embodiments = "\n\n".join(
        f"In one embodiment, {c['statement'].rstrip('.')}. This embodiment is enabled by the "
        f"disclosed results and may be varied in fallback configurations to support design-arounds."
        for c in method) or "In one embodiment, the method is performed as described."
    data = ""
    for r in results:
        rows = "; ".join(f"{m.get('name','')}={m.get('value','')}{m.get('unit','')}"
                         for m in (r.get("metrics") or []) if isinstance(m, dict))
        data += f"\nResult {r.get('result_id', '?')} ({r.get('description', '')}): {rows}."
    return (
        "FIELD OF THE INVENTION\n\n"
        f"The present invention relates to {title.lower()}.\n\n"
        "BACKGROUND\n\n"
        "Existing approaches leave an unmet need that the present invention addresses; the "
        "background is framed without admitting more prior art than necessary.\n\n"
        "SUMMARY\n\n"
        "The invention provides a method (and corresponding system and computer-readable medium) "
        "aligned with the independent claims below.\n\n"
        "DETAILED DESCRIPTION\n\n"
        f"{embodiments}\n\n"
        "Enablement is supported by the following disclosed results (written-description support):"
        f"{data or ' (no result refs supplied).'}\n"
    )


def _build_abstract(title: str, method: list[dict]) -> str:
    core = method[0]["statement"].rstrip(".") if method else "the disclosed method"
    a = f"A computer-implemented method, system, and computer-readable medium for {title.lower()}, in which {core.lower()}."
    return a[:600]  # ≤ ~150 words


_LATEX_ESC = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
              "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
              "^": r"\textasciicircum{}"}
_LATEX_ESC_RE = re.compile(r"[\\&%$#_{}~^]")


def _build_tex(title: str, spec: str, claim_set: dict, abstract: str) -> str:
    def esc(s):
        # single pass over the ORIGINAL chars so bundle-derived text with { } ~ ^ can't break
        # the compile, and the braces we introduce (\textbackslash{}) are never re-escaped.
        return _LATEX_ESC_RE.sub(lambda m: _LATEX_ESC[m.group()], s)
    # P11: USPTO-style numbered paragraphs [0001]... for the specification body
    paras, i = [], 0
    for block in spec.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.isupper() and len(block) < 60:      # a section heading line
            paras.append(r"\medskip\noindent\textbf{" + esc(block) + "}")
        else:
            i += 1
            paras.append(f"\\noindent [{i:04d}] " + esc(block))
    spec_tex = "\n\n".join(paras)
    claims_tex = "\n".join(r"\item " + esc(c[c.find(".")+1:].strip())
                           for c in claim_set["independent"] + claim_set["dependent"])
    return (
        "\\documentclass[11pt]{article}\n\\usepackage[margin=1in]{geometry}\n"
        "\\title{PATENT APPLICATION (DRAFT --- NOT A FILING)\\\\ " + esc(title) + "}\n\\date{}\n"
        "\\begin{document}\n\\maketitle\n"
        "\\begin{center}\\small\\itshape CONFIDENTIAL. Machine-drafted by Veridraft minimal-patent; "
        "a patent attorney must review. Not a filing.\\end{center}\n"
        "\\section*{Abstract}\n" + esc(abstract) + "\n\n"
        "\\section*{Brief Description of the Drawings}\n"
        "\\noindent FIG.~1 is a flowchart of the claimed method. FIG.~2 is a block diagram of the "
        "corresponding system. \\textit{(Formal drawings with reference numerals to be supplied "
        "before filing --- see open\\_items.json.)}\n\n"
        "\\section*{Detailed Description}\n" + spec_tex + "\n\n"
        "\\section*{Claims}\n\\textit{What is claimed is:}\n\\begin{enumerate}\n" + claims_tex +
        "\n\\end{enumerate}\n"
        "\\vfill\\noindent\\textit{Human/attorney review mandatory before any filing; FTO + "
        "post-claim prior-art search required. See open\\_items.json.}\n"
        "\\end{document}\n"
    )


def _render(tex_path, out_dir, title, spec, claim_set, abstract, pdf_path) -> str:
    for eng in ("tectonic", "pdflatex"):
        if shutil.which(eng):
            try:
                if eng == "tectonic":
                    r = subprocess.run(["tectonic", str(tex_path), "--outdir", str(out_dir)],
                                       capture_output=True, text=True, timeout=180)
                else:
                    r = subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                                        "-output-directory", str(out_dir), str(tex_path)],
                                       capture_output=True, text=True, timeout=180)
                if r.returncode == 0 and pdf_path.exists():
                    return eng
            except (OSError, subprocess.SubprocessError):
                pass
    lines = [f"PATENT APPLICATION (DRAFT — NOT A FILING): {title}", "", "ABSTRACT", abstract, ""]
    lines += spec.splitlines() + ["", "CLAIMS"]
    lines += claim_set["independent"] + claim_set["dependent"]
    write_text_pdf(str(pdf_path), f"PATENT DRAFT: {title}", lines)
    return "builtin"
