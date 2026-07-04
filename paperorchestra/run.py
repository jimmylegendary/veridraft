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
import re
import shutil
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
    # veridraft assembles a pre-populated workspace (inputs/ + refs.bib + final/); overlay it.
    _run_script(cfg, "init_workspace.py", ["--out", str(ws), "--force"])
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
    # Some backends reliably CREATE a fresh file but won't overwrite an existing one. Move any
    # pre-existing target aside so the step must (re)create it; restore the backup if it doesn't
    # (never lose a valid file). refs.bib may legitimately be reused in degraded (no-web) lit-review.
    backup = None
    if target.exists():
        backup = target.with_suffix(target.suffix + ".prev")
        if backup.exists():
            backup.unlink()
        target.rename(backup)
    providers.dispatch(cfg, prompt, str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
    if not target.exists():
        if backup is not None and output == "refs.bib":
            backup.rename(target)
            log(f"step {step_no} ok (kept existing {output}; degraded lit-review)")
            return output
        if backup is not None:
            backup.rename(target)   # restore so a retry has the prior file
        raise SystemExit(f"step {step_no} did not (re)produce {output} — a weak backend may have "
                         f"exited without writing it, or it lacks a required tool. See degrade modes.")
    if backup is not None and backup.exists():
        backup.unlink()
    log(f"step {step_no} ok")
    return output


_OVERFULL_PT = 5.0   # overfull \hbox wider than this (pt) is a clipped/off-page box


def _stage_figures(ws: Path, final: Path) -> None:
    """Stage EVERY figure type into final/figures/, preserving the `figures/` subdir the
    \\includegraphics{figures/<name>.pdf|png} paths expect (was: only *.png, flat → missing-figure
    placeholder boxes). Also warn on byte-identical duplicate figures."""
    figdst = final / "figures"
    figdst.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    for src in sorted((ws / "figures").glob("*")):
        if src.is_file() and src.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".eps"):
            data = src.read_bytes()
            (figdst / src.name).write_bytes(data)
            (final / src.name).write_bytes(data)   # also flat, for \includegraphics{<name>}
            import hashlib
            h = hashlib.md5(data).hexdigest()
            if h in seen:
                log(f"WARNING: figure {src.name} is byte-identical to {seen[h]} (duplicate — check captions)")
            else:
                seen[h] = src.name


_BIBTEX_SKIP = "BIBTEX-DID-NOT-RUN"


def _needs_bib(tex_src: str) -> bool:
    return bool(re.search(r"\\(cite[a-z]*|bibliography|addbibresource)\b", tex_src))


def _pdf_text(pdf: Path) -> str:
    """Extract the FINAL PDF's text (pdftotext). '' if the PDF/tool is absent."""
    if not pdf.exists() or not shutil.which("pdftotext"):
        return ""
    try:
        return subprocess.run(["pdftotext", "-q", str(pdf), "-"],
                              capture_output=True, text=True, timeout=60).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _has_link_annots(pdf: Path) -> bool:
    """True if the PDF carries `/Link` annotations (hyperref cross-reference links). Modern engines
    compress object streams, hiding `/Link` from a raw grep, so fall back to a mutool decompress."""
    try:
        if b"/Link" in pdf.read_bytes():
            return True
    except OSError:
        return False
    if shutil.which("mutool"):
        out = pdf.with_suffix(".decomp.pdf")
        try:
            subprocess.run(["mutool", "clean", "-d", str(pdf), str(out)], capture_output=True, timeout=30)
            hit = out.exists() and b"/Link" in out.read_bytes()
            return hit
        except (OSError, subprocess.SubprocessError):
            return False
        finally:
            if out.exists():
                out.unlink()
    return False


