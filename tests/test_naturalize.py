"""Tests for the naturalize honesty gate — a readability copyedit must not change what the paper
SAYS (numbers/citations/math/claims/disclosure).  python -m unittest discover -s tests"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paperorchestra"))
import naturalize_lints as nl

ORIG = (r"\section{Results}"
        "\n" r"It is important to note that our method may reduce latency by 12.5\% and the throughput "
        r"is 900 tokens/s \cite{smith2024}. The model $y = Wx + b$ was evaluated \ref{fig:arch}. "
        r"Results appear promising, though limitations remain. We used generative AI to assist drafting.")


class CleanCopyeditTest(unittest.TestCase):
    def test_deaiese_reword_preserving_content_passes(self):
        # removed "It is important to note that", reworded verbs — same numbers/cites/math/claims
        good = (r"\section{Results}"
                "\n" r"Our method may cut latency by 12.5\% and reaches a throughput of 900 tokens/s "
                r"\cite{smith2024}. We evaluated the model $y = Wx + b$ \ref{fig:arch}. Results appear "
                r"promising, though limitations remain. We used generative AI to assist drafting.")
        g = nl.lint_naturalize(good, ORIG)
        self.assertEqual(g["blocking"], [], g)


class ContentChangeBlockingTest(unittest.TestCase):
    def _blk(self, natural):
        return nl.lint_naturalize(natural, ORIG)["blocking"]

    def test_changed_number_blocks(self):
        bad = ORIG.replace("12.5", "15")
        self.assertTrue(any("12.5" in b or "15" in b for b in self._blk(bad)))

    def test_dropped_citation_blocks(self):
        bad = ORIG.replace(r"\cite{smith2024}", "")
        self.assertTrue(any("smith2024" in b and "dropped" in b for b in self._blk(bad)))

    def test_added_citation_blocks(self):
        bad = ORIG.replace(r"\cite{smith2024}", r"\cite{smith2024,jones2025}")
        self.assertTrue(any("jones2025" in b and "added" in b for b in self._blk(bad)))

    def test_changed_math_blocks(self):
        bad = ORIG.replace(r"$y = Wx + b$", r"$y = Wx$")
        self.assertTrue(any("math/verbatim" in b for b in self._blk(bad)))

    def test_claim_strengthening_blocks(self):
        bad = ORIG.replace("may reduce latency", "reduces latency").replace(
            "Results appear promising", "This proves the method is optimal")
        blk = self._blk(bad)
        self.assertTrue(any("proves" in b for b in blk))
        self.assertTrue(any("optimal" in b for b in blk))

    def test_disclosure_removal_blocks(self):
        bad = ORIG.replace(" We used generative AI to assist drafting.", "")
        self.assertTrue(any("disclosure" in b for b in self._blk(bad)))


class AdvisoryTest(unittest.TestCase):
    def test_large_hedge_drop_is_advisory(self):
        orig = ("The method may improve X. It might help Y. Results appear to suggest Z. "
                "It could potentially assist W, and likely benefits V.")
        stripped = "The method improves X. It helps Y. Results show Z. It assists W, and benefits V."
        g = nl.lint_naturalize(stripped, orig)
        self.assertTrue(any("hedges dropped" in a for a in g["advisory"]))


class DisclosureDrafterTest(unittest.TestCase):
    def test_drafts_a_responsible_disclosure(self):
        s = nl.draft_disclosure("paper", "ACL 2027")
        self.assertIn("Responsible NLP", s)
        self.assertIn("full responsibility", s)
        self.assertIn("not an author", s)
        # explicitly disavows detector evasion
        self.assertIn("does not target", s)


if __name__ == "__main__":
    unittest.main()
