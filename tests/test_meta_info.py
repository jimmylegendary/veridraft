"""Tests for the meta-info schema + forced elicitation step (ask-the-user gate).
Run from repo root:  python -m unittest discover -s tests"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import paperorchestra.meta_info as mi
import paperorchestra.patent_run as patent_run


def _ws(meta: dict | None = None) -> tuple[tempfile.TemporaryDirectory, Path]:
    tmp = tempfile.TemporaryDirectory()
    ws = Path(tmp.name)
    (ws / "inputs").mkdir()
    if meta is not None:
        (ws / "inputs" / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp, ws


PAPER_OK = {"paper_type": "research", "venue": "MLSys 2027", "anonymize": True,
            "authors": [{"name": "J. Lee", "affiliation": "X"}], "audience": "systems researchers"}
PATENT_OK = {"inventors": ["Jimmy Lee"], "assignee": "self", "jurisdictions": ["US", "KR"],
             "filing_type": "provisional", "known_prior_art": "none known", "public_disclosure": "none"}


class MetaGateTest(unittest.TestCase):
    def test_missing_meta_blocks_with_questions(self):
        tmp, ws = _ws(None)
        with self.assertRaises(SystemExit) as cm:
            mi.enforce(ws, "paper", log=lambda *a: None)
        self.assertEqual(cm.exception.code, 3)
        qs = json.loads((ws / "meta.questions.json").read_text())
        self.assertEqual({q["key"] for q in qs},
                         {"paper_type", "venue", "anonymize", "authors", "audience"})   # deadline optional, derive absent
        self.assertTrue(all(q["question"] and q["why"] for q in qs))
        tmp.cleanup()

    def test_complete_meta_passes_silently(self):
        tmp, ws = _ws(PAPER_OK)
        mi.enforce(ws, "paper", log=lambda *a: None)   # no raise
        self.assertFalse((ws / "meta.questions.json").exists())
        tmp.cleanup()

    def test_partial_meta_asks_only_the_gaps(self):
        tmp, ws = _ws({"paper_type": "research", "venue": "MLSys", "anonymize": False})
        with self.assertRaises(SystemExit):
            mi.enforce(ws, "paper", log=lambda *a: None)
        keys = {q["key"] for q in json.loads((ws / "meta.questions.json").read_text())}
        self.assertEqual(keys, {"authors", "audience"})   # already-given info passes
        tmp.cleanup()

    def test_type_and_choice_validation(self):
        bad = dict(PATENT_OK, filing_type="maybe", inventors="Jimmy")   # wrong choice + not a list
        tmp, ws = _ws(bad)
        keys = {f["key"] for f in mi.check_meta(ws, "patent")["missing"]}
        self.assertIn("filing_type", keys)
        self.assertIn("inventors", keys)
        tmp.cleanup()

    def test_paper_type_accepts_case_and_synonyms(self):
        # "Survey"/"STUDY"/"review"/"Research" must PASS the gate (matching what preflight accepts),
        # not loop the meta question forever; a real wrong value still fails.
        for val in ("Survey", "STUDY", "review", "Research"):
            tmp, ws = _ws(dict(PAPER_OK, paper_type=val))
            self.assertNotIn("paper_type", {f["key"] for f in mi.check_meta(ws, "paper")["missing"]}, val)
            tmp.cleanup()
        tmp, ws = _ws(dict(PAPER_OK, paper_type="poem"))
        self.assertIn("paper_type", {f["key"] for f in mi.check_meta(ws, "paper")["missing"]})
        tmp.cleanup()

    def test_patent_questions_cover_legal_facts(self):
        tmp, ws = _ws(None)
        keys = {f["key"] for f in mi.check_meta(ws, "patent")["missing"]}
        self.assertEqual(keys, {"inventors", "assignee", "jurisdictions", "filing_type",
                                "known_prior_art", "public_disclosure"})
        tmp.cleanup()

    def test_prompt_block_shapes_writing(self):
        tmp, ws = _ws(PAPER_OK)
        block = mi.prompt_block(ws, "paper")
        self.assertIn("DOUBLE-BLIND", block)              # anonymize=True → strip authors
        self.assertIn("MLSys 2027", block)
        self.assertIn("Derive", block)                    # derivable fields delegated, grounded
        tmp.cleanup()

    def test_patent_run_gates_on_meta_before_dispatch(self):
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name)
        (ws / "inputs").mkdir()
        (ws / "inputs" / "invention.md").write_text("# Invention")
        (ws / "inputs" / "claims.json").write_text("[]")
        called = []
        orig = patent_run.providers.dispatch
        patent_run.providers.dispatch = lambda *a, **k: called.append(1)
        try:
            with self.assertRaises(SystemExit) as cm:
                patent_run.draft({"backend": "x"}, ws, force=True)
            self.assertEqual(cm.exception.code, 3)        # blocked BEFORE any LLM call
            self.assertEqual(called, [])
        finally:
            patent_run.providers.dispatch = orig
        tmp.cleanup()

    def test_patent_run_passes_with_meta_and_injects_it(self):
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name)
        (ws / "inputs").mkdir()
        (ws / "inputs" / "invention.md").write_text("# Invention")
        (ws / "inputs" / "claims.json").write_text("[]")
        (ws / "inputs" / "meta.json").write_text(json.dumps(PATENT_OK))
        seen = {}
        orig = patent_run.providers.dispatch
        def fake(cfg, prompt, cwd, timeout=0):
            seen["prompt"] = prompt
            (ws / "final").mkdir(exist_ok=True)
            (ws / "final" / "patent.tex").write_text(r"\documentclass{article}\begin{document}d\end{document}")
        patent_run.providers.dispatch = fake
        try:
            patent_run.draft({"backend": "x"}, ws, force=True)
        finally:
            patent_run.providers.dispatch = orig
        self.assertIn("Jimmy Lee", seen["prompt"])        # meta reached the drafting prompt
        self.assertIn("public_disclosure", seen["prompt"])
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
