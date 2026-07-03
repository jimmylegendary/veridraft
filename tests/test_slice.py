"""Smoke test for the Veridraft `gated claim → PDF` vertical slice (stdlib unittest).

Run from the impl/ directory:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veridraft.config import HarnessConfig
from veridraft.core.assemble import UngatedClaimError
from veridraft.core.harness import Harness
from veridraft.core.models import Evidence, EvidenceKind

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "bundle_demo"
BUNDLE = str(EXAMPLES / "bundle.json")
TEMPLATE = str(EXAMPLES / "template.tex")
GUIDELINES = str(EXAMPLES / "conference_guidelines.md")


class SliceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig()
        cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def test_gate_passes_c1_c2_blocks_c3(self):
        self.h.import_bundle(BUNDLE)
        report = self.h.run_gate("demo-2026-07")
        self.assertEqual(sorted(report.passed_claim_ids), ["c1", "c2"])
        self.assertEqual(report.blocked_claim_ids, ["c3"])
        # c3 blocked because its only evidence is generated text (the hard invariant).
        c3 = next(r for r in report.results if r.claim_id == "c3")
        self.assertTrue(any("generated" in reason for reason in c3.reasons))

    def test_ungated_claim_cannot_be_assembled(self):
        self.h.import_bundle(BUNDLE)
        # Skip the gate on purpose: no gated set exists → assembly must refuse.
        with self.assertRaises(Exception):
            self.h.assemble_inputs("demo-2026-07", TEMPLATE, GUIDELINES)

    def test_end_to_end_produces_valid_pdf(self):
        self.h.import_bundle(BUNDLE)
        self.h.run_gate("demo-2026-07")
        self.h.assemble_inputs("demo-2026-07", TEMPLATE, GUIDELINES, title="Veridraft Demo Paper")
        result = self.h.draft("demo-2026-07")
        pdf = Path(result["paper_pdf"])
        self.assertTrue(pdf.exists(), "engine did not produce a PDF")
        self.assertEqual(pdf.read_bytes()[:5], b"%PDF-", "output is not a valid PDF")
        review = self.h.run_review("demo-2026-07")
        self.assertEqual(review["verdict"], "ready_for_human_review")

    def test_gate_requires_experimental_results(self):
        # the tool demands an evaluation — a bundle with no results is refused at the
        # gate rather than letting the engine fabricate one.
        import json
        bundle = {
            "bundle_id": "no-results", "boundary": "public",
            "claims": [{"claim_id": "n1", "type": "P2", "boundary": "public",
                        "visibility": "team", "statement": "a claim with no evaluation",
                        "evidence": [{"id": "e", "kind": "caw02_evidence", "ref": "caw02://x"}]}],
            "results": [],
        }
        p = Path(self.tmp.name) / "no_results.json"
        p.write_text(json.dumps(bundle), encoding="utf-8")
        self.h.import_bundle(str(p))
        with self.assertRaises(Exception):
            self.h.run_gate("no-results")

    def test_source_artifact_is_admissible_evidence(self):
        # the "code + design docs → paper" case: a repo file @ commit is a concrete,
        # resolvable artifact and counts as evidence; generated prose still does not.
        self.assertTrue(
            Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "repo://docs/CORE_MODEL.md@abc123").is_admissible())
        self.assertFalse(
            Evidence("e", EvidenceKind.GENERATED_TEXT, "some prose").is_admissible())

    def test_assemble_only_uses_gated_claims(self):
        self.h.import_bundle(BUNDLE)
        self.h.run_gate("demo-2026-07")
        manifest = self.h.assemble_inputs("demo-2026-07", TEMPLATE, GUIDELINES)
        self.assertEqual(sorted(manifest["provenance"]["claim_ids"]), ["c1", "c2"])
        idea = Path(manifest["idea"]).read_text(encoding="utf-8")
        self.assertNotIn("future accelerator", idea)  # c3 (blocked) must not leak in


if __name__ == "__main__":
    unittest.main()
