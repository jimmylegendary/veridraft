#!/usr/bin/env python
"""Binoculars AI-text detector runner (arXiv:2401.12070), CPU-only.

This script runs INSIDE the detector venv (`~/.veridraft-detector-venv`) which carries
`torch` + `transformers`. The stdlib Veridraft core NEVER imports it directly — the
`binoculars-local` DetectorAdapter shells out to this file, so torch/transformers
stay out of the harness runtime (same pattern as the LaTeX renderer subprocess).

Algorithm (Hans et al., 2024):

    score = log-perplexity(text | observer) / cross-perplexity(observer, performer)

  * log-perplexity  = mean per-token negative log-likelihood of `text` under the
                      OBSERVER model (a base LM).
  * cross-perplexity = mean over token positions of the cross-entropy
                      H(softmax(observer_logits), softmax(performer_logits)),
                      i.e. how much the two models disagree on the next token.

  Low score  -> text is easy for the observer AND the two models agree  -> LIKELY MACHINE.
  High score -> text surprises the observer / the models disagree        -> LIKELY HUMAN.

The 0.9015 threshold in the paper is calibrated for a Falcon-7B pair. This 0.5B
pair is WEAKER, so the adapter uses a locally recalibrated threshold
(`detector_calibration.json`) and only ever emits a RISK band, never a verdict.

I/O contract (consumed by adapters/detector_binoculars.py):
    input : text via a file path arg, else stdin
    output: one JSON object on stdout:
        {"binoculars_score": float, "log_perplexity": float, "cross_perplexity": float,
         "observer": str, "performer": str, "n_tokens": int, "too_short": bool}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

OBSERVER = os.environ.get("BINO_OBSERVER", "Qwen/Qwen2.5-0.5B")
PERFORMER = os.environ.get("BINO_PERFORMER", "Qwen/Qwen2.5-0.5B-Instruct")
MAX_TOKENS = int(os.environ.get("BINO_MAX_TOKENS", "512"))
MIN_TOKENS = int(os.environ.get("BINO_MIN_TOKENS", "50"))


def strip_latex(text: str) -> str:
    """Light LaTeX -> prose cleanup so perplexity is measured on words, not markup.

    The minimal-latex engine wraps assembled prose in `verbatim` blocks and real
    PaperOrchestra output is full LaTeX; either way we want the natural-language
    tokens, not `\\section{...}` control sequences.
    """
    text = re.sub(r"(?<!\\)%.*", "", text)                      # line comments
    text = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", " ", text)  # env markers
    text = re.sub(r"\\[a-zA-Z@]+\*?", " ", text)                 # control words
    text = re.sub(r"[{}$&#~^\\]", " ", text)                     # stray specials
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Binoculars AI-text RISK scorer (CPU)")
    ap.add_argument("path", nargs="?", help="text file to score; omit to read stdin")
    ap.add_argument("--observer", default=OBSERVER)
    ap.add_argument("--performer", default=PERFORMER)
    ap.add_argument("--max-tokens", type=int, default=MAX_TOKENS)
    ap.add_argument("--min-tokens", type=int, default=MIN_TOKENS)
    ap.add_argument("--raw", action="store_true", help="skip LaTeX stripping")
    args = ap.parse_args()

    if args.path:
        with open(args.path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    if not args.raw:
        text = strip_latex(text)

    if not text.strip():
        print(json.dumps({"error": "empty text", "n_tokens": 0, "too_short": True,
                          "binoculars_score": None}))
        return 2

    # Heavy deps live here, never in the veridraft core import path.
    import torch
    import torch.nn.functional as F
    from transformers import AutoModelForCausalLM, AutoTokenizer

    torch.manual_seed(0)
    try:
        torch.set_num_threads(max(1, os.cpu_count() or 1))
    except Exception:
        pass

    tok = AutoTokenizer.from_pretrained(args.observer)
    # float32 on CPU (bf16/fp16 CPU kernels are slow / unsupported for these ops).
    observer = AutoModelForCausalLM.from_pretrained(args.observer, torch_dtype=torch.float32)
    performer = AutoModelForCausalLM.from_pretrained(args.performer, torch_dtype=torch.float32)
    observer.eval()
    performer.eval()

    enc = tok(text, return_tensors="pt", truncation=True, max_length=args.max_tokens)
    ids = enc["input_ids"]
    n_tokens = int(ids.shape[1])

    if n_tokens < 2:
        print(json.dumps({"error": "too few tokens", "n_tokens": n_tokens,
                          "too_short": True, "binoculars_score": None}))
        return 2

    with torch.no_grad():
        obs_logits = observer(**enc).logits          # [1, L, V]
        perf_logits = performer(**enc).logits         # [1, L, V]

    # Next-token positions only (shift by one) for BOTH metrics.
    obs = obs_logits[:, :-1, :]
    perf = perf_logits[:, :-1, :]
    labels = ids[:, 1:]
    vocab = obs.shape[-1]

    # log-perplexity under the observer = mean per-token NLL.
    obs_logp = F.log_softmax(obs, dim=-1)
    nll = F.nll_loss(obs_logp.reshape(-1, vocab), labels.reshape(-1), reduction="mean")
    log_ppl = float(nll.item())

    # cross-perplexity = mean_t H(softmax(observer_t), softmax(performer_t)).
    p = F.softmax(obs, dim=-1)                         # observer distribution
    logq = F.log_softmax(perf, dim=-1)                # performer log-distribution
    xce = -(p * logq).sum(dim=-1)                      # [1, L-1]
    cross_ppl = float(xce.mean().item())

    score = log_ppl / cross_ppl if cross_ppl > 0 else None

    print(json.dumps({
        "binoculars_score": score,
        "log_perplexity": log_ppl,
        "cross_perplexity": cross_ppl,
        "observer": args.observer,
        "performer": args.performer,
        "n_tokens": n_tokens,
        "too_short": n_tokens < args.min_tokens,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
