"""Tests for the evidence-gated marketing flow (P1): the deterministic honesty gate + the revision
runner's idempotent compile path. Run from repo root: python -m unittest discover -s tests"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import paperorchestra.marketing_lints as ml
import paperorchestra.market_run as market_run

ORIG = r"We achieve 92\% accuracy and a 1.8x speedup. Limitations: dense-attention only."


class MarketingGateTest(unittest.TestCase):
    def test_inflated_number_is_blocking(self):
        g = ml.lint_marketing(r"The first method to hit 99\% accuracy at 1.8x.", ORIG)
        self.assertTrue(any("99" in b for b in g["blocking"]))     # fabricated stat blocked
        self.assertTrue(any("first" in a for a in g["advisory"]))  # added superlative advised

    def test_honest_reframe_passes(self):
        g = ml.lint_marketing(r"High-accuracy attention: 92\% accuracy at a 1.8x speedup.", ORIG)
        self.assertEqual(g["blocking"], [])
        self.assertEqual(g["advisory"], [])

    def test_number_from_experimental_log_allowed(self):
        g = ml.lint_marketing(r"We reach 87\% recall.", r"See results.", source_numbers=r"recall 87\%")
        self.assertEqual(g["blocking"], [])

    def test_superlative_already_in_paper_not_flagged(self):
        # if the original paper earned "state-of-the-art", the marketing version may keep it
        orig = ORIG + r" Our method is state-of-the-art on this benchmark."
        g = ml.lint_marketing(r"A state-of-the-art approach: 92\% accuracy.", orig)
        self.assertEqual(g["advisory"], [])

    def test_metadata_kit_shape(self):
        kit = ml.metadata_kit("Fast Serving", "We cut latency.", ["latency", "serving"], url="http://x")
        self.assertEqual(kit["json_ld"]["@type"], "ScholarlyArticle")
        self.assertEqual(kit["keywords"], ["latency", "serving"])
        self.assertTrue(any("citation_title" in t for t in kit["meta_tags"]))


class MarketRunTest(unittest.TestCase):
    def test_extract_title_abstract_keywords(self):
        tex = (r"\title{Fast Attention Serving}\begin{abstract}We cut p99 latency."
               r"\end{abstract}\keywords{attention, serving, latency}")
        title, abs, kw = market_run._extract(tex)
        self.assertEqual(title, "Fast Attention Serving")
        self.assertIn("latency", abs)
        self.assertEqual(kw, ["attention", "serving", "latency"])

    @unittest.skipUnless(shutil.which("latexmk"), "latexmk absent")
    def test_market_idempotent_compile_and_gate(self):
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); final = ws / "final"; final.mkdir()
        (ws / "inputs").mkdir(); (ws / "inputs" / "experimental_log.md").write_text("accuracy: 92%")
        (final / "paper.tex").write_text(
            r"\documentclass{article}\begin{document}We achieve 92\% accuracy.\end{document}")
        (final / "paper-marketing.tex").write_text(
            r"\documentclass{article}\title{High-Accuracy Attention Serving}"
            r"\begin{document}\keywords{attention, accuracy, serving}"
            + (r"Reaching 92\% accuracy with a practical, search-friendly method. " * 5)
            + r"\end{document}")   # >200 bytes → idempotent resume path
        called = []
        orig = market_run.providers.dispatch
        market_run.providers.dispatch = lambda *a, **k: called.append(1)
        try:
            rc = market_run.market({"backend": "x"}, ws, "paper", force=False)
        finally:
            market_run.providers.dispatch = orig
        self.assertEqual(called, [])                                    # idempotent → no LLM
        self.assertTrue((final / "paper-marketing.pdf").exists())       # compiled
        gate = json.loads((final / "paper-marketing.gate.json").read_text())
        self.assertEqual(gate["blocking"], [])                          # honest reframe → no over-claim
        meta = json.loads((final / "paper-marketing.meta.json").read_text())
        self.assertIn("attention", meta["keywords"])
        self.assertEqual(rc, 0)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
