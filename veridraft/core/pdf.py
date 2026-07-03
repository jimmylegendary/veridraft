"""A tiny, dependency-free PDF writer.

Only used as the LAST-RESORT renderer for the `minimal-latex` engine when neither
`tectonic` nor `pdflatex` is on PATH — so the `gated claim → PDF` slice always
produces a real, openable PDF with zero system dependencies. It renders plain text
(Helvetica) with simple pagination. It is NOT a LaTeX engine; PaperOrchestra /
tectonic / pdflatex remain the real renderers behind the WritingEngine port.
"""
from __future__ import annotations

from pathlib import Path


def _esc(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(line: str, width: int) -> list[str]:
    line = line.replace("\t", "    ").rstrip("\n")
    if line == "":
        return [""]
    out: list[str] = []
    cur = ""
    for word in line.split(" "):
        while len(word) > width:  # hard-break very long tokens
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:width])
            word = word[width:]
        cand = word if not cur else f"{cur} {word}"
        if len(cand) <= width:
            cur = cand
        else:
            out.append(cur)
            cur = word
    out.append(cur)
    return out


def write_text_pdf(
    path: str,
    title: str,
    lines: list[str],
    font_size: int = 11,
    leading: int = 15,
    margin: int = 72,
    page_w: int = 612,
    page_h: int = 792,
    wrap_cols: int = 92,
) -> str:
    # Wrap + paginate.
    flat: list[str] = [title, ""]
    for ln in lines:
        flat.extend(_wrap(ln, wrap_cols))
    max_lines = max(1, int((page_h - 2 * margin) / leading))
    pages = [flat[i : i + max_lines] for i in range(0, len(flat), max_lines)] or [[title]]

    # Object numbering: 1=Catalog, 2=Pages, 3=Font, then per page (page, content).
    n_pages = len(pages)
    page_obj_nums = [4 + 2 * i for i in range(n_pages)]
    content_obj_nums = [5 + 2 * i for i in range(n_pages)]
    total_objs = 3 + 2 * n_pages

    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("latin-1")
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"

    for i, page_lines in enumerate(pages):
        pnum = page_obj_nums[i]
        cnum = content_obj_nums[i]
        objects[pnum] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {cnum} 0 R >>"
        ).encode("latin-1")

        start_y = page_h - margin
        body = [
            "BT",
            f"/F1 {font_size} Tf",
            f"{margin} {start_y} Td",
            f"{leading} TL",
        ]
        for j, ln in enumerate(page_lines):
            body.append(f"({_esc(ln)}) Tj")
            if j != len(page_lines) - 1:
                body.append("T*")
        body.append("ET")
        # latin-1 with replace: non-encodable glyphs (e.g. Hangul) degrade to '?'
        # rather than crash; /Length is computed from the resulting bytes so the
        # xref stays valid.
        stream = "\n".join(body).encode("latin-1", "replace")
        objects[cnum] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream
            + b"\nendstream"
        )

    # Serialize with a correct xref table.
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in range(1, total_objs + 1):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("latin-1")
        out += objects[num]
        out += b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {total_objs + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for num in range(1, total_objs + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {total_objs + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode("latin-1")

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(out))
    return path
