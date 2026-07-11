#!/usr/bin/env python3
"""Semantic Scholar Graph API client for citation-graph SNOWBALLING (deep-survey mode).

Thin network layer over the S2 Academic Graph:
  - references(paper_id) : GET /graph/v1/paper/{id}/references  (backward snowballing)
  - citations(paper_id)  : GET /graph/v1/paper/{id}/citations   (forward snowballing)
  - batch(ids)           : POST /graph/v1/paper/batch           (cheap metadata hydration, <=500)

Honors the real constraints found for the current API: offset/limit paging with limit<=1000; a
keyless shared pool that 429s under load (self-throttle ~1 rps + exponential backoff); an optional
SEMANTIC_SCHOLAR_API_KEY (x-api-key header) for a stable rate. Every response is cached on disk so
re-runs and the pure snowball orchestrator never re-hit the network. Failures degrade to [] (a WARN),
never an exception — the survey completes on whatever the network returned.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_BASE = "https://api.semanticscholar.org/graph/v1"
_FIELDS = ("paperId,title,abstract,year,authors,venue,externalIds,citationCount,"
           "referenceCount,fieldsOfStudy,s2FieldsOfStudy,isOpenAccess")
_THROTTLE = 1.1          # seconds between live requests (keyless shared pool is brutal)
_last = [0.0]


def _key() -> str | None:
    return os.environ.get("SEMANTIC_SCHOLAR_API_KEY")


def _throttle() -> None:
    dt = time.time() - _last[0]
    if dt < _THROTTLE:
        time.sleep(_THROTTLE - dt)
    _last[0] = time.time()


def _get(url: str, retries: int = 4) -> dict | None:
    hdr = {"User-Agent": "veridraft-deep-survey"}
    if _key():
        hdr["x-api-key"] = _key()
    for attempt in range(retries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)      # exponential backoff on rate limit
                continue
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
    return None


def _post(url: str, body: dict, retries: int = 4) -> list | None:
    hdr = {"User-Agent": "veridraft-deep-survey", "Content-Type": "application/json"}
    if _key():
        hdr["x-api-key"] = _key()
    data = json.dumps(body).encode("utf-8")
    for attempt in range(retries):
        _throttle()
        try:
            req = urllib.request.Request(url, data=data, headers=hdr, method="POST")
            with urllib.request.urlopen(req, timeout=45) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
    return None


class S2Cache:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, k):
        return self.data.get(k)

    def put(self, k, v):
        self.data[k] = v
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


def _edge_list(paper_id: str, kind: str, cache: S2Cache | None, max_pages: int = 5) -> list[dict]:
    """Paged references/citations → normalized paper dicts carrying `isInfluential`."""
    ck = f"{kind}:{paper_id}"
    if cache and cache.get(ck) is not None:
        return cache.get(ck)
    nested = "citedPaper" if kind == "references" else "citingPaper"
    out, offset = [], 0
    for _ in range(max_pages):
        q = urllib.parse.urlencode({"fields": _FIELDS, "offset": offset, "limit": 1000})
        resp = _get(f"{_BASE}/paper/{urllib.parse.quote(paper_id)}/{kind}?{q}")
        if not resp or "data" not in resp:
            break
        for item in resp["data"]:
            p = item.get(nested) or {}
            if p.get("paperId"):
                p = dict(p)
                p["isInfluential"] = bool(item.get("isInfluential"))
                p["edge"] = kind
                out.append(p)
        if len(resp["data"]) < 1000 or "next" not in resp:
            break
        offset += 1000
    if cache:
        cache.put(ck, out)
    return out


def references(paper_id: str, cache: S2Cache | None = None) -> list[dict]:
    return _edge_list(paper_id, "references", cache)


def citations(paper_id: str, cache: S2Cache | None = None) -> list[dict]:
    return _edge_list(paper_id, "citations", cache)


def batch(ids: list[str], cache: S2Cache | None = None) -> list[dict]:
    """Hydrate up to 500 ids/call via POST /paper/batch."""
    out: list[dict] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        resp = _post(f"{_BASE}/paper/batch?fields={_FIELDS}", {"ids": chunk})
        if isinstance(resp, list):
            out += [p for p in resp if isinstance(p, dict) and p.get("paperId")]
    return out


def fetch_edges(paper_id: str, cache: S2Cache | None = None) -> list[dict]:
    """Both directions merged (backward references + forward citations)."""
    return references(paper_id, cache) + citations(paper_id, cache)
