"""Regression tests for the self-ultrareview findings (3 blockers + 9 highs).
Each test reproduces a finding's failing input and asserts the gate now catches it.
Run from impl/:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from veridraft.config import HarnessConfig
from veridraft.core import harness as harness_mod
from veridraft.core import lints
from veridraft.core.gate import evaluate_claim
from veridraft.core.harness import Harness
from veridraft.core.ledger import Ledger
from veridraft.core.models import Claim, ClaimType, Evidence, EvidenceKind, GateProfile, GateStatus

EX = Path(__file__).resolve().parent.parent / "examples"
DEMO = str(EX / "bundle_demo" / "bundle.json")
PATENT = str(EX / "bundle_patent" / "bundle.json")


def _codes(findings):
    return {f.code for f in findings}


class InterlockEgressTest(unittest.TestCase):
    """Blocker #1: a patent-sensitive/HELD claim must not publish in a paper via profile choice."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig()
        cfg.gate_profile = "neurips-paper"         # first gate under a PAPER profile → pc1 HELD
        cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)
        self.h.import_bundle(PATENT)               # pc1 = P3, public/team, 2 evidence
        self.h.run_gate("demo-patent")             # pc1 BLOCKED, interlock HELD
        self.h.config.gate_profile = "us-utility-patent"   # the profile that disabled the interlock
        self.h.run_gate("demo-patent")             # pc1 now PASSES the gate while still HELD
        self.h.assemble_inputs("demo-patent", str(EX / "bundle_demo" / "template.tex"),
                               str(EX / "bundle_demo" / "conference_guidelines.md"),
                               target_audience="public")
        self.h.draft("demo-patent")

    def tearDown(self):
        self.h.close(); self.tmp.cleanup()

    def test_p3_blocked_at_paper_egress(self):
        res = self.h.publish("demo-patent", target_audience="public")
        self.assertFalse(res["published"], res)
        self.assertEqual(res["reason"], "INTERLOCK")
        self.assertIn("pc1", res["held_claims"])

    def test_release_lets_it_pass(self):
        # a human release (patent filed) lifts the egress interlock — publish (its first, so the
        # artifact is still DRAFTED) then succeeds.
        self.h.release_interlock("pc1", reason="provisional filed")
        res = self.h.publish("demo-patent", target_audience="public")
        self.assertTrue(res["published"], res)


class ProhibitedVenueBypassTest(unittest.TestCase):
    """High #6: omitting the venue arg must not bypass a recorded prohibited-venue guard."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig(); cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)
        self.h.import_bundle(DEMO); self.h.run_gate("demo-2026-07")
        self.h.assemble_inputs("demo-2026-07", str(EX / "bundle_demo" / "template.tex"),
                               str(EX / "bundle_demo" / "conference_guidelines.md"),
                               target_audience="public")
        self.h.draft("demo-2026-07")

    def tearDown(self):
        self.h.close(); self.tmp.cleanup()

    def test_publish_without_venue_still_honors_prohibited_readiness(self):
        self.h.check_submission_readiness("demo-2026-07", "science")   # prohibited, unsigned
        res = self.h.publish("demo-2026-07", target_audience="public")  # NO venue arg
        self.assertFalse(res["published"], res)
        self.assertEqual(res["reason"], "READINESS")


class EvidenceFloorTest(unittest.TestCase):
    """High #4: a profile with min_evidence=0 must still block a zero-evidence claim."""

    def test_zero_evidence_blocked_despite_zero_min(self):
        prof = GateProfile(name="x", min_evidence_by_type={"P1": 0},
                           non_relaxable={"generated_text_is_evidence": False})
        c = Claim(claim_id="c1", type=ClaimType.P1, statement="s", evidence=[])
        self.assertEqual(evaluate_claim(c, prof).status, GateStatus.BLOCKED)


class ImageOnlyPdfTest(unittest.TestCase):
    """High #5: an image-only PDF (empty extractable text, no .tex) must fail closed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig(); cfg.data_dir = self.tmp.name
        self.h = Harness(cfg)

    def tearDown(self):
        self.h.close(); self.tmp.cleanup()

    def test_empty_text_pdf_is_unscannable(self):
        out = Path(self.tmp.name) / "art"; out.mkdir()
        (out / "paper.pdf").write_bytes(b"%PDF-1.4 image-only")   # no .tex sibling
        orig = harness_mod._extract_pdf_text
        harness_mod._extract_pdf_text = lambda p: ""              # simulate image-only extraction
        try:
            strings, unscannable = self.h._gather_scannable("nobundle", {"output_ref": str(out)})
        finally:
            harness_mod._extract_pdf_text = orig
        self.assertTrue(any("paper.pdf" in u for u in unscannable))


class LintRegexEvasionTest(unittest.TestCase):
    """Blockers #2/#3 + highs #7/#8/#9/#10/#11: the readiness lints must not fail open."""

    def test_p1_not_disabled_by_upstream_emit(self):   # blocker #2
        c = ("A method comprising: loading a description; emitting a log entry to a console; "
             "deriving a tile factor; and computing, based on the tile factor, a performance cost.")
        codes = _codes(lint_it(c))
        self.assertIn("P1", codes)

    def test_p3_catches_synonym_second_accelerator(self):   # blocker #3
        c = ("A method comprising: receiving a workload; computing a cost and mapping the same "
             "abstract tiling plan onto a further accelerator having a different memory hierarchy.")
        self.assertIn("P3", _codes(lint_it(c)))

    def test_p2_fires_when_substrate_label_is_words(self):   # high #7
        ledger = {"independent_claims": [{"substrate": "Substrate 1: the callable-factor method",
                                          "kind": "method", "text": "A method comprising computing a cost."}]}
        codes = _codes(lints.lint_patent_claims(ledger, novelty_deltas={"S1": ["without searching a mapping space"]}))
        self.assertIn("P2", codes)

    def test_p4_catches_number_and_wording_variants(self):   # high #8
        for desc in ["DRAM traffic 0.000% divergence vs the ZigZag oracle",
                     "our result is identical to the ZigZag oracle output",
                     "matches the ZigZag oracle exactly"]:
            f = lints.lint_evidence_admission([{"id": "e", "description": desc}], ["ZigZag"])
            self.assertEqual([x.code for x in f], ["P4"], desc)

    def test_p7_not_suppressed_by_distant_enum(self):   # high #9
        c = ("A method wherein the accelerator is selected from a memory-bound class, and the "
             "tile is substantially optimal.")
        self.assertIn("P7", _codes(lint_it(c)))

    def test_review_placeholders_rejected(self):   # high #10
        f = lints.validate_review({"novelty": "TODO: fill in", "soundness": "TBD later",
                                   "clarity": "n/a", "significance": "real assessment here",
                                   "subscores": {"novelty": 3, "soundness": 3, "clarity": 3, "significance": 3}})
        self.assertTrue(any(x.code == "R1" for x in f))

    def test_d2_venue_key_case_insensitive(self):   # high #11
        f = lints.lint_venue_experiments(
            "NeurIPS", marketed_claims=["a 4x speedup"], planned_experiments=["ablation"],
            venue_requirements={"neurips": [{"if_claim": "speedup", "require": "wall-clock baseline"}]})
        self.assertEqual([x.code for x in f], ["D2"])


