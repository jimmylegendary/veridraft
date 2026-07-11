#!/usr/bin/env python3
"""Thematic clustering of a snowballed pool → survey section buckets (dependency-free).

A deep survey needs the pool organized into themes. Two model-free strategies, best-effort:
  - COARSE: group by primary fieldsOfStudy / s2FieldsOfStudy (when S2 supplies them).
  - FINE:   TF-IDF over title+abstract + greedy cosine clustering (pure Python; the zero-dependency
            fallback when fields are missing). Each cluster is labeled by its top TF-IDF terms.

This is the deterministic organizer; a model can later name/refine the sections, but the grouping
holds without one.
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

_STOP = set("the a an and or of to in for on with is are was were be by this that these those we our "
            "using based via from as at it its their they can not but also which such into than then "
            "more most other data paper method model results approach show propose use used".split())


def _tokens(paper: dict) -> list[str]:
    text = f"{paper.get('title','')} {paper.get('abstract','') or ''}".lower()
    return [t for t in re.findall(r"[a-z][a-z0-9+\-]{2,}", text) if t not in _STOP]


def _tfidf(docs: list[list[str]]) -> tuple[list[dict], dict]:
    n = len(docs)
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    idf = {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}
    vecs = []
    for d in docs:
        tf = Counter(d)
        v = {t: (f / max(len(d), 1)) * idf[t] for t, f in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs.append({t: x / norm for t, x in v.items()})
    return vecs, idf


def _cos(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def cluster(papers: list[dict], sim_threshold: float = 0.12, max_clusters: int = 12) -> list[dict]:
    """Greedy cosine clustering over TF-IDF vectors. Deterministic in input order."""
    if not papers:
        return []
    docs = [_tokens(p) for p in papers]
    vecs, _ = _tfidf(docs)
    clusters: list[dict] = []               # {centroid, members:[idx]}
    for i, v in enumerate(vecs):
        best, bj = 0.0, -1
        for j, cl in enumerate(clusters):
            s = _cos(v, cl["centroid"])
            if s > best:
                best, bj = s, j
        if bj >= 0 and best >= sim_threshold and len(clusters) >= 1:
            cl = clusters[bj]
            cl["members"].append(i)
            for t, x in v.items():          # running-mean centroid
                cl["centroid"][t] = cl["centroid"].get(t, 0.0) + x
        elif len(clusters) < max_clusters:
            clusters.append({"centroid": dict(v), "members": [i]})
        else:                               # cap reached → attach to nearest regardless
            (clusters[bj] if bj >= 0 else clusters[0])["members"].append(i)
    out = []
    for k, cl in enumerate(clusters):
        agg: Counter = Counter()
        for idx in cl["members"]:
            agg.update(docs[idx])
        terms = [t for t, _ in agg.most_common(6)]
        out.append({"cluster_id": k, "label": " / ".join(terms[:3]), "terms": terms,
                    "size": len(cl["members"]),
                    "paper_ids": [papers[idx].get("paperId") or papers[idx].get("title") for idx in cl["members"]]})
    return sorted(out, key=lambda c: -c["size"])


def by_field(papers: list[dict]) -> list[dict]:
    """Coarse buckets by primary field of study (when S2 supplies them)."""
    buckets: dict[str, list] = {}
    for p in papers:
        fos = (p.get("fieldsOfStudy") or []) or [
            d.get("category") for d in (p.get("s2FieldsOfStudy") or []) if isinstance(d, dict)]
        key = (fos[0] if fos else "Uncategorized")
        buckets.setdefault(key, []).append(p.get("paperId") or p.get("title"))
    return sorted(({"field": k, "size": len(v), "paper_ids": v} for k, v in buckets.items()),
                  key=lambda b: -b["size"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cluster-pool")
    ap.add_argument("--pool", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sim", type=float, default=0.12)
    args = ap.parse_args(argv)
    papers = json.loads(Path(args.pool).read_text(encoding="utf-8"))
    result = {"clusters": cluster(papers, sim_threshold=args.sim), "by_field": by_field(papers),
              "pool_size": len(papers)}
    Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[cluster-pool] {len(papers)} papers → {len(result['clusters'])} theme cluster(s), "
          f"{len(result['by_field'])} field bucket(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
