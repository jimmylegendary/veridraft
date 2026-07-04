#!/usr/bin/env python3
"""Portable multilingual translate/localize runner (F5) — backend-agnostic, a sibling of run.py.

Localizes a compiled manuscript (paper OR patent) into ANY target language over the same backends,
so translation is a first-class harness action, not a hand-script. The translation is IDIOMATIC
(meaning rendered naturally in the target language's academic register — not a word-for-word calque)
while established technical terms / acronyms stay in English, and every LaTeX command, key, number,
equation, and figure is preserved verbatim.

    python3 translate_run.py --config backend.json --workspace <ws> --lang ko [--source paper]

Reads   <ws>/final/<source>.tex   →   writes+compiles   <ws>/final/<source>-<code>.tex(.pdf)
CJK targets compile with lualatex + a CJK package (kotex/xeCJK) automatically; other non-Latin
scripts prefer lualatex when available.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers
import run as po_run   # reuse _cjk_prep / _pdf_text / _stage_figures / _needs_bib / _has_link_annots


def log(m: str) -> None:
    print(f"[translate-run] {m}", flush=True)


# code → (English name, native name, script). script: "cjk" | "latin" | "other".
_LANGS = {
    "ko": ("Korean", "한국어", "cjk"), "ja": ("Japanese", "日本語", "cjk"),
    "zh": ("Chinese (Simplified)", "简体中文", "cjk"), "zh-tw": ("Chinese (Traditional)", "繁體中文", "cjk"),
    "de": ("German", "Deutsch", "latin"), "fr": ("French", "Français", "latin"),
    "es": ("Spanish", "Español", "latin"), "pt": ("Portuguese", "Português", "latin"),
    "it": ("Italian", "Italiano", "latin"), "nl": ("Dutch", "Nederlands", "latin"),
    "pl": ("Polish", "Polski", "latin"), "tr": ("Turkish", "Türkçe", "latin"),
    "vi": ("Vietnamese", "Tiếng Việt", "latin"), "id": ("Indonesian", "Bahasa Indonesia", "latin"),
    "ru": ("Russian", "Русский", "other"), "uk": ("Ukrainian", "Українська", "other"),
    "el": ("Greek", "Ελληνικά", "other"), "ar": ("Arabic", "العربية", "other"),
    "he": ("Hebrew", "עברית", "other"), "hi": ("Hindi", "हिन्दी", "other"),
    "th": ("Thai", "ไทย", "other"),
}


def _resolve_lang(arg: str) -> tuple[str, tuple[str, str, str]]:
    """Accept a code ('ko'), an English name ('Korean'), or the native name ('한국어')."""
    a = arg.strip()
    low = a.lower()
    if low in _LANGS:
        return low, _LANGS[low]
    for code, ent in _LANGS.items():
        if low == ent[0].lower() or a == ent[1]:
            return code, ent
    # unknown → carry the string through to the model; compile with lualatex (safest for any script)
    return low.replace(" ", "-"), (a, a, "other")


def _translate_prompt(ws: Path, src_stem: str, out_stem: str, name: str, native: str, script: str) -> str:
    cjk_note = ("The target uses a non-Latin script — write the UTF-8 characters directly; the document "
                "is compiled with lualatex + a CJK/Unicode font, so do NOT use LaTeX escapes for them. "
                if script != "latin" else "")
    return (
        f"Translate the LaTeX manuscript {ws}/final/{src_stem}.tex into {name} ({native}). Write the "
        f"result to {ws}/final/{out_stem}.tex, then stop.\n\nRULES:\n"
        f"- IDIOMATIC translation: render the meaning naturally in {name}'s ACADEMIC register and "
        "conventions — a fluent paraphrase, NEVER a word-for-word literal calque.\n"
        "- KEEP established technical terms and acronyms in ENGLISH (e.g. attention, throughput, latency, "
        "KV cache, transformer, tensor, GPU, FLOP, MoE, tiling, scheduler). Do not force-translate a term "
        f"that the {name} research community normally writes in English; translate the prose around it.\n"
        "- PRESERVE ALL LaTeX EXACTLY and untranslated: every command and environment "
        "(\\section, \\begin{...}\\end{...}, \\usepackage, tables, floats), all math ($...$, equations), "
        "\\label{...} keys, \\ref/\\eqref/\\cite keys, \\includegraphics paths, the bibliography, and every "
        "NUMBER, unit, and symbol. Translate ONLY human-readable prose: section bodies, the abstract, "
        "titles, and figure/table CAPTION text. Never translate a key, a citation, or code.\n"
        "- Do NOT add, drop, reorder, or summarize content; keep the exact structure and float placement.\n"
        f"{cjk_note}"
        f"When done, confirm {ws}/final/{out_stem}.tex exists.")


def _refuses_latexmkrc(final: Path) -> bool:
    if any((final / n).exists() for n in ("latexmkrc", ".latexmkrc")):
        log("refusing to compile: a latexmkrc is present (code-exec risk)"); return True
    return False


def _compile(final: Path, stem: str, prefer_lualatex: bool) -> Path | None:
    tex = final / f"{stem}.tex"
    if _refuses_latexmkrc(final):
        return None
    engine_flag = po_run._cjk_prep(tex)   # CJK → -lualatex + kotex/xeCJK injection
    if engine_flag == "-pdf" and prefer_lualatex and shutil.which("lualatex"):
        engine_flag = "-lualatex"
    try:
        subprocess.run(["latexmk", engine_flag, "-no-shell-escape", "-interaction=nonstopmode", f"{stem}.tex"],
                       cwd=str(final), capture_output=True, text=True, timeout=240)
    except (OSError, subprocess.SubprocessError):
        log("latexmk unavailable/failed — the translated .tex is left for review"); return None
    pdf = final / f"{stem}.pdf"
    if not pdf.exists():
        return None
    src = tex.read_text(encoding="utf-8", errors="replace")
    bbl = final / f"{stem}.bbl"
    if po_run._needs_bib(src) and (not bbl.exists() or bbl.stat().st_size == 0):
        engine = "lualatex" if engine_flag == "-lualatex" else "pdflatex"
        if shutil.which(engine) and shutil.which("bibtex"):
            log("running an explicit bibtex recovery pass for the translation")
            step = [engine, "-no-shell-escape", "-interaction=nonstopmode", f"{stem}.tex"]
            for cmd in (step, ["bibtex", stem], step, step):
                try:
                    subprocess.run(cmd, cwd=str(final), capture_output=True, text=True, timeout=240)
                except (OSError, subprocess.SubprocessError):
                    log(f"bibtex recovery step '{cmd[0]}' failed"); break
    return pdf


def _verify(final: Path, stem: str) -> list[str]:
    import re
    issues: list[str] = []
    txt = po_run._pdf_text(final / f"{stem}.pdf")
    if "[?]" in txt:
        issues.append("unresolved \\cite rendered as [?] in the translated PDF")
    if re.search(r"(?<!\d)\?\?(?!\d)", txt):
        issues.append("unresolved \\ref rendered as ?? in the translated PDF")
    return issues


def translate(cfg: dict, ws: Path, lang: str, source: str = "paper", force: bool = False) -> int:
    final = ws / "final"
    src_stem = source
    if not (final / f"{src_stem}.tex").exists():
        alt = "patent" if source == "paper" else "paper"
        if (final / f"{alt}.tex").exists():
            src_stem = alt
        else:
            raise SystemExit(f"no {source}.tex (or {alt}.tex) in {final} — draft it first")
    code, (name, native, script) = _resolve_lang(lang)
    out_stem = f"{src_stem}-{code}"
    out_tex = final / f"{out_stem}.tex"
    po_run._stage_figures(ws, final)   # ensure figures are staged for the translated compile too

    if out_tex.exists() and out_tex.stat().st_size > 200 and not force:
        log(f"existing {out_stem}.tex found — idempotent resume (skipping the LLM; --force to re-translate)")
    else:
        log(f"translating {src_stem}.tex → {name} ({native}) [{code}] via backend={cfg.get('backend')}")
        providers.dispatch(cfg, _translate_prompt(ws, src_stem, out_stem, name, native, script),
                           str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        if not out_tex.exists():
            raise SystemExit(f"backend did not produce {out_stem}.tex (too weak / wrong workspace)")

    pdf = _compile(final, out_stem, prefer_lualatex=(script != "latin"))
    issues = _verify(final, out_stem) if pdf else ["translated PDF did not compile"]
    (final / f"{out_stem}.verify.json").write_text(json.dumps(issues, indent=2), encoding="utf-8")
    if pdf:
        log(f"translated PDF: {pdf}" + (f"  — {len(issues)} render issue(s), see {out_stem}.verify.json"
                                        if issues else "  — self-check clean"))
    for i in issues:
        log(f"  ✗ {i}")
    return 0 if pdf and not issues else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="translate-run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--lang", required=True, help="target language: code (ko), name (Korean), or native (한국어)")
    ap.add_argument("--source", default="paper", choices=["paper", "patent"])
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"could not read/parse --config {args.config}: {e}")
    ok, detail = providers.probe(cfg)
    log(f"backend probe: {'OK' if ok else 'FAIL'} — {detail}")
    if args.probe:
        return 0 if ok else 1
    if not ok:
        raise SystemExit(detail)
    return translate(cfg, Path(os.path.expanduser(args.workspace)).resolve(),
                     args.lang, args.source, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