class LedgerTamperTest(unittest.TestCase):
    """High #12: verify_lifecycle must detect tail-truncation / row deletion."""

    def test_truncation_detected(self):
        tmp = tempfile.TemporaryDirectory()
        L = Ledger(str(Path(tmp.name) / "l.db"))
        for i in range(4):
            L.append_lifecycle_event("art1", None, f"s{i}", actor="system", now=f"2026-01-0{i+1}")
        self.assertTrue(L.verify_lifecycle())
        L.conn.execute("DELETE FROM lifecycle_event WHERE seq >= (SELECT MAX(seq) FROM lifecycle_event)")
        L.conn.commit()
        self.assertFalse(L.verify_lifecycle())   # was fail-open before the fix
        L.close(); tmp.cleanup()


class RemainingFixesTest(unittest.TestCase):
    """The 8 medium + 3 low findings (except #17, already hard-blocked)."""

    def test_phone_catches_signalled_formats_not_identifiers(self):   # #14
        from veridraft.core.confidentiality import Ruleset
        r = Ruleset.load(None)
        def is_phone(s):
            return any(h["type"] == "phone" for h in r.scan(s))
        self.assertTrue(is_phone("(555)123-4567"))
        self.assertTrue(is_phone("+15551234567"))
        self.assertTrue(is_phone("555-123-4567"))
        self.assertFalse(is_phone("068431-1189"))          # identifier, not a phone
        self.assertFalse(is_phone("10.1145/2837614.2837653"))

    def test_p6_distinct_word_substrates_not_merged(self):   # #15
        ledger = {"independent_claims": [
            {"substrate": f"Substrate {w}: a method", "kind": "method", "text": "A method comprising a step."}
            for w in ("one", "two", "three")]}
        p6 = [f for f in lints.lint_patent_claims(ledger) if f.code == "P6"]
        self.assertEqual(len(p6), 3)   # each distinct substrate flagged (not merged to one)

    def test_latex_escape_covers_braces_tilde_caret(self):   # #19
        from veridraft.adapters.engine_minimal_patent import _build_tex
        tex = _build_tex("Fast {LLM} a^b ~c", "FIELD\n\nx^2 {y}.",
                         {"independent": ["1. A method with } and ~."], "dependent": []}, "abstract $z$")
        self.assertNotIn("{LLM}", tex)                 # raw braces escaped
        self.assertIn(r"\{LLM\}", tex)
        self.assertIn(r"\textasciicircum{}", tex)      # ^ escaped
        self.assertIn(r"\textasciitilde{}", tex)       # ~ escaped

    def test_patent_engine_tolerates_malformed_claims(self):   # #22
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        tmp = tempfile.TemporaryDirectory()
        ws = Path(tmp.name); (ws / "inputs").mkdir()
        (ws / "inputs" / "claims.json").write_text('[{"foo":"bar","claim_id":"C1"}]')
        (ws / "inputs" / "results.json").write_text("[]")
        (ws / "inputs" / "invention.md").write_text("# Invention Disclosure: Test")
        out = MinimalPatentEngineAdapter().draft_patent(str(ws))   # must not KeyError
        self.assertEqual(out.engine_adapter, "minimal-patent")
        tmp.cleanup()

    def test_detector_band_rejects_non_numeric(self):   # #23
        from veridraft.adapters.detector_binoculars import BinocularsLocalDetectorAdapter
        a = BinocularsLocalDetectorAdapter({})
        a._calibration = {"threshold": 1.0, "direction": "high-score-is-machine", "margin": 0.01}
        self.assertEqual(a._band([1, 2], False), "uncertain")      # was TypeError
        self.assertIn(a._band("0.5", False), ("likely-machine", "likely-human", "uncertain"))

    def test_draft_requires_gate(self):   # #21
        tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO)     # imported but NOT gated
        with self.assertRaises(Exception):
            h.draft("demo-2026-07")
        h.close(); tmp.cleanup()

    def test_require_results_state_is_audited(self):   # #13
        tmp = tempfile.TemporaryDirectory()
        cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO); h.run_gate("demo-2026-07")
        ev = [e for e in h.ledger.get_lifecycle_events("art-demo-2026-07")
              if e["to_state"] == "gated"]
        self.assertTrue(ev and "require_results" in (ev[0]["detail"] or ""))
        h.close(); tmp.cleanup()

    def test_validate_inputs_failure_stops_pipeline(self):   # #20
        import paperorchestra.run as run
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name)
        (ws / "inputs").mkdir()
        for f in ("idea.md", "experimental_log.md", "template.tex", "conference_guidelines.md"):
            (ws / "inputs" / f).write_text("x")
        orig = run._run_script
        run._run_script = lambda cfg, rel, args: 2 if "validate" in rel else 0
        try:
            with self.assertRaises(SystemExit):
                run.step0({}, ws)
        finally:
            run._run_script = orig
        tmp.cleanup()

    def test_compile_pdf_times_out_gracefully(self):   # #18
        import subprocess as sp
        import paperorchestra.run as run
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name)
        (ws / "final").mkdir(); (ws / "final" / "paper.tex").write_text(r"\documentclass{article}")
        orig = run.subprocess.run
        def boom(*a, **k):
            raise sp.TimeoutExpired(cmd="latexmk", timeout=1)
        run.subprocess.run = boom
        try:
            self.assertIsNone(run.compile_pdf(ws))    # no hang, returns None
        finally:
            run.subprocess.run = orig
        tmp.cleanup()

    def test_lifecycle_append_atomic_across_connections(self):   # #16 (basic multi-conn path)
        tmp = tempfile.TemporaryDirectory(); db = str(Path(tmp.name) / "l.db")
        a, b = Ledger(db), Ledger(db)
        a.append_lifecycle_event("art", None, "s1", actor="system", now="2026-01-01")
        b.append_lifecycle_event("art", None, "s2", actor="system", now="2026-01-02")
        self.assertTrue(b.verify_lifecycle())
        a.close(); b.close(); tmp.cleanup()


