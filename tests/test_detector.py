"""binoculars-local detector adapter tests (ADR-0009).

The veridraft core is stdlib-only; torch/transformers live in a SEPARATE venv the
adapter shells out to. These tests therefore:

  * ALWAYS verify the stdlib-safe surface — registration (real adapter, not a stub),
    capability shape, the graceful-unavailable path, that the core imports without
    torch, and the readiness detector hook (OFF by default; attaches a band when on).
  * SKIP the real-inference assertion unless the detector venv + calibration are
    present, in which case detect() must return a RISK band with a numeric score.

Run from impl/:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from veridraft.adapters.detector_binoculars import (
    CALIBRATION,
    DEFAULT_VENV_PYTHON,
    RUNNER,
    BinocularsLocalDetectorAdapter,
)
from veridraft.config import HarnessConfig
from veridraft.core.harness import Harness
from veridraft.core.registry import get_adapter_class, instantiate

EX = Path(__file__).resolve().parent.parent / "examples" / "bundle_demo"
DEMO = str(EX / "bundle.json")
TEMPLATE = str(EX / "template.tex")
GUIDELINES = str(EX / "conference_guidelines.md")

DETECTOR_AVAILABLE = (
    Path(DEFAULT_VENV_PYTHON).exists() and RUNNER.exists() and CALIBRATION.exists()
)

HUMAN_SAMPLE = (
    "It was the best of times, it was the worst of times, it was the age of wisdom, "
    "it was the age of foolishness, it was the epoch of belief, it was the epoch of "
    "incredulity, it was the season of Light, it was the season of Darkness. I went to "
    "the woods because I wished to live deliberately, to front only the essential facts "
    "of life, and see if I could not learn what it had to teach, and not, when I came to "
    "die, discover that I had not lived. Whenever it is a damp, drizzly November in my "
    "soul, I account it high time to get to sea as soon as I can."
)


class DetectorAdapterTest(unittest.TestCase):
    def test_registered_as_real_adapter_not_stub(self):
        cls = get_adapter_class("detector", "binoculars-local")
        self.assertIs(cls, BinocularsLocalDetectorAdapter)
        self.assertFalse(cls.capabilities.is_stub())
        self.assertIn("false_flag_risk", cls.capabilities.provides)
        self.assertIn("no-egress", cls.capabilities.features)

    def test_core_imports_without_torch(self):
        # Importing the adapter/core must never pull torch/transformers into the
        # stdlib runtime — that is the whole point of the subprocess split.
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("transformers", sys.modules)

    def test_detect_graceful_when_venv_missing(self):
        det = BinocularsLocalDetectorAdapter({"venv_python": "/nonexistent/python"})
        self.assertFalse(det.health().ok)
        r = det.detect("word " * 80)
        self.assertEqual(r["band"], "unavailable")
        self.assertIsNone(r["score"])
        self.assertIn("not a verdict", r["note"])

    @unittest.skipUnless(DETECTOR_AVAILABLE, "detector venv/calibration absent")
    def test_detect_returns_band_when_present(self):
        det = instantiate("detector", "binoculars-local", {})
        self.assertTrue(det.health().ok, det.health().detail)
        r = det.detect(HUMAN_SAMPLE)
        self.assertEqual(r["detector"], "binoculars-local")
        self.assertIn(r["band"], {"likely-human", "likely-machine", "uncertain"})
        self.assertIsNotNone(r["score"])
        self.assertIsInstance(r["score"], float)
        self.assertIsNotNone(r["threshold"])


class ReadinessDetectorHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = HarnessConfig()
        self.cfg.data_dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _drive_to_draft(self, h):
        h.import_bundle(DEMO)
        h.run_gate("demo-2026-07")
        h.assemble_inputs("demo-2026-07", TEMPLATE, GUIDELINES, target_audience="public")
        h.draft("demo-2026-07")

    def test_detector_off_by_default_readiness_still_works(self):
        h = Harness(self.cfg)
        try:
            self._drive_to_draft(h)
            r = h.check_submission_readiness("demo-2026-07", "neurips")
            self.assertIsNone(r["false_flag_risk"])  # OFF by default
            self.assertEqual(r["policy_class"], "disclosure")
        finally:
            h.close()

    def test_detector_enabled_attaches_band_to_readiness(self):
        # Enable the hook but force graceful-unavailable so the test stays hermetic
        # (no torch/venv needed): the readiness report must still carry a band.
        self.cfg.adapters["detector"]["enabled"] = True
        self.cfg.adapters["detector"]["config"] = {"venv_python": "/nonexistent/python"}
        h = Harness(self.cfg)
        try:
            self._drive_to_draft(h)
            r = h.check_submission_readiness("demo-2026-07", "neurips")
            self.assertIsNotNone(r["false_flag_risk"])
            self.assertEqual(r["false_flag_risk"]["band"], "unavailable")
            self.assertTrue(any("Local detector" in g for g in r["guidance"]))
        finally:
            h.close()


if __name__ == "__main__":
    unittest.main()
