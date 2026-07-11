#!/usr/bin/env python3
"""Patent drawings — USPTO-style generation rules + image-based verification & bounded fix loop.

Patent figures are NOT paper plots: USPTO/KIPO drawings are monochrome LINE ART with reference
numerals (10, 12, 14 …) attached by lead lines, a "FIG. N" label, no color, no photographic
shading, and the numerals must be recited in the Detailed Description. This module verifies the
RENDERED IMAGE (not the source code that drew it):

- Deterministic lints (always run): color presence, photographic/shading density, content touching
  the border, minimum raster size. Model-independent floor.
- VLM render-and-look (when `vision_model` is set): overlapping arrows/lead lines, objects too
  close or touching, missing reference numerals / FIG. N label, clipped text — the visual defects
  a log can never show.
- A bounded fix loop re-dispatches the backend to REGENERATE the offending figures under the
  patent-style rules; stops when clean / not improving / iteration cap.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import providers

PATENT_FIGURE_RULES = """PATENT DRAWING RULES (USPTO-style — a gate verifies the rendered image):
- Monochrome LINE ART only: black lines/text on white. NO color, NO grayscale shading, NO photos.
- Every depicted element carries a REFERENCE NUMERAL (10, 12, 14, ... even numbers) attached by a
  thin LEAD LINE that does not cross other elements; the same numeral means the same element in
  every figure; every numeral MUST be recited in the Detailed Description text.
- A centered label "FIG. 1" / "FIG. 2" below each drawing.
- Generous spacing: boxes never touch; arrows/lead lines never overlap each other or text; inset
  arrow endpoints; keep a clear margin — nothing touches the image border.