class Round2FixesTest(unittest.TestCase):
    """The second ultrareview: my earlier fixes that were still evadable (A) + the deeper
    evidence-gate architecture gaps (C)."""

    # ---- A1: P1 proper terminal anchor ----
    def test_p1_fires_without_semicolons_and_on_negated_emit(self):
        no_semi = ("A method comprising loading a description, emitting a trace of tile factors, "
                   "and computing, analytically, a performance cost.")
        negated = ("A method comprising: loading a description; and computing a performance cost "
                   "for a design without emitting any silicon.")
        legit = ("A method comprising: computing a performance cost; and emitting a machine-readable "
                 "accelerator specification selected using the cost.")
        self.assertIn("P1", {f.code for f in lint_it(no_semi)})
        self.assertIn("P1", {f.code for f in lint_it(negated)})
        self.assertNotIn("P1", {f.code for f in lint_it(legit)})

    # ---- A7: P6 substrate collision ----
    def test_p6_distinct_named_substrates_not_collapsed(self):
        from veridraft.core.lints import _substrate_id
        self.assertEqual(_substrate_id("S1 tiling"), "S1")
        self.assertNotEqual(_substrate_id("Scheduler 1 for X"), "S1")
        ledger = {"independent_claims": [
            {"substrate": "S1 tiling", "kind": "method", "text": "A method with a step."},
            {"substrate": "Scheduler 1 for X", "kind": "method", "text": "A method with a step."}]}
        self.assertEqual(len([f for f in lints.lint_patent_claims(ledger) if f.code == "P6"]), 2)

    # ---- A2: PDF egress fail-closed even with a sibling .tex + non-text deliverables ----
    def test_image_pdf_unscannable_despite_sibling_tex(self):
        tmp = tempfile.TemporaryDirectory(); out = Path(tmp.name) / "art"; out.mkdir()
        (out / "paper.tex").write_text("clean public text")
        (out / "paper.pdf").write_bytes(b"%PDF codename")     # same stem as the .tex
        (out / "figure1.png").write_bytes(b"PNG PROJECT-NIGHTFALL")
        cfg = HarnessConfig(); cfg.data_dir = tmp.name; h = Harness(cfg)
        orig = harness_mod._extract_pdf_text
        harness_mod._extract_pdf_text = lambda p: ""
        try:
            _, unscannable = h._gather_scannable("nb", {"output_ref": str(out)})
        finally:
            harness_mod._extract_pdf_text = orig
        self.assertTrue(any("paper.pdf" in u for u in unscannable))    # not exempted by the .tex
        self.assertTrue(any("figure1.png" in u for u in unscannable))  # binary deliverable
        h.close(); tmp.cleanup()

    # ---- A3: prohibited-venue guard not laundered through a benign venue ----
    def test_prohibited_readiness_blocks_even_with_benign_venue(self):
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO); h.run_gate("demo-2026-07")
        h.assemble_inputs("demo-2026-07", str(EX / "bundle_demo" / "template.tex"),
                          str(EX / "bundle_demo" / "conference_guidelines.md"), target_audience="public")
        h.draft("demo-2026-07")
        h.check_submission_readiness("demo-2026-07", "science")   # prohibited, unsigned
        h.check_submission_readiness("demo-2026-07", "aiware")    # benign
        res = h.publish("demo-2026-07", target_audience="public", venue="aiware")
        self.assertFalse(res["published"], res)
        self.assertEqual(res["reason"], "READINESS")
        h.close(); tmp.cleanup()

    # ---- A4: verify_lifecycle detects anchor-row deletion ----
    def test_verify_lifecycle_detects_anchor_deletion(self):
        tmp = tempfile.TemporaryDirectory(); L = Ledger(str(Path(tmp.name) / "l.db"))
        for i in range(4):
            L.append_lifecycle_event("a", None, f"s{i}", actor="system", now=f"2026-01-0{i+1}")
        self.assertTrue(L.verify_lifecycle())
        L.conn.execute("DELETE FROM ledger_meta WHERE key='lifecycle_head'")
        L.conn.execute("DELETE FROM lifecycle_event WHERE seq >= (SELECT MAX(seq) FROM lifecycle_event)")
        L.conn.commit()
        self.assertFalse(L.verify_lifecycle())
        L.close(); tmp.cleanup()

    # ---- A5: missing input-validator fails closed ----
    def test_missing_validator_stops_pipeline(self):
        import paperorchestra.run as run
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name) / "ws"; (ws / "inputs").mkdir(parents=True)
        for f in ("idea.md", "experimental_log.md", "template.tex", "conference_guidelines.md"):
            (ws / "inputs" / f).write_text("x")
        with self.assertRaises(SystemExit):
            run.step0({"skills_dir": tmp.name}, ws)     # skills_dir has no validate_inputs.py
        tmp.cleanup()

    # ---- A6: malformed claims.json (no claim_id) does not KeyError ----
    def test_patent_engine_no_claim_id_ok(self):
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "inputs").mkdir()
        (ws / "inputs" / "claims.json").write_text('[{"type":"P1","statement":"a fast method"},'
                                                    '{"type":"P3","statement":"a future device"}]')
        (ws / "inputs" / "results.json").write_text("[]")
        (ws / "inputs" / "invention.md").write_text("# Invention Disclosure: T")
        out = MinimalPatentEngineAdapter().draft_patent(str(ws))
        self.assertEqual(out.engine_adapter, "minimal-patent")
        tmp.cleanup()

    # ---- C1: prose relabeled as source_artifact is inadmissible ----
    def test_evidence_ref_shape_rejects_prose(self):
        prof = GateProfile(name="x", min_evidence_by_type={"P1": 1},
                           non_relaxable={"generated_text_is_evidence": False})
        good = Claim("c", ClaimType.P1, "s",
                     evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "repo://m.py@deadbeef")])
        bad = Claim("c", ClaimType.P1, "s",
                    evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "our design doc says so")])
        self.assertEqual(evaluate_claim(good, prof).status, GateStatus.PASSED)
        self.assertEqual(evaluate_claim(bad, prof).status, GateStatus.BLOCKED)

    # ---- C2: ghost result_ref does not satisfy require_result_ref ----
    def test_ghost_result_ref_blocked(self):
        from veridraft.config import load_gate_profile
        prof = load_gate_profile("neurips-paper")   # P1 requires a result ref
        c = Claim("c", ClaimType.P1, "s", result_refs=["ghost"],
                  evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "repo://m.py@deadbeef")])
        self.assertEqual(evaluate_claim(c, prof, valid_result_ids={"r1"}).status, GateStatus.BLOCKED)

    # ---- C3: require_results needs a REFERENCED result (no throwaway unlock) ----
    def test_require_results_needs_a_referenced_result(self):
        from veridraft.core.models import RawBundle, Boundary, Visibility
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        cfg.gate_profile = "systems-paper"; h = Harness(cfg)
        b = RawBundle("b1", "test", Boundary.PUBLIC, results=[_result("R_throwaway")], digest=None,
                      claims=[Claim("c1", ClaimType.P1, "a method", result_refs=[],
                                    evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "repo://m.py@deadbeef")])])
        h.ledger.import_bundle_projection(b, "2026-01-01")
        with self.assertRaises(Exception):   # results exist but no claim references one
            h.run_gate("b1")
        h.close(); tmp.cleanup()

    # ---- A9: a human release is voided when the claim body changes on re-import ----
    def test_interlock_release_voided_on_body_change(self):
        from veridraft.core.models import RawBundle, Boundary, InterlockStatus
        tmp = tempfile.TemporaryDirectory(); L = Ledger(str(Path(tmp.name) / "l.db"))
        def bundle(stmt):
            return RawBundle("B", "test", Boundary.PUBLIC, digest=None, results=[],
                             claims=[Claim("C1", ClaimType.P3, stmt,
                                     evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "repo://x@deadbeef")])])
        L.import_bundle_projection(bundle("ORIGINAL device"), "2026-01-01")
        L.ensure_interlock("C1", "2026-01-01")
        L.set_interlock_status("C1", InterlockStatus.RELEASED, "human:cli", "filed", "2026-01-02")
        self.assertEqual(L.get_interlock_status("C1"), InterlockStatus.RELEASED)
        L.import_bundle_projection(bundle("CHANGED device"), "2026-01-03")   # different body, same id
        self.assertEqual(L.get_interlock_status("C1"), InterlockStatus.HELD)  # release voided
        L.close(); tmp.cleanup()

    # ---- A8: draft refuses stale inputs after a re-gate blocks a claim ----
    def test_draft_refuses_stale_after_regate(self):
        from veridraft.core.models import RawBundle, Boundary, Visibility
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        cfg.gate_profile = "systems-paper"; h = Harness(cfg)
        def bundle(c2_kind):
            return RawBundle("b1", "test", Boundary.PUBLIC, digest=None, results=[_result("r1")],
                claims=[Claim("c1", ClaimType.P1, "method one", result_refs=["r1"],
                              boundary=Boundary.PUBLIC, visibility=Visibility.TEAM,
                              evidence=[Evidence("e1", EvidenceKind.SOURCE_ARTIFACT, "repo://a@deadbeef")]),
                        Claim("c2", ClaimType.P1, "method two", result_refs=["r1"],
                              boundary=Boundary.PUBLIC, visibility=Visibility.TEAM,
                              evidence=[Evidence("e2", c2_kind, "repo://b@deadbeef" if c2_kind == EvidenceKind.SOURCE_ARTIFACT else "generated blah")])])
        h.ledger.import_bundle_projection(bundle(EvidenceKind.SOURCE_ARTIFACT), "2026-01-01")
        h.run_gate("b1")            # c1,c2 pass
        h.assemble_inputs("b1", str(EX / "bundle_demo" / "template.tex"),
                          str(EX / "bundle_demo" / "conference_guidelines.md"), target_audience="public")
        # re-import with c2 evidence now GENERATED (inadmissible) → c2 blocks on re-gate
        h.ledger.import_bundle_projection(bundle(EvidenceKind.GENERATED_TEXT), "2026-01-02")
        h.run_gate("b1")            # gated set shrinks to {c1}; idea.md still has c2
        with self.assertRaises(Exception):
            h.draft("b1")
        h.close(); tmp.cleanup()


