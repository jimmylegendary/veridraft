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


class PriorArtRobustnessTest(unittest.TestCase):
    def test_malformed_disclosed_elements_does_not_crash(self):
        # a model may emit disclosed_elements / its per-claim value / claim_elements as non-dicts
        for pa in (
            {"claim_elements": {"C1": ["a", "b"]}, "references": [{"id": "R", "disclosed_elements": "oops"}]},
            {"claim_elements": {"C1": ["a"]}, "references": [{"id": "R", "disclosed_elements": {"C1": "disclosed"}}]},
            {"claim_elements": ["not", "a", "dict"], "references": [{"id": "R"}]},
            {"claim_elements": {"C1": ["a"]}, "references": "not a list"},
        ):
            a = priorart.analyze(pa)                                     # must not raise
            self.assertIn("anticipation_102_risks", a)

    def test_low_risk_clearance_is_still_stripped(self):
        pa = {"references": [], "open_items": ["free to file, low risk", "the invention is patentable"]}
        priorart.enforce_honesty(pa)
        self.assertFalse(any("free to file" in i for i in pa["open_items"]))   # escape hatch removed
        self.assertFalse(any("is patentable" in i for i in pa["open_items"]))

    def test_legit_gap_items_mentioning_patentable_survive(self):
        pa = {"references": [], "open_items": ["assess §101 patentable subject matter with counsel",
                                               "conduct a novelty search"]}
        priorart.enforce_honesty(pa)
        self.assertTrue(any("patentable subject matter" in i for i in pa["open_items"]))
        self.assertTrue(any("novelty search" in i for i in pa["open_items"]))


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

    def test_forbidden_regex_does_not_strip_unclear_gap_items(self):
        pa = {"references": [], "open_items": [
            "the invention is novel and clear to file",          # clearance → must be stripped
            "the scope of element X is unclear",                 # legit gap → must be kept
            "§102 anticipation risk on R1"]}                     # risk → must be kept
        priorart.enforce_honesty(pa)
        self.assertFalse(any("clear to file" in i for i in pa["open_items"]))
        self.assertTrue(any("unclear" in i for i in pa["open_items"]))
        self.assertTrue(any("anticipation risk" in i for i in pa["open_items"]))

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

    def test_boundary_as_blockquote_still_passes(self):
        md = patent_idea.skeleton_markdown("X").replace(
            patent_idea.BOUNDARY_STATEMENT, "> " + patent_idea.BOUNDARY_STATEMENT)   # Markdown blockquote
        self.assertTrue(patent_idea.completeness(md, self._complete_case())["passed"])

    def test_non_string_basis_does_not_crash(self):
        case = self._complete_case()
        case["requirements"][0]["basis"] = {"weird": "object"}          # model emits a non-string basis
        rep = patent_idea.completeness(patent_idea.skeleton_markdown("X"), case)   # must not raise
        self.assertFalse(rep["passed"])                                 # treated as empty basis → fail

    def test_102_103_must_keep_open_items(self):
        case = self._complete_case()
        for r in case["requirements"]:
            if r["requirement"] == "novelty_102":
                r["open_items"] = []                                     # trying to settle novelty
        rep = patent_idea.completeness(patent_idea.skeleton_markdown("X"), case)
        self.assertFalse(rep["passed"])
        self.assertTrue(any("novelty" in f for f in rep["failures"]))


class PatentPreflightTest(unittest.TestCase):
    def _harness(self):
        import tempfile as _tf
        from veridraft.config import HarnessConfig
        from veridraft.core.harness import Harness
        self._tmp = _tf.TemporaryDirectory()
        cfg = HarnessConfig(); cfg.gate_profile = "us-utility-patent"; cfg.data_dir = self._tmp.name
        h = Harness(cfg)
        h.import_bundle("examples/bundle_demo/bundle.json"); h.run_gate("demo-2026-07")
        return h

    def test_preflight_not_ready_before_pre_steps(self):
        h = self._harness()
        try:
            r = h.patent_preflight("demo-2026-07")
            self.assertFalse(r["ready"])
            self.assertFalse(r["prior_art_run"])
            self.assertFalse(r["idea_complete"])
            self.assertTrue(any("prior-art" in g for g in r["gaps"]))
        finally:
            h.close(); self._tmp.cleanup()

    def test_prior_art_flips_the_flag_and_draft_surfaces_gaps(self):
        h = self._harness()
        try:
            h.patent_prior_art("demo-2026-07")
            self.assertTrue(h.patent_preflight("demo-2026-07")["prior_art_run"])
            d = h.draft_patent("demo-2026-07", title="X")
            self.assertIn("pre_draft", d)
            self.assertFalse(d["pre_draft"]["ready"])          # idea still incomplete
            self.assertTrue(any(o.startswith("PRE-DRAFT:") for o in d["open_items"]))
        finally:
            h.close(); self._tmp.cleanup()


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
