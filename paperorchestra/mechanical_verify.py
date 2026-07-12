#!/usr/bin/env python3
"""Mechanical verification — the explicit final-QA stage (deterministic floor + model ceiling).

The content REVIEW (peer-review sim / venue rubric) judges scientific quality; it does NOT check
typesetting or translation mechanics. This stage does — deterministically, so a defect is caught
regardless of the connected model:

  1. LAYOUT      — every Overfull \\hbox over the visible threshold, with its pt overflow AND source
                   line location + total count. FAIL-LOUD: it is reported and blocks a "clean"
                   verdict even when the bounded self-fix cannot fix them all (so a 300-page draft
                   never silently ships with text spilling into the right margin).
  2. TEXT        — mechanical prose defects visible in the RENDERED text (pdftotext): doubled words,
                   doubled/space-before punctuation, and a control sequence that LEAKED into the
                   output (a stray "\\command" the reader can see).
  3. TRANSLATION — for a translated version vs its original: technical terms that must stay ENGLISH
                   but went missing from the translation (possibly Koreanized), and — a strong
                   signal — Hangul characters sitting INSIDE code/math where a token was translated.
  4. PROOFREAD   — (ceiling, optional, bounded) a model pass for typos + translation-nuance
                   appropriateness + wrongly-translated technical terms. ADVISORY: it never edits
                   content (meaning is never silently changed); it reports for the human to act on.

A companion GLOBAL LAYOUT REMEDY (microtype + URL hyphenation + \\emergencystretch) fixes many
overfull-from-long-token boxes at ONCE — the right primitive for large documents, where per-line
edits do not scale.

    python3 mechanical_verify.py --final <ws>/final [--tex paper.tex] [--original <orig>.tex]
                                 [--config backend.json] [--proofread]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

_OVERFULL_MIN_PT = 5.0            # < this is invisible; > this spills visibly into the margin
_LEGIT_DOUBLES = {"that", "had"}      # "had had", "that that" can be legitimate; "the the" is a typo
# only a KNOWN LaTeX command name leaking into the rendered text is a defect — a bare "\word" could
# be a Windows path etc.; this set keeps the leaked-latex check from false-positiving on those.
_LEAK_CMDS = {"textbf", "textit", "texttt", "textsc", "textsf", "textrm", "emph", "cite", "citep",
              "citet", "citeauthor", "ref", "eqref", "autoref", "cref", "label", "section",
              "subsection", "subsubsection", "paragraph", "begin", "end", "item", "footnote",
              "caption", "includegraphics", "mathrm", "mathbf", "mathcal", "hline", "midrule",
              "toprule", "bottomrule", "centering", "textwidth", "linewidth", "url", "textsuperscript"}
_HEADING_STOP = {"ABSTRACT", "INTRODUCTION", "CONCLUSION", "CONCLUSIONS", "REFERENCES", "RELATED",
                 "WORK", "BACKGROUND", "METHOD", "METHODS", "METHODOLOGY", "RESULTS", "DISCUSSION",
                 "EVALUATION", "EXPERIMENTS", "APPENDIX", "ACKNOWLEDGMENTS", "ACKNOWLEDGEMENTS",
                 "THE", "AND", "FIG", "REF", "EQ", "ET", "AL", "VS", "VIA", "FOR", "WITH", "TABLE"}


def _has_cjk(s: str) -> bool:
    """Any non-Latin CJK script (Hangul, CJK ideographs incl. ext-A, and Japanese kana) — so the
    'technical token stayed English' check works for Korean AND Japanese/Chinese translation targets."""
    return any(0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F   # Hangul
               or 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF                          # CJK ideographs
               or 0x3040 <= o <= 0x30FF                                                   # kana
               for o in map(ord, s))


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


# ---- 1. LAYOUT: overfull map from the LaTeX log ------------------------------------------------

def parse_overfull(log_text: str, min_pt: float = _OVERFULL_MIN_PT) -> list[dict]:
    """Every Overfull \\hbox over min_pt, with pt + source line location. Sorted worst-first."""
    out = []
    pat = re.compile(
        r"Overfull \\hbox \(([\d.]+)pt too wide\)"
        r"(?:[^\n]*?(?:at lines (\d+)--(\d+)|detected at line (\d+)|at line (\d+)))?")
    for m in pat.finditer(log_text):
        pt = float(m.group(1))
        if pt < min_pt:
            continue
        a, b, c, d = m.group(2), m.group(3), m.group(4), m.group(5)
        loc = f"lines {a}--{b}" if a and b else (f"line {c or d}" if (c or d) else "unknown location")
        out.append({"pt": round(pt, 1), "loc": loc})
    return sorted(out, key=lambda x: -x["pt"])


def missing_figures(log_text: str) -> list[str]:
    return sorted({m.group(1) for m in re.finditer(
        r"File [`']?([^'\s]+\.(?:pdf|png|jpg|jpeg|eps))'? not found", log_text, re.I)})


# ---- 2. TEXT MECHANICS on the RENDERED text (pdftotext output) ----------------------------------

def text_mechanics(rendered: str, cap: int = 60) -> list[dict]:
    """Defects a reader can SEE in the rendered text. MUST run on pdftotext output, not the .tex
    source (where every LaTeX command would look like a 'leak')."""
    issues: list[dict] = []
    for m in re.finditer(r"\b([A-Za-z]{2,})\s+\1\b", rendered, re.IGNORECASE):   # incl. "The the"
        if m.group(1).lower() not in _LEGIT_DOUBLES:
            issues.append({"type": "doubled-word", "detail": m.group(0)})
    for m in re.finditer(r"[A-Za-z] +([,.;:!?])", rendered):        # space before punctuation
        issues.append({"type": "space-before-punct", "detail": repr(m.group(0))})
    # doubled comma/semicolon or 3+ colons; a bare "::" (C++ scope) is NOT a defect
    for m in re.finditer(r"[,;]{2,}|:{3,}|(?<![.\d])\.\.(?!\.)", rendered):
        issues.append({"type": "doubled-punct", "detail": repr(m.group(0))})
    for m in re.finditer(r"\\([A-Za-z]{2,})\b", rendered):         # a KNOWN control sequence leaked
        if m.group(1) in _LEAK_CMDS:
            issues.append({"type": "leaked-latex", "detail": m.group(0)})
    # dedup by (type, detail); cap
    seen, out = set(), []
    for i in issues:
        k = (i["type"], i["detail"])
        if k not in seen:
            seen.add(k); out.append(i)
    return out[:cap]


# ---- 3. TRANSLATION: technical-term preservation ------------------------------------------------

_GLOSSARY = {"KV cache", "attention", "transformer", "tokenizer", "embedding", "latency",
             "throughput", "batch", "kernel", "tensor", "gradient", "checkpoint", "quantization",
             "inference", "scheduler", "accelerator", "pipeline", "prefill", "decode", "softmax"}


def technical_terms(tex: str) -> set[str]:
    """Terms that should stay ENGLISH: acronyms, \\texttt/\\verb tokens, and known-glossary hits.
    Section-heading words (ABSTRACT, CONCLUSION …) and Roman numerals are NOT technical terms."""
    acronyms = {t for t in re.findall(r"\b[A-Z]{2,}[a-z]?\b", tex)
                if t.upper() not in _HEADING_STOP and not re.fullmatch(r"[IVXLCDM]+", t)}
    terms = set(acronyms)
    terms |= {t.strip() for t in re.findall(r"\\texttt\{([^}]{1,40})\}", tex) if t.strip()}
    terms |= {t.strip() for t in re.findall(r"\\verb\|([^|]{1,40})\|", tex) if t.strip()}
    terms |= {t for t in _GLOSSARY if re.search(rf"\b{re.escape(t)}\b", tex, re.I)}
    return {t for t in terms if len(t) >= 2}


def _prose_view(tex: str) -> str:
    """Strip verbatim-PRESERVED LaTeX (keys/labels/cites/urls/bibliography/comments) so a term that
    survives only inside a preserved \\cite/\\label key does not mask its loss from the actual prose."""
    t = re.sub(r"(?m)%.*$", " ", tex)
    t = re.sub(r"\\(?:label|ref|eqref|autoref|cref|Cref|cite[a-z]*|url|nolinkurl|includegraphics|"
               r"input|include|bibliography|bibliographystyle)\s*(\[[^\]]*\])?\{[^}]*\}", " ", t)
    t = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", " ", t, flags=re.S)
    return t


def translation_term_check(orig_tex: str, trans_tex: str, cap: int = 40) -> list[dict]:
    """Technical terms that may have been wrongly translated out of a translated version."""
    findings: list[dict] = []
    prose = _prose_view(trans_tex)                                   # search the PROSE, not keys/bib
    for t in sorted(technical_terms(orig_tex)):
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(t)}(?![A-Za-z0-9])", prose, re.I):  # case-insensitive
            findings.append({"type": "term-missing-in-translation", "term": t,
                             "note": "present (English) in the original but ABSENT from the translated "
                                     "prose — verify it was not translated into the target language"})
    for env, pat in (("texttt", r"\\texttt\{([^}]*)\}"), ("verb", r"\\verb\|([^|]*)\|"),
                     ("inline-math", r"(?<!\$)\$([^$]{1,80})\$(?!\$)")):
        for m in re.finditer(pat, trans_tex):
            if _has_cjk(m.group(1)):
                findings.append({"type": "cjk-in-code", "context": m.group(0)[:70],
                                 "note": f"non-Latin (CJK) characters inside {env} — a technical token "
                                         "was translated; it must stay English"})
    return findings[:cap]


# ---- patent-specific: reference-numeral consistency (§112) --------------------------------------

# words that precede a number WITHOUT it being an element reference numeral
_NUM_STOP = {"fig", "figure", "figs", "figures", "eq", "equation", "section", "table", "claim",
             "claims", "step", "page", "line", "chapter", "part", "no", "item", "example", "embodiment",
             "in", "of", "the", "to", "at", "by", "on", "and", "or", "a", "an"}


def _numeral_prose(tex: str) -> str:
    """Prose for numeral analysis: LaTeX ties → space, KEEP the text of formatting commands
    (\\textbf{scheduler} → scheduler), strip other commands + comments."""
    t = re.sub(r"(?m)%.*$", " ", tex)
    t = t.replace("~", " ")                                    # LaTeX tie / nbsp binds elem to numeral
    t = re.sub(r"\\(?:textbf|textit|texttt|textsc|textsf|textrm|emph|mathrm|mathbf|underline)"
               r"\{([^}]*)\}", r"\1", t)                       # keep the element name inside \textbf{}
    return re.sub(r"\\[A-Za-z@]+\*?(?:\[[^\]]*\])?(?:\{[^}]*\})?", " ", t)


def _norm_elem(w: str) -> str:
    """Canonical element key so surface variants of ONE element collapse (scheduler/schedulers,
    sub-system/subsystem) and don't read as two elements."""
    n = w.lower().replace("-", "")
    return re.sub(r"(?:es|s)$", "", n) if len(n) > 4 else n    # strip a trailing plural (guard short)