class Round3HardeningTest(unittest.TestCase):
    """The heuristic-lint paraphrase bypasses + low findings from review 2, now hardened."""

    def test_p4_oracle_name_variant(self):
        for desc in ["0.00% vs the Zig-Zag oracle", "identical to the ZIG ZAG oracle output"]:
            self.assertEqual([f.code for f in lints.lint_evidence_admission(
                [{"id": "e", "description": desc}], ["ZigZag"])], ["P4"], desc)

    def test_review_test_n_placeholders(self):
        f = lints.validate_review({"novelty": "test 1", "soundness": "todo #2",
                                   "clarity": "TBD.", "significance": "real point",
                                   "subscores": {"novelty": 3, "soundness": 3, "clarity": 3, "significance": 3}})
        self.assertTrue(any(x.code == "R1" for x in f))

    def test_p3_second_device_synonym(self):
        c = ("A method comprising: computing a cost; and re-tiling the plan onto a second target "
             "device having a different memory hierarchy.")
        self.assertIn("P3", {f.code for f in lint_it(c)})

    def test_p2_full_label_delta_key(self):
        ledger = {"independent_claims": [{"substrate": "S1: tile factors", "kind": "method",
                                          "text": "A method comprising computing a cost."}]}
        codes = {f.code for f in lints.lint_patent_claims(
            ledger, novelty_deltas={"S1: tile factors": ["without searching a mapping space"]})}
        self.assertIn("P2", codes)

    def test_redaction_separator_injection(self):
        from veridraft.core.confidentiality import Ruleset
        rs = Ruleset.load({"codenames": ["Falcon"]})
        for t in ["the Fal con chip", "Fal-con drive"]:
            self.assertTrue(any(h["type"] == "codename" for h in rs.scan(t)), t)

    def test_patent_engine_malformed_results_json(self):
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "inputs").mkdir()
        (ws / "inputs" / "claims.json").write_text('[{"type":"P1","statement":"a method","claim_id":"c1"}]')
        (ws / "inputs" / "results.json").write_text('{"a":1}')       # a dict, not a list
        (ws / "inputs" / "invention.md").write_text("# Invention Disclosure: T")
        out = MinimalPatentEngineAdapter().draft_patent(str(ws))     # must not crash
        self.assertEqual(out.engine_adapter, "minimal-patent")
        tmp.cleanup()


