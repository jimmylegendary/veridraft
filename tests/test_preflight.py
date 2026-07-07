"""Tests for the preflight input-sufficiency gate + grounded auto-remediation.
Run from repo root:  python -m unittest discover -s tests"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import paperorchestra.preflight as pf

META_OK = {"venue": "MLSys", "anonymize": True, "authors": [{"name": "J"}], "audience": "systems"}


def _ws(idea="", exp="", bib="", meta=None, figs=0):
    tmp = tempfile.TemporaryDirectory()
    ws = Path(tmp.name)
    (ws / "inputs").mkdir()
    (ws / "inputs" / "idea.md").write_text(idea)
    (ws / "inputs" / "experimental_log.md").write_text(exp)
    (ws / "inputs" / "template.tex").write_text("x")
    (ws / "inputs" / "conference_guidelines.md").write_text("x")
    if bib:
        (ws / "refs.bib").write_text(bib)
    if meta is not None:
        (ws / "inputs" / "meta.json").write_text(json.dumps(meta))
    if figs:
        (ws / "inputs" / "figures").mkdir()
        for i in range(figs):
            (ws / "inputs" / "figures" / f"f{i}.png").write_bytes(b"png")
    return tmp, ws


def _by(items, check):
    return next(i for i in items if i["check"] == check)


RICH_IDEA = ("problem approach contribution " * 60)
RICH_EXP = "latency 12.5 ms, throughput 900 tokens/s, 92 %, 1.8x speedup, memory 4.2 GB, p99 31 ms"
RICH_BIB = "\n".join(f"@article{{k{i}, title={{T{i}}}, year={{2024}}}}" for i in range(10))


class PreflightCheckTest(unittest.TestCase):
    def test_rich_workspace_is_all_ok(self):
        tmp, ws = _ws(idea=RICH_IDEA, exp=RICH_EXP, bib=RICH_BIB, meta=META_OK)
        items = pf.check(ws, "paper")
        self.assertEqual({i["status"] for i in items}, {pf.OK}, items)
        tmp.cleanup()

    def test_thin_idea_and_lit_are_autofill(self):
        tmp, ws = _ws(idea="do a paper", exp=RICH_EXP, meta=META_OK)
        items = pf.check(ws, "paper")
        self.assertEqual(_by(items, "idea")["status"], pf.AUTOFILL)
        self.assertEqual(_by(items, "idea")["remedy"], "densify-idea")
        self.assertEqual(_by(items, "literature")["status"], pf.AUTOFILL)
        tmp.cleanup()

    def test_missing_numbers_is_needs_human_never_autofill(self):
        tmp, ws = _ws(idea=RICH_IDEA, exp="we ran some things and they went well", meta=META_OK)
        item = _by(pf.check(ws, "paper"), "experimental-numbers")
        self.assertEqual(item["status"], pf.HUMAN)          # blocking
        self.assertNotIn("remedy", item)                    # NO auto-fill path for numbers, ever
        self.assertIn("NEVER auto-filled", item["detail"])
        tmp.cleanup()

    def test_missing_meta_is_needs_human(self):
        tmp, ws = _ws(idea=RICH_IDEA, exp=RICH_EXP, bib=RICH_BIB, meta=None)
        self.assertEqual(_by(pf.check(ws, "paper"), "meta-info")["status"], pf.HUMAN)
        tmp.cleanup()

    def test_undocumented_source_repo_is_autofill(self):
        tmp, ws = _ws(idea=RICH_IDEA, exp=RICH_EXP, bib=RICH_BIB,
                      meta=dict(META_OK, source_repo=str(Path(tempfile.mkdtemp()) / "nope")))
        # nonexistent repo → coverage 0 → docfill
        item = _by(pf.check(ws, "paper"), "code-documentation")
        self.assertEqual(item["status"], pf.AUTOFILL)
        self.assertEqual(item["remedy"], "docfill")
        tmp.cleanup()

    def test_figures_ok_when_numbers_plottable(self):
        tmp, ws = _ws(idea=RICH_IDEA, exp=RICH_EXP, bib=RICH_BIB, meta=META_OK, figs=0)
        self.assertEqual(_by(pf.check(ws, "paper"), "figures")["status"], pf.OK)
        tmp.cleanup()


class RemediateTest(unittest.TestCase):
    def test_remediation_order_and_grounding(self):
        tmp, ws = _ws(idea="thin", exp=RICH_EXP,
                      meta=dict(META_OK, source_repo="/nonexistent/repo"))
        items = pf.check(ws, "paper")
        prompts = []
        orig = pf.providers.dispatch
        pf.providers.dispatch = lambda cfg, prompt, cwd, timeout=0: prompts.append(prompt)
        try:
            ran = pf.remediate({"backend": "x"}, ws, items)
        finally:
            pf.providers.dispatch = orig
        self.assertEqual(ran, ["docfill", "densify-idea", "litfill"])   # docs BEFORE idea densify
        joined = "\n".join(prompts)
        self.assertIn("NEVER fabricate a reference", joined)            # grounded-only rules present
        self.assertIn("Do NOT invent results, numbers", joined)
        tmp.cleanup()

    def test_no_remedy_dispatch_for_human_gaps(self):
        # figs=1 so the ONLY gap is the HUMAN one (missing numbers) — it must never reach a model
        tmp, ws = _ws(idea=RICH_IDEA, exp="no numbers here", bib=RICH_BIB, meta=META_OK, figs=1)
        items = pf.check(ws, "paper")
        called = []
        orig = pf.providers.dispatch
        pf.providers.dispatch = lambda *a, **k: called.append(1)
        try:
            pf.remediate({"backend": "x"}, ws, items)
        finally:
            pf.providers.dispatch = orig
        self.assertEqual(called, [])   # the HUMAN gap (numbers) is never dispatched to a model
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
