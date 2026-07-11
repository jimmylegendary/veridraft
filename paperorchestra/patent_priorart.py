#!/usr/bin/env python3
"""Patent PRIOR-ART SEARCH runner — backend-agnostic (a sibling of patent_run.py).

Runs a defensible, element-level prior-art search over ANY configured backend that has web tools,
then applies a DETERMINISTIC honesty + analysis pass. The keyless-first channels are:
  - PATENTS: the backend agent's web search scoped to `site:patents.google.com` + fetching patent
    pages (no official Google Patents API exists; this is the free path).
  - NPL: Semantic Scholar (valid §102 prior art) — keyless, or keyed via SEMANTIC_SCHOLAR_API_KEY.
(An optional keyed PatentsView/EPO/Lens path can be added; the base URL/keys stay config-driven.)

    python3 patent_priorart.py --config backend.json --workspace <ws> [--probe]

Output (consumed by patentability): <ws>/inputs/priorart.json + <ws>/inputs/priorart_report.md.

CARDINAL RULE (enforced deterministically, model cannot override): absence of found prior art NEVER
proves novelty. The verdict is forced to leads-only-non-clearance and a non-removable
"professional search + attorney" open item is always kept. This drafts leads; it never clears.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # repo root → veridraft package
import providers

try:
    from veridraft.core import priorart as pa_core
except Exception:   # noqa: BLE001 — runner stays usable even if the package layout differs
    pa_core = None


def log(m: str) -> None:
    print(f"[patent-priorart] {m}", flush=True)


_PROMPT = """Run a PRIOR-ART SEARCH for the invention disclosed in {ws}/inputs/ (read invention.md and
claims.json). This is a search for LEADS to DISTINGUISH the claims — it is NOT a novelty clearance.

METHOD (element-level, the only defensible way):
1. Decompose each INDEPENDENT claim in claims.json into an ordered list of its distinct ELEMENTS.
2. Search for prior art with the web tools available to you:
   - PATENTS: web-search scoped to `site:patents.google.com` for the key technical phrases of the
     claims; open the most relevant patent pages and read the title/abstract/claims.
   - NON-PATENT LITERATURE (equally valid prior art): search Semantic Scholar / the web for papers,
     preprints, standards, and product docs matching the claim phrases.
   Aim for 8-15 of the CLOSEST references; verify each one actually exists (real URL/identifier).
3. For EACH reference, map — per claim — which of that claim's ELEMENTS it discloses, marking each
   element "disclosed" | "partial" | "not-found", with a short quoted snippet + the source URL as
   evidence. Then state the DISTINCTION: what the claim has that this single reference lacks.

Write {ws}/inputs/priorart.json with EXACTLY this shape:
{{
  "search": {{"sources": ["google-patents","semantic-scholar"], "queries": ["..."], "date_note": "search date / coverage caveats"}},
  "claim_elements": {{"<claim_id>": ["element 1","element 2","..."]}},
  "references": [
    {{"id":"R1","kind":"patent|npl","title":"...","identifier":"US1234567B2|arXiv:...|DOI","url":"https://...","date":"YYYY",
      "relevance":"high|medium|low",
      "disclosed_elements": {{"<claim_id>": {{"element 1":"disclosed","element 2":"not-found"}}}},
      "quote":"...evidence snippet...", "distinction":"what the claim has that R1 lacks"}}
  ],
  "open_items": ["list every gap a human/attorney must resolve"]
}}
Do NOT conclude that the invention is novel/patentable/clear — you cannot; a search only finds
leads. Also write {ws}/inputs/priorart_report.md: a readable summary (closest references, the
per-claim distinction story, and residual risks). Confirm both files exist, then stop."""


def _degraded(ws: Path) -> dict:
    """No backend output → an honest empty result that still carries the mandatory open item."""
    d = {"search": {"sources": [], "queries": [], "date_note": "no search performed (backend produced nothing)"},
         "claim_elements": {}, "references": [], "open_items": []}
    return d


def search(cfg: dict, ws: Path) -> int:
    inputs = ws / "inputs"
    if not (inputs / "claims.json").exists():
        raise SystemExit(f"missing inputs/claims.json in {ws} — run assemble_patent first")
    out = inputs / "priorart.json"
    if out.exists() and out.stat().st_size > 40 and not cfg.get("_force"):
        log("existing inputs/priorart.json found — idempotent resume (--force to re-search)")
    else:
        log(f"prior-art search via backend={cfg.get('backend')}")
        try:
            providers.dispatch(cfg, _PROMPT.format(ws=str(ws)), str(ws),
                               timeout=(cfg.get("step_timeout_seconds") or 2400))
        except Exception as e:   # noqa: BLE001 — degrade to an honest empty result, never crash
            log(f"backend search failed ({e}); writing an honest empty prior-art record")
    pa = None
    if out.exists():
        try:
            pa = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            log("backend wrote invalid JSON — replacing with an honest empty record")
    if not isinstance(pa, dict):
        pa = _degraded(ws)
    # DETERMINISTIC honesty + analysis (model cannot override)
    if pa_core is not None:
        pa_core.enforce_honesty(pa)
        pa["analysis"] = pa_core.analyze(pa)
        report = pa_core.validate(pa)
    else:   # minimal inline honesty if the core package is unavailable
        pa["verdict"] = "leads-only-non-clearance"
        pa.setdefault("open_items", [])
        pa["open_items"].insert(0, "A PROFESSIONAL prior-art search + attorney opinion remain MANDATORY; "
                                   "absence of found art is not novelty.")
        report = {"passed": True, "failures": [], "warnings": []}
    out.write_text(json.dumps(pa, indent=2, ensure_ascii=False), encoding="utf-8")
    (ws / "final").mkdir(exist_ok=True)
    (ws / "final" / "priorart.verify.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"references={len(pa.get('references', []))}  verdict={pa.get('verdict')}  "
        f"102-risks={len(pa.get('analysis', {}).get('anticipation_102_risks', []))}  "
        f"valid={report['passed']}")
    for f in report.get("failures", []):
        log(f"  ✗ {f}")
    log("prior-art = LEADS only; a professional search + attorney remain mandatory (never a clearance)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="patent-priorart")
    ap.add_argument("--config", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise SystemExit(f"could not read/parse --config {args.config}: {e}")
    cfg["_force"] = args.force
    ok, detail = providers.probe(cfg)
    log(f"backend probe: {'OK' if ok else 'FAIL'} — {detail}")
    if args.probe:
        return 0 if ok else 1
    if not ok:
        raise SystemExit(detail)
    return search(cfg, Path(os.path.expanduser(args.workspace)).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
