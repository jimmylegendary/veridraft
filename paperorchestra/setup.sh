#!/usr/bin/env bash
#
# Self-host setup + checker for the portable PaperOrchestra runner.
# Verifies (and best-effort installs, non-privileged) every LOCAL dependency a run
# needs, and checks the configured backend. Prints a PASS/WARN checklist.
#
#   bash setup.sh [path/to/paperorchestra.config.json]
#
# System packages that need admin (TeX Live, poppler) are CHECKED and the install
# command is printed — this script never sudo's for you.
set -u
CFG="${1:-}"
PASS=0; WARN=0
ok(){ printf '  [OK]   %s\n' "$1"; PASS=$((PASS+1)); }
warn(){ printf '  [WARN] %s\n' "$1"; WARN=$((WARN+1)); }
have(){ command -v "$1" >/dev/null 2>&1; }

echo "== 1. core =="
have python3 && ok "python3 ($(python3 --version 2>&1))" || warn "python3 missing"

echo "== 2. LaTeX (final PDF) =="
if have latexmk && have pdflatex && have bibtex; then
  ok "latexmk + pdflatex + bibtex present"
  # required style packages for common conference templates
  for sty in cleveref nicefrac microtype booktabs; do
    if kpsewhich "$sty.sty" >/dev/null 2>&1; then ok "sty: $sty"; else
      warn "missing $sty.sty  → install:  tlmgr install $sty   (or: tlmgr install scheme-full)"; fi
  done
else
  warn "TeX Live incomplete → install a full TeX Live (Ubuntu: sudo apt install texlive-full; mac: brew install --cask mactex). Without it the runner cannot compile the PDF."
fi

echo "== 3. figures (plotting) =="
VENV="${PO_MPL_VENV:-$HOME/.veridraft-detector-venv}"
if [ -x "$VENV/bin/python" ] && "$VENV/bin/python" -c "import matplotlib,numpy" 2>/dev/null; then
  ok "matplotlib+numpy venv: $VENV"
else
  warn "no matplotlib venv → create one:  python3 -m venv $VENV && $VENV/bin/pip install matplotlib numpy   (plotting agent will use it; PEP-668 safe)"
fi

echo "== 4. PDF egress scan (Veridraft) =="
have pdftotext && ok "pdftotext (poppler)" || warn "pdftotext missing → Veridraft egress fails closed on PDF-only output. Install: sudo apt install poppler-utils / brew install poppler"

echo "== 5. literature verification =="
if curl -sS --max-time 8 "https://api.semanticscholar.org/graph/v1/paper/search/match?query=attention+is+all+you+need" >/dev/null 2>&1; then
  ok "Semantic Scholar reachable (set s2_api_key in config to raise the 1 QPS limit)"
else warn "Semantic Scholar not reachable → literature review will degrade (network/egress?)"; fi

echo "== 6. backend =="
if [ -n "$CFG" ] && [ -f "$CFG" ]; then
  python3 "$(dirname "$0")/run.py" --config "$CFG" --workspace /tmp --probe && ok "backend probe passed ($CFG)" || warn "backend probe failed — see config"
else
  for b in claude openclaw codex opencode aider; do have "$b" && ok "backend CLI available: $b" || true; done
  warn "no config given — pass paperorchestra.config.json to probe the chosen backend"
fi

echo; echo "SUMMARY: $PASS ok, $WARN warn"
[ "$WARN" -eq 0 ] && echo "READY ✅" || echo "Resolve WARN items above before a full run."
