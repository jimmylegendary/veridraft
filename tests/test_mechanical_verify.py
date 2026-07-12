"""Tests for the mechanical-verification stage: overfull map, text mechanics, translation
terminology preservation, and the global layout remedy.  python -m unittest discover -s tests"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "paperorchestra"))
import mechanical_verify as mv


class OverfullMapTest(unittest.TestCase):
    LOG = ("Overfull \\hbox (15.34pt too wide) in paragraph at lines 120--127\n"
           "Overfull \\hbox (3.0pt too wide) in paragraph at lines 44--50\n"
           "Overfull \\hbox (88.0pt too wide) in alignment at lines 200--210\n"
           "Overfull \\hbox (9.5pt too wide) detected at line 300\n"
           "Underfull \\hbox (badness 10000) at lines 5--9\n"
           "File `figures/missing.pdf' not found on input line 60.\n")

    def test_parses_over_threshold_worst_first_with_location(self):
        of = mv.parse_overfull(self.LOG)
        self.assertEqual([o["pt"] for o in of], [88.0, 15.3, 9.5])   # 3.0pt (<5) dropped
        self.assertEqual(of[0]["loc"], "lines 200--210")
        self.assertEqual(of[2]["loc"], "line 300")

    def test_underfull_ignored_missing_figure_found(self):
        self.assertEqual(mv.missing_figures(self.LOG), ["figures/missing.pdf"])


class TextMechanicsTest(unittest.TestCase):
    def test_flags_visible_defects_on_rendered_text(self):
        rendered = "This is is a test . The the result ,, shows \\textbf leaked here."
        got = mv.text_mechanics(rendered)
        types = {i["type"] for i in got}
        self.assertIn("doubled-word", types)          # "is is" and "The the"
        self.assertIn("space-before-punct", types)    # " ."
        self.assertIn("doubled-punct", types)         # ",,"
        self.assertIn("leaked-latex", types)          # "\textbf" visible in rendered text
        self.assertTrue(any(i["detail"] == "The the" for i in got))   # case-insensitive doubled-word

    def test_legit_doubles_not_flagged(self):
        # "that that" / "had had" are legitimate; a bare number decimal must not be doubled-punct
        rendered = "We show that that value is 3.5 and had had no effect."
        self.assertFalse(any(i["type"] == "doubled-word" for i in mv.text_mechanics(rendered)))
        self.assertFalse(any(i["type"] == "doubled-punct" for i in mv.text_mechanics(rendered)))

    def test_cpp_scope_resolution_not_flagged(self):
        for tok in ("std::vector", "torch::Tensor", "namespace::member"):
            self.assertFalse(any(i["type"] == "doubled-punct" for i in mv.text_mechanics(tok)), tok)
        self.assertTrue(any(i["type"] == "doubled-punct" for i in mv.text_mechanics("a::: b")))  # 3+ colons

    def test_windows_path_not_flagged_but_real_command_is(self):
        self.assertEqual([i for i in mv.text_mechanics(r"see C:\Users\Program files here")
                          if i["type"] == "leaked-latex"], [])
        self.assertTrue(any(i["type"] == "leaked-latex" for i in mv.text_mechanics(r"a \citep leaked")))


class TranslationTermTest(unittest.TestCase):
    def test_missing_term_and_hangul_in_code_flagged(self):
        orig = r"We use KV cache and GPU batching with \texttt{prefill} and attention. FLOPs matter."
        trans = r"우리는 GPU 배칭과 어텐션을 사용한다. \texttt{프리필}. KV 캐시는 언급 안함."
        f = mv.translation_term_check(orig, trans)
        terms = {x.get("term") for x in f if x["type"] == "term-missing-in-translation"}
        self.assertIn("FLOPs", terms)                 # translated/omitted from the translation
        self.assertIn("prefill", terms)
        self.assertTrue(any(x["type"] == "cjk-in-code" for x in f))   # 프리필 inside \texttt

    def test_preserved_terms_not_flagged(self):
        orig = r"GPU and attention throughput."
        trans = r"GPU와 attention throughput 처리량."   # tech terms kept English
        self.assertEqual([x for x in mv.translation_term_check(orig, trans)
                          if x["type"] == "term-missing-in-translation"], [])

    def test_japanese_token_in_code_flagged(self):   # CJK, not just Hangul
        f = mv.translation_term_check(r"use \texttt{prefill}", r"\texttt{プレフィル} を使う")
        self.assertTrue(any(x["type"] == "cjk-in-code" for x in f))

    def test_term_surviving_only_in_a_cite_key_does_not_mask_loss(self):
        orig = r"We build on KV cache."
        trans = r"우리는 이를 기반으로 한다 \cite{kvcache2023}."   # 'cache' only in the cite key
        terms = {x.get("term") for x in mv.translation_term_check(orig, trans)
                 if x["type"] == "term-missing-in-translation"}
        self.assertIn("KV cache", terms)             # prose-view ignores the preserved \cite key

    def test_headings_and_roman_numerals_are_not_terms(self):
        terms = mv.technical_terms("ABSTRACT INTRODUCTION CONCLUSION III VII and GPU KV")
        self.assertNotIn("ABSTRACT", terms); self.assertNotIn("III", terms)
        self.assertIn("GPU", terms); self.assertIn("KV", terms)

    def test_glossary_term_capitalized_in_translation_not_flagged(self):
        # 'Latency' (glossary hit, detected case-insensitively) present capitalized in the translation
        f = mv.translation_term_check("Latency is important.", "Latency 는 중요하다.")
        self.assertEqual([x for x in f if x["type"] == "term-missing-in-translation"], [])


class GlobalRemedyTest(unittest.TestCase):
    def test_injects_after_documentclass_and_is_idempotent(self):
        tex = r"\documentclass{article}" + "\n" + r"\begin{document}Hi\end{document}"
        new, applied = mv.global_layout_remedy_tex(tex)
        self.assertIn("emergencystretch", applied)
        self.assertIn(r"\emergencystretch", new)
        self.assertLess(new.index("emergencystretch"), new.index(r"\begin{document}"))  # in preamble
        _, applied2 = mv.global_layout_remedy_tex(new)
        self.assertEqual(applied2, [])                # no double-injection

    def test_no_url_when_hyperref_present(self):
        tex = r"\documentclass{article}\usepackage{hyperref}\begin{document}x\end{document}"
        _, applied = mv.global_layout_remedy_tex(tex)
        self.assertNotIn("url-hyphens", applied)      # avoid load-order conflict with hyperref

    def test_detects_packages_in_comma_list(self):
        # microtype + hyperref already loaded via a comma-list → do NOT re-inject / clash
        tex = r"\documentclass{article}\usepackage{amsmath,microtype,hyperref}\begin{document}x\end{document}"
        _, applied = mv.global_layout_remedy_tex(tex)
        self.assertNotIn("microtype", applied)        # already present in the list
        self.assertNotIn("url-hyphens", applied)      # hyperref in the list manages url


class VerifyFailLoudTest(unittest.TestCase):
    def test_verify_reports_and_is_not_clean_with_overfull(self):
        final = Path(tempfile.mkdtemp())
        (final / "paper.log").write_text("Overfull \\hbox (42.0pt too wide) at lines 10--12\n")
        (final / "paper.tex").write_text(r"\documentclass{article}\begin{document}x\end{document}")
        rep = mv.verify(str(final))                   # no pdftotext text → mechanics skipped
        self.assertFalse(rep["clean"])                # fail-loud on the overfull
        self.assertEqual(rep["overfull_count"], 1)
        self.assertEqual(rep["worst_overfull_pt"], 42.0)
        self.assertTrue((final / "paper.verification.json").exists())

    def test_missing_build_is_never_clean(self):
        final = Path(tempfile.mkdtemp())              # empty dir: no pdf, no log, no tex
        rep = mv.verify(str(final))
        self.assertFalse(rep["clean"])                # a failed/absent compile must NOT read clean
        self.assertFalse(rep["build_ok"])
        # a wrong --tex stem (pdf for that stem absent) is likewise not clean
        (final / "paper.pdf").write_bytes(b"%PDF")
        self.assertTrue(mv.verify(str(final))["build_ok"])   # correct stem now has a pdf


if __name__ == "__main__":
    unittest.main()
