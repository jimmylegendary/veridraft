#!/usr/bin/env python3
"""Reference-figure extraction op — the paperorchestra-layer entry point (sibling of run.py).

Chains the deterministic figure-triple extractor with the VLM description pass over one PDF or a
whole directory of source PDFs, producing per-paper manifests plus a combined index. Used to build
the figure library for a study/survey paper (and to ground related-work), where each figure is
THIRD-PARTY evidence of a cited work — provenance is stamped on every record; nothing here is the
author's own result.

    python3 figure_extract_run.py --config backend.json --pdf-dir sources/ --out workspace/reffigs
    python3 figure_extract_run.py --config backend.json --pdf a.pdf --out workspace/reffigs

Deterministic contract (model-free): images + windowed reference contexts + captions are always
produced (PyMuPDF → poppler → text-only). The VLM description is the only model-dependent part and
degrades to caption-only without a `vision_model`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SKILLS = Path(__file__).resolve().parents[1] / "skills" / "reference-figure-extractor" / "scripts"
sys.path.insert(0, str(_SKILLS))
import describe_figures  # noqa: E402
import extract_figures   # noqa: E402


def log(m: str) -> None:
    print(f"[figure-extract] {m}", flush=True)


def _paper_id(pdf: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", pdf.stem)[:80] or "paper"


def run(cfg: dict, pdfs: list[Path], out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for pdf in pdfs:
        pid = _paper_id(pdf)
        dst = out_dir / pid
        log(f"{pdf.name} → {dst}")
        manifest = extract_figures.extract(str(pdf), str(dst),
                                           context_chars=int(cfg.get("figure_context_chars", 350)))
        mpath = dst / "figures_manifest.json"
        if manifest.get("figure_count"):
            describe_figures.describe(cfg, mpath)                 # VLM ceiling; degrades to caption
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        index.append({"paper_id": pid, "source_pdf": pdf.name, "manifest": str(mpath),
                      "figure_count": manifest.get("figure_count", 0), "engine": manifest.get("engine")})
        log(f"  {manifest.get('figure_count', 0)} figure(s) via {manifest.get('engine')}")
    (out_dir / "reffigs_index.json").write_text(
        json.dumps({"papers": index, "total_figures": sum(i["figure_count"] for i in index),
                    "note": "THIRD-PARTY figures from cited sources — never present as own results"},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    return {"papers": len(index), "figures": sum(i["figure_count"] for i in index)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="figure-extract-run")
    ap.add_argument("--config")
    ap.add_argument("--pdf", help="a single source PDF")
    ap.add_argument("--pdf-dir", help="a directory of source PDFs (non-recursive)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    cfg = {}
    if args.config and Path(args.config).exists():
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    pdfs: list[Path] = []
    if args.pdf:
        pdfs.append(Path(args.pdf))
    if args.pdf_dir:
        pdfs += sorted(Path(args.pdf_dir).glob("*.pdf"))
    if not pdfs:
        raise SystemExit("no PDFs — pass --pdf or --pdf-dir")
    r = run(cfg, pdfs, Path(args.out))
    log(f"done: {r['figures']} figure(s) across {r['papers']} paper(s) → {args.out}/reffigs_index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
