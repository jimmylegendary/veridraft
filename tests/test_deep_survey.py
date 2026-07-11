"""Tests for deep-survey mode: snowball saturation/dedup/fan-out + thematic clustering (offline)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "literature-review-agent" / "scripts"))
import cluster_pool
import snowball


def _p(pid, title, abstract="", infl=False, cites=0, fields=None):
    return {"paperId": pid, "title": title, "abstract": abstract, "isInfluential": infl,
            "citationCount": cites, "fieldsOfStudy": fields or ["Computer Science"]}


class SnowballTest(unittest.TestCase):
    def test_saturates_and_dedups(self):
        # graph: s1 -> a,b ; a -> b,c ; c -> (nothing new). All relevant. b reached twice (dedup).
        graph = {"s1": [_p("a", "scheduling latency"), _p("b", "batching latency")],
                 "a": [_p("b", "batching latency"), _p("c", "queueing latency")],
                 "b": [], "c": []}
        pool, rep = snowball.snowball([_p("s1", "adaptive scheduling latency")],
                                      lambda pid: graph.get(pid, []),
                                      lambda p: 1.0, max_rounds=4, newrel_stop=1, yield_stop=0.01)
        ids = {p["paperId"] for p in pool}
        self.assertEqual(ids, {"s1", "a", "b", "c"})           # b not double-counted
        self.assertEqual(rep["stop_reason"], "no-new-papers")

    def test_relevance_gate_filters(self):
        graph = {"s1": [_p("a", "totally unrelated botany flowers"), _p("b", "scheduling latency gpu")]}
        centroid = {"scheduling", "latency", "gpu"}
        pool, rep = snowball.snowball([_p("s1", "gpu scheduling latency")],
                                      lambda pid: graph.get(pid, []),
                                      snowball.keyword_relevance(centroid),
                                      max_rounds=2, relevance_min=0.2)
        ids = {p["paperId"] for p in pool}
        self.assertIn("b", ids)
        self.assertNotIn("a", ids)                             # off-topic candidate rejected

    def test_fanout_cap_prefers_influential(self):
        edges = [_p(f"x{i}", f"paper {i}", cites=i) for i in range(10)]
        edges.append(_p("infl", "influential paper", infl=True, cites=0))
        capped = snowball._cap_fanout(edges, 3)
        self.assertEqual(capped[0]["paperId"], "infl")         # influential first
        self.assertEqual(len(capped), 3)

    def test_hard_round_cap(self):
        # an infinite fresh-paper generator must still stop at the round cap
        def fetch(pid):
            n = int(pid.split("_")[-1]) if "_" in pid else 0
            return [_p(f"g_{n+1}", f"scheduling paper {n+1}", cites=100)]
        pool, rep = snowball.snowball([_p("g_0", "scheduling seed")], fetch, lambda p: 1.0,
                                      max_rounds=3, newrel_stop=1, yield_stop=0.0)
        self.assertEqual(rep["rounds_run"], 3)
        self.assertEqual(rep["stop_reason"], "max-rounds")


class ClusterTest(unittest.TestCase):
    def test_groups_by_theme(self):
        papers = [
            _p("1", "sparse attention transformers long context"),
            _p("2", "efficient sparse attention for transformers"),
            _p("3", "reinforcement learning for robot locomotion"),
            _p("4", "robot locomotion via reinforcement learning policies"),
        ]
        clusters = cluster_pool.cluster(papers, sim_threshold=0.05)
        # the two attention papers and the two RL papers should not all collapse into one cluster
        self.assertGreaterEqual(len(clusters), 2)
        sizes = sorted(c["size"] for c in clusters)
        self.assertEqual(sizes[-1], 2)

    def test_by_field_buckets(self):
        papers = [_p("1", "a", fields=["Computer Science"]), _p("2", "b", fields=["Biology"]),
                  _p("3", "c", fields=["Computer Science"])]
        buckets = cluster_pool.by_field(papers)
        cs = next(b for b in buckets if b["field"] == "Computer Science")
        self.assertEqual(cs["size"], 2)


if __name__ == "__main__":
    unittest.main()