def reference_numeral_check(tex: str, cap: int = 30) -> list[dict]:
    """Patent §112 mechanics on the spec prose. Detects ONLY the unambiguous PARENTHETICAL convention
    ("processor (110)") — this is the reliable form, and it avoids colliding with plain-prose
    quantities ("512 activations"), which have no parentheses. Flags the SAME reference numeral used
    for MULTIPLE distinct elements (a definiteness defect). If the draft uses no parenthetical
    numerals the check is inert (safe). (Claim STRUCTURE is checked separately in core/lints.py.)"""
    prose = _numeral_prose(tex)
    num_to_elems: dict[str, set] = {}
    for m in re.finditer(r"\b([A-Za-z][A-Za-z\-]{2,20})\s*\(\s*(\d{2,4})\s*\)", prose):
        raw, num = m.group(1), m.group(2)
        if _norm_elem(raw) in _NUM_STOP or raw.lower() in _NUM_STOP:
            continue
        num_to_elems.setdefault(num, {})[_norm_elem(raw)] = raw    # normalized key → a raw label
    findings: list[dict] = []
    for num, elems in sorted(num_to_elems.items()):
        if len(elems) >= 2:
            findings.append({"type": "numeral-multiple-elements", "numeral": num,
                             "note": f"reference numeral ({num}) labels multiple elements "
                                     f"({', '.join(sorted(elems.values()))}) — one numeral must denote "
                                     "ONE element (§112 definiteness)"})
    return findings[:cap]