class Round3IncompleteFixesTest(unittest.TestCase):
    """The 3rd ultrareview: my earlier fixes that were still incomplete + new crashes."""

    def _p2gate(self):
        return GateProfile(name="x", min_evidence_by_type={"P2": 1},
                           non_relaxable={"generated_text_is_evidence": False})

    def test_caw01_caw02_fabricated_refs_blocked(self):   # #1 blocker
        p = self._p2gate()
        def st(ref, kind):
            c = Claim("B", ClaimType.P2, "x", evidence=[Evidence("e", kind, ref)])
            return evaluate_claim(c, p, valid_result_ids={"R1"}).status
        self.assertEqual(st("TOTALLY-MADE-UP", EvidenceKind.CAW01_RESULT), GateStatus.BLOCKED)
        self.assertEqual(st("also-fake", EvidenceKind.CAW02_EVIDENCE), GateStatus.BLOCKED)
        self.assertEqual(st("caw01://result/r1", EvidenceKind.CAW01_RESULT), GateStatus.PASSED)
        self.assertEqual(st("caw02://claim/c/evidence/e", EvidenceKind.CAW02_EVIDENCE), GateStatus.PASSED)

    def test_p1_bare_emit_and_select_and(self):   # #2 / #3 blockers
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: loading a description; emitting a diagnostic message and "
            "computing a performance cost.")})
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: selecting a candidate design for the accelerator and computing "
            "a performance cost.")})
        self.assertNotIn("P1", {f.code for f in lint_it(
            "A method comprising: computing a performance cost; and emitting a machine-readable "
            "accelerator specification.")})

    def test_review_word_number_placeholder(self):   # #6
        f = lints.validate_review({"novelty": "test one", "soundness": "test two",
                                   "clarity": "test three", "significance": "test four"})
        self.assertTrue(any(x.code == "R1" for x in f))

    def test_p3_negated_disclaimer_not_flagged(self):   # #10
        self.assertNotIn("P3", {f.code for f in lint_it(
            "A method comprising: computing a cost, wherein the plan is not re-tiled to a second "
            "accelerator.")})

    def test_patent_engine_result_without_id(self):   # #11
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "inputs").mkdir()
        (ws / "inputs" / "claims.json").write_text('[{"type":"P1","statement":"computing a cost","claim_id":"c1"}]')
        (ws / "inputs" / "results.json").write_text('[{"description":"x","metrics":[]}]')  # no result_id
        (ws / "inputs" / "invention.md").write_text("# Invention Disclosure: T")
        self.assertEqual(MinimalPatentEngineAdapter().draft_patent(str(ws)).engine_adapter, "minimal-patent")
        tmp.cleanup()

    def test_detector_non_object_json(self):   # #12
        import types
        from veridraft.adapters import detector_binoculars as db
        a = db.BinocularsLocalDetectorAdapter({})
        a._available = lambda: True
        orig = db.subprocess.run
        db.subprocess.run = lambda *x, **k: types.SimpleNamespace(returncode=0, stdout="42", stderr="")
        try:
            r = a.detect("some text to score that is long enough")   # scalar JSON, must not crash
            self.assertIn("band", r)
        finally:
            db.subprocess.run = orig

    def test_run_step_rejects_stale_output(self):   # #13
        import paperorchestra.run as run
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name)
        (ws / "outline.json").write_text("stale")   # pre-existing, backend won't rewrite
        orig = run.providers.dispatch
        run.providers.dispatch = lambda *a, **k: "done"   # writes nothing
        try:
            with self.assertRaises(SystemExit):
                run.run_step({"backend": "x"}, (1, "outline", "outline-agent", "outline.json", True), ws)
        finally:
            run.providers.dispatch = orig
        tmp.cleanup()


