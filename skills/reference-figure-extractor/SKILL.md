---
name: reference-figure-extractor
description: Extract figures from source-paper PDFs as (image · in-text reference context · description) TRIPLES for study/survey writing and related-work grounding. For each figure it renders the image, captures every place the body text references it (referencing sentence + a surrounding window so context is preserved), and produces a detailed image description (caption deterministically, VLM on top). Every record carries THIRD-PARTY provenance so a mined figure is never presented as the author's own result. TRIGGER when the user asks to "extract figures from these papers", "pull the figures and where they're cited", "build a figure library for a survey", or when the deep literature survey wants source figures.
---

# Reference Figure Extractor

For a **study / survey paper** (and to ground related-work in any paper) you often need the figures
of the works you cite — not to copy them, but to discuss them accurately. This skill turns a source
PDF into a set of **figure triples**:

| Part | What | How (floor → ceiling) |
|---|---|---|
| **① image** | the figure itself, rendered to PNG | PyMuPDF clip render (raster+vector) → poppler page-level dump → text-only |
| **② reference context** | every in-text mention ("As Figure 3 shows…"), each with a **surrounding sentence window** so the context is kept | deterministic regex + sentence-window (model-free) |
| **③ description** | a detailed account of what the figure depicts | caption text (deterministic) → VLM render-and-look (model-dependent) |

**Honesty invariant (non-negotiable):** an extracted figure is **third-party** — evidence of what a
*cited* work showed. Every record carries `source` provenance (pdf, page, extractor, `third_party:
true`, `license: "unknown"`, a usage note). It must **never** be presented as the author's own
result, and reuse of the actual image needs a license/permission check the tool records but does not
assert.

## Two-step pipeline

### 1. Extract (deterministic — always runs)

```bash
python skills/reference-figure-extractor/scripts/extract_figures.py \
    --pdf path/to/source_paper.pdf --out workspace/reffigs/<paper_id> \
    [--context-chars 350] [--zoom 2.0]
```

Writes `workspace/reffigs/<paper_id>/figures_manifest.json` and
`.../figures/fig<N>.png`. Each record:

```json
{
  "figure_id": "fig3", "figure_number": "3",
  "caption": "Figure 3: ...",
  "image": "figures/fig3.png",
  "references": [{"page": 2, "context": "<prev sent> As Figure 3 shows, latency ... <next sent>"}],
  "reference_count": 2,
  "description": null,
  "source": {"pdf": "source_paper.pdf", "page": 4, "extractor": "pymupdf-clip",
             "third_party": true, "attribution_required": true, "license": "unknown",
             "usage_note": "Third-party figure ... NOT the author's own result; reuse needs a license check."}
}
```

**Fidelity tiers (auto-selected, graceful degradation):**

1. **PyMuPDF present** — best. Renders the figure *region* clip (works for vector plots AND embedded
   rasters), tight caption↔figure association, per-page text for references.
2. **PyMuPDF absent, poppler present** (this environment) — captions + reference windows from
   `pdftotext -layout`; images from `pdfimages` (embedded rasters) or a rendered page (`pdftoppm`).
   Association is **page-level** (recorded honestly in `extractor`).
3. **Neither** — the manifest reports it cannot read the PDF (install `pymupdf`).

Caption vs mention: a **caption** is a line starting `Figure N:` / `Figure N.` (a separator is
required — this is what distinguishes a caption from a body line that merely *begins* with a
reference like "Figure 1 also shows…"). The caption's own token is excluded from `references`.

### 2. Describe (model-dependent ceiling — optional)

```bash
python skills/reference-figure-extractor/scripts/describe_figures.py \
    --config backend.json --manifest workspace/reffigs/<paper_id>/figures_manifest.json
```

With a `vision_model` in `backend.json`, a VLM looks at each PNG and writes a grounded description
back into `description` (`description_source: "vlm:<model>"`). Without one, it falls back to the
caption (`description_source: "caption-only"`) — the pipeline still completes.

## Where this plugs into Veridraft

- **Survey / study papers:** run the extractor over the top-cited PDFs the
  [`literature-review-agent`](../literature-review-agent/SKILL.md) deep-survey mode surfaces, then
  cite/discuss each figure with its real context — never as an own result.
- **Any paper's related work:** grounds "prior method X uses architecture Y (their Fig. 3)" in the
  actual source figure + the sentence that framed it.
- The third-party `source` block is the governance hook: these figures are *inputs/evidence about
  others' work*, kept distinct from the author's own gated results and figures.

## Resources

- `scripts/extract_figures.py` — deterministic triple extractor (image + reference windows + caption)
- `scripts/describe_figures.py` — VLM description layer (degrades to caption-only)