# ---- GLOBAL LAYOUT REMEDY (scales to large docs) ------------------------------------------------

def _kpsewhich(sty: str) -> bool:
    if not shutil.which("kpsewhich"):
        return True                # can't check → assume present, latexmk will tell us
    try:
        return subprocess.run(["kpsewhich", sty], capture_output=True, timeout=15).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True


def global_layout_remedy_tex(tex: str) -> tuple[str, list[str]]:
    """Inject preamble-level overfull remedies that fix MANY boxes at once. Returns (new_tex, applied).
    No-op if already present / nothing applicable. Never touches content, only the preamble."""
    def has_pkg(name):
        # understand comma-separated \usepackage{a,b,c} lists, not just a sole argument
        for m in re.finditer(r"\\usepackage(?:\[[^\]]*\])?\{([^}]*)\}", tex):
            if name in [p.strip() for p in m.group(1).split(",")]:
                return True
        return False

    inject, applied = [], []
    if not has_pkg("microtype") and _kpsewhich("microtype.sty"):
        inject.append(r"\usepackage{microtype}"); applied.append("microtype")
    # URL hyphenation — only if no url/hyperref already manages it (avoid load-order conflicts)
    if not (has_pkg("url") or has_pkg("hyperref") or has_pkg("xurl")):
        inject.append(r"\usepackage[hyphens]{url}"); applied.append("url-hyphens")
    if r"\emergencystretch" not in tex:
        inject.append(r"\setlength{\emergencystretch}{3em}"); applied.append("emergencystretch")
    if not inject:
        return tex, []
    m = re.search(r"(\\documentclass(\[[^\]]*\])?\{[^}]+\})", tex)
    if not m:
        return tex, []
    block = "\n% --- mechanical-verify: global overfull remedy (preamble) ---\n" + "\n".join(inject)
    return tex[:m.end()] + block + tex[m.end():], applied


