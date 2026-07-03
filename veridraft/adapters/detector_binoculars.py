"""`binoculars-local` — a REAL, local, CPU-only AI-text-detection RISK estimator.

This is the v1 DetectorAdapter behind the `detector` port (ADR-0009). It runs the
Binoculars zero-shot detector (arXiv:2401.12070) ON-BOX so a confidential manuscript
never leaves the host and no LLM API is called.

Architecture (keeps the veridraft core stdlib-pure): the heavy deps (`torch`,
`transformers`) live in a SEPARATE venv (`~/.veridraft-detector-venv`). This adapter is
pure stdlib and shells out to `veridraft/scripts/binoculars_score.py` inside that venv,
parses its JSON, and maps the score to a RISK band against a LOCALLY CALIBRATED
threshold (`veridraft/detector_calibration.json`).

Honesty rules (design §5):
  * It returns a RISK band, never a verdict. Detectors false-flag human (esp.
    non-native / ESL) writing; a low score is a proxy, not proof.
  * The 0.9015 paper threshold is for a Falcon-7B pair. This 0.5B pair is WEAKER,
    so we recalibrate locally and report the MEASURED false-positive rate.

If the venv or runner is missing, `health()` is unhealthy and `detect()` returns a
graceful `band="unavailable"` dict (readiness already handles the no-detector case).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core.registry import register
from ..ports import AdapterCapabilities, HealthStatus, Maturity

VERSION = "0.1.0"

_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
RUNNER = _PKG_ROOT / "scripts" / "binoculars_score.py"
CALIBRATION = _PKG_ROOT / "detector_calibration.json"
DEFAULT_VENV_PYTHON = str(Path.home() / ".veridraft-detector-venv" / "bin" / "python")

_NOTE = "RISK proxy, not a verdict; detectors false-flag human/ESL writing"


@register(port="detector", id="binoculars-local")
class BinocularsLocalDetectorAdapter:
    """Local Binoculars detector; subprocess-calls the venv runner (never imports torch)."""

    capabilities = AdapterCapabilities(
        port="detector",
        id="binoculars-local",
        version=VERSION,
        provides=("false_flag_risk",),
        features=frozenset({"local", "no-egress", "cpu", "no-llm-api"}),
        requires_config=(),
        maturity=Maturity.EXPERIMENTAL,
    )

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.venv_python = self.config.get("venv_python", DEFAULT_VENV_PYTHON)
        self.timeout = int(self.config.get("timeout", 900))
        self.margin = self.config.get("margin")  # None -> use calibration's margin
        self._calibration = self._load_calibration()

    # -- calibration ---------------------------------------------------------
    def _load_calibration(self) -> dict:
        path = self.config.get("calibration_path") or CALIBRATION
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @property
    def threshold(self):
        return self._calibration.get("threshold")

    def _effective_margin(self) -> float:
        if self.margin is not None:
            return float(self.margin)
        return float(self._calibration.get("margin", 0.05))

    # -- availability / health ----------------------------------------------
    def _available(self) -> bool:
        return RUNNER.exists() and Path(self.venv_python).exists()

    def health(self) -> HealthStatus:
        if not RUNNER.exists():
            return HealthStatus.unhealthy(f"binoculars runner missing: {RUNNER}")
        if not Path(self.venv_python).exists():
            return HealthStatus.unhealthy(
                f"detector venv missing: {self.venv_python} — create it with "
                "`python3 -m venv ~/.veridraft-detector-venv && "
                "~/.veridraft-detector-venv/bin/pip install torch --index-url "
                "https://download.pytorch.org/whl/cpu transformers` (see README §detector)")
        if not self._calibration.get("threshold"):
            return HealthStatus.unhealthy(
                f"no calibrated threshold in {CALIBRATION} — run the calibration step")
        return HealthStatus.healthy(
            f"binoculars-local ready (venv: {self.venv_python}; "
            f"threshold={self._calibration.get('threshold')})")

    # -- detect --------------------------------------------------------------
    def _base(self) -> dict:
        return {
            "detector": "binoculars-local",
            "version": VERSION,
            "threshold": self.threshold,
            "measured_false_positive_rate":
                self._calibration.get("measured_false_positive_rate"),
            "model_pair": self._calibration.get("model_pair"),
            "note": _NOTE,
        }

    def _unavailable(self, reason: str) -> dict:
        out = self._base()
        out.update({
            "band": "unavailable",
            "score": None,
            "note": f"detector unavailable ({reason}); {_NOTE}",
        })
        return out

    def detect(self, text: str) -> dict:
        if not self._available():
            missing = "runner" if not RUNNER.exists() else "venv"
            return self._unavailable(f"{missing} missing")
        try:
            proc = subprocess.run(
                [self.venv_python, str(RUNNER)],
                input=text, capture_output=True, text=True, timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return self._unavailable(f"runner failed to launch: {exc}")

        if proc.returncode != 0:
            return self._unavailable(
                "runner error: " + (proc.stderr.strip().splitlines()[-1] if proc.stderr.strip()
                                    else f"exit {proc.returncode}"))
        try:
            out = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return self._unavailable("could not parse runner output")
        if not isinstance(out, dict):        # untrusted runner may emit a JSON scalar/array
            return self._unavailable("runner output was not a JSON object")

        score = out.get("binoculars_score")
        result = self._base()
        result.update({
            "score": score,
            "n_tokens": out.get("n_tokens"),
            "observer": out.get("observer"),
            "performer": out.get("performer"),
            "band": self._band(score, out.get("too_short", False)),
        })
        if out.get("too_short"):
            result["note"] = (
                f"text too short (<50 tokens) for a reliable score; {_NOTE}")
        return result

    def _band(self, score, too_short: bool) -> str:
        """Map a score to a RISK band, respecting the calibration's MEASURED direction.

        The paper's convention is "low score -> machine", but the direction is
        model-pair-dependent and is measured during local calibration (with a weak
        0.5B pair it inverts). A margin around the threshold yields an explicit
        `uncertain` band rather than forcing a binary call — an honest reflection of
        a weak pair.
        """
        if score is None or self.threshold is None or too_short:
            return "uncertain"
        try:                                  # the runner's JSON is untrusted — coerce, never crash
            score = float(score)
        except (TypeError, ValueError):
            return "uncertain"
        margin = self._effective_margin()
        thr = float(self.threshold)
        machine_high = self._calibration.get("direction") == "high-score-is-machine"
        if machine_high:
            if score > thr + margin:
                return "likely-machine"
            if score < thr - margin:
                return "likely-human"
        else:  # canonical: low score -> machine
            if score < thr - margin:
                return "likely-machine"
            if score > thr + margin:
                return "likely-human"
        return "uncertain"