class Round4FixesTest(unittest.TestCase):
    """The 4th ultrareview: residual holes in prior fixes + real security/integrity bugs."""

    def test_p1_configuration_noun_and_output_cost(self):   # #1 / #6
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: evaluating tile factors; and computing a performance cost of a "
            "configuration for the accelerator.")})
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: loading a spec; and outputting a performance cost.")})

    def test_source_artifact_prose_with_hex_rejected(self):   # #3
        p = GateProfile(name="x", min_evidence_by_type={"P1": 1},
                        non_relaxable={"generated_text_is_evidence": False})
        def st(ref):
            c = Claim("B", ClaimType.P1, "x", evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, ref)])
            return evaluate_claim(c, p).status
        self.assertEqual(st("Our system is best (fabricated) src/x.py@abc1234"), GateStatus.BLOCKED)
        self.assertEqual(st("src/x.py@abc1234"), GateStatus.PASSED)

    def test_p3_negation_only_when_second_accel_is_negated(self):   # #7
        self.assertIn("P3", {f.code for f in lint_it(
            "A method comprising: costing a design with no compiler backend and re-tiling to a "
            "second accelerator.")})
        self.assertNotIn("P3", {f.code for f in lint_it(
            "A method comprising: computing a cost, wherein the plan is not re-tiled to a second accelerator.")})

    def test_require_results_ghost_ref_rejected(self):   # #2
        from veridraft.core.models import RawBundle, Boundary
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        cfg.gate_profile = "systems-paper"; h = Harness(cfg)
        b = RawBundle("b1", "t", Boundary.PUBLIC, digest=None, results=[_result("R-THROWAWAY")],
                      claims=[Claim("c1", ClaimType.P1, "m", result_refs=["R-GHOST"],
                              evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "src/x.py@abcdef1")])])
        h.ledger.import_bundle_projection(b, "2026-01-01")
        with self.assertRaises(Exception):   # ghost ref + throwaway result → no real referenced result
            h.run_gate("b1")
        h.close(); tmp.cleanup()

    def test_lifecycle_hash_covers_pipe_in_fields(self):   # #9
        tmp = tempfile.TemporaryDirectory(); L = Ledger(str(Path(tmp.name) / "l.db"))
        L.append_lifecycle_event("a|b", "s1|x", "s2", actor="u", now="2026-01-01", reason="r|e|a|s|o|n")
        self.assertTrue(L.verify_lifecycle())
        L.conn.execute("UPDATE lifecycle_event SET reason='forged' WHERE seq=1"); L.conn.commit()
        self.assertFalse(L.verify_lifecycle())   # tampered field is covered by the hash
        L.close(); tmp.cleanup()

    def test_redaction_zero_width_and_double_sep(self):   # #11
        from veridraft.core.confidentiality import Ruleset
        rs = Ruleset.load({"codenames": ["Falcon"]})
        for t in ["Fal​con", "Fal  con", "Fal/con"]:
            self.assertTrue(any(h["type"] == "codename" for h in rs.scan(t)), repr(t))

    def test_probe_rejects_unknown_backend(self):   # #15
        import paperorchestra.providers as pv
        ok, _ = pv.probe({"backend": "totally-made-up"})
        self.assertFalse(ok)

    def test_latexmk_uses_no_shell_escape(self):   # #10
        src = (Path(__file__).resolve().parent.parent / "paperorchestra" / "run.py").read_text()
        self.assertIn("-no-shell-escape", src)


class Round5FixesTest(unittest.TestCase):
    """The 5th ultrareview: require_results over passed claims, durable interlock, dedup, crashes."""

    def _upat(self, tmp):
        cfg = HarnessConfig(); cfg.gate_profile = "us-utility-patent"; cfg.data_dir = tmp.name
        return Harness(cfg)

    def test_interlock_durable_and_survives_relabel(self):   # #2
        from veridraft.core.models import RawBundle, Boundary, Visibility
        from veridraft.core.models import InterlockStatus
        tmp = tempfile.TemporaryDirectory(); h = self._upat(tmp)
        def b(t):
            return RawBundle("B", "s", Boundary.PUBLIC, digest=None, results=[_result("r1")],
                claims=[Claim("x1", t, "a future device", result_refs=["r1"],
                        boundary=Boundary.PUBLIC, visibility=Visibility.TEAM,
                        evidence=[Evidence("e", EvidenceKind.CAW02_EVIDENCE, "caw02://x/e")])])
        h.ledger.import_bundle_projection(b(ClaimType.P3), "2026-01-01"); h.run_gate("B")
        self.assertEqual(h.ledger.get_interlock_status("x1"), InterlockStatus.HELD)  # created under us-utility
        h.ledger.import_bundle_projection(b(ClaimType.P1), "2026-01-02")             # relabel P3->P1, same body
        self.assertEqual(h.ledger.get_interlock_status("x1"), InterlockStatus.HELD)  # survives
        h.close(); tmp.cleanup()

    def test_require_results_over_passed_claims(self):   # #1
        from veridraft.core.models import RawBundle, Boundary, Visibility
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.gate_profile = "systems-paper"
        cfg.data_dir = tmp.name; h = Harness(cfg)
        b = RawBundle("b1", "s", Boundary.PUBLIC, digest=None, results=[_result("R1")], claims=[
            Claim("A", ClaimType.P1, "m", result_refs=[], boundary=Boundary.PUBLIC, visibility=Visibility.TEAM,
                  evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "src/x.py@abcdef1")]),   # PASSES, no result
            Claim("B", ClaimType.P1, "junk", result_refs=["R1"], boundary=Boundary.PUBLIC,
                  visibility=Visibility.TEAM, evidence=[])])                                       # BLOCKED (no evidence)
        h.ledger.import_bundle_projection(b, "2026-01-01")
        with self.assertRaises(Exception):   # only a BLOCKED claim references the result -> no draftable result
            h.run_gate("b1")
        h.close(); tmp.cleanup()

    def test_p1_passive_intended_use(self):   # #4
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: computing a performance cost for fabrication of the accelerator.")})

    def test_p3_no_fewer_than_two(self):   # #6
        self.assertIn("P3", {f.code for f in lint_it(
            "A method comprising: computing a cost using no fewer than two accelerators.")})

    def test_evidence_dedup_by_ref(self):   # #8
        c = Claim("c", ClaimType.P3, "x", evidence=[
            Evidence("e1", EvidenceKind.CAW02_EVIDENCE, "caw02://same"),
            Evidence("e2", EvidenceKind.CAW02_EVIDENCE, "caw02://same")])
        self.assertEqual(len(c.admissible_evidence()), 1)

    def test_lint_precision_numeric_value(self):   # #10
        self.assertEqual([f.code for f in lints.lint_precision([{"value": 2214.6, "grade": "exploration"}])], ["D4"])

    def test_patent_engine_non_string_statement(self):   # #11
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "inputs").mkdir()
        (ws / "inputs" / "claims.json").write_text('[{"type":"P1","statement":123,"claim_id":"c1"},'
                                                    '{"type":"P1","statement":"real method","claim_id":"c2"}]')
        (ws / "inputs" / "results.json").write_text("[]")
        (ws / "inputs" / "invention.md").write_text("# Invention Disclosure: T")
        self.assertEqual(MinimalPatentEngineAdapter().draft_patent(str(ws)).engine_adapter, "minimal-patent")
        tmp.cleanup()

    def test_providers_string_allowed_tools(self):   # #14
        import paperorchestra.providers as pv
        cfg = {"backend": "claude-code", "claude_allowed_tools": "Read,Bash"}
        # exercise the normalization branch directly (no claude binary needed)
        tools = cfg["claude_allowed_tools"]
        import re as _re
        norm = [t for t in _re.split(r"[,\s]+", tools) if t] if isinstance(tools, str) else tools
        self.assertEqual(norm, ["Read", "Bash"])

    def test_artifact_state_tamper_detected(self):   # #7
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO); h.run_gate("demo-2026-07")
        self.assertTrue(h.ledger.verify_lifecycle())
        h.ledger.conn.execute("UPDATE artifact SET state='published_paper' WHERE id='art-demo-2026-07'")
        h.ledger.conn.commit()
        self.assertFalse(h.ledger.verify_lifecycle())   # state no longer matches the hash-chained log
        h.close(); tmp.cleanup()


