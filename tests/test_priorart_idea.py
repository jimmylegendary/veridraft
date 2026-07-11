"""Tests for the prior-art search analysis + honesty invariants and the patent-idea completeness
gate, plus the offline degraded adapter paths.  python -m unittest discover -s tests"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from veridraft.core import patent_idea, priorart


class PriorArtAnalysisTest(unittest.TestCase):
    def test_single_reference_anticipation_102(self):
        pa = {"claim_elements": {"C1": ["a", "b", "c"]},
              "references": [{"id": "R1", "disclosed_elements": {"C1": {"a": "disclosed", "b": "disclosed", "c": "disclosed"}}}]}
        a = priorart.analyze(pa)
        self.assertEqual([r["reference"] for r in a["anticipation_102_risks"]], ["R1"])

    def test_multi_reference_obviousness_103(self):
        pa = {"claim_elements": {"C1": ["a", "b", "c"]},
              "references": [{"id": "R2", "disclosed_elements": {"C1": {"a": "disclosed"}}},
                             {"id": "R3", "disclosed_elements": {"C1": {"b": "disclosed", "c": "disclosed"}}}]}
        a = priorart.analyze(pa)
        self.assertEqual(a["anticipation_102_risks"], [])                 # no single ref covers all
        self.assertEqual(len(a["obviousness_103_combinations"]), 1)
        self.assertEqual(set(a["obviousness_103_combinations"][0]["references"]), {"R2", "R3"})
        self.assertIn("NOT established", a["obviousness_103_combinations"][0]["motivation_to_combine"])


class PriorArtHonestyTest(unittest.TestCase):
    def test_verdict_forced_and_mandatory_open_item_injected(self):
        pa = {"references": [], "open_items": ["the invention is novel and clear to file"]}
        priorart.enforce_honesty(pa)
        self.assertEqual(pa["verdict"], priorart.NON_CLEARANCE_VERDICT)
        self.assertTrue(any("PROFESSIONAL prior-art search" in i for i in pa["open_items"]))
        self.assertFalse(any("clear to file" in i for i in pa["open_items"]))   # clearance stripped

    def test_validate_rejects_missing_verdict_or_open_item(self):
        bad = {"references": [], "open_items": [], "verdict": "novel"}
        r = priorart.validate(bad)
        self.assertFalse(r["passed"])
        self.assertTrue(any("verdict must be" in f for f in r["failures"]))
        self.assertTrue(any("professional-search" in f for f in r["failures"]))

    def test_empty_search_is_not_novelty(self):
        pa = {"references": [], "open_items": []}
        priorart.enforce_honesty(pa)
        self.assertTrue(priorart.validate(pa)["passed"])                 # honest empty still valid
        self.assertTrue(any("not evidence of novelty" in i.lower() or "not novelty" in i.lower()
                            or "cannot establish novelty" in i.lower() for i in pa["open_items"]))


class PatentIdeaCompletenessTest(unittest.TestCase):
    def _complete_case(self):
        case = patent_idea.skeleton_case("X")
        for r in case["requirements"]:
            r["basis"] = "a grounded basis"
            r["grounded_in"] = ["idea:Inventive Concept"]
        return case

    def test_skeleton_is_incomplete(self):
        md = patent_idea.skeleton_markdown("X")
        rep = patent_idea.completeness(md, patent_idea.skeleton_case("X"))
        self.assertFalse(rep["passed"])                                  # empty basis/grounded_in

    def test_complete_memo_passes(self):
        md = patent_idea.skeleton_markdown("X")
        self.assertTrue(patent_idea.completeness(md, self._complete_case())["passed"])

    def test_missing_section_fails(self):
        md = patent_idea.skeleton_markdown("X").replace("## Advantages", "## Benefits")
        rep = patent_idea.completeness(md, self._complete_case())
        self.assertFalse(rep["passed"])
        self.assertTrue(any("Advantages" in f for f in rep["failures"]))

    def test_boundary_statement_required(self):
        md = patent_idea.skeleton_markdown("X").replace(patent_idea.BOUNDARY_STATEMENT, "we will file soon")
        rep = patent_idea.completeness(md, self._complete_case())
        self.assertFalse(rep["passed"])
        self.assertTrue(any("Boundary Statement" in f for f in rep["failures"]))

    def test_102_103_must_keep_open_items(self):
        case = self._complete_case()
        for r in case["requirements"]:
            if r["requirement"] == "novelty_102":
                r["open_items"] = []                                     # trying to settle novelty
        rep = patent_idea.completeness(patent_idea.skeleton_markdown("X"), case)
        self.assertFalse(rep["passed"])
        self.assertTrue(any("novelty" in f for f in rep["failures"]))


class DegradedAdapterTest(unittest.TestCase):
    def test_minimal_patent_writes_honest_priorart_and_skeleton_idea(self):
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        eng = MinimalPatentEngineAdapter()
        ws = Path(tempfile.mkdtemp())
        eng.search_prior_art(str(ws))
        pa = json.loads((ws / "inputs" / "priorart.json").read_text())
        self.assertEqual(pa["verdict"], priorart.NON_CLEARANCE_VERDICT)
        self.assertTrue(priorart.validate(pa)["passed"])
        eng.draft_idea(str(ws), "Test Invention")
        md = (ws / "idea" / "patent_idea.md").read_text()
        self.assertIn(patent_idea.BOUNDARY_STATEMENT, md)
        for sec in patent_idea.REQUIRED_SECTIONS:
            self.assertIn(f"## {sec}", md)


if __name__ == "__main__":
    unittest.main()