# ---- 4. model proofread (ceiling, optional, bounded) --------------------------------------------

def _model_proofread(cfg: dict, rendered: str, kind: str, is_translation: bool,
                     max_chunks: int = 4, chunk_chars: int = 6000) -> dict:
    import providers
    chunks = [rendered[i:i + chunk_chars] for i in range(0, len(rendered), chunk_chars)]
    ran = chunks[:max_chunks]
    extra = ("Also flag any TECHNICAL TERM that was translated into Korean but should stay English, "
             "and any awkward/incorrect paraphrase NUANCE. " if is_translation else "")
    findings: list[str] = []
    for idx, ch in enumerate(ran):
        prompt = (f"Proofread this rendered paper text for TYPOS, doubled words, agreement errors, and "
                  f"broken punctuation. {extra}Return a JSON array of short strings (each a concrete "
                  f"defect with the offending phrase); return [] if clean. Do NOT rewrite the text.\n\n"
                  f"---\n{ch}\n---\nReturn ONLY the JSON array.")
        try:
            out = providers.dispatch(cfg, prompt, ".", timeout=int(cfg.get("step_timeout_seconds", 600)))
        except Exception:   # noqa: BLE001
            continue
        i, j = out.find("["), out.rfind("]")
        if i != -1 and j > i:
            try:
                arr = json.loads(out[i:j + 1])
                findings += [f"[chunk {idx+1}] {x}" for x in arr if isinstance(x, str)][:20]
            except (ValueError, TypeError):
                pass
    return {"findings": findings, "coverage": f"{len(ran)}/{len(chunks)} chunk(s) proofread",
            "complete": len(ran) >= len(chunks)}


