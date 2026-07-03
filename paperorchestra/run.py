#!/usr/bin/env python3
"""Portable PaperOrchestra runner — backend-agnostic.

Runs the 5-agent PaperOrchestra pipeline over a prepared workspace against ANY
configured backend (self-hosted OpenAI-compatible API, openclaw, claude-code, codex),
so the same skills work wherever you got them. The LLM + tools come from the backend;
this runner only orchestrates the steps, the 2‖3 parallelism, the deterministic
scripts, degrade modes, and the LaTeX compile.

    python3 run.py --config paperorchestra.config.json --workspace <ws> [--from N] [--to N]

The workspace must already contain inputs/{idea.md, experimental_log.md, template.tex,
conference_guidelines.md, figures/} (Veridraft's assemble step produces exactly this).
This is what Veridraft's `paperorchestra` WritingEngine adapter invokes via `po_command`.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # importable as a module or run as a script
import providers

# (step_no, key, skill_folder, output_artifact_relpath, writes_paper_content)
STEPS = [
    (1, "outline", "outline-agent", "outline.json", True),
    (2, "plotting", "plotting-agent", "figures/captions.json", False),
    (3, "litreview", "literature-review-agent", "refs.bib", True),
    (4, "sections", "section-writing-agent", "drafts/paper.tex", True),
    (5, "refine", "content-refinement-agent", "final/paper.tex", True),
]

ANTI_LEAKAGE = "paper-orchestra/references/anti-leakage-prompt.md"


def log(msg: str) -> None:
    print(f"[paperorchestra-run] {msg}", flush=True)


def _skills_dir(cfg: dict) -> Path:
    if cfg.get("skills_dir"):
        return Path(os.path.expanduser(cfg["skills_dir"]))
    bundled = Path(__file__).resolve().parent.parent / "skills"   # vendored PaperOrchestra (MIT)
    return bundled if bundled.exists() else Path(os.path.expanduser("~/.claude/skills"))


def _degrade_notes(cfg: dict) -> str:
    notes = []
    if not cfg.get("vision_model"):
        notes.append("No vision_model configured: SKIP the VLM figure-critique loop and do the "
                     "section-writing call TEXT-ONLY (render each figure once).")
    if cfg.get("web_search", "none") == "none":
        notes.append("web_search=none: literature review runs DEGRADED — use ONLY a user-provided "
                     "workspace/inputs/refs.bib; do not invent citations; emit a TODO marker if "
                     "Intro/Related Work cannot be cited.")
    return ("\nDEGRADE MODES (host capability limits — obey):\n- " + "\n- ".join(notes)) if notes else ""


def _step_prompt(cfg: dict, step_no: int, skill: str, output: str, writes: bool, ws: Path) -> str:
    sd = _skills_dir(cfg)
    p = (f"Execute STEP {step_no} of the PaperOrchestra pipeline.\n"
         f"Read and follow {sd}/{skill}/SKILL.md and its references/ faithfully.\n"
         f"WORKSPACE: {ws} — inputs are already in {ws}/inputs/; write outputs under {ws}/.\n"
         f"Produce {ws}/{output}. Consume the prior steps' artifacts already in the workspace.\n")
    if writes:
        p += f"Apply the anti-leakage prompt at {sd}/{ANTI_LEAKAGE} to every writing call. "
        p += "Do NOT invent numbers — every value comes verbatim from inputs/experimental_log.md §2. "
    if step_no == 3 and cfg.get("s2_api_key"):
        p += "Use the Semantic Scholar API key from env S2_API_KEY. "
    p += _degrade_notes(cfg)
    p += f"\nWhen done, confirm {output} exists and stop. Do not proceed to other steps."
    return p


def _run_script(cfg: dict, rel: str, args: list[str]) -> int:
    script = _skills_dir(cfg) / "paper-orchestra" / "scripts" / rel
    if not script.exists():
        return 0
    try:
        return subprocess.run([sys.executable, str(script), *args], check=False,
                              timeout=(cfg.get("step_timeout_seconds") or 2400)).returncode
    except subprocess.TimeoutExpired:
        return 124


def step0(cfg: dict, ws: Path) -> None:
    log("step 0: scaffold + validate inputs + tex profile")
    _run_script(cfg, "init_workspace.py", ["--out", str(ws)])
    # The input-validation gate is fail-CLOSED: a missing validator (wrong skills_dir) must stop
    # the pipeline, not silently skip validation; a non-zero validator result stops it too.
    validator = _skills_dir(cfg) / "paper-orchestra" / "scripts" / "validate_inputs.py"
    if not validator.exists():
        raise SystemExit(f"input validator missing at {validator} — set skills_dir; refusing to "
                         f"draft on unvalidated inputs")
    if _run_script(cfg, "validate_inputs.py", ["--workspace", str(ws)]) not in (0, None):
        raise SystemExit("input validation failed (validate_inputs.py) — fix inputs/ before drafting")
    _run_script(cfg, "check_tex_packages.py", ["--out", str(ws / "tex_profile.json")])
    for req in ("idea.md", "experimental_log.md", "template.tex", "conference_guidelines.md"):
        if not (ws / "inputs" / req).exists():
            raise SystemExit(f"missing required input: inputs/{req} (Veridraft assemble produces these)")


def run_step(cfg: dict, step, ws: Path) -> str:
    step_no, key, skill, output, writes = step
    log(f"step {step_no} ({key}) → {output}  [backend={cfg.get('backend')}]")
    prompt = _step_prompt(cfg, step_no, skill, output, writes, ws)
    target = ws / output
    before = target.stat().st_mtime if target.exists() else None   # detect a stale (un-rewritten) file
    providers.dispatch(cfg, prompt, str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
    if not target.exists() or (before is not None and target.stat().st_mtime <= before):
        raise SystemExit(f"step {step_no} did not (re)produce {output} — a weak backend may have "
                         f"exited without writing it, or it lacks a required tool. See degrade modes.")
    log(f"step {step_no} ok")
    return output


def compile_pdf(ws: Path) -> Path | None:
    final = ws / "final"
    final.mkdir(exist_ok=True)
    # stage figures + refs so relative \includegraphics / \bibliography resolve
    for src in list((ws / "figures").glob("*.png")) + ([ws / "refs.bib"] if (ws / "refs.bib").exists() else []):
        if src.exists():
            (final / src.name).write_bytes(src.read_bytes())
    if not (final / "paper.tex").exists():
        return None
    # A backend-written ./latexmkrc (or ./.latexmkrc) is executed by latexmk → arbitrary code even
    # with -no-shell-escape. Refuse to compile if one is present (fail-closed).
    if any((final / n).exists() for n in ("latexmkrc", ".latexmkrc")):
        log("refusing to compile: a latexmkrc is present in the workspace (code-exec risk)")
        return None
    if not (final / "refs.bib").exists() and (ws / "refs.bib").exists():
        (final / "refs.bib").write_bytes((ws / "refs.bib").read_bytes())
    try:
        subprocess.run(["latexmk", "-pdf", "-no-shell-escape", "-interaction=nonstopmode", "paper.tex"],
                       cwd=str(final), capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        log("latexmk timed out — a runaway/injected .tex; compile aborted")
        return None
    pdf = final / "paper.pdf"
    return pdf if pdf.exists() else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="paperorchestra-run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--from", dest="frm", type=int, default=0)
    ap.add_argument("--to", type=int, default=6)
    ap.add_argument("--probe", action="store_true", help="just check the backend is reachable")
    args = ap.parse_args(argv)

    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"could not read/parse --config {args.config}: {e}")
    if not isinstance(cfg, dict):
        raise SystemExit(f"--config {args.config} must be a JSON object")
    ws = Path(os.path.expanduser(args.workspace)).resolve()

    ok, detail = providers.probe(cfg)
    log(f"backend probe: {'OK' if ok else 'FAIL'} — {detail}")
    if args.probe:
        return 0 if ok else 1
    if not ok:
        raise SystemExit(detail)

    if args.frm <= 0:
        step0(cfg, ws)

    # Steps 2 and 3 are independent → run concurrently when allowed.
    pending = [s for s in STEPS if args.frm <= s[0] <= args.to]
    if cfg.get("parallel_2_3", True) and {2, 3} <= {s[0] for s in pending}:
        s2 = next(s for s in pending if s[0] == 2)
        s3 = next(s for s in pending if s[0] == 3)
        for s in [s for s in pending if s[0] == 1]:
            run_step(cfg, s, ws)
        log("steps 2 ‖ 3 in parallel")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futs = [ex.submit(run_step, cfg, s, ws) for s in (s3, s2)]  # step 3 first (slower)
            for f in futs:
                f.result()
        for s in [s for s in pending if s[0] in (4, 5)]:
            run_step(cfg, s, ws)
    else:
        for s in pending:
            run_step(cfg, s, ws)

    if args.to >= 6:
        pdf = compile_pdf(ws)
        log(f"compiled: {pdf}" if pdf else "compile skipped/failed (no final/paper.tex or latexmk issue)")
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