- Draw with matplotlib/graphviz in pure black-and-white (linewidth ~1.2, axis off, tight bounding
  box with padding); one drawing per file: figures/fig1.png, figures/fig2.png, ..."""


def log(m: str) -> None:
    print(f"[patent-figures] {m}", flush=True)


def _rasterize(path: Path) -> Path | None:
    """Return a PNG for the figure (rasterizing pdf/eps via pdftoppm/mutool/gs). None if impossible."""
    if path.suffix.lower() in (".png", ".jpg", ".jpeg"):
        return path
    out = path.with_suffix(".pfcheck.png")
    cands = []
    if path.suffix.lower() == ".pdf" and shutil.which("pdftoppm"):
        cands.append(["pdftoppm", "-png", "-singlefile", "-r", "100", str(path), str(out.with_suffix(""))])
    if shutil.which("mutool"):
        cands.append(["mutool", "draw", "-o", str(out), "-r", "100", str(path), "1"])
    if shutil.which("gs"):
        cands.append(["gs", "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dEPSCrop", "-sDEVICE=png16m",
                      "-r100", f"-sOutputFile={out}", str(path)])
    for cmd in cands:
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.exists():
            return out
    return None


def lint_image(path: Path) -> list[str]:
    """Deterministic USPTO-style lints on the RENDERED image. Empty list = clean."""
    try:
        from PIL import Image
    except ImportError:
        return []
    png = _rasterize(path)
    if png is None:
        return [f"{path.name}: could not rasterize for inspection"]
    issues: list[str] = []
    try:
        img = Image.open(png).convert("RGB")
        w, h = img.size
        if w < 400 or h < 300:
            issues.append(f"{path.name}: raster too small ({w}x{h}) — render at higher resolution")
        px = list(img.tobytes())
        n = len(px) // 3
        color = sum(1 for i in range(0, len(px), 3)
                    if max(px[i], px[i+1], px[i+2]) - min(px[i], px[i+1], px[i+2]) > 40)
        if color / max(n, 1) > 0.005:
            issues.append(f"{path.name}: contains COLOR ({color/n:.1%} of pixels) — patent drawings "
                          "must be black-and-white line art")
        # photographic shading: mid-gray mass (not near-white bg, not near-black line)
        gray = [(px[i] + px[i+1] + px[i+2]) // 3 for i in range(0, len(px), 3)]
        mid = sum(1 for g in gray if 60 < g < 200)
        if mid / max(n, 1) > 0.30:
            issues.append(f"{path.name}: heavy shading/photographic tone ({mid/n:.0%} mid-gray) — "
                          "line art required")
        # border contact: ink in the outer 2% frame
        bw, bh = max(1, w // 50), max(1, h // 50)
        def ink(x, y):
            return gray[y * w + x] < 128
        touch = (any(ink(x, y) for x in range(w) for y in (*range(bh), *range(h - bh, h)))
                 or any(ink(x, y) for y in range(h) for x in (*range(bw), *range(w - bw, w))))
        if touch:
            issues.append(f"{path.name}: content touches the image border — add margin "
                          "(bbox_inches='tight', pad_inches>=0.3)")
        # geometric defects the mean-color lints can't see: crossing lead-lines/arrows and
        # objects too close/touching — measured deterministically on the rendered image (numpy).
        issues += _overlap_defects(png, path.name)
    except Exception:   # noqa: BLE001 — a bad image must not crash the pipeline
        issues.append(f"{path.name}: unreadable image")
    finally:
        if png is not None and png != path and png.exists():
            png.unlink()
    return issues


# ---- deterministic geometry: crowding + objects-too-close (numpy floor) --------------------------
# The color/shading lints above are per-pixel and blind to LAYOUT, so this pass measures the geometry
# of the RENDERED line art. Two signals are deterministic AND text-tolerant (reference numerals must
# not trip them), so they are hard defects that drive the fix loop:
#   (1) ink-CROWDING — tiles with high ink fraction = tangled / overlapping / touching regions.
#   (2) objects-TOO-CLOSE — the largest connected morphological-closing BRIDGE component: a narrow
#       white channel between two near-touching elements is ONE large bridge blob, whereas text only
#       bridges tiny per-glyph gaps, so the max-component size cleanly separates them.
# Clean single line/arrow CROSSINGS are deliberately NOT hard-flagged: box corners and glyphs create
# the same degree>=4 skeleton points, so a deterministic crossing count false-positives badly — that
# adjudication is left to the VLM pass (vlm_lint), which is explicitly asked about crossings. numpy-
# only (present here); absent numpy → the whole pass no-ops (floor degrades, never crashes). scipy/
# cv2/skimage would only sharpen this and are NOT required.

def _numpy():
    try:
        import numpy as np
        return np
    except ImportError:
        return None


def _load_binary(png, np, work=1000):
    from PIL import Image
    img = Image.open(png).convert("L")
    w, h = img.size
    if max(w, h) > work:                       # DOWNSCALE only (never upscale — upscaling blurs thin
        s = work / max(w, h)                   # lines and false-positives). Portability across DPIs
        img = img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.BILINEAR)  # is achieved
                                               # instead by scaling the pixel thresholds by `sc` below
    ink = (np.asarray(img) < 160).astype(np.uint8)      # white bg, dark ink (anti-alias-tolerant cut)
    ink[0, :] = ink[-1, :] = ink[:, 0] = ink[:, -1] = 0  # zero the border so np.roll can't wrap ink
    return ink


def _max_true_cluster(np, grid) -> int:
    """Largest 4-connected run of True cells in a small boolean grid."""
    trues = {(int(r), int(c)) for r, c in zip(*np.where(grid))}
    seen, best = set(), 0
    for cell in list(trues):
        if cell in seen:
            continue
        size, stack = 0, [cell]
        while stack:
            r, c = stack.pop()
            if (r, c) in seen or (r, c) not in trues:
                continue
            seen.add((r, c)); size += 1
            stack += [(r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)]
        best = max(best, size)
    return best


def _crowding(np, ink, tile=24, dense_frac=0.22):
    """(crowded-tile fraction, largest dense cluster, dense-tile count) — tangled/touching regions."""
    tile = max(10, int(tile))
    H, W = ink.shape
    Ht, Wt = H // tile, W // tile
    if Ht < 2 or Wt < 2:
        return 0.0, 0, 0
    dens = ink[:Ht * tile, :Wt * tile].reshape(Ht, tile, Wt, tile).astype(np.float32).mean(axis=(1, 3))
    dense = dens > dense_frac
    inkt = int((dens > 0.002).sum())
    n_dense = int(dense.sum())
    return n_dense / max(inkt, 1), _max_true_cluster(np, dense), n_dense


def _dilate(np, m, it):
    r = np.roll
    for _ in range(it):
        m = (m | r(m, 1, 0) | r(m, -1, 0) | r(m, 1, 1) | r(m, -1, 1)
             | r(r(m, 1, 0), 1, 1) | r(r(m, 1, 0), -1, 1) | r(r(m, -1, 0), 1, 1) | r(r(m, -1, 0), -1, 1))
    return m


def _erode(np, m, it):
    r = np.roll
    for _ in range(it):
        m = (m & r(m, 1, 0) & r(m, -1, 0) & r(m, 1, 1) & r(m, -1, 1)
             & r(r(m, 1, 0), 1, 1) & r(r(m, 1, 0), -1, 1) & r(r(m, -1, 0), 1, 1) & r(r(m, -1, 0), -1, 1))
    return m


def _max_component_size(coords) -> int:
    """Largest 8-connected component among a set of (row,col) pixels (union by flood, set-backed)."""
    S = set(coords)
    best = 0
    while S:
        size, stack = 0, [S.pop()]
        while stack:
            r, c = stack.pop(); size += 1
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    p = (r + dr, c + dc)
                    if p in S:
                        S.discard(p); stack.append(p)
        best = max(best, size)
    return best


def _too_close_maxbridge(np, ink, g=2, cap=60000) -> int:
    """Largest connected closing-BRIDGE component (px): a narrow channel between two near-touching
    elements is one big bridge blob; text bridges only tiny per-glyph gaps → max-component separates."""
    ib = ink.astype(bool)
    bridge = _erode(np, _dilate(np, ib, g), g) & (~ib)     # morphological closing residue
    ys, xs = np.where(bridge)
    if len(xs) == 0:
        return 0
    if len(xs) > cap:                                      # pathological tangle — treat as heavy
        return len(xs)
    return _max_component_size(list(zip(ys.tolist(), xs.tolist())))


def _overlap_defects(png, name, work=1000) -> list[str]:
    """Deterministic layout defects on the rendered drawing. Empty when numpy is unavailable."""
    np = _numpy()
    if np is None:
        return []
    try:
        ink = _load_binary(png, np, work)
    except Exception:   # noqa: BLE001
        return []
    if int(ink.sum()) < 50:                     # near-empty raster — nothing to measure
        return []
    issues: list[str] = []
    try:
        # tile=24 + the 0.22 ink-fraction RATIO keep this resolution-tolerant across the realistic
        # raster range (min-size gate 400px .. work=1000; larger is downscaled to 1000). The bridge
        # threshold sits ABOVE text-glyph artifacts (~<=62px) and BELOW real near-touch channels
        # (>=140px seen even at 640px), fixing the earlier 150px false-negative on sub-1000 rasters.
        crowd_frac, cluster, n_dense = _crowding(np, ink)
        if crowd_frac > 0.03 or cluster >= 4:
            issues.append(f"{name}: crowded/overlapping region(s) — {n_dense} dense tile(s) "
                          f"(largest block {cluster}); move elements apart / enlarge the drawing")
        bridge = _too_close_maxbridge(np, ink)
        if bridge >= 90:
            issues.append(f"{name}: objects too close — a {bridge}px narrow gap/channel between "
                          f"elements; increase spacing so nothing nearly touches")
    except Exception:   # noqa: BLE001 — a detector bug must never break the gate
        return issues
    return issues


def vlm_lint(cfg: dict, figs: list[Path]) -> list[str]:
    """Image-based VLM pass (advisory floor-raiser; [] without vision_model or on any failure)."""
    vm = cfg.get("vision_model")
    pngs = [str(p) for p in figs if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    if not vm or not pngs:
        return []
    prompt = ("LOOK at each PATENT DRAWING image and report defects as a JSON array of short strings "
              "(return [] if none), checking: (a) overlapping arrows/lead lines, or lines crossing "
              "text/boxes; (b) elements too close or touching (need clear gaps); (c) missing reference "
              "numerals (10, 12, 14...) or numerals without lead lines; (d) missing 'FIG. N' label; "
              "(e) clipped/illegible text; (f) color or photographic shading (must be B/W line art). "
              "Prefix each defect with the file name. Images:\n" + "\n".join(pngs)
              + "\nReturn ONLY the JSON array.")
    try:
        out = providers.dispatch({**cfg, "model": vm}, prompt, str(figs[0].parent), timeout=1200)
    except Exception as e:   # noqa: BLE001
        log(f"VLM figure check skipped ({e})"); return []
    i, j = out.find("["), out.rfind("]")
    if i == -1 or j <= i:
        return []
    try:
        arr = json.loads(out[i:j + 1])
        return [str(x) for x in arr][:20] if isinstance(arr, list) else []
    except (ValueError, TypeError):
        return []


def check(cfg: dict, figs_dir: Path) -> list[str]:
    figs = sorted(p for p in figs_dir.glob("*")
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".eps")) \
        if figs_dir.exists() else []
    issues: list[str] = []
    for f in figs:
        issues += lint_image(f)
    issues += vlm_lint(cfg, figs)
    return issues


def check_and_fix(cfg: dict, ws: Path, figs_dir: Path, max_iters: int = 2) -> list[str]:
    """Verify the rendered drawings; feed defects back into a bounded REGENERATE loop."""
    issues = check(cfg, figs_dir)
    it = 0
    while issues and it < max_iters:
        it += 1
        log(f"figure-fix iter {it}/{max_iters}: {len(issues)} defect(s) → regenerating")
        prompt = (f"The patent drawings in {figs_dir} have these VERIFIED visual defects:\n- "
                  + "\n- ".join(issues)
                  + f"\n\nREGENERATE only the offending figure files in {figs_dir} (same file names, "
                  f"same depicted elements and reference numerals — fix the layout/style only).\n"
                  + PATENT_FIGURE_RULES + "\nWhen done, confirm the files exist and stop.")
        try:
            providers.dispatch(cfg, prompt, str(ws), timeout=(cfg.get("step_timeout_seconds") or 2400))
        except Exception as e:   # noqa: BLE001
            log(f"figure-fix dispatch failed ({e})"); break
        after = check(cfg, figs_dir)
        if len(after) >= len(issues):
            log(f"figure-fix iter {it}: defects did not decrease ({len(after)}) — stopping")
            issues = after
            break
        issues = after
    return issues
