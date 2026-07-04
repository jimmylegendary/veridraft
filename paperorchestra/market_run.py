#!/usr/bin/env python3
"""Marketing revision runner (P1) — produce a discoverability-optimized SECOND version of the paper.

The marketing flow does NOT touch the canonical manuscript; it emits a parallel
`final/<source>-marketing.tex(.pdf)` optimized for SEO (search/Google Scholar discoverability) and
GEO (LLM answer-engine citability), plus a deterministic metadata kit. The revision is model-driven,
but a deterministic honesty gate (marketing_lints) runs over it: any statistic or superlative the
marketing version ADDS beyond the original is flagged — blocking on fabricated numbers. Two versions
coexist: the honest manuscript and its evidence-bounded marketing revision.

    python3 market_run.py --config backend.json --workspace <ws> [--source paper] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import providers
import run as po_run
import translate_run
import marketing_lints


def log(m: str) -> None:
    print(f"[market-run] {m}", flush=True)


_PROMPT = """Revise the paper at {ws}/final/{src}.tex into a MARKETING-OPTIMIZED SECOND VERSION at
{ws}/final/{out}.tex (leave the original untouched), for search discoverability (SEO) and LLM
answer-engine citability (GEO). Optimize:
- TITLE: accurate but compelling and search-friendly — front-load the key concept/finding and a term
  a reader would actually search.
- ABSTRACT: put the single most important finding in the FIRST 1-2 sentences; front-load the key
  search terms; keep terminology consistent; add one plain-language "why it matters" sentence.
- Add a \\keywords{{...}} line with 5-8 precise terms a reader would search.
- Present the contributions / key results as a crisp, EXTRACTABLE list an LLM can quote and cite, each
  stated with its exact number FROM THE PAPER.

HONESTY — hard, non-negotiable (a deterministic gate checks this and will FAIL the version):
- Do NOT introduce any statistic or number that is not already in the paper.
- Do NOT add a superlative/absolute the paper did not earn ("first", "only", "state-of-the-art",
  "solves", "proves", "revolutionary", "breakthrough"). Reframe for clarity and appeal, NEVER inflate.
- Do NOT add a claim the paper does not make, and do NOT drop the paper's limitations/caveats.
- Preserve every \\label/\\cite key, all math, figures, tables, and the bibliography.
When done, confirm {ws}/final/{out}.tex exists and stop."""


def _extract(tex: str) -> tuple[str, str, list[str]]:
    def grp(pat):
        m = re.search(pat, tex, re.I | re.S)
        return m.group(1).strip() if m else ""
    title = re.sub(r"\s+", " ", grp(r"\\title\s*\{(.+?)\}"))
    abs = grp(r"\\begin\{abstract\}(.+?)\\end\{abstract\}")
    abs = re.sub(r"\s+", " ", re.sub(r"\\[a-zA-Z@]+\s*(?:\[[^\]]*\])?|[{}]", " ", abs)).strip()
    kw = grp(r"\\keywords\s*\{(.+?)\}")
    keywords = [k.strip() for k in re.split(r"[,;]", re.sub(r"\\[a-zA-Z@]+|[{}]", " ", kw)) if k.strip()]
    return title, abs, keywords


def market(cfg: dict, ws: Path, source: str = "paper", force: bool = False) -> int:
    final = ws / "final"
    src_stem = source
    if not (final / f"{src_stem}.tex").exists():
        raise SystemExit(f"no {source}.tex in {final} — draft it first")
    out_stem = f"{src_stem}-marketing"
    out_tex = final / f"{out_stem}.tex"
    log_text = po_run_read(ws / "inputs" / "experimental_log.md")   # source of record for numbers
    po_run._stage_figures(ws, final)

    if out_tex.exists() and out_tex.stat().st_size > 200 and not force:
        log(f"existing {out_stem}.tex found — idempotent resume (skipping the LLM; --force to redo)")
    else:
        log(f"revising {src_stem}.tex → marketing version via backend={cfg.get('backend')}")
        providers.dispatch(cfg, _PROMPT.format(ws=str(ws), src=src_stem, out=out_stem),
                           str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        if not out_tex.exists():
            raise SystemExit(f"backend did not produce {out_stem}.tex")

    original = (final / f"{src_stem}.tex").read_text(encoding="utf-8", errors="replace")
    marketing = out_tex.read_text(encoding="utf-8", errors="replace")
    gate = marketing_lints.lint_marketing(marketing, original, log_text)   # DETERMINISTIC honesty gate
    title, abstract, keywords = _extract(marketing)
    kit = marketing_lints.metadata_kit(title, abstract, keywords)
    (final / f"{out_stem}.meta.json").write_text(json.dumps(kit, indent=2, ensure_ascii=False), encoding="utf-8")
    (final / f"{out_stem}.gate.json").write_text(json.dumps(gate, indent=2, ensure_ascii=False), encoding="utf-8")

    pdf = translate_run._compile(final, out_stem, prefer_lualatex=False)
    render = translate_run._verify(final, out_stem) if pdf else ["marketing version did not compile"]

    for b in gate["blocking"]:
        log(f"  ✗ BLOCK (over-claim): {b}")
    for a in gate["advisory"]:
        log(f"  ⚠ advisory: {a}")
    for r in render:
        log(f"  ✗ render: {r}")
    ok = pdf is not None and not gate["blocking"] and not render
    log(f"marketing version: {pdf} — " + ("evidence-gated OK ✅" if ok else
        f"{len(gate['blocking'])} over-claim block(s), {len(render)} render issue(s) — NOT ready"))
    log(f"metadata kit → {out_stem}.meta.json ({len(keywords)} keywords)")
    return 0 if ok else 1


def po_run_read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="market-run")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
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
    return market(cfg, Path(os.path.expanduser(args.workspace)).resolve(), args.source, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
