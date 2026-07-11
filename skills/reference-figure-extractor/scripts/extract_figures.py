#!/usr/bin/env python3
"""Deterministic extraction of (figure image · in-text reference context · caption) triples
from a source-paper PDF — for study/survey writing and related-work grounding.

For each figure in a referenced paper we emit a THREE-part record:
  1. figure   — the rendered image (raster xref if present, else the vector/page region clip)
  2. reference— every place the body text cites the figure ("Figure N shows …"), each captured
                WITH a surrounding sentence window so the context is preserved (not just the token)
  3. caption  — the figure's own caption text (the deterministic seed for the description); a
                separate VLM pass (describe_figures.py) fills a detailed `description`.

Provenance / honesty: an extracted figure is THIRD-PARTY evidence of what a *cited* work showed.
Every record carries `source_pdf` + `page`; it must never be presented as the author's own result.
This is the model-independent floor: pure-Python text/regex + PyMuPDF rendering when available,
degrading to a `pdftotext` text-only manifest (no images) when PyMuPDF is absent.

    python3 extract_figures.py --pdf paper.pdf --out <dir> [--context-chars 350] [--zoom 2.0]

Output: <dir>/figures/<figure_id>.png  +  <dir>/figures_manifest.json
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

# "Figure 3", "Fig. 3", "Figs 3-4", "Figures 2 and 3", "Figure 3a" — capture the prefix (group 1,
# for plurality) and the whole trailing reference LIST (group 2: number, optionally followed by
# ranges/conjunctions), so a figure cited only via a range/conjunction is not dropped.
_FIG_TOKEN = re.compile(
    r"\b(fig(?:ure)?s?)\.?\s*(\d+[a-z]?(?:\s*(?:[-–—]|,|and|&|to|through)\s*\d+[a-z]?)*)", re.I)
_RANGE = re.compile(r"(\d+)\s*(?:[-–—]|to|through)\s*(\d+)", re.I)


def _expand_figrefs(prefix: str, group: str) -> set[str]:
    """Normalized figure numbers a mention refers to. Ranges ('2-4') always expand; conjunctions
    ('2 and 3') expand only for a PLURAL prefix ('Figures'/'Figs'), so a singular 'Figure 2 and 3
    steps' does not spuriously pull in figure 3."""
    out: set[str] = set()
    for a, b in _RANGE.findall(group):
        ai, bi = int(a), int(b)
        if 0 <= bi - ai <= 30:
            out.update(str(n) for n in range(ai, bi + 1))
    nums = re.findall(r"\d+[a-z]?", group)
    if nums:
        out.add(_norm_fig(nums[0]))                       # the head number always
        if prefix.rstrip(".").lower().endswith("s"):      # plural → include the conjoined numbers
            out.update(_norm_fig(n) for n in nums[1:])
    return out


def _caption_extent(text: str, body_start: int, cap: int = 500) -> int:
    """End offset of a caption: through its first sentence (where cross-figure mentions live), but
    never past the next caption head — so 'Figure 2: compared to Figure 1' excludes that 'Figure 1'
    from figure 1's body references without swallowing real body prose after the caption."""
    seg = text[body_start:body_start + cap]
    sm = re.search(r"[.!?][)\]]?\s", seg)
    end = body_start + (sm.end() if sm else len(seg))
    nxt = _CAPTION_HEAD.search(text, body_start)
    return min(end, nxt.start()) if (nxt and nxt.start() > body_start) else end
# A caption line/BLOCK starts (at a line start, possibly indented for a centered caption) with a
# figure number then a REQUIRED separator (colon/period/paren/dash) then descriptive text. The
# separator is what distinguishes a real caption ("Figure 1: ...") from a body line that merely
# begins with a reference ("Figure 1 also motivates ...") — without it those get mis-tagged as
# captions and their in-text mention is wrongly dropped. re.M so finditer catches every line.
_CAPTION_HEAD = re.compile(r"^[ \t]*(?:fig(?:ure)?s?)\.?\s*(\d+[a-z]?)\s*[.:)\]–—-]\s*", re.I | re.M)
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[])")


def _norm_fig(n: str) -> str:
    """Normalize a figure token: strip a trailing subfigure letter so 'Figure 3a' ~ 'Fig. 3'."""
    return re.sub(r"[a-z]$", "", n.strip().lower())


