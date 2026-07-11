#!/usr/bin/env python3
"""Deep-survey SNOWBALLING orchestrator — the model-free core of deep-survey mode.

Given a seed set of verified papers, iteratively expand along the citation graph (backward
references + forward citations), screening each candidate for topical relevance, until the pool
SATURATES (a round adds few/no new relevant papers and no new theme). This is the standard
systematic-review expansion method; it turns a thin one-round search into exhaustive coverage while
the S2-verify + relevance gate keep precision high.

The orchestration (rounds · dedup · fan-out cap · saturation stop · coverage report) is PURE and
deterministic — `fetch_edges` and `relevance` are injected, so the whole loop is unit-testable
offline. `snowball.py --config ...` wires the real S2 graph client + a keyword relevance gate.

Stopping (any one, after >=1 full round): (a) new_relevant < newrel_stop AND yield < yield_stop;
(b) no NEW thematic field appeared AND yield < yield_stop; (c) hard round cap. Fan-out per node is
capped, influential + high-citation edges first, so a seminal seed can't explode the round.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def _fields(paper: dict) -> set[str]:
    fos = paper.get("fieldsOfStudy") or []
    s2 = paper.get("s2FieldsOfStudy") or []
    out = set(f for f in fos if f)
    out |= set(d.get("category") for d in s2 if isinstance(d, dict) and d.get("category"))
    return out


def keyword_relevance(centroid_terms: set[str]):
    """A cheap, dependency-free relevance gate: Jaccard-ish overlap of title+abstract terms with the
    survey centroid vocabulary. Returns a relevance(paper)->float in [0,1]."""
    def rel(paper: dict) -> float:
        text = f"{paper.get('title','')} {paper.get('abstract','') or ''}".lower()
        toks = set(re.findall(r"[a-z][a-z0-9+\-]{2,}", text))
        if not toks or not centroid_terms:
            return 0.0
        return len(toks & centroid_terms) / (len(centroid_terms) ** 0.5 + 1e-9) / 3.0
    return rel


def _cap_fanout(edges: list[dict], cap: int) -> list[dict]:
    """Influential-first, then high citationCount — bound a high-degree node's contribution."""
    return sorted(edges, key=lambda p: (not p.get("isInfluential"), -(p.get("citationCount") or 0)))[:cap]


def snowball(seeds, fetch_edges, relevance, *, max_rounds=4, fanout_cap=200,
            relevance_min=0.35, yield_stop=0.05, newrel_stop=5):
    """Expand `seeds` along the citation graph until saturation. Returns (pool, report).

    seeds: list[dict] with at least 'paperId' (already accepted). fetch_edges(paper_id)->list[dict]
    of candidate papers (references+citations, each ideally carrying isInfluential/citationCount/
    fieldsOfStudy). relevance(paper)->float; a candidate is admitted iff >= relevance_min.
    """
    pool = {p["paperId"]: p for p in seeds if p.get("paperId")}
    seen_fields = set().union(*[_fields(p) for p in pool.values()]) if pool else set()
    frontier = list(pool.values())
    rounds = []
    stop_reason = "max-rounds"
    for r in range(1, max_rounds + 1):
        candidates: dict[str, dict] = {}
        for p in frontier:
            for c in _cap_fanout(fetch_edges(p["paperId"]), fanout_cap):
                pid = c.get("paperId")
                if pid and pid not in pool and pid not in candidates:
                    candidates[pid] = c
        accepted = [c for c in candidates.values() if relevance(c) >= relevance_min]
        new_fields = set().union(*[_fields(c) for c in accepted]) - seen_fields if accepted else set()
        new_relevant = len(accepted)
        yield_ratio = new_relevant / max(len(pool), 1)
        for c in accepted:
            pool[c["paperId"]] = c
        seen_fields |= new_fields
        rounds.append({"round": r, "expanded_from": len(frontier), "candidates": len(candidates),
                       "new_relevant": new_relevant, "yield_ratio": round(yield_ratio, 4),
                       "new_fields": sorted(new_fields), "pool_size": len(pool)})
        frontier = accepted
        if new_relevant == 0:
            stop_reason = "no-new-papers"; break
        if new_relevant < newrel_stop and yield_ratio < yield_stop:
            stop_reason = "low-yield-saturation"; break
        if not new_fields and yield_ratio < yield_stop:
            stop_reason = "no-new-theme-saturation"; break
    report = {"seeds": len([s for s in seeds if s.get('paperId')]), "final_pool": len(pool),
              "rounds_run": len(rounds), "stop_reason": stop_reason,
              "themes": sorted(seen_fields), "per_round": rounds}
    return list(pool.values()), report


# ---- CLI: wire the real S2 client + keyword relevance -------------------------------------------

def _centroid_terms(*texts) -> set[str]:
    stop = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "our", "using",
            "based", "which", "can", "not", "but", "all", "via", "these", "such", "into", "than"}
    toks = re.findall(r"[a-z][a-z0-9+\-]{2,}", " ".join(texts).lower())
    freq: dict[str, int] = {}
    for t in toks:
        if t not in stop:
            freq[t] = freq.get(t, 0) + 1
    return {t for t, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:60]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="snowball")
    ap.add_argument("--seeds", required=True, help="JSON list of seed papers (paperId + title/abstract)")
    ap.add_argument("--idea", help="idea.md to derive the relevance centroid")
    ap.add_argument("--out", required=True, help="output dir for expanded_pool.json + saturation.json")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--max-rounds", type=int, default=4)
    ap.add_argument("--relevance-min", type=float, default=0.35)
    args = ap.parse_args(argv)
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    import s2_graph

    seeds = json.loads(Path(args.seeds).read_text(encoding="utf-8"))
    idea = Path(args.idea).read_text(encoding="utf-8") if args.idea and Path(args.idea).exists() else ""
    centroid = _centroid_terms(idea, *[f"{s.get('title','')} {s.get('abstract','') or ''}" for s in seeds])
    cache = s2_graph.S2Cache(Path(args.cache)) if args.cache else None
    pool, report = snowball(seeds, lambda pid: s2_graph.fetch_edges(pid, cache),
                            keyword_relevance(centroid), max_rounds=args.max_rounds,
                            relevance_min=args.relevance_min)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    (out / "expanded_pool.json").write_text(json.dumps(pool, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "saturation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[snowball] {report['seeds']} seeds → {report['final_pool']} papers in "
          f"{report['rounds_run']} round(s); stop={report['stop_reason']}; themes={len(report['themes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
