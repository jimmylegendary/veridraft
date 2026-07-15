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
        self.assertTrue(any("prove" in b for b in blk))       # family key normalizes proves→prove
        self.assertTrue(any("optimal" in b for b in blk))

    def test_disclosure_removal_blocks(self):
        bad = ORIG.replace(" We used generative AI to assist drafting.", "")
        self.assertTrue(any("disclosure" in b for b in self._blk(bad)))


class HedgeTest(unittest.TestCase):
    ORIG = r"Our method may reduce latency and suggests that throughput improves \cite{a}. It might increase accuracy."

    def test_de_hedging_a_claim_blocks(self):
        # "may reduce" -> "reduces", "suggests" -> "shows", "might increase" -> "increases"
        bad = r"Our method reduces latency and shows that throughput improves \cite{a}. It increases accuracy."
        g = nl.lint_naturalize(bad, self.ORIG)
        self.assertTrue(any("de-hedged" in b for b in g["blocking"]))

    def test_verb_reword_keeping_hedges_passes(self):
        # "may reduce" -> "may cut", "might increase" -> "might raise" — hedges preserved
        good = r"Our approach may cut latency and suggests that throughput improves \cite{a}. It might raise accuracy."
        g = nl.lint_naturalize(good, self.ORIG)
        self.assertEqual([b for b in g["blocking"] if "epistemic" in b], [])


class ReviewFixesTest(unittest.TestCase):
    """Regressions for the 8 adversarial-review findings."""
    def _blk(self, nat, orig):
        return nl.lint_naturalize(nat, orig)["blocking"]

    def test_bare_integer_and_comma_number_changes_block(self):
        self.assertTrue(any("number" in b for b in self._blk("we ran 100 trials", "we ran 500 trials")))
        self.assertTrue(any("number" in b for b in self._blk("87 F1 points", "85 F1 points")))
        self.assertTrue(any("number" in b for b in self._blk(r"a 2,000x speedup", r"a 1,000x speedup")))
        self.assertEqual([b for b in self._blk("1000 samples", "1,000 samples") if "number" in b], [])

    def test_dehedge_survives_a_compensating_hedge_elsewhere(self):
        # a hedge added in an unrelated clause must not hide de-hedging of the real claim
        blk = self._blk("we suggest X and our method reduces Y.", "we think X and our method may reduce Y.")
        self.assertTrue(any("de-hedged" in b for b in blk))

    def test_dropping_a_redundant_or_nonclaim_hedge_is_not_blocked(self):
        self.assertEqual([b for b in self._blk("the method may reduce latency.",
                                               "the method may possibly reduce latency.") if "de-hedged" in b], [])
        self.assertEqual([b for b in self._blk("results are in Table 2.",
                                               "results would be found in Table 2.") if "de-hedged" in b], [])

    def test_math_whitespace_only_change_is_not_a_defect(self):
        self.assertEqual([b for b in self._blk(r"see $a+b$", r"see $a + b$") if "math" in b], [])
        self.assertTrue(any("math" in b for b in self._blk(r"see $a+c$", r"see $a+b$")))   # real change blocks

    def test_reworded_disclosure_still_present_passes(self):
        self.assertEqual([b for b in self._blk("We used an LLM to help write this.",
                                               "We used generative AI to assist drafting.") if "disclosure" in b], [])

    def test_strong_modal_inflection_is_not_a_false_positive(self):
        self.assertEqual([b for b in self._blk("this ensure correctness", "this ensures correctness")
                          if "strengthened" in b], [])


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