def _source(pdf: Path, page: int, extractor: str) -> dict:
    """Provenance every extracted figure MUST carry — it is THIRD-PARTY content from a cited work."""
    return {
        "pdf": pdf.name,
        "path": str(pdf.resolve()),
        "page": page,                          # 1-indexed
        "extractor": extractor,
        "third_party": True,
        "attribution_required": True,
        "license": "unknown",                  # recorded, NOT asserted — reuse needs a permission check
        "usage_note": ("Third-party figure from a cited source; source+page recorded; NOT the "
                       "author's own result; reproducing it needs a license/permission check."),
    }


def is_caption(text: str) -> str | None:
    """Return the (normalized) figure number if `text` reads like a figure caption, else None.
    A caption must have a figure head AND some descriptive words after it (not a bare mention)."""
    m = _CAPTION_HEAD.match(text or "")
    if not m:
        return None
    rest = text[m.end():].strip()
    if len(rest.split()) < 2:          # "Figure 3" alone is a reference/label, not a caption
        return None
    return _norm_fig(m.group(1))


def sentence_window(text: str, start: int, end: int, context_chars: int = 350) -> str:
    """The sentence(s) covering [start,end) plus the neighbours, capped at ~context_chars each side.
    Preserves the surrounding context of an in-text figure reference without dragging the whole page."""
    text = text or ""
    # sentence boundaries
    bounds = [0] + [m.end() for m in _SENT_SPLIT.finditer(text)] + [len(text)]
    # find the sentence index containing `start`
    lo = hi = 0
    for i in range(len(bounds) - 1):
        if bounds[i] <= start < bounds[i + 1]:
            lo = hi = i
            break
    while hi + 1 < len(bounds) - 1 and bounds[hi + 1] < end:
        hi += 1
    s = max(0, bounds[lo - 1]) if lo > 0 else 0
    e = bounds[min(hi + 2, len(bounds) - 1)]        # include the FOLLOWING sentence too (neighbours)
    # character cap around the actual reference span
    s = max(s, start - context_chars)
    e = min(e, end + context_chars)
    return re.sub(r"\s+", " ", text[s:e]).strip()


def find_references(fig_no: str, page_texts: list[str], caption_spans: dict[int, list[tuple[int, int]]],
                    context_chars: int = 350) -> list[dict]:
    """Every in-text mention of figure `fig_no` across the document, each with a context window.
    Mentions that fall inside a caption span on the same page are excluded (those are the caption)."""
    refs: list[dict] = []
    want = _norm_fig(fig_no)
    for pi, txt in enumerate(page_texts):
        for m in _FIG_TOKEN.finditer(txt):
            if want not in _expand_figrefs(m.group(1), m.group(2)):   # handles ranges/conjunctions
                continue
            if any(a <= m.start() < b for a, b in caption_spans.get(pi, [])):
                continue
            refs.append({"page": pi + 1,
                         "context": sentence_window(txt, m.start(), m.end(), context_chars)})
    return refs


# ---- PyMuPDF-backed rendering (optional; degrades to text-only) ----------------------------------

def _fitz():
    try:
        import fitz  # PyMuPDF
        return fitz
    except ImportError:
        return None


def _blocks_and_text(page):
    """(text_blocks, full_page_text) — text_blocks are (x0,y0,x1,y1,text)."""
    blocks = []
    for b in page.get_text("blocks"):
        if len(b) >= 5 and isinstance(b[4], str) and b[4].strip():
            blocks.append((b[0], b[1], b[2], b[3], b[4]))
    return blocks, page.get_text("text")


