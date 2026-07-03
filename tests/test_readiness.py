"""Submission-readiness tests (ADR-0009): venue disclosure + prohibited-venue guard.
Run from impl/:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veridraft.config import HarnessConfig
from veridraft.core.harness import Harness

EX = Path(__file__).resolve().parent.parent / "examples" / "bundle_demo"
DEMO = str(EX / "bundle.json")
TEMPLATE = str(EX / "template.tex")
GUIDELINES = str(EX / "conference_guidelines.md")


class ReadinessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig()
        cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)
        self.h.import_bundle(DEMO)
        self.h.run_gate("demo-2026-07")
        self.h.assemble_inputs("demo-2026-07", TEMPLATE, GUIDELINES, target_audience="public")
        self.h.draft("demo-2026-07")

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def test_disclosure_venue_generates_text_not_blocked(self):
        r = self.h.check_submission_readiness("demo-2026-07", "neurips")
        self.assertEqual(r["policy_class"], "disclosure")
        self.assertTrue(r["disclosure_required"])
        self.assertIn("authors", r["disclosure_text"].lower())
        self.assertFalse(r["blocked"])
        self.assertFalse(r["human_signoff_required"])

    def test_prohibited_venue_blocks_and_requires_signoff(self):
        r = self.h.check_submission_readiness("demo-2026-07", "science")
        self.assertEqual(r["policy_class"], "prohibited")
        self.assertTrue(r["blocked"])
        self.assertTrue(r["human_signoff_required"])
        # publish targeting the prohibited venue is default-denied until sign-off
        res = self.h.publish("demo-2026-07", target_audience="public", venue="science")
        self.assertFalse(res["published"])
        self.assertEqual(res["reason"], "READINESS")

    def test_human_signoff_unblocks_publish(self):
        self.h.check_submission_readiness("demo-2026-07", "science")
        self.h.sign_off_readiness("demo-2026-07", "science", user="human:jimmy")
        res = self.h.publish("demo-2026-07", target_audience="public", venue="science")
        self.assertTrue(res["published"], res)   # readiness guard cleared; egress then passed

    def test_publish_with_venue_requires_prior_readiness_check(self):
        with self.assertRaises(Exception):
            self.h.publish("demo-2026-07", target_audience="public", venue="neurips")


if __name__ == "__main__":
    unittest.main()
