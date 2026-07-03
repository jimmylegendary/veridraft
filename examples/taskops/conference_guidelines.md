# Conference Guidelines — Systems Venue (TaskOps)

- **Venue:** Systems / infrastructure track (systems venue) for agentic execution-control and developer tooling.
- **Page limit:** 8-10 pages (excluding references).
- **Mandatory sections:** Abstract, Introduction, Related Work, System Design, Evaluation, Conclusion.
- **Format:** single-column LaTeX `article` document class; `natbib` citations with the `plainnat` bibliography style.
- **Submission deadline:** 2026-10-01 (this date is the literature **temporal cutoff** — do not cite work published after it).
- **Evaluation stance:** This is a **design + functional-validation** paper, not a performance paper. Evaluation is **functional (end-to-end)**, not throughput/latency benchmarking. TaskOps has **no performance benchmark**, so the paper must not report any latency, throughput, speedup, or cost-per-token numbers.
- **Reproducibility / number tracing:** Every number that appears in the paper MUST trace to a `result_id` in `bundle.json` (e.g. the 5/5 end-to-end pass rate traces to `r_e2e`; the 4 run-readiness states to `r_readiness_states`; the retry/attempt/loopback cap of 3 to `r_retry_bounds`; the 3 distribution channels to `r_distribution_channels`). **Do not invent benchmarks or numbers** — no fabricated performance results.
- **Evidence stance:** Each contribution (claim) must be substantiated by concrete in-repo source artifacts (design docs and CLI source at a pinned commit), not by generated prose. Claims are typed P1 (core method/model) or P2 (tooling/engineering).
- **Scope:** agentic execution control for AI coding agents; task-graph/run-graph separation; run-readiness gating; delegated-waiting and exploratory runs for unknown-unknowns; markdown-first storage with a derived SQLite queue/lease projection; versioned task-groups and snapshots; lease-based one-shot worker execution; work-level claim-safety auditing.