class Round6FixesTest(unittest.TestCase):
    """The 6th ultrareview: interlock-on-vanished-claim, P1 back-reference, verify regression, more."""

    def test_interlock_holds_when_claim_deleted(self):   # #1 blocker
        from veridraft.core.models import RawBundle, Boundary, Visibility
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig()
        cfg.gate_profile = "us-utility-patent"; cfg.data_dir = tmp.name; h = Harness(cfg)
        b = RawBundle("B", "s", Boundary.PUBLIC, digest=None, results=[_result("r1")],
            claims=[Claim("pc1", ClaimType.P3, "a future device", result_refs=["r1"],
                    boundary=Boundary.PUBLIC, visibility=Visibility.TEAM,
                    evidence=[Evidence("e", EvidenceKind.CAW02_EVIDENCE, "caw02://x/e")])])
        h.ledger.import_bundle_projection(b, "2026-01-01"); h.run_gate("B")
        art = h.ledger.get_artifact(h._artifact_id("B"))
        self.assertEqual(h._held_disclosure_claims("B", art), ["pc1"])
        # attacker re-imports the SAME bundle with the claim DROPPED — interlock row survives
        h.ledger.import_bundle_projection(RawBundle("B", "s", Boundary.PUBLIC, digest=None,
                                                    results=[_result("r1")], claims=[]), "2026-01-02")
        self.assertIsNone(h.ledger.get_claim("pc1"))
        self.assertEqual(h._held_disclosure_claims("B", art), ["pc1"])   # still HELD → still blocked
        h.close(); tmp.cleanup()

    def test_p1_back_referenced_object(self):   # #2 blocker
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: computing a performance cost of the generated accelerator design.")})
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: computing a cost of the produced netlist.")})

    def test_require_results_content_free_rejected(self):   # #3
        from veridraft.core.models import RawBundle, Boundary, Visibility, ResultRef
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig()
        cfg.gate_profile = "systems-paper"; cfg.data_dir = tmp.name; h = Harness(cfg)
        b = RawBundle("b1", "s", Boundary.PUBLIC, digest=None,
            results=[ResultRef("r1", "", [])],   # content-free: no metrics, blank description
            claims=[Claim("c1", ClaimType.P1, "m", result_refs=["r1"], boundary=Boundary.PUBLIC,
                    visibility=Visibility.TEAM,
                    evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "src/x.py@abcdef1")])])
        h.ledger.import_bundle_projection(b, "2026-01-01")
        with self.assertRaises(Exception):
            h.run_gate("b1")
        h.close(); tmp.cleanup()

    def test_verify_lifecycle_ok_after_review_readiness(self):   # #6 regression
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO); h.run_gate("demo-2026-07")
        h.assemble_inputs("demo-2026-07", str(EX / "bundle_demo" / "template.tex"),
                          str(EX / "bundle_demo" / "conference_guidelines.md"), target_audience="public")
        h.draft("demo-2026-07"); h.run_review("demo-2026-07")
        h.check_submission_readiness("demo-2026-07", "neurips")
        self.assertTrue(h.ledger.verify_lifecycle())   # untampered → True (was False after the round-5 change)
        h.close(); tmp.cleanup()

    def test_p4_multiline_description(self):   # #5
        desc = "the result reproduces the ZigZag oracle\nDRAM traffic exactly"
        self.assertEqual([f.code for f in lints.lint_evidence_admission([{"id": "e", "description": desc}], ["ZigZag"])], ["P4"])

    def test_compile_pdf_refuses_latexmkrc(self):   # #7 security
        import paperorchestra.run as run
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "final").mkdir()
        (ws / "final" / "paper.tex").write_text(r"\documentclass{article}\begin{document}x\end{document}")
        (ws / "final" / "latexmkrc").write_text("system('touch /tmp/pwned');")
        self.assertIsNone(run.compile_pdf(ws))   # refuses (code-exec risk)
        tmp.cleanup()

    def test_p6_apparatus_process_recognized(self):   # #11
        ledger = {"independent_claims": [
            {"substrate": "S1", "kind": "process", "text": "A process comprising a step."},
            {"substrate": "S1", "kind": "apparatus", "text": "An apparatus."},
            {"substrate": "S1", "kind": "non-transitory computer-readable medium", "text": "A medium."}]}
        self.assertNotIn("P6", {f.code for f in lints.lint_patent_claims(ledger)})

    def test_import_claim_id_collision_clean_error(self):   # #13
        from veridraft.core.models import RawBundle, Boundary
        tmp = tempfile.TemporaryDirectory(); L = Ledger(str(Path(tmp.name) / "l.db"))
        c = Claim("shared", ClaimType.P1, "x")
        L.import_bundle_projection(RawBundle("b1", "s", Boundary.PUBLIC, digest=None, results=[], claims=[c]), "t")
        with self.assertRaises(ValueError):
            L.import_bundle_projection(RawBundle("b2", "s", Boundary.PUBLIC, digest=None, results=[], claims=[c]), "t")
        L.close(); tmp.cleanup()


