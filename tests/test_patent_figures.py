"""Tests for the patent-drawing image gate: deterministic USPTO-style lints + the numpy geometry
floor (crowding / objects-too-close) + the bounded regenerate loop.  python -m unittest discover -s tests"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paperorchestra"))
import patent_figures as pf

try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False


def _png(fn, size=(800, 600), mode="L", bg=255):
    img = Image.new(mode, size, bg)
    fn(ImageDraw.Draw(img))
    p = Path(tempfile.mkdtemp()) / "f.png"
    img.save(p)
    return p


def _clean_lineart(d):
    d.rectangle([80, 80, 240, 180], outline=0, width=2)
    d.rectangle([560, 420, 720, 520], outline=0, width=2)
    d.line([240, 130, 560, 470], fill=0, width=2)      # single connector, no crossing
    d.text((150, 210), "processor 10", fill=0)


@unittest.skipUnless(_HAS_PIL, "PIL required for image lints")
class LintImageTest(unittest.TestCase):
    def test_clean_line_art_passes(self):
        self.assertEqual(pf.lint_image(_png(_clean_lineart)), [])

    def test_color_flagged(self):
        def f(d):
            d.rectangle([100, 100, 400, 400], outline=(0, 0, 0), width=2)
            d.rectangle([150, 150, 350, 350], fill=(220, 30, 30))     # a big red fill
        issues = pf.lint_image(_png(f, mode="RGB", bg=(255, 255, 255)))
        self.assertTrue(any("COLOR" in i for i in issues), issues)

    def test_shading_flagged(self):
        def f(d):
            d.rectangle([50, 50, 750, 550], fill=128)                 # half-tone photographic mass
        issues = pf.lint_image(_png(f))
        self.assertTrue(any("shading" in i for i in issues), issues)

    def test_border_contact_flagged(self):
        def f(d):
            d.rectangle([0, 0, 799, 599], outline=0, width=3)         # ink hugging the frame
            d.line([0, 300, 799, 300], fill=0, width=2)
        issues = pf.lint_image(_png(f))
        self.assertTrue(any("border" in i for i in issues), issues)

    def test_too_small_flagged(self):
        issues = pf.lint_image(_png(lambda d: d.rectangle([5, 5, 50, 40], outline=0), size=(120, 90)))
        self.assertTrue(any("too small" in i for i in issues), issues)


@unittest.skipUnless(_HAS_PIL, "PIL required for geometry floor")
class OverlapGeometryTest(unittest.TestCase):
    def test_clean_has_no_geometry_defect(self):
        self.assertEqual(pf._overlap_defects(_png(_clean_lineart), "c.png"), [])

    def test_objects_too_close_flagged(self):
        def f(d):
            d.rectangle([200, 200, 360, 320], outline=0, width=2)
            d.rectangle([364, 200, 520, 320], outline=0, width=2)     # ~4px channel between them
            d.text((210, 340), "unit 20", fill=0)                     # text must NOT trip it alone
        issues = pf._overlap_defects(_png(f), "tc.png")
        self.assertTrue(any("too close" in i for i in issues), issues)

    def test_tangled_region_flagged_as_crowding(self):
        def f(d):
            for i in range(20):
                d.line([100 + i * 3, 100, 400, 300 + i * 5], fill=0, width=3)   # dense overlap
        issues = pf._overlap_defects(_png(f), "tg.png")
        self.assertTrue(any("crowded" in i for i in issues), issues)

    def test_text_labels_do_not_false_positive(self):
        def f(d):
            d.rectangle([100, 100, 300, 200], outline=0, width=2)
            d.rectangle([500, 350, 700, 450], outline=0, width=2)
            d.line([300, 150, 500, 400], fill=0, width=2)
            d.text((120, 220), "scheduler 10", fill=0)
            d.text((520, 470), "accelerator 12", fill=0)
        self.assertEqual(pf._overlap_defects(_png(f), "t.png"), [])

    def test_no_numpy_degrades_to_empty(self):
        real = pf._numpy
        pf._numpy = lambda: None
        try:
            self.assertEqual(pf._overlap_defects(_png(_clean_lineart), "x.png"), [])
        finally:
            pf._numpy = real


@unittest.skipUnless(_HAS_PIL, "PIL required")
class CheckAndFixTest(unittest.TestCase):
    def test_vlm_noop_without_vision_model(self):
        self.assertEqual(pf.vlm_lint({}, [Path("/nonexistent.png")]), [])

    def test_check_and_fix_stops_when_not_improving(self):
        # a persistently-defective figure with NO backend configured must stop (not loop forever)
        d = Path(tempfile.mkdtemp())
        figs = d / "figures"; figs.mkdir()
        _png(lambda dr: dr.rectangle([0, 0, 39, 29], outline=0), size=(40, 30)).replace(figs / "fig1.png")
        # dispatch will raise (no backend) → loop breaks after the first attempt
        remaining = pf.check_and_fix({"backend": "api"}, d, figs, max_iters=2)
        self.assertTrue(any("too small" in r for r in remaining), remaining)

    def test_rules_constant_present(self):
        self.assertIn("REFERENCE NUMERAL", pf.PATENT_FIGURE_RULES)
        self.assertIn("FIG.", pf.PATENT_FIGURE_RULES)


if __name__ == "__main__":
    unittest.main()
