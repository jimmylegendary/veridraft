"""L3a patent-first interlock tests (UC-3). Run from impl/:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veridraft.config import HarnessConfig
from veridraft.core.harness import Harness
from veridraft.core.models import GateStatus, InterlockStatus

EX = Path(__file__).resolve().parent.parent / "examples"
PATENT = str(EX / "bundle_patent" / "bundle.json")
DEMO = str(EX / "bundle_demo" / "bundle.json")
TEMPLATE = str(EX / "bundle_demo" / "template.tex")
GUIDELINES = str(EX / "bundle_demo" / "conference_guidelines.md")


class InterlockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig()
        cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def test_p3_held_blocks_then_release_lets_it_pass(self):
        self.h.import_bundle(PATENT)
        # pc1 has sufficient evidence (2 admissible) — it is blocked ONLY by the interlock.
        r1 = self.h.run_gate("demo-patent")
        self.assertEqual(r1.blocked_claim_ids, ["pc1"])
        pc1 = next(x for x in r1.results if x.claim_id == "pc1")
        self.assertTrue(any("interlock HELD" in reason for reason in pc1.reasons), pc1.reasons)
        self.assertIs(self.h.ledger.get_interlock_status("pc1"), InterlockStatus.HELD)

        # human releases the interlock (patent filed) → next gate lets it through
        self.h.release_interlock("pc1", reason="patent filed")
        self.assertIs(self.h.ledger.get_interlock_status("pc1"), InterlockStatus.RELEASED)
        r2 = self.h.run_gate("demo-patent")
        self.assertEqual(r2.passed_claim_ids, ["pc1"])

        # and it now flows all the way to a published PDF
        self.h.assemble_inputs("demo-patent", TEMPLATE, GUIDELINES, target_audience="public")
        self.h.draft("demo-patent")
        res = self.h.publish("demo-patent", target_audience="public")
        self.assertTrue(res["published"], res)

    def test_release_is_recorded_in_audit_chain(self):
        self.h.import_bundle(PATENT)
        self.h.run_gate("demo-patent")
        self.h.release_interlock("pc1", actor="human:cli", reason="filed")
        events = self.h.ledger.get_lifecycle_events("interlock:pc1")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["to_state"], "released")
        self.assertEqual(events[0]["actor"], "human:cli")
        self.assertTrue(self.h.ledger.verify_lifecycle())

    def test_non_patent_claim_has_no_interlock(self):
        self.h.import_bundle(DEMO)
        self.h.run_gate("demo-2026-07")
        self.assertIsNone(self.h.ledger.get_interlock("c1"))       # P1 → no interlock
        self.assertIs(self.h.ledger.get_interlock_status("c3"), InterlockStatus.HELD)  # P3

    def test_cannot_release_unknown_interlock(self):
        with self.assertRaises(RuntimeError):
            self.h.release_interlock("nope")


if __name__ == "__main__":
    unittest.main()
