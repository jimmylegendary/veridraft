# Deep-survey mode — citation-graph snowballing to saturation

The base literature agent (§ SKILL.md) does **one round** of web-search discovery + Semantic
Scholar verification — enough for a normal paper's Related Work. **Deep-survey mode** layers
exhaustive **citation-graph snowballing** on top, so a *survey/study paper* (and a demanding
related-work section) reaches saturation rather than stopping at the first round. It reuses the
existing S2-verify step as its per-paper admission gate; it only adds recall.

Turn it on with `survey_depth` in `backend.json`:

| `survey_depth` | Behavior |
|---|---|
| `shallow` (default) | one-round discovery + verify (the base pipeline) |
| `deep` | seed → snowball rounds → relevance screen → saturation → cluster (this doc) |

## Pipeline

```
1. SEED     verified pool P0 from the base pipeline (10–40 papers spanning the known subtopics)
2. SNOWBALL for each paper accepted last round, fetch backward /references + forward /citations
            (offset paging, limit<=1000); cap per-node fan-out (influential + high-citation first)
3. SCREEN   dedup vs P by paperId; admit a candidate iff the relevance gate >= relevance_min
4. SATURATE per round compute new_relevant, yield = new_relevant/|P_before|, and new themes; STOP
            when saturated (below) or the hard round cap (4) is hit
5. CLUSTER  organize the accumulated pool into thematic buckets (survey sections)
6. EMIT     expanded_pool.json (verified, cluster-labeled) + saturation.json (coverage report)
```

### Scripts (model-free core; `fetch_edges`/`relevance` are injected → unit-tested offline)

```bash
# 2–4. snowball to saturation (wires the real S2 graph client + a keyword relevance centroid)
python skills/literature-review-agent/scripts/snowball.py \
    --seeds workspace/citation_pool.json --idea workspace/inputs/idea.md \
    --out workspace/survey --cache workspace/cache/s2_graph.json --max-rounds 4

# 5. cluster the expanded pool into themes
python skills/literature-review-agent/scripts/cluster_pool.py \
    --pool workspace/survey/expanded_pool.json --out workspace/survey/themes.json
```

- `s2_graph.py` — `references()`, `citations()`, `batch()` over `/graph/v1`; offset paging,
  ~1 rps self-throttle + 429 exponential backoff, disk cache, `SEMANTIC_SCHOLAR_API_KEY` honored,
  failures degrade to `[]`.
- `snowball.py` — pure `snowball(seeds, fetch_edges, relevance, ...)` returning `(pool, report)`.
- `cluster_pool.py` — pure TF-IDF greedy cosine clustering + coarse `by_field` buckets.

## Saturation stopping (any one, after ≥1 full round)

- `new_relevant == 0` → `no-new-papers`
- `new_relevant < 5` **and** `yield < 0.05` → `low-yield-saturation`
- no **new** thematic field appeared **and** `yield < 0.05` → `no-new-theme-saturation`
- round cap (default 4) → `max-rounds`

Snowballing converges in 2–3 rounds once the seed set is representative; the cap is a safety net.
A single thin round never trips the stop alone — low yield must co-occur with no-new-theme (or the
absolute new count must be tiny) so the graph isn't abandoned early.

## Guardrails (from the API + method)

- **Fan-out cap** (default 200/node, influential + high-citation first) — a seminal seed has
  thousands of citers; never expand them all or the round never finishes and blows the rate budget.
- **Rate limits are real** — keyless S2 is a shared pool that 429s under load; the client
  self-throttles to ~1 rps, backs off, and caches every response. Prefer a key for large surveys.
- **Relevance gate is separate from verify** — S2-verify only checks a paper *exists*; forward
  citers of a foundational paper wander off-topic, so the topical relevance gate (`relevance_min`)
  is what keeps precision up as recall grows.
- **Dedup strictly on `paperId`** — the same work recurs under DOI/arXiv/CorpusId; the orchestrator
  keys on paperId so coverage isn't inflated.
- **Verify still applies** — admitted papers go through the same Semantic Scholar verification +
  cutoff rules as the base pipeline before they can be cited. Deep mode widens the net; it does not
  relax the citation-integrity gate. Pair with `reference-figure-extractor` to pull the top papers'
  figures (with context) for the survey.