# ---- orchestration -------------------------------------------------------------------------------

def _pdftotext(pdf: Path) -> str:
    if not pdf.exists() or not shutil.which("pdftotext"):
        return ""
    try:
        return subprocess.run(["pdftotext", "-layout", str(pdf), "-"],
                              capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def verify(final_dir: str, tex_name: str = "paper.tex", original_tex: str | None = None,
           cfg: dict | None = None, proofread: bool = False, kind: str = "paper") -> dict:
    final = Path(final_dir)
    stem = tex_name[:-4] if tex_name.endswith(".tex") else tex_name
    log_txt = _read(final / f"{stem}.log")
    tex = _read(final / tex_name)
    rendered = _pdftotext(final / f"{stem}.pdf")

    overfull = parse_overfull(log_txt)
    mech = text_mechanics(rendered) if rendered else []
    build_ok = (final / f"{stem}.pdf").exists()      # a compile that never produced a PDF is NOT clean
    report = {
        "kind": kind, "tex": tex_name,
        "build_ok": build_ok,
        "overfull": overfull[:80], "overfull_count": len(overfull),
        "worst_overfull_pt": overfull[0]["pt"] if overfull else 0.0,
        "missing_figures": missing_figures(log_txt),
        "text_mechanics": mech,
        "rendered_text_available": bool(rendered),
    }
    if original_tex:
        report["translation"] = translation_term_check(_read(Path(original_tex)), tex)
    numeral_defects = []
    if kind == "patent":
        report["reference_numerals"] = reference_numeral_check(tex)
        # only a numeral labelling MULTIPLE elements is a hard defect; introduced-once is advisory
        numeral_defects = [f for f in report["reference_numerals"]
                           if f["type"] == "numeral-multiple-elements"]
    # FAIL-LOUD: a missing build OR any layout/mechanics/translation defect blocks "clean" — a
    # failed/absent compile (no PDF), a wrong --final path, or a wrong --tex stem never reads clean.
    report["clean"] = build_ok and not (overfull or mech or report.get("missing_figures")
                                        or report.get("translation") or numeral_defects)
    if proofread and cfg:
        report["proofread"] = _model_proofread(cfg, rendered, kind, bool(original_tex))
    (final / f"{stem}.verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                                     encoding="utf-8")
    return report


def _log(m: str) -> None:
    print(f"[mechanical-verify] {m}", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mechanical-verify")
    ap.add_argument("--final", required=True, help="the workspace final/ dir")
    ap.add_argument("--tex", default="paper.tex")
    ap.add_argument("--original", default=None, help="original .tex to check a translation against")
    ap.add_argument("--config", default=None)
    ap.add_argument("--proofread", action="store_true")
    ap.add_argument("--kind", default="paper")
    args = ap.parse_args(argv)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    cfg = json.loads(Path(args.config).read_text()) if (args.config and Path(args.config).exists()) else None
    r = verify(args.final, tex_name=args.tex, original_tex=args.original, cfg=cfg,
               proofread=args.proofread, kind=args.kind)
    _log(f"clean={r['clean']}  overfull={r['overfull_count']} (worst {r['worst_overfull_pt']}pt)  "
         f"mechanics={len(r['text_mechanics'])}  missing_figs={len(r['missing_figures'])}"
         + (f"  translation={len(r['translation'])}" if 'translation' in r else ""))
    for o in r["overfull"][:10]:
        _log(f"  ▸ overfull {o['pt']}pt at {o['loc']}")
    for t in r.get("translation", [])[:10]:
        _log(f"  ▸ {t['type']}: {t.get('term') or t.get('context')}")
    return 0 if r["clean"] else 2      # non-zero = defects to surface (never silently clean)


if __name__ == "__main__":
    raise SystemExit(main())
