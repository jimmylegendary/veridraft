#!/usr/bin/env python3
"""Fill the DESCRIPTION third of each figure triple — the model-dependent ceiling.

extract_figures.py produces the deterministic two-thirds (image + windowed reference context +
caption). This pass asks a VLM to LOOK at each figure and write a detailed, grounded description,
writing it back into `description` in the manifest. It NEVER invents findings the figure doesn't
show, and it is told the figure is THIRD-PARTY (from a cited work) so the description stays a
neutral account of what the source figure depicts — not a claim of the author's own result.

Degrades gracefully: with no `vision_model` configured (or providers unavailable), it falls back to
a caption-derived description and flags `description_source: "caption-only"`, so the pipeline still
runs — quality scales with the connected model, the contract does not.

    python3 describe_figures.py --config backend.json --manifest <dir>/figures_manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_providers():
    """Best-effort import of the PaperOrchestra backend dispatcher (sibling paperorchestra/)."""
    here = Path(__file__).resolve()
    for cand in (here.parents[3] / "paperorchestra", here.parents[4] / "paperorchestra"):
        if (cand / "providers.py").exists():
            sys.path.insert(0, str(cand))
            break
    try:
        import providers
        return providers
    except ImportError:
        return None


_PROMPT = """LOOK at this figure image and write a DETAILED, factual description of what it depicts.
This figure is THIRD-PARTY — it comes from a cited source paper — so describe ONLY what is visibly
in the image; do NOT claim it as anyone's own result and do NOT invent numbers or findings the
figure does not show. Cover: the figure type (architecture/flow/plot/table/photo), the components
and how they connect or the axes/trends, any labels/legend, and the single main takeaway a reader
should get. 4-8 sentences, plain prose (no markdown headings).

Caption (verbatim from the source): {caption}

How the source text refers to it (for context only):
{refs}

Image file: {image}
Write ONLY the description paragraph."""


def describe(cfg: dict, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent
    providers = _load_providers()
    vm = cfg.get("vision_model")
    for fig in manifest.get("figures", []):
        img = fig.get("image")
        refs = "\n".join(f"- {r['context']}" for r in fig.get("references", [])[:5]) or "- (none)"
        cap = fig.get("caption", "")
        if img and vm and providers is not None:
            img_abs = str((base / img).resolve())
            prompt = _PROMPT.format(caption=cap, refs=refs, image=img_abs)
            try:
                out = providers.dispatch({**cfg, "model": vm}, prompt, str(base),
                                         timeout=int(cfg.get("step_timeout_seconds", 600)))
                fig["description"] = out.strip()
                fig["description_source"] = f"vlm:{vm}"
                continue
            except Exception as e:   # noqa: BLE001 — a VLM failure must degrade, not crash
                print(f"[describe-figures] VLM failed for {fig.get('figure_id')} ({e}); caption fallback")
        # deterministic fallback: the caption IS a description seed (honest about the degradation)
        fig["description"] = (cap or f"Figure {fig.get('figure_number','?')} (no caption extracted).")
        fig["description_source"] = "caption-only"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="describe-figures")
    ap.add_argument("--config", help="backend.json (needs vision_model for real descriptions)")
    ap.add_argument("--manifest", required=True)
    args = ap.parse_args(argv)
    cfg = {}
    if args.config and Path(args.config).exists():
        cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    m = describe(cfg, Path(args.manifest))
    vlm = sum(1 for f in m["figures"] if f.get("description_source", "").startswith("vlm"))
    print(f"[describe-figures] described {len(m['figures'])} figure(s): {vlm} via VLM, "
          f"{len(m['figures']) - vlm} caption-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
