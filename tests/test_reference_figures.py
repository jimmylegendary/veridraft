"""Tests for the reference-figure extractor: caption vs mention discrimination, sentence-window
reference context, and third-party provenance.  python -m unittest discover -s tests"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "reference-figure-extractor" / "scripts"))
import extract_figures as ef


class CaptionDiscriminationTest(unittest.TestCase):
    def test_caption_needs_a_separator(self):
        self.assertEqual(ef.is_caption("Figure 1: Overall architecture of the system"), "1")
        self.assertEqual(ef.is_caption("Fig. 2. The pipeline stages and their data flow"), "2")

    def test_body_line_starting_with_reference_is_not_a_caption(self):
        self.assertIsNone(ef.is_caption("Figure 1 also motivates the batching policy"))
        self.assertIsNone(ef.is_caption("Figure 1"))                      # bare label / reference
        self.assertIsNone(ef.is_caption("Figure 3: one"))                # <2 descriptive words

    def test_subfigure_normalizes(self):
        self.assertEqual(ef._norm_fig("3a"), "3")
        self.assertEqual(ef.is_caption("Figure 3a: a zoomed inset view of the module"), "3")


class ReferenceContextTest(unittest.TestCase):
    PAGE = ("Intro. As shown in Figure 1, the scheduler feeds the accelerator.\n"
            "Figure 1: Overall architecture of the scheduler and accelerator.\n"
            "Method. Figure 1 also motivates batching. We revisit Figure 1 later.")

    def _spans(self, page):
        return [(m.start(), m.end()) for m in ef._CAPTION_HEAD.finditer(page)
                if ef.is_caption(page[m.start():m.start() + 300])]

    def test_finds_all_body_mentions_excludes_caption(self):
        refs = ef.find_references("1", [self.PAGE], {0: self._spans(self.PAGE)}, context_chars=70)
        self.assertEqual(len(refs), 3)                                   # intro + 2 method, caption out
        self.assertTrue(all("Figure 1" in r["context"] for r in refs))
        self.assertFalse(any(r["context"].strip().startswith("Figure 1: Overall") for r in refs))

    def test_window_keeps_surrounding_sentence(self):
        refs = ef.find_references("1", [self.PAGE], {0: self._spans(self.PAGE)}, context_chars=200)
        # the intro mention's window should include its own sentence (context preserved)
        self.assertTrue(any("scheduler feeds the accelerator" in r["context"] for r in refs))

    def test_window_is_bounded(self):
        big = "x " * 5000 + "see Figure 2 here. " + "y " * 5000
        w = ef.sentence_window(big, big.index("Figure 2"), big.index("Figure 2") + 8, context_chars=120)
        self.assertLess(len(w), 400)                                     # not the whole page
        self.assertIn("Figure 2", w)

    def test_window_includes_following_sentence(self):
        txt = "Intro sentence. See Figure 5 now. The next sentence follows here."
        w = ef.sentence_window(txt, txt.index("Figure 5"), txt.index("Figure 5") + 8, context_chars=300)
        self.assertIn("next sentence follows", w)                        # neighbour AFTER included

    def test_multi_figure_range_and_conjunction_refs(self):
        txt = "See Figures 2 and 3 for details. Figs. 4-6 summarize. Figure 7a shows the inset."
        for want in ("2", "3", "4", "5", "6", "7"):
            self.assertEqual(len(ef.find_references(want, [txt], {0: []}, 60)), 1, f"fig {want} dropped")

    def test_singular_figure_does_not_pull_conjunction(self):
        txt = "Figure 2 and 3 steps were executed in sequence."          # singular → "3 steps" is not a fig
        self.assertEqual(ef.find_references("3", [txt], {0: []}, 60), [])

    def test_cross_caption_reference_excluded(self):
        page = ("Figure 1: Overview of the system.\n"
                "Figure 2: Comparison to Figure 1 baseline results shown here.")
        spans = [(m.start(), ef._caption_extent(page, m.end()))
                 for m in ef._CAPTION_HEAD.finditer(page) if ef.is_caption(page[m.start():m.start()+200])]
        self.assertEqual(ef.find_references("1", [page], {0: spans}, 60), [])   # not a body ref


@unittest.skipUnless(shutil.which("pdftotext") and shutil.which("latexmk"),
                     "needs latexmk + poppler for the end-to-end PDF path")
class EndToEndPopplerTest(unittest.TestCase):
    def test_extract_from_compiled_pdf(self):
        work = Path(tempfile.mkdtemp())
        (work / "paper.tex").write_text(
            r"\documentclass{article}\begin{document}"
            r"\section{Intro} As shown in Figure~1, the scheduler feeds the accelerator backend here."
            r"\begin{figure}[h]\centering\rule{4cm}{3cm}"
            r"\caption{Overall architecture of the adaptive request scheduler and accelerator backend.}"
            r"\end{figure}"
            r"\section{Method} Figure 1 also motivates the batching policy we describe next.\end{document}",
            encoding="utf-8")
        subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "paper.tex"],
                       cwd=work, capture_output=True, timeout=120)
        self.assertTrue((work / "paper.pdf").exists(), "latexmk did not produce a PDF")
        m = ef.extract(str(work / "paper.pdf"), str(work / "out"))
        self.assertEqual(m["figure_count"], 1, m)
        fig = m["figures"][0]
        self.assertEqual(fig["figure_number"], "1")
        self.assertTrue(fig["source"]["third_party"])
        self.assertGreaterEqual(fig["reference_count"], 2)              # intro + method, caption excluded
        self.assertTrue(fig["caption"].lower().startswith("figure 1"))


if __name__ == "__main__":
    unittest.main()