def _fig_ahash(path: Path) -> int | None:
    """8×8 average-hash of a figure (perceptual). Rasterizes pdf/eps via pdftoppm/mutool first.
    None if the image can't be read (PIL/tool absent)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    src = path
    tmp = None
    if path.suffix.lower() in (".pdf", ".eps"):
        tmp = path.with_suffix(".ahash.png")
        try:
            if shutil.which("pdftoppm"):
                subprocess.run(["pdftoppm", "-png", "-singlefile", "-r", "50", str(path),
                                str(tmp.with_suffix(""))], capture_output=True, timeout=30)
            elif shutil.which("mutool"):
                subprocess.run(["mutool", "draw", "-o", str(tmp), "-r", "50", str(path), "1"],
                               capture_output=True, timeout=30)
            else:
                return None
        except (OSError, subprocess.SubprocessError):
            return None
        src = tmp if tmp.exists() else None
    try:
        if src is None or not src.exists():
            return None
        img = Image.open(src).convert("L").resize((8, 8))
        px = list(img.getdata())
        avg = sum(px) / len(px)
        return sum(1 << i for i, p in enumerate(px) if p >= avg)
    except Exception:   # noqa: BLE001 — a bad image must not crash the compile
        return None
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def _perceptual_near_dups(final: Path, threshold: int = 6) -> list[str]:
    """G4/A1/F3: flag near-identical figures a BYTE hash misses (e.g. a resized crop of another
    figure). Hamming distance of the 8×8 average-hash ≤ threshold ⇒ near-duplicate."""
    figs = sorted(p for p in (final / "figures").glob("*")
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".eps")) \
        if (final / "figures").exists() else []
    hashes = [(p.name, _fig_ahash(p)) for p in figs]
    hashes = [(n, h) for n, h in hashes if h is not None]
    out = []
    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            if bin(hashes[i][1] ^ hashes[j][1]).count("1") <= threshold:
                out.append(f"near-duplicate figures: {hashes[i][0]} ≈ {hashes[j][0]} "
                           "(perceptually similar — check for a mislabeled/resized dup)")
    return out


def _vlm_findings(cfg: dict, final: Path) -> list[str]:
    """G4: an actual render-and-LOOK pass. When `vision_model` is set, ask a VLM to inspect the
    rendered figure images for VISUAL defects the log parser structurally cannot see (overlap,
    cramped arrows, clipped/illegible text, LaTeX-escape leaks in labels, near-duplicate plots).
    Returns [] with no vision_model or on any failure — advisory, never blocks the compile."""
    vm = cfg.get("vision_model")
    figs = sorted(str(p) for p in (final / "figures").glob("*")
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg")) if (final / "figures").exists() else []
    if not vm or not figs:
        return []
    prompt = ("LOOK at each figure image below and report VISUAL defects as a JSON array of short "
              "strings (return [] if none). For each image check: overlapping/cramped elements; text "
              "touching, clipped, or illegible; arrows crossing or crushed between boxes; a LaTeX-escape "
              "leak in a label (a literal '\\_', '\\&', '\\%', '\\#'); or two figures that look like "
              "near-duplicates. Images:\n" + "\n".join(figs) + "\nReturn ONLY the JSON array.")
    try:
        out = providers.dispatch({**cfg, "model": vm}, prompt, str(final.parent),
                                 timeout=(cfg.get("step_timeout_seconds") or 1200))
    except Exception as e:   # noqa: BLE001
        log(f"VLM visual check skipped ({e})"); return []
    i, j = out.find("["), out.rfind("]")
    if i == -1 or j <= i:
        return []
    try:
        arr = json.loads(out[i:j + 1])
        return [str(x) for x in arr][:20] if isinstance(arr, list) else []
    except (ValueError, TypeError):
        return []


def _verify_compile(final: Path) -> list[str]:
    """Deterministic render-and-check (F2/A3/G1). Overfull boxes + missing figures are read from the
    compile LOG (final-pass truths). Unresolved refs/cites are judged from the FINAL PDF's TEXT
    (pdftotext → literal [?]/??), NOT the log: a healthy multi-pass build emits 100s of transient
    'Citation/Reference undefined' lines before bibtex runs (G1 — that log grep was a false positive
    on every real build). Also flags a missing .bbl when the paper cites (bibtex never ran)."""
    logf = final / "paper.log"
    txt = logf.read_text(encoding="utf-8", errors="replace") if logf.exists() else ""
    issues: list[str] = []
    for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt too wide\)", txt):
        if float(m.group(1)) > _OVERFULL_PT:
            issues.append(f"overfull hbox {m.group(1)}pt too wide (clipped/off-page table or line)")
    for m in re.finditer(r"File [`']?([^'\s]+\.(?:pdf|png|jpg|jpeg|eps))'? not found", txt, re.I):
        issues.append(f"missing figure file: {m.group(1)} (placeholder box in the PDF)")
    tex_src = ((final / "paper.tex").read_text(encoding="utf-8", errors="replace")
               if (final / "paper.tex").exists() else "")
    if _needs_bib(tex_src):
        bbl = final / "paper.bbl"
        if not bbl.exists() or bbl.stat().st_size == 0:
            issues.append(f"{_BIBTEX_SKIP}: no non-empty paper.bbl though the paper \\cite's — every "
                          "citation renders as [?]; recompile with a bibtex pass (do NOT edit the tex)")
    pdf_txt = _pdf_text(final / "paper.pdf")
    if "[?]" in pdf_txt:
        issues.append("unresolved \\cite rendered as [?] in the final PDF")
    if re.search(r"(?<!\d)\?\?(?!\d)", pdf_txt):
        issues.append("unresolved \\ref rendered as ?? in the final PDF")
    seen, out = set(), []
    for i in issues:
        k = i.split(":")[0].split("(")[0][:40]
        if k not in seen or "missing figure" in i:
            seen.add(k); out.append(i)
    return out


def _cjk_prep(tex_path: Path) -> str:
    """Return the latexmk engine flag. pdflatex can't set non-Latin (e.g. Korean); if the .tex has
    substantial CJK/Hangul AND lualatex is available, switch to lualatex and inject a CJK package
    (kotex/xeCJK) when the preamble has none. Fully guarded — no tools ⇒ unchanged pdflatex."""
    try:
        src = tex_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "-pdf"
    cjk = sum(1 for ch in src if 0x1100 <= ord(ch) <= 0x11FF or 0x3000 <= ord(ch) <= 0x9FFF
              or 0xAC00 <= ord(ch) <= 0xD7A3)
    if cjk < 20 or not shutil.which("lualatex"):
        return "-pdf"
    if not re.search(r"\\usepackage(\[[^\]]*\])?\{(kotex|xeCJK|CJKutf8|fontspec)\}", src):
        for pkg in ("kotex", "xeCJK"):
            if shutil.which("kpsewhich") and subprocess.run(
                    ["kpsewhich", f"{pkg}.sty"], capture_output=True).returncode == 0:
                src = re.sub(r"(\\documentclass(\[[^\]]*\])?\{[^}]+\})",
                             r"\1\n\\usepackage{" + pkg + "}", src, count=1)
                tex_path.write_text(src, encoding="utf-8")
                log(f"non-Latin text detected → lualatex + \\usepackage{{{pkg}}}")
                return "-lualatex"
        log("non-Latin text detected but no kotex/xeCJK found — trying lualatex without a CJK package")
    return "-lualatex"


def _fix_prompt(ws: Path, issues: list[str]) -> str:
    figs = sorted(p.name for p in (ws / "final" / "figures").glob("*")) if (ws / "final" / "figures").exists() else []
    return (
        f"The compiled PDF at {ws}/final/paper.pdf has these render DEFECTS (from the LaTeX log):\n- "
        + "\n- ".join(issues) + f"\n\nEdit ONLY {ws}/final/paper.tex to fix exactly these, then stop:\n"
        "- Overfull hbox / clipped table: wrap the offending tabular in \\resizebox{\\linewidth}{!}{ … }, "
        "or convert it to tabularx with p{…} columns so it fits the text width — do NOT drop columns or data.\n"
        f"- Missing figure file: repoint each \\includegraphics to an EXISTING file under figures/ "
        f"(available: {figs}); if a referenced figure truly does not exist, remove that \\includegraphics "
        "and its float — not the surrounding prose.\n"
        "- Unresolved \\ref (?? in the PDF): add the missing \\label, or fix the \\ref key.\n"
        "- Unresolved \\cite ([?] in the PDF): the key is missing from refs.bib — ADD the correct "
        "@article/@inproceedings entry (or fix the key). NEVER delete a \\cite or \\bibliography to "
        "'resolve' it (that CAUSES the [?]).\n"
        "HARD RULES: do NOT change any number, result, table value, or prose content; do NOT add claims. "
        f"Rewrite {ws}/final/paper.tex in place and confirm it exists.")


def _self_fix(cfg: dict, ws: Path, max_iters: int = 2) -> None:
    """F4: feed the deterministic self-check issues back into a BOUNDED fix loop (re-dispatch the
    backend to patch final/paper.tex, recompile, re-verify). Stops when clean, when issues don't
    decrease, or after max_iters; REVERTS an iteration that makes it worse (halt-rule discipline)."""
    final = ws / "final"
    prev = None
    for it in range(1, max_iters + 1):
        issues = _verify_compile(final)
        # G1: a bibtex-skip is a COMPILE problem, not a text problem. Recompile (compile_pdf runs the
        # bibtex recovery), NEVER hand it to the backend — an over-eager edit deletes \cite and causes
        # the very [?] the user saw. Only the remaining defects are eligible for a backend edit.
        if any(i.startswith(_BIBTEX_SKIP) for i in issues):
            log(f"self-fix iter {it}: bibtex-skip → recompile recovery (no backend edit)")
            compile_pdf(ws)
            issues = _verify_compile(final)
        editable = [i for i in issues if not i.startswith(_BIBTEX_SKIP)]
        if not editable:
            if issues:
                log(f"self-fix: {len(issues)} unfixable issue(s) remain (bibtex/env) — see verify.json")
            return
        if prev is not None and len(editable) >= len(prev):
            log(f"self-fix iter {it}: {len(editable)} issue(s) did not decrease — stopping"); return
        prev = editable
        log(f"self-fix iter {it}/{max_iters}: {len(editable)} issue(s) → dispatching a targeted fix")
        backup = (final / "paper.tex").read_bytes()
        try:
            providers.dispatch(cfg, _fix_prompt(ws, editable), str(ws),
                               timeout=(cfg.get("step_timeout_seconds") or 2400))
        except Exception as e:   # noqa: BLE001 — a backend failure must not lose the paper
            log(f"self-fix dispatch failed ({e}) — keeping the pre-fix paper"); return
        compile_pdf(ws)          # re-stage + recompile (with bibtex recovery) + rewrite verify.json
        after = [i for i in _verify_compile(final) if not i.startswith(_BIBTEX_SKIP)]
        if len(after) > len(editable):
            log(f"self-fix iter {it} made it worse ({len(after)} > {len(editable)}) — reverting")
            (final / "paper.tex").write_bytes(backup)
            compile_pdf(ws)
            return
    remain = _verify_compile(final)
    log(f"self-fix: {len(remain)} issue(s) remain after {max_iters} iters" if remain else "self-fix: clean")


def compile_pdf(ws: Path) -> Path | None:
    final = ws / "final"
    final.mkdir(exist_ok=True)
    _stage_figures(ws, final)                       # B3: all figure types → final/figures/
    if (ws / "refs.bib").exists() and not (final / "refs.bib").exists():
        (final / "refs.bib").write_bytes((ws / "refs.bib").read_bytes())
    if not (final / "paper.tex").exists():
        return None
    # A backend-written ./latexmkrc (or ./.latexmkrc) is executed by latexmk → arbitrary code even
    # with -no-shell-escape. Refuse to compile if one is present (fail-closed).
    if any((final / n).exists() for n in ("latexmkrc", ".latexmkrc")):
        log("refusing to compile: a latexmkrc is present in the workspace (code-exec risk)")
        return None
    engine_flag = _cjk_prep(final / "paper.tex")   # F1: non-Latin → lualatex + kotex if available
    try:
        subprocess.run(["latexmk", engine_flag, "-no-shell-escape", "-interaction=nonstopmode", "paper.tex"],
                       cwd=str(final), capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        log("latexmk timed out — a runaway/injected .tex; compile aborted")
        return None
    pdf = final / "paper.pdf"
    if not pdf.exists():
        return None
    # G1: latexmk skips the bibtex+rerun cycle if an earlier pass errored → no .bbl → every \cite
    # becomes [?]. If the paper cites but produced no .bbl, run an explicit bibtex recovery pass.
    tex_src = (final / "paper.tex").read_text(encoding="utf-8", errors="replace")
    bbl = final / "paper.bbl"
    if _needs_bib(tex_src) and (not bbl.exists() or bbl.stat().st_size == 0):
        log("citations present but no paper.bbl — running an explicit bibtex recovery pass")
        _bibtex_recover(final, engine_flag)
        if not pdf.exists():
            return None
    # A3/F4 self-verification (deterministic): clipped tables / missing figures / unresolved cites.
    issues = _verify_compile(final)
    dups = _perceptual_near_dups(final)   # G4/A1/F3: perceptual near-duplicate figures
    (final / "verify.json").write_text(
        json.dumps({"issues": issues, "figure_warnings": dups}, indent=2), encoding="utf-8")
    if issues:
        log(f"SELF-CHECK found {len(issues)} render issue(s) — see final/verify.json:")
        for i in issues:
            log(f"  ✗ {i}")
        log("  (fix the offending step / template and recompile; the PDF was produced but is flawed)")
    else:
        log("self-check: no overfull boxes / missing figures / unresolved refs")
    for d in dups:
        log(f"  ⚠ {d}")
    # G2: hyperref + resolved citations ⇒ clickable cross-references (checked, not assumed):
    # both conditions are verified deterministically; /Link detection is a bonus confirmation.
    if _needs_bib(tex_src) and "hyperref" in tex_src and "[?]" not in _pdf_text(pdf):
        log("clickable cross-references: hyperref active + citations resolved"
            + (" (/Link annotations confirmed)" if _has_link_annots(pdf) else ""))
    return pdf


def _bibtex_recover(final: Path, engine_flag: str) -> None:
    """Explicit pdflatex/lualatex → bibtex → 2× relatex, for when latexmk stopped before bibtex."""
    engine = "lualatex" if engine_flag == "-lualatex" else "pdflatex"
    if not shutil.which(engine) or not shutil.which("bibtex"):
        log(f"cannot recover citations: {engine}/bibtex not on PATH"); return
    tex_step = [engine, "-no-shell-escape", "-interaction=nonstopmode", "paper.tex"]
    for cmd in (tex_step, ["bibtex", "paper"], tex_step, tex_step):
        try:
            subprocess.run(cmd, cwd=str(final), capture_output=True, text=True, timeout=240)
        except (OSError, subprocess.SubprocessError):
            log(f"bibtex recovery step '{cmd[0]}' failed"); return


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
        # F4: if the deterministic self-check found render defects, feed them back into a bounded
        # fix loop (re-dispatch the backend to patch paper.tex, recompile, re-verify). Opt-out via
        # self_fix:false; the loop reverts any iteration that makes it worse.
        if pdf and cfg.get("self_fix", True) and _verify_compile(ws / "final"):
            _self_fix(cfg, ws, int(cfg.get("self_fix_max_iters", 2)))
            pdf = (ws / "final" / "paper.pdf") if (ws / "final" / "paper.pdf").exists() else None
        # G4: an actual VLM render-and-LOOK pass (advisory) when a vision_model is configured.
        if pdf:
            vlm = _vlm_findings(cfg, ws / "final")
            for v in vlm:
                log(f"  👁 VLM: {v}")
            if vlm:
                vf = ws / "final" / "verify.json"
                data = json.loads(vf.read_text()) if vf.exists() else {"issues": [], "figure_warnings": []}
                data["vlm_findings"] = vlm
                vf.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log(f"compiled: {pdf}" if pdf else "compile skipped/failed (no final/paper.tex or latexmk issue)")
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
