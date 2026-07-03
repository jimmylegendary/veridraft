#!/usr/bin/env bash
#
# One-command verification of the Veridraft implementation. Run from anywhere:
#     bash products/veridraft/impl/verify.sh
#
# It compiles the package, runs the full test suite, and drives every acceptance
# scenario through the CLI end-to-end, then prints a PASS/FAIL board mapped to the
# governance guarantee each check proves. Exit code = number of failures (0 = all green).
#
# Requires only Python 3.10+ (stdlib). pdflatex/tectonic/cue are optional.
set -u
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
PASS=0; FAIL=0
LOG="$(mktemp)"

chk() { # $1 = exit status, $2 = description
  if [ "$1" -eq 0 ]; then printf '  [PASS] %s\n' "$2"; PASS=$((PASS+1));
  else printf '  [FAIL] %s\n' "$2"; FAIL=$((FAIL+1)); fi
}

T="examples/bundle_demo/template.tex"
G="examples/bundle_demo/conference_guidelines.md"
run() { "$PY" -m veridraft --data-dir "$D" "$@" 2>&1; }

echo "== 1. compile =="
"$PY" -m compileall -q veridraft tests; chk $? "package + tests compile"

echo "== 2. unit tests (the invariant contract) =="
"$PY" -m unittest discover -s tests >"$LOG" 2>&1; UT=$?
chk $UT "unittest suite — $(grep -Eo 'Ran [0-9]+ tests' "$LOG" | tail -1)"
[ $UT -ne 0 ] && tail -25 "$LOG"

echo "== 3. acceptance scenarios (end-to-end via the CLI) =="
D="$(mktemp -d)"

# L2/L4 — evidence gate → gated claim → PDF, and c3 (generated-text only) blocked
OUT="$(run run examples/bundle_demo/bundle.json --template $T --guidelines $G --audience public)"
echo "$OUT" | grep -q "egress: PUBLISHED"; chk $? "L2/L4/L6 public: gated claims → PDF → PUBLISHED"
echo "$OUT" | grep -q "\[BLOCK\] c3"; chk $? "L2 evidence gate: c3 (generated-text) BLOCKED"

# L6 — an internal claim must not reach a public-target assembly (blocked pre-draft)
OUT="$(run run examples/bundle_internal/bundle.json --template $T --guidelines $G --audience public)"
echo "$OUT" | grep -q "BLOCKED before drafting"; chk $? "L6 internal: internal claim blocked before drafting"

# L6 — a public-labeled codename is still caught by the egress redaction re-sweep
OUT="$(run run examples/bundle_redaction/bundle.json --template $T --guidelines $G --audience public)"
echo "$OUT" | grep -q "BLOCKED at egress"; chk $? "L6 redaction: embedded codename blocked at egress"

# L3a — a P3 claim is default-denied until the patent-first interlock is released
run import-bundle examples/bundle_patent/bundle.json >/dev/null
run gate demo-patent | grep -q "\[BLOCK\] pc1"; chk $? "L3a interlock: P3 pc1 BLOCKED while interlock HELD"
run release-interlock pc1 --reason "patent filed" >/dev/null
run gate demo-patent | grep -q "\[PASS\] pc1"; chk $? "L3a interlock: pc1 PASSES after human release"

# audit — lifecycle hash chain intact
OUT="$(run events)"
echo "$OUT" | grep -q "hash chain intact: True"; chk $? "audit: lifecycle hash chain intact"

rm -rf "$D" "$LOG"
echo
echo "SUMMARY: $PASS passed, $FAIL failed"
if [ "$FAIL" -eq 0 ]; then echo "ALL GREEN ✅"; else echo "FAILURES ✗ ($FAIL)"; fi
exit "$FAIL"