def _figure_clip(fitz, page, caption_bbox):
    """Best-effort figure region ABOVE a caption: prefer a placed raster/vector rect that sits just
    above the caption and overlaps it horizontally; else a column-band clip up to the previous block."""
    cx0, cy0, cx1, cy1 = caption_bbox
    cand = []
    try:
        for info in page.get_image_info():          # placed raster images
            cand.append(fitz.Rect(info["bbox"]))
    except Exception:  # noqa: BLE001
        pass
    try:
        for d in page.get_drawings():               # vector art
            cand.append(fitz.Rect(d["rect"]))
    except Exception:  # noqa: BLE001
        pass

    def h_overlap(r):
        return min(cx1, r.x1) - max(cx0, r.x0) > 0.3 * (cx1 - cx0)

    above = [r for r in cand if r.y1 <= cy0 + 2 and (cy0 - r.y1) < 0.5 * page.rect.height and h_overlap(r)]
    if above:
        x0 = min(r.x0 for r in above); x1 = max(r.x1 for r in above)
        y0 = min(r.y0 for r in above); y1 = max(cy0, max(r.y1 for r in above))
        rect = fitz.Rect(x0, y0, x1, y1)
    else:
        # fallback: caption-width column band from the previous text block's bottom up to the caption
        prev_bottom = 0.0
        for (bx0, by0, bx1, by1, _t) in _blocks_and_text(page)[0]:
            if by1 <= cy0 - 2 and min(cx1, bx1) - max(cx0, bx0) > 0 and by1 > prev_bottom:
                prev_bottom = by1
        pad = 0.10 * (cx1 - cx0)
        rect = fitz.Rect(max(page.rect.x0, cx0 - pad), max(page.rect.y0, prev_bottom),
                         min(page.rect.x1, cx1 + pad), cy0)
    rect.normalize()
    rect = rect & page.rect
    return rect if (rect.width > 20 and rect.height > 20) else None


