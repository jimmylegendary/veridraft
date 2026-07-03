"""L3b patent-path tests (ADR-0004). Run from impl/:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veridraft.config import HarnessConfig
from veridraft.core.harness import Harness

EX = Path(__file__).resolve().parent.parent / "examples"
DEMO = str(EX / "bundle_demo" / "bundle.json")
PATENT = str(EX / "bundle_patent" / "bundle.json")


class PatentTest(unittest.TestCase):
    def _harness(self):
        cfg = HarnessConfig()
        cfg.gate_profile = "us-utility-patent"   # P3 admitted (not patent-first-blocked)
        cfg.data_dir = self.tmp.name
        return Harness(cfg)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = self._harness()

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def test_patentability_recommend_on_method_claims(self):
        self.h.import_bundle(DEMO)
        self.h.run_gate("demo-2026-07")          # c1(P1)+c2(P2) pass; c3(P3) blocked by evidence
        s = self.h.patentability("demo-2026-07")
        self.assertEqual(s["verdict"], "recommend")
        self.assertEqual(sorted(s["substrate_claims"]), ["c1", "c2"])

    def test_draft_patent_produces_pdf_with_claims(self):
        self.h.import_bundle(DEMO)
        self.h.run_gate("demo-2026-07")
        d = self.h.draft_patent("demo-2026-07", title="Adaptive Serving Scheduler")
        self.assertTrue(d["needs_human"])
        self.assertGreaterEqual(d["independent_claims"], 1)   # method + system + CRM
        pdf = Path(d["patent_pdf"])
        self.assertEqual(pdf.read_bytes()[:5], b"%PDF-")
        # a patent draft must carry open items a human/attorney resolves (102/101/§112)
        self.assertTrue(any("102" in o or "101" in o for o in d["open_items"]))
        # claims section really contains an independent method claim
        tex = Path(d["patent_tex"]).read_text(encoding="utf-8")
        self.assertIn("method", tex.lower())

    def test_no_go_when_no_patentable_substrate(self):
        # bundle_patent has only a P3 projection claim → no P1/P2 substrate → NO-GO
        self.h.import_bundle(PATENT)
        self.h.run_gate("demo-patent")
        self.assertEqual(self.h.patentability("demo-patent")["verdict"], "no-go")
        with self.assertRaises(RuntimeError):
            self.h.draft_patent("demo-patent")

    def test_patent_review_requires_attorney(self):
        self.h.import_bundle(DEMO)
        self.h.run_gate("demo-2026-07")
        self.h.draft_patent("demo-2026-07")
        r = self.h.patent_review("demo-2026-07")
        self.assertEqual(r["verdict"], "attorney-review-required")
        self.assertFalse(all(c["ok"] for c in r["checklist"]))  # legal checks stay open


if __name__ == "__main__":
    unittest.main()
