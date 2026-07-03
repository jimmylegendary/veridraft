"""L6 confidentiality gate tests (ADR-0007). Run from impl/:
    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veridraft.config import HarnessConfig
from veridraft.core.confidentiality import Ruleset, classify, decide, scan_strings
from veridraft.core.harness import Harness
from veridraft.core.ledger import canonical_digest
from veridraft.core.models import (
    Audience,
    Boundary,
    Claim,
    ClaimType,
    RawBundle,
    Visibility,
)

EX = Path(__file__).resolve().parent.parent / "examples"
DEMO = str(EX / "bundle_demo" / "bundle.json")
INTERNAL = str(EX / "bundle_internal" / "bundle.json")
REDACTION = str(EX / "bundle_redaction" / "bundle.json")
TEMPLATE = str(EX / "bundle_demo" / "template.tex")
GUIDELINES = str(EX / "bundle_demo" / "conference_guidelines.md")


class DecideMatrixTest(unittest.TestCase):
    def test_decide_matrix(self):
        self.assertTrue(decide(Boundary.PUBLIC, Visibility.TEAM, Audience.PUBLIC).allow)
        self.assertFalse(decide(Boundary.INTERNAL, Visibility.TEAM, Audience.PUBLIC).allow)
        self.assertTrue(decide(Boundary.INTERNAL, Visibility.TEAM, Audience.INTERNAL).allow)
        self.assertFalse(decide(Boundary.CONFIDENTIAL, Visibility.TEAM, Audience.INTERNAL).allow)
        self.assertTrue(decide(Boundary.CONFIDENTIAL, Visibility.TEAM, Audience.COUNSEL).allow)
        # private is never exportable, for any audience
        for aud in Audience:
            self.assertFalse(decide(Boundary.PUBLIC, Visibility.PRIVATE, aud).allow)

    def test_classify_lattice_max_and_fail_closed(self):
        pub = Claim("a", ClaimType.P1, "x", boundary=Boundary.PUBLIC, visibility=Visibility.TEAM)
        intl = Claim("b", ClaimType.P1, "y", boundary=Boundary.INTERNAL, visibility=Visibility.TEAM)
        self.assertIs(classify([pub]).boundary, Boundary.PUBLIC)
        self.assertIs(classify([pub, intl]).boundary, Boundary.INTERNAL)  # lattice-max
        self.assertIs(classify([]).boundary, Boundary.CONFIDENTIAL)       # fail-closed


class RedactionTest(unittest.TestCase):
    def test_scan_catches_codename_and_pii(self):
        hits = scan_strings(["The Falcon scheduler emailed a@b.com"], Ruleset())
        kinds = {h["type"] for h in hits}
        self.assertIn("codename", kinds)
        self.assertIn("email", kinds)

    def test_clean_text_no_hits(self):
        self.assertEqual(scan_strings(["a scheduling method reduces latency"], Ruleset()), [])

    def test_term_matched_as_substring(self):
        # adjacent alphanumerics must NOT let a codename/customer evade the sweep
        hits = scan_strings(["Meteor2024 shipped for AcmeCorporation"], Ruleset())
        kinds = {h["type"] for h in hits}
        self.assertIn("codename", kinds)
        self.assertIn("customer", kinds)

    def test_phone_precision_no_false_positive_on_academic_numbers(self):
        rs = Ruleset()
        # a real phone number is caught
        self.assertTrue(
            any(h["type"] == "phone" for h in scan_strings(["call +1 415-555-0123 now"], rs)))
        # DOIs, arXiv ids, and pdftotext table-column runs must NOT trip the phone rule
        clean = ["doi 068431-1189 ref", "128\n41\n14200\n\n92\n38\n14600", "arXiv:2401.01234"]
        self.assertEqual([h for h in scan_strings(clean, rs) if h["type"] == "phone"], [])


class DigestTest(unittest.TestCase):
    def _bundle(self, boundary):
        return RawBundle(
            bundle_id="b", source_adapter="caw02-bundle",
            claims=[Claim("c", ClaimType.P1, "x", boundary=boundary, visibility=Visibility.TEAM)])

    def test_digest_covers_confidentiality_labels(self):
        # flipping a claim's boundary MUST change the integrity digest (no silent
        # confidential→public relabel that still imports as digest_ok)
        d_pub = canonical_digest(self._bundle(Boundary.PUBLIC))
        d_conf = canonical_digest(self._bundle(Boundary.CONFIDENTIAL))
        self.assertNotEqual(d_pub, d_conf)


class HarnessConfidentialityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig()
        cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def _drive(self, bundle, audience="public"):
        imp = self.h.import_bundle(bundle)
        bid = imp["bundle_id"]
        self.h.run_gate(bid)
        self.h.assemble_inputs(bid, TEMPLATE, GUIDELINES, target_audience=audience)
        self.h.draft(bid)
        return bid

    def test_public_demo_publishes(self):
        bid = self._drive(DEMO, "public")
        res = self.h.publish(bid, target_audience="public")
        self.assertTrue(res["published"], res)
        self.assertTrue(self.h.ledger.verify_lifecycle())

    def test_internal_claim_blocked_from_public_assembly(self):
        imp = self.h.import_bundle(INTERNAL)
        bid = imp["bundle_id"]
        self.h.run_gate(bid)  # ic1 passes the evidence gate (internal/team)
        with self.assertRaises(RuntimeError):
            self.h.assemble_inputs(bid, TEMPLATE, GUIDELINES, target_audience="public")
        art = self.h.ledger.get_artifact(f"art-{bid}")
        self.assertEqual(art["state"], "blocked")  # BOUNDARY, no leak reached the engine

    def test_internal_claim_ok_for_internal_audience(self):
        bid = self._drive(INTERNAL, "internal")
        res = self.h.publish(bid, target_audience="internal")
        self.assertTrue(res["published"], res)

    def test_redaction_resweep_blocks_public_despite_public_label(self):
        bid = self._drive(REDACTION, "public")  # public/team label → passes decide()
        res = self.h.publish(bid, target_audience="public")
        self.assertFalse(res["published"], res)       # but the re-sweep catches the codename
        self.assertEqual(res["reason"], "BOUNDARY")
        spans = {h["span"].lower() for h in res["redaction_hits"]}
        self.assertIn("falcon", spans, res["redaction_hits"])
        art = self.h.ledger.get_artifact(f"art-{bid}")
        self.assertEqual(art["state"], "blocked")

    def test_egress_fail_closed_when_deliverable_unscannable(self):
        # If the deliverable is a PDF we cannot read and there is no .tex source, the
        # egress gate must FAIL CLOSED rather than publish it unswept.
        import veridraft.core.harness as hmod
        bid = self._drive(DEMO, "public")
        art = self.h.ledger.get_artifact(f"art-{bid}")
        out = Path(art["output_ref"])
        wsdir = self.h.data_dir / "workspace" / bid
        for d in (out, wsdir / "final", wsdir / "inputs"):
            for p in list(d.glob("*")):
                if p.is_file() and p.suffix.lower() != ".pdf":
                    p.unlink()
        orig = hmod._extract_pdf_text
        hmod._extract_pdf_text = lambda p: None      # simulate no PDF extractor
        try:
            res = self.h.publish(bid, target_audience="public")
        finally:
            hmod._extract_pdf_text = orig
        self.assertFalse(res["published"], res)
        self.assertTrue(res.get("unscannable") or res.get("nothing_to_sweep"), res)
        self.assertEqual(self.h.ledger.get_artifact(f"art-{bid}")["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