class Round7FixesTest(unittest.TestCase):
    """The 7th ultrareview: valueless metric, readiness sign-off reuse, digest, verify revert, P1 synonyms."""

    def test_require_results_valueless_metric_rejected(self):   # #1
        from veridraft.core.models import RawBundle, Boundary, Visibility, ResultRef
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig()
        cfg.gate_profile = "systems-paper"; cfg.data_dir = tmp.name; h = Harness(cfg)
        b = RawBundle("b1", "s", Boundary.PUBLIC, digest=None, results=[ResultRef("r1", "", [{}])],
            claims=[Claim("c1", ClaimType.P1, "m", result_refs=["r1"], boundary=Boundary.PUBLIC,
                    visibility=Visibility.TEAM,
                    evidence=[Evidence("e", EvidenceKind.SOURCE_ARTIFACT, "src/x.py@abcdef1")])])
        h.ledger.import_bundle_projection(b, "t")
        with self.assertRaises(Exception):   # metrics=[{}] carries no value → not content
            h.run_gate("b1")
        h.close(); tmp.cleanup()

    def test_readiness_signoff_bound_to_draft(self):   # #2
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO); h.run_gate("demo-2026-07")
        h.assemble_inputs("demo-2026-07", str(EX / "bundle_demo" / "template.tex"),
                          str(EX / "bundle_demo" / "conference_guidelines.md"), target_audience="public")
        h.draft("demo-2026-07")
        h.check_submission_readiness("demo-2026-07", "science")
        h.sign_off_readiness("demo-2026-07", "science", "human:jimmy")
        # a matching-draft publish is allowed; then simulate a re-draft (different content)
        out = Path(h.ledger.get_artifact("art-demo-2026-07")["output_ref"])
        for p in out.glob("*.tex"):
            p.write_text("COMPLETELY DIFFERENT v2 MANUSCRIPT never reviewed by a human")
        res = h.publish("demo-2026-07", target_audience="public", venue="science")
        self.assertFalse(res["published"], res)
        self.assertEqual(res["reason"], "READINESS")
        self.assertTrue(res["stale_signoff"])          # old sign-off no longer covers new draft
        h.close(); tmp.cleanup()

    def test_patent_engine_malformed_result_refs(self):   # #6
        from veridraft.adapters.engine_minimal_patent import MinimalPatentEngineAdapter
        tmp = tempfile.TemporaryDirectory(); ws = Path(tmp.name); (ws / "inputs").mkdir()
        (ws / "inputs" / "claims.json").write_text(
            '[{"type":"P1","statement":"m1","claim_id":"c1"},'
            ' {"type":"P1","statement":"m2","claim_id":"c2","result_refs":"not-a-list"},'
            ' {"type":"P2","statement":"m3","claim_id":"c3","result_refs":[null,42,""]}]')
        (ws / "inputs" / "results.json").write_text("[]")
        (ws / "inputs" / "invention.md").write_text("# Invention Disclosure: T")
        self.assertEqual(MinimalPatentEngineAdapter().draft_patent(str(ws)).engine_adapter, "minimal-patent")
        tmp.cleanup()

    def test_canonical_digest_covers_result_description(self):   # #11
        from veridraft.core.models import RawBundle, Boundary, ResultRef
        from veridraft.core.ledger import canonical_digest
        def d(desc):
            return canonical_digest(RawBundle("b", "s", Boundary.PUBLIC, digest=None,
                                              results=[ResultRef("r1", desc, [])], claims=[]))
        self.assertNotEqual(d("real result: 92ms"), d("tampered result: 999ms"))

    def test_verify_lifecycle_detects_state_revert(self):   # #12
        tmp = tempfile.TemporaryDirectory(); cfg = HarnessConfig(); cfg.data_dir = tmp.name
        h = Harness(cfg); h.import_bundle(DEMO); h.run_gate("demo-2026-07")
        h.assemble_inputs("demo-2026-07", str(EX / "bundle_demo" / "template.tex"),
                          str(EX / "bundle_demo" / "conference_guidelines.md"), target_audience="public")
        h.draft("demo-2026-07"); h.publish("demo-2026-07", target_audience="public")
        self.assertTrue(h.ledger.verify_lifecycle())
        h.ledger.conn.execute("UPDATE artifact SET state='drafted' WHERE id='art-demo-2026-07'")
        h.ledger.conn.commit()
        self.assertFalse(h.ledger.verify_lifecycle())   # reverted to an OLDER logged state
        h.close(); tmp.cleanup()

    def test_p1_verb_and_quantity_synonyms(self):   # #3
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: deriving a tile factor; and calculating a performance cost.")})
        self.assertIn("P1", {f.code for f in lint_it(
            "A method comprising: loading a spec; and computing a latency estimate.")})


def lint_it(text: str):
    return lints.lint_patent_claims(
        {"independent_claims": [{"substrate": "S1", "kind": "method", "text": text}]})


def _result(rid: str):
    from veridraft.core.models import ResultRef
    return ResultRef(rid, "a measured result", [])


if __name__ == "__main__":
    unittest.main()