def extract(pdf_path: str, out_dir: str, context_chars: int = 350, zoom: float = 2.0) -> dict:
    """Extract every figure triple from `pdf_path` into `out_dir`. Returns the manifest dict."""
    pdf = Path(pdf_path)
    out = Path(out_dir)
    figs_dir = out / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)
    fitz = _fitz()

    if fitz is None:
        return _extract_text_only(pdf, out, figs_dir, context_chars)

    try:
        doc = fitz.open(pdf_path)
        page_texts: list[str] = []
        per_page_blocks = []
        caption_spans: dict[int, list[tuple[int, int]]] = {}
        for pno in range(doc.page_count):
            page = doc[pno]
            blocks, full = _blocks_and_text(page)
            per_page_blocks.append((page, blocks))
            page_texts.append(full)
            spans = []
            for m in _CAPTION_HEAD.finditer(full):
                if is_caption(full[m.start():m.start() + 220]):
                    # exclude the caption HEAD + its first sentence (where cross-figure mentions live)
                    spans.append((m.start(), _caption_extent(full, m.end())))
            caption_spans[pno] = spans
    except Exception as e:   # noqa: BLE001 — corrupt/encrypted/truncated PDF: degrade, don't crash
        return _extract_text_only(pdf, out, figs_dir, context_chars)

    records: list[dict] = []
    seen: set[str] = set()
    for pno, (page, blocks) in enumerate(per_page_blocks):
        for (x0, y0, x1, y1, text) in blocks:
            fig_no = is_caption(text)
            if not fig_no:
                continue
            head = _CAPTION_HEAD.match(text)
            raw = head.group(1) if head else fig_no          # raw token ("3a") keeps subfigures distinct
            if raw in seen:
                continue
            seen.add(raw)
            caption = re.sub(r"\s+", " ", text).strip()[:1200]
            image_rel = None
            clip = _figure_clip(fitz, page, (x0, y0, x1, y1))
            if clip is not None:
                try:
                    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
                    name = f"fig{raw}.png"
                    pix.save(str(figs_dir / name))
                    image_rel = f"figures/{name}"
                except Exception:  # noqa: BLE001
                    image_rel = None
            refs = find_references(raw, page_texts, caption_spans, context_chars)   # normalizes internally
            records.append({
                "figure_id": f"fig{raw}",
                "figure_number": raw,
                "caption": caption,
                "image": image_rel,
                "references": refs,             # (2) in-text reference context, windowed
                "reference_count": len(refs),
                "description": None,            # (3) detailed image description — describe_figures.py (VLM)
                "source": _source(pdf, pno + 1, "pymupdf-clip" if image_rel else "pymupdf-textonly"),
            })
    doc.close()
    manifest = {"source_pdf": str(pdf), "engine": "pymupdf", "figure_count": len(records),
                "figures": sorted(records, key=lambda r: _figkey(r["figure_id"]))}
    (out / "figures_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    return manifest


def _figkey(fid: str):
    m = re.search(r"(\d+)", fid)
    return int(m.group(1)) if m else 0


def _page_images(pdf: Path, page: int, figs_dir: Path, fig_no: str) -> str | None:
    """Poppler fallback: dump embedded rasters on `page`; if none, render the whole page. Association
    is PAGE-LEVEL (no tight text↔image coordinate join without PyMuPDF), which we record honestly."""
    stem = figs_dir / f"fig{fig_no}"
    if shutil.which("pdfimages"):
        try:
            subprocess.run(["pdfimages", "-png", "-f", str(page), "-l", str(page), str(pdf), str(stem)],
                           capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        dumped = sorted(figs_dir.glob(f"fig{fig_no}-*.png"))
        # keep the largest dumped raster (drop logos/rules); return its relative path
        dumped = [p for p in dumped if p.stat().st_size > 1500]
        if dumped:
            best = max(dumped, key=lambda p: p.stat().st_size)
            for p in dumped:
                if p != best:
                    p.unlink()
            return f"figures/{best.name}"
    if shutil.which("pdftoppm"):               # vector-only figure → render the page as a fallback
        try:
            subprocess.run(["pdftoppm", "-png", "-r", "150", "-f", str(page), "-l", str(page),
                            "-singlefile", str(pdf), str(stem)], capture_output=True, timeout=60)
            if stem.with_suffix(".png").exists():
                return f"figures/fig{fig_no}.png"
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _extract_text_only(pdf: Path, out: Path, figs_dir: Path, context_chars: int) -> dict:
    """Degraded path (no PyMuPDF): poppler gives captions + reference windows AND page-level images."""
    if not shutil.which("pdftotext"):
        manifest = {"source_pdf": str(pdf), "engine": "none", "figure_count": 0, "figures": [],
                    "note": "neither PyMuPDF nor pdftotext available — cannot read the PDF"}
        (out / "figures_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    # -layout preserves column order (better than -raw for multi-column papers); pages split on \f.
    proc = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, timeout=120)
    page_texts = proc.stdout.split("\f")
    caption_spans: dict[int, list[tuple[int, int]]] = {}
    captions: dict[str, tuple[int, str]] = {}
    for pi, txt in enumerate(page_texts):
        spans = []
        for m in _CAPTION_HEAD.finditer(txt):
            if is_caption(txt[m.start():m.start() + 300]):
                # exclude the caption HEAD + its first sentence (cross-figure mentions live there)
                spans.append((m.start(), _caption_extent(txt, m.end())))
                raw = m.group(1)                     # raw token ("3a") keeps subfigures distinct
                captions.setdefault(raw, (pi, re.sub(r"\s+", " ", txt[m.start():m.start() + 400]).strip()))
        caption_spans[pi] = spans
    records = []
    for fno, (pi, cap) in captions.items():
        refs = find_references(fno, page_texts, caption_spans, context_chars)   # normalizes internally
        image_rel = _page_images(pdf, pi + 1, figs_dir, fno)
        records.append({"figure_id": f"fig{fno}", "figure_number": fno, "caption": cap,
                        "image": image_rel, "references": refs, "reference_count": len(refs),
                        "description": None,
                        "source": _source(pdf, pi + 1, "poppler-pageimage" if image_rel else "poppler-textonly")})
    manifest = {"source_pdf": str(pdf), "engine": "poppler", "figure_count": len(records),
                "figures": sorted(records, key=lambda r: _figkey(r["figure_id"])),
                "note": ("PyMuPDF not installed — images are PAGE-LEVEL (embedded rasters or a rendered "
                         "page), not region-tight; install pymupdf for tight figure clips")}
    (out / "figures_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="extract-figures")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--context-chars", type=int, default=350)
    ap.add_argument("--zoom", type=float, default=2.0)
    args = ap.parse_args(argv)
    m = extract(args.pdf, args.out, context_chars=args.context_chars, zoom=args.zoom)
    print(f"[extract-figures] engine={m['engine']} figures={m['figure_count']} → {args.out}/figures_manifest.json")
    for r in m["figures"]:
        print(f"  {r['figure_id']}: {r['reference_count']} in-text ref(s), image={r['image']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
