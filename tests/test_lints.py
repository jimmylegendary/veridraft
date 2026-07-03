"""Deterministic patent/paper readiness gates (from the CAW-07 Loom review). Run from impl/:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import unittest

from veridraft.core import lints

# a Loom-like S1 independent that reproduces the reviewed blockers
LOOM_S1 = ("A method comprising: loading a hardware-twin description for an accelerator lacking a "
           "compiler backend and lacking fabricated silicon; deriving a concrete tile factor by "
           "evaluating a callable against the hardware twin; computing, analytically and without "
           "executing the tensor computation, a performance cost; and automatically re-tiling the "
           "same abstract tiling plan to a second accelerator having a different memory hierarchy.")


class PatentLintTest(unittest.TestCase):
    def setUp(self):
        self.ledger = {
            "independent_claims": [
                {"substrate": "S1: tile factors as callables of hardware symbols across "
                              "structurally different accelerators", "kind": "method", "text": LOOM_S1},
            ],
            "dependent_claims": [
                {"substrate": "S1", "kind": "dependent",
                 "text": "The method of claim 1, wherein the cost is computed without searching a "
                         "mapping space and without solving an optimization for the tile factor."},
            ],
        }
        self.deltas = {"S1": ["without searching a mapping space",
                              "without solving an optimization for the tile factor"]}
        self.prior_caps = ["lacking a compiler backend", "lacking fabricated silicon"]

    def _codes(self, findings):
        return {f.code for f in findings}

    def test_all_four_blockers_fire(self):
        f = lints.lint_patent_claims(self.ledger, self.deltas, self.prior_caps,
                                     enabled_scope=["GEMM", "LevelStack"],
                                     claimed_scope=["GEMM", "LevelGraph", "PIM", "softmax"])
        codes = self._codes(f)
        self.assertIn("P1", codes)   # ends at "compute a performance cost", no terminal effect
        self.assertIn("P3", codes)   # second accelerator baked into base independent
        self.assertIn("P2", codes)   # dispositive "without searching" delta only in a dependent
        self.assertIn("P6", codes)   # only a method independent — no system/CRM
        self.assertIn("P7", codes)   # "structurally different" relative term, no enumerated list
        self.assertIn("P5", codes)   # "lacking a compiler backend" = prior-art domain
        self.assertIn("P8", codes)   # PIM/LevelGraph/softmax claimed but only GEMM enabled

    def test_p4_oracle_exact_match_admission(self):
        ev = [{"id": "E1", "description": "DRAM traffic byte-exact (0.00%) vs the ZigZag oracle "
                                          "including partial-sum spill"}]
        f = lints.lint_evidence_admission(ev, cited_oracles=["ZigZag"])
        self.assertEqual([x.code for x in f], ["P4"])
        self.assertEqual(f[0].severity, "blocker")

    def test_p1_passes_when_terminal_effect_present(self):
        good = dict(self.ledger)
        good["independent_claims"] = [
            {"substrate": "S1", "kind": "method",
             "text": "A method comprising: computing a performance cost; and emitting a machine-readable "
                     "accelerator specification selected using the performance cost."},
            {"substrate": "S1", "kind": "system", "text": "A system ... perform the method of claim 1."},
            {"substrate": "S1", "kind": "computer-readable medium",
             "text": "A non-transitory computer-readable medium ... method of claim 1."},
        ]
        codes = self._codes(lints.lint_patent_claims(good))
        self.assertNotIn("P1", codes)
        self.assertNotIn("P6", codes)

    def test_readiness_not_filing_ready_and_fto_gate(self):
        r = lints.patent_readiness(
            self.ledger, evidence_items=[{"id": "E1", "description": "0.00% vs ZigZag"}],
            cited_oracles=["ZigZag"], fto_status="not_run", post_search_status="not_run",
            pdf_built=False, novelty_deltas=self.deltas, prior_art_capabilities=self.prior_caps)
        self.assertEqual(r["verdict"], "NOT-filing-ready")
        codes = {x["code"] for x in r["findings"]}
        self.assertIn("P12", codes)   # FTO + post-search gates
        self.assertIn("P11", codes)   # PDF not built


class PaperLintTest(unittest.TestCase):
    def test_d1_headline_needs_validation(self):
        f = lints.map_contributions_to_evidence([
            {"name": "single-op traffic", "headline": True, "evidence_grade": "validated"},
            {"name": "auto-retile DSE", "headline": True, "evidence_grade": "self-reported"},
        ])
        self.assertEqual([x.code for x in f], ["D1"])
        self.assertIn("auto-retile", f[0].target)

    def test_d4_spurious_precision(self):
        f = lints.lint_precision([{"value": "2214.6", "grade": "exploration"},
                                  {"value": "1350", "grade": "validated"}])
        self.assertEqual([x.code for x in f], ["D4"])


class ReviewValidateTest(unittest.TestCase):
    def test_rejects_stub_review(self):
        stub = {"novelty": "test", "soundness": "test", "clarity": "test", "verdict": "test"}
        f = lints.validate_review(stub)
        codes = {x.code for x in f}
        self.assertIn("R1", codes)   # sentinel "test"
        self.assertIn("R2", codes)   # all identical
        self.assertIn("R3", codes)   # no subscores

    def test_accepts_real_review(self):
        real = {"summary": "The IR is validated against ZigZag to 0.00%.",
                "weaknesses": "No rank-correlation to a simulator.",
                "subscores": {"novelty": 4, "soundness": 3, "clarity": 4, "significance": 3}}
        self.assertEqual(lints.validate_review(real), [])


if __name__ == "__main__":
    unittest.main()
