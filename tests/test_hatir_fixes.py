"""Regression tests for the HATIR-run feedback fixes (feedback/from-hatir-run-2026-07.md).
Run from repo root:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import shutil

import paperorchestra.run as run
import paperorchestra.patent_run as patent_run
import paperorchestra.translate_run as translate_run
import paperorchestra.providers as providers
from veridraft.config import HarnessConfig
from veridraft.core.harness import Harness

EX = Path(__file__).resolve().parent.parent / "examples" / "bundle_demo"


class RunnerFixesTest(unittest.TestCase):
    def test_verify_compile_overfull_and_missing_from_log(self):   # A2/F2/B3
        # overfull + missing-figure are LOG truths; cite/ref 'undefined' lines in the log are NOT
        # (a healthy multi-pass build emits 100s before bibtex) → they must NOT surface here (G1).
        tmp = tempfile.TemporaryDirectory(); final = Path(tmp.name); (final / "paper.log").write_text(
            "Overfull \\hbox (215.6pt too wide) in paragraph\n"
            "LaTeX Warning: File `figures/x.pdf' not found on input line 5.\n"
            "LaTeX Warning: Citation `smith' on page 3 undefined on input line 12.\n")
        issues = run._verify_compile(final)
        self.assertTrue(any("overfull" in i for i in issues))
        self.assertTrue(any("figures/x.pdf" in i for i in issues))
        self.assertFalse(any("cite" in i for i in issues))   # NOT from the log (false-positive source)
        tmp.cleanup()

    def test_bibtex_skip_and_pdf_marker_detection(self):   # G1
        tmp = tempfile.TemporaryDirectory(); final = Path(tmp.name)
        (final / "paper.tex").write_text(r"\documentclass{article}\begin{document}"
                                         r"\cite{a}\bibliography{refs}\end{document}")
        orig = run._pdf_text
        run._pdf_text = lambda pdf: ""            # no PDF text yet
        try:
            issues = run._verify_compile(final)   # \cite but no paper.bbl → bibtex-skip
            self.assertTrue(any(i.startswith(run._BIBTEX_SKIP) for i in issues))
            (final / "paper.bbl").write_text(r"\begin{thebibliography}{1}\bibitem{a}A.\end{thebibliography}")
            run._pdf_text = lambda pdf: "body text with an unresolved [?] citation and a ?? ref"
            issues = run._verify_compile(final)   # bbl now present → judge the FINAL PDF text
            self.assertFalse(any(i.startswith(run._BIBTEX_SKIP) for i in issues))
            self.assertTrue(any("[?]" in i for i in issues))
            self.assertTrue(any("??" in i for i in issues))
        finally:
            run._pdf_text = orig
        tmp.cleanup()

    def test_healthy_build_has_no_cite_false_positive(self):   # G1
        tmp = tempfile.TemporaryDirectory(); final = Path(tmp.name)
        (final / "paper.tex").write_text(r"\documentclass{article}\begin{document}\cite{a}\bibliography{refs}\end{document}")
        (final / "paper.bbl").write_text(r"\begin{thebibliography}{1}\bibitem{a}A.\end{thebibliography}")
        (final / "paper.log").write_text("Package natbib Warning: Citation `a' undefined\n" * 164)
        orig = run._pdf_text
        run._pdf_text = lambda pdf: "a clean paper body, all citations resolved [1]"
        try:
            self.assertEqual(run._verify_compile(final), [])   # 164 log warnings → 0 issues
        finally:
            run._pdf_text = orig
        tmp.cleanup()

    def test_self_fix_routes_bibtex_skip_to_recompile_not_backend(self):   # G1
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "final").mkdir()
        (ws / "final" / "paper.tex").write_bytes(b"ORIGINAL")
        seq = iter([[f"{run._BIBTEX_SKIP}: no bbl"], []])   # after recompile → clean
        calls = {"dispatch": 0, "compile": 0}
        orig = (run._verify_compile, run.compile_pdf, run.providers.dispatch)
        run._verify_compile = lambda final: next(seq, [])
        run.compile_pdf = lambda w: calls.__setitem__("compile", calls["compile"] + 1)
        run.providers.dispatch = lambda *a, **k: calls.__setitem__("dispatch", calls["dispatch"] + 1)
        try:
            run._self_fix({}, ws, 2)
        finally:
            run._verify_compile, run.compile_pdf, run.providers.dispatch = orig
        self.assertEqual(calls["dispatch"], 0)   # bibtex-skip never goes to the backend
        self.assertGreaterEqual(calls["compile"], 1)
        tmp.cleanup()

    def test_stage_all_figure_types_and_dedup(self):   # B3 / A1
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "figures").mkdir()
        (ws / "figures" / "a.pdf").write_bytes(b"SAME"); (ws / "figures" / "b.png").write_bytes(b"SAME")
        (ws / "figures" / "c.png").write_bytes(b"DIFF")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            run._stage_figures(ws, ws / "final")
        staged = {p.name for p in (ws / "final" / "figures").glob("*")}
        self.assertEqual(staged, {"a.pdf", "b.png", "c.png"})   # all types, under figures/
        self.assertIn("byte-identical", buf.getvalue())          # duplicate warned
        tmp.cleanup()

    def test_config_warns_on_top_level_engine_spec(self):   # D1
        tmp = tempfile.TemporaryDirectory(); p = Path(tmp.name) / "c.json"
        p.write_text('{"writing_engine": {"id": "paperorchestra"}}')
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            HarnessConfig.load(str(p))
        self.assertIn("TOP LEVEL and IGNORED", buf.getvalue())
        tmp.cleanup()

    def test_codex_write_access_is_explicit_optin(self):   # C1
        calls = {}
        orig = providers._run
        providers._run = lambda argv, *a, **k: calls.setdefault("argv", argv) or "ok"
        try:
            providers._codex({"model": "m"}, "P", "/tmp", 60)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", calls["argv"])  # default: no
            calls.clear()
            providers._codex({"codex_full_access": True}, "P", "/tmp", 60)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", calls["argv"])     # opt-in: yes
        finally:
            providers._run = orig

    def test_patent_run_idempotent_skip(self):   # E2
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "final").mkdir()
        (ws / "final" / "patent.tex").write_text(
            r"\documentclass{article}\begin{document}" + ("A complete patent draft body. " * 12)
            + r"\end{document}")   # >200 bytes → counts as a complete existing draft
        called = []
        orig = patent_run.providers.dispatch
        patent_run.providers.dispatch = lambda *a, **k: called.append(1)
        try:
            patent_run.draft({"backend": "x"}, ws, force=False)   # existing tex → no LLM dispatch
            self.assertEqual(called, [])
        finally:
            patent_run.providers.dispatch = orig
        tmp.cleanup()


class VisualCheckTest(unittest.TestCase):   # G4 / A1 / F3
    def test_perceptual_near_dup_detection(self):
        from PIL import Image
        tmp = tempfile.TemporaryDirectory(); final = Path(tmp.name); (final / "figures").mkdir()
        grad = Image.new("L", (64, 64))
        for x in range(64):
            for y in range(64):
                grad.putpixel((x, y), x * 4)          # monotone gradient
        grad.save(final / "figures" / "a.png")
        grad.resize((48, 48)).save(final / "figures" / "b.png")     # resized near-dup (byte hash misses)
        Image.new("L", (64, 64), color=255).save(final / "figures" / "c.png")   # distinct (blank)
        dups = run._perceptual_near_dups(final)
        self.assertTrue(any("a.png" in d and "b.png" in d for d in dups))
        self.assertFalse(any("c.png" in d for d in dups))
        tmp.cleanup()

    def test_vlm_findings_noop_without_vision_model(self):
        from PIL import Image
        tmp = tempfile.TemporaryDirectory(); final = Path(tmp.name); (final / "figures").mkdir()
        Image.new("L", (8, 8)).save(final / "figures" / "a.png")
        self.assertEqual(run._vlm_findings({}, final), [])           # no vision_model → no dispatch
        tmp.cleanup()


class SelfFixLoopTest(unittest.TestCase):   # F4
    def _run(self, verify_seq, dispatch_effect=None, max_iters=2):
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "final").mkdir()
        (ws / "final" / "paper.tex").write_bytes(b"ORIGINAL")
        seq = iter(verify_seq); calls = {"n": 0}
        orig = (run._verify_compile, run.compile_pdf, run.providers.dispatch)
        run._verify_compile = lambda final: next(seq, [])
        run.compile_pdf = lambda w: (w / "final" / "paper.pdf")
        def fake_dispatch(cfg, prompt, cwd, timeout=0):
            calls["n"] += 1
            if dispatch_effect:
                dispatch_effect(ws)
            return "ok"
        run.providers.dispatch = fake_dispatch
        try:
            run._self_fix({}, ws, max_iters)
        finally:
            run._verify_compile, run.compile_pdf, run.providers.dispatch = orig
        return ws, calls, tmp

    def test_converges_then_stops(self):
        _, calls, tmp = self._run([["a", "b"], [], []])
        self.assertEqual(calls["n"], 1)          # fixed in one dispatch, then clean
        tmp.cleanup()

    def test_reverts_iteration_that_regresses(self):
        ws, calls, tmp = self._run([["a"], ["a", "b", "c"]],
                                    dispatch_effect=lambda w: (w / "final" / "paper.tex").write_bytes(b"BROKEN"))
        self.assertEqual((ws / "final" / "paper.tex").read_bytes(), b"ORIGINAL")   # made worse → reverted
        self.assertEqual(calls["n"], 1)
        tmp.cleanup()

    def test_stops_when_issues_do_not_decrease(self):
        _, calls, tmp = self._run([["a", "b"], ["a", "b"], ["a", "b"]])
        self.assertEqual(calls["n"], 1)          # 2nd iter sees no decrease → stop, no re-dispatch
        tmp.cleanup()


class TranslateTest(unittest.TestCase):   # F5 multilingual
    def test_resolve_lang_code_name_native_unknown(self):
        self.assertEqual(translate_run._resolve_lang("ko")[0], "ko")
        self.assertEqual(translate_run._resolve_lang("Korean")[0], "ko")
        self.assertEqual(translate_run._resolve_lang("한국어")[0], "ko")
        self.assertEqual(translate_run._resolve_lang("Deutsch")[0], "de")
        code, ent = translate_run._resolve_lang("Klingon")   # unknown → carried through, non-Latin path
        self.assertEqual(code, "klingon")
        self.assertEqual(ent[2], "other")

    def test_translate_idempotent_skip(self):
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); final = ws / "final"; final.mkdir()
        (final / "paper.tex").write_text(r"\documentclass{article}\begin{document}x\end{document}")
        (final / "paper-ko.tex").write_text("한글 " * 80)   # >200 bytes → complete existing translation
        called = []
        origd, origc = translate_run.providers.dispatch, translate_run._compile
        translate_run.providers.dispatch = lambda *a, **k: called.append(1)
        translate_run._compile = lambda final, stem, prefer_lualatex: None
        try:
            translate_run.translate({"backend": "x"}, ws, "ko", "paper", force=False)
            self.assertEqual(called, [])         # existing translation → no LLM dispatch
        finally:
            translate_run.providers.dispatch, translate_run._compile = origd, origc
        tmp.cleanup()

    def test_technical_terms_kept_english_in_prompt(self):
        p = translate_run._translate_prompt(Path("/ws"), "paper", "paper-ko", "Korean", "한국어", "cjk")
        self.assertIn("KEEP established technical terms", p)
        self.assertIn("PRESERVE ALL LaTeX EXACTLY", p)
        self.assertIn("IDIOMATIC", p)

    @unittest.skipUnless(shutil.which("lualatex") and shutil.which("latexmk"), "lualatex/latexmk absent")
    def test_translate_compiles_cjk_via_idempotent_path(self):
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); final = ws / "final"; final.mkdir()
        (final / "paper.tex").write_text(r"\documentclass{article}\begin{document}English.\end{document}")
        (final / "paper-ko.tex").write_text(
            r"\documentclass{article}\usepackage[numbers]{natbib}\usepackage{hyperref}"
            r"\begin{document}" + "한국어 초록 매우 긴 문단 테스트 " * 6 +
            r"\citep{k1} attention throughput \bibliographystyle{plainnat}\bibliography{refs}\end{document}")
        (final / "refs.bib").write_text("@book{k1,title={T},author={A,B},year={2020}}\n")
        called = []
        orig = translate_run.providers.dispatch
        translate_run.providers.dispatch = lambda *a, **k: called.append(1)
        try:
            rc = translate_run.translate({"backend": "x"}, ws, "ko", "paper", force=False)
        finally:
            translate_run.providers.dispatch = orig
        self.assertEqual(called, [])
        self.assertTrue((final / "paper-ko.pdf").exists())      # compiled with lualatex+kotex
        self.assertNotIn("[?]", translate_run.po_run._pdf_text(final / "paper-ko.pdf"))
        self.assertEqual(rc, 0)
        tmp.cleanup()


class ReviewProvenanceTest(unittest.TestCase):
    def test_unsigned_bundle_is_na_not_failed(self):   # E3
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(str(EX / "bundle.json")); h.run_gate("demo-2026-07")
        h.assemble_inputs("demo-2026-07", str(EX / "template.tex"),
                          str(EX / "conference_guidelines.md"), target_audience="public")
        h.draft("demo-2026-07")
        prov = [c for c in h.run_review("demo-2026-07")["checklist"] if c["item"].startswith("bundle provenance")]
        self.assertEqual(len(prov), 1)
        self.assertTrue(prov[0]["ok"])                    # not a scary [x]
        self.assertIn("self-authored", prov[0]["item"])   # unsigned demo bundle → N/A
        h.close(); tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
