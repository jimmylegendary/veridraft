"""Regression tests for the HATIR-run feedback fixes (feedback/from-hatir-run-2026-07.md).
Run from repo root:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import paperorchestra.run as run
import paperorchestra.patent_run as patent_run
import paperorchestra.providers as providers
from veridraft.config import HarnessConfig
from veridraft.core.harness import Harness

EX = Path(__file__).resolve().parent.parent / "examples" / "bundle_demo"


class RunnerFixesTest(unittest.TestCase):
    def test_verify_compile_catches_overfull_missing_ref(self):   # A2/A3/F2/B3
        tmp = tempfile.TemporaryDirectory(); final = Path(tmp.name); (final / "paper.log").write_text(
            "Overfull \\hbox (215.6pt too wide) in paragraph\n"
            "LaTeX Warning: File `figures/x.pdf' not found on input line 5.\n"
            "LaTeX Warning: Reference `fig:y' on page 2 undefined on input line 9.\n"
            "LaTeX Warning: Citation `smith' on page 3 undefined on input line 12.\n")
        issues = run._verify_compile(final)
        self.assertTrue(any("overfull" in i for i in issues))
        self.assertTrue(any("figures/x.pdf" in i for i in issues))
        self.assertTrue(any("\\ref" in i for i in issues))
        self.assertTrue(any("\\cite" in i for i in issues))
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
