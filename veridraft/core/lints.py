"""Deterministic patent/paper readiness gates (from the CAW-07 Loom review, feedback codes
P1-P12 / D1-D15 / cross-cutting). These are TOOL gates: they read a claim-ledger, a
prior-art memo, an evidence set, a contribution list, or an ai-review, and return typed
findings — so the harness can BLOCK ready-to-file / ready-to-submit until they clear,
instead of a human catching them post-hoc. No LLM; pure structural checks.

A gate never edits an artifact; it reports. The drafting engine + human resolve.

ADVISORY, NOT ADVERSARIAL-PROOF. The claim/prose gates below (P1-P8, D*, R*) are pattern/heuristic
checks over natural-language claims — they reliably catch the COMMON and ACCIDENTAL forms of each
defect, but a determined author can paraphrase around any single regex (a §101 effect, a novelty
delta, an oracle name, a placeholder review can always be re-worded). They are assistants that
raise the floor, not a substitute for attorney/reviewer judgment. The LOAD-BEARING, non-heuristic
invariants live elsewhere and ARE meant to be robust: the evidence gate (core/gate.py), the
patent-first egress interlock + confidentiality decide()/redaction (core/harness.py), and the
hash-chained ledger (core/ledger.py). Treat a green lint run as "no obvious defect", never "proven
clean".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Finding:
    code: str            # P1..P12, D1..D15, R* (review), F* (figure)
    severity: str        # blocker | major | minor
    target: str          # claim id / contribution / field
    message: str

    def as_dict(self) -> dict:
        return {"code": self.code, "severity": self.severity, "target": self.target,
                "message": self.message}


# ---- patent claim gates (P1-P8) -------------------------------------------

# P1: an independent claim must end in a concrete practical application / technical effect,
# not "compute a performance cost" (an abstract idea on a generic computer).
# Must be an ACTIVE producing step (emit a spec / select+emit a config / rank+output a design /
# fabricate|configure THE ACCELERATOR) — not the word "fabricated" in "lacking fabricated silicon".
# A concrete practical-application OBJECT — every producing verb must carry one (a bare "emit"
# or a trailing bare "and" is NOT a technical effect).
_APP_OBJ = (r"(?:machine-readable|specification|netlist|register-transfer|parameter set|bitstream|"
            r"configuration|design|hardware|accelerator spec\w*|control signal|rtl|hls)")
# Producing verbs must be the ACTIVE gerund form (emitting/generating/…), NOT a past participle
# used as an adjective — so "cost of the GENERATED accelerator design" (a back-reference to an
# earlier artifact) does not false-satisfy the terminal effect.
_TERMINAL_EFFECT = re.compile(
    r"(?:emitting|generating|outputting|producing|rendering) .{0,40}" + _APP_OBJ + r"|"
    r"selecting .{0,50}(?:configuration|design|accelerator).{0,50}(?:emitting|outputting|producing)|"
    r"ranking .{0,60}(?:outputting|emitting|selecting)|"
    r"configuring .{0,25}(?:the )?accelerator|"
    r"(?:manufacturing|fabricating|synthesizing) .{0,25}(?:the )?accelerator", re.I)
    # NOTE: a passive "…for fabrication of the accelerator" is intended-use, not an active step,
    # so it is deliberately NOT a terminal effect.
_ENDS_AT_COST = re.compile(
    r"(comput\w+|calculat\w+|estimat\w+|deriv\w+|produc\w+|output\w+|return\w+|report\w+|"
    r"provid\w+|present\w+|predict\w+|assess\w+)[^.;]{0,80}"
    r"(?:performance )?(?:cost|latenc\w+|energy(?:-delay(?: product)?)?|throughput|"
    r"runtime|utilization|score|figure of merit|metric|estimate)\b", re.I)

# P3: the broadest independent must be the smallest independently-novel unit; a
# second-accelerator / re-tile limitation baked into the base makes single-design costing
# non-infringing.
# Matches a second accelerator baked into the base independent, via synonyms
# (further/additional/subsequent/another/other/different accelerator) or via any of a family
# of verbs (re-tile / map / apply / port / evaluate / cost / transfer / deploy ... onto/to/...).
# The "second target" can be named by any hardware noun, not just "accelerator".
_HW_NOUN = r"(?:accelerator|device|chip|processor|core|target|hardware|npu|gpu|asic|fabric|die|architecture|design)"
_SECOND_ACCEL = re.compile(
    r"(?:second|another|a further|an additional|subsequent|the other|a different)\s+" + _HW_NOUN + r"s?\b|"
    r"two\s+.{0,20}" + _HW_NOUN + r"s\b|"
    r"(?:re-?til\w+|map\w+|appl\w+|port\w+|evaluat\w+|cost\w+|transfer\w+|deploy\w+|run\w*)\s+"
    r".{0,60}?(?:to|onto|across|for|against|on)\s+.{0,25}?"
    r"(?:second|another|further|additional|different|other)\s+" + _HW_NOUN + r"s?\b", re.I)

# P7: relative terms are indefinite unless paired with an enumerated closed list NEARBY.
_RELATIVE_TERMS = ("structurally different", "substantially", "approximately", "optimal",
                   "efficient", "high-performance", "better", "improved", "as needed")
# A real enumerated closed list — 'namely' only counts if it actually introduces a comma-list,
# not as a bare filler word.
_ENUM_NEARBY = re.compile(r"differing in [^.]{0,30}of\b|selected from|consisting of|"
                          r"namely[^.]{0,40},|at least one of[^.]{0,40},", re.I)


_WORD_NUM = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
             "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def _norm_kind(k: str) -> str:
    """Fold statutory-class synonyms to method|system|crm (apparatus=system, process=method, …)."""
    k = (k or "").lower()
    if "medium" in k or "crm" in k or "article of manufacture" in k or "computer-readable" in k:
        return "crm"
    if "apparatus" in k or "system" in k or "device" in k:
        return "system"
    if "process" in k or "method" in k:
        return "method"
    return k


def _substrate_id(text: str) -> str:
    """Normalize a substrate label to a canonical 'S<n>' id so P2/P6 group correctly across
    'S1', 'S-1', 'S 1', 'Substrate 1', 'Substrate one'. Only a REAL substrate id is collapsed:
    'Scheduler 1' or 'System 2' are NOT 'S1'/'S2' (that merged distinct substrates into a
    false-open completeness pass). Anything else falls back to the FULL normalized text."""
    t = (text or "").strip()
    m = re.match(r"[sS][\s\-]?(\d+)\b", t) or re.match(r"substrate[\s\-]+(\d+)\b", t, re.I)
    if m:
        return "S" + m.group(1)
    m2 = re.match(r"substrate\s+([a-z]+)", t, re.I)
    if m2 and m2.group(1).lower() in _WORD_NUM:
        return "S" + _WORD_NUM[m2.group(1).lower()]
    return _norm(t) or "?"


def _terminal_effect_present(text: str) -> bool:
    """Is there an ACTIVE terminal practical-application effect in the claim's FINAL limitation?
    Anchored to the last step (split on ';', ', and', or a sentence end even with no ';'), with
    NEGATED spans ('without emitting ...') stripped so a negation can't false-satisfy the effect."""
    parts = re.split(r";\s*|,?\s+and\s+|(?<=[a-z])\.\s+", (text or "").strip())
    last = parts[-1] if parts else (text or "")
    last = re.sub(r"\bwithout\b[^,;.]*", " ", last, flags=re.I)   # drop negated "without emitting ..."
    return bool(_TERMINAL_EFFECT.search(last))


def lint_patent_claims(ledger: dict, novelty_deltas: dict | None = None,
                       prior_art_capabilities: list[str] | None = None,
                       enabled_scope: list[str] | None = None,
                       claimed_scope: list[str] | None = None) -> list[Finding]:
    """Structural claim-ledger gates. ledger = {independent_claims:[{substrate,kind,text}],
    dependent_claims:[...]}. novelty_deltas = {substrate_id: [dispositive phrase, ...]}."""
    out: list[Finding] = []
    ind = ledger.get("independent_claims", []) or []
    # normalize delta keys to canonical S<n> ids so a caller keying by the full substrate label
    # ('S1: tile factors') still lands on the claim whose substrate normalizes to 'S1'.
    novelty_deltas = {_substrate_id(k): v for k, v in (novelty_deltas or {}).items()}
    prior_art_capabilities = prior_art_capabilities or []

    # P6 completeness matrix: each substrate needs method + system + CRM independents
    kinds_by_sub: dict[str, set] = {}
    for c in ind:
        sub = _substrate_id(c.get("substrate", ""))
        kinds_by_sub.setdefault(sub, set()).add((c.get("kind") or "").lower())
    for sub, kinds in kinds_by_sub.items():
        norm = {_norm_kind(k) for k in kinds}
        missing = {"method", "system", "crm"} - norm
        if missing:
            out.append(Finding("P6", "major", sub,
                               f"substrate {sub} missing statutory-class independents: "
                               f"{sorted(missing)} (need method+system+CRM)"))

    for c in ind:
        sub = _substrate_id(c.get("substrate", ""))
        text = c.get("text", "") or ""
        tail = text[-260:]

        # P1: the claim ends at an abstract cost and its FINAL limitation carries no active
        # terminal practical-application effect (an upstream or negated "emit" cannot disable it).
        if _ENDS_AT_COST.search(tail) and not _terminal_effect_present(text):
            out.append(Finding("P1", "blocker", sub,
                               "independent claim terminates at 'compute a performance cost' with no "
                               "terminal practical-application limitation (emit a machine-readable "
                               "accelerator spec / select+emit a configuration / rank+output a design)"))
        # P3: second-accelerator baked into the base independent — suppressed ONLY when the
        # second accelerator is directly negated (its OWN clause), not merely near an unrelated
        # "no"/"not" (e.g. "no compiler backend AND ... a second accelerator" still bakes it in).
        _p3 = _SECOND_ACCEL.search(text)
        _pre = text[max(0, _p3.start() - 40):_p3.start()] if _p3 else ""
        _neg = re.search(r"\b(?:without|not|never|no|excluding|other than)\b", _pre, re.I) if _p3 else None
        _negated = (_neg and not re.search(r"[,;]|\band\b", _pre[_neg.end():])
                    # "no fewer/less than two accelerators" is a COUNT ≥2, not a negation
                    and not re.match(r"\s*(?:fewer|less|more|greater)\s+than", _pre[_neg.end():], re.I))
        if _p3 and not _negated:
            out.append(Finding("P3", "blocker", sub,
                               "base independent bakes in re-tiling to a SECOND accelerator, so "
                               "costing a single design never infringes — demote the two-accelerator "
                               "re-tile to a dependent; base = single-accelerator callable-factor costing"))
        # P2: the dispositive novelty delta must live in the independent
        for phrase in novelty_deltas.get(sub, []):
            if _norm(phrase) not in _norm(text):
                out.append(Finding("P2", "blocker", sub,
                                   f"dispositive novelty delta not in the independent claim: "
                                   f"'{phrase}' (it must not live only in a dependent)"))
        # P5: non-distinguishing hook shared with a cited prior-art class
        for cap in prior_art_capabilities:
            if _norm(cap) in _norm(text):
                out.append(Finding("P5", "major", sub,
                                   f"novelty hook '{cap}' is ordinary subject matter of a cited "
                                   f"reference class (prior art already costs it) — not distinguishing"))

    # P7 definiteness across all claims: a relative term is indefinite unless an enumerated
    # closed list sits NEAR that term (not merely somewhere else in the claim).
    for c in ind + (ledger.get("dependent_claims", []) or []):
        text = (c.get("text", "") or "") + " " + (c.get("substrate", "") or "")
        low = text.lower()
        for term in _RELATIVE_TERMS:
            idx = low.find(term)
            if idx == -1:
                continue
            window = text[max(0, idx - 40): idx + len(term) + 120]
            if not _ENUM_NEARBY.search(window):
                out.append(Finding("P7", "major", _substrate_id(c.get("substrate", "")),
                                   f"indefinite relative term '{term}' without a NEARBY enumerated "
                                   f"closed list + spec lexicography (112(b))"))
                break

    # P2 (orphan): a declared dispositive delta whose substrate has no independent claim
    seen_subs = {_substrate_id(c.get("substrate", "")) for c in ind}
    for sub_key, phrases in novelty_deltas.items():   # keys already normalized to S<n>
        if sub_key not in seen_subs:
            for phrase in phrases:
                out.append(Finding("P2", "blocker", sub_key,
                                   f"dispositive novelty delta '{phrase}' has no independent claim "
                                   f"for substrate {sub_key!r}"))

    # P8 enablement: claimed scope beyond the enabling derivations in the inputs. Runs whenever a
    # claimed_scope is given (a missing enabled_scope means NOTHING is enabled → flag all).
    if claimed_scope is not None:
        en = {_norm(x) for x in (enabled_scope or [])}
        for term in claimed_scope:
            if _norm(term) not in en:
                out.append(Finding("P8", "major", term,
                                   f"claimed capability '{term}' has no enabling derivation in the "
                                   f"inputs (enabled: {enabled_scope}); add a worked derivation or narrow"))
    return out


def lint_evidence_admission(evidence_items: list[dict], cited_oracles: list[str]) -> list[Finding]:
    """P4: flag evidence that matches a CITED oracle EXACTLY as non-distinguishing for novelty —
    it corroborates, it is not a novel quantity. evidence_items=[{id,description,used_for}]."""
    out: list[Finding] = []
    exact = re.compile(
        r"0(?:\.0+)?\s*%|(?:byte|bit)[- ]?exact|(?:byte|bit)[- ]for[- ](?:byte|bit)|to the (?:byte|bit)|identical|"
        r"perfect(?:ly)?\s+(?:match|agree)|zero\s+(?:error|divergence|difference|deviation)|"
        # "match(es) ... exactly" / "exactly ... match(es)" / "reproduces ... exactly" with a gap
        r"(?:match\w*|reproduc\w+|agree\w*|equal\w*)\s+.{0,90}?\bexact(?:ly)?\b|"
        r"\bexact(?:ly)?\b\s+.{0,90}?(?:match\w*|reproduc\w+|agree\w*)", re.I | re.S)
    for e in evidence_items:
        desc = e.get("description", "") or ""
        # match the oracle name on alphanumerics only, so 'Zig-Zag'/'ZIG ZAG' == 'ZigZag'
        if exact.search(desc) and any(_alnum(o) in _alnum(desc) for o in cited_oracles):
            out.append(Finding("P4", "blocker", e.get("id", "?"),
                               "exact match with a CITED oracle is corroboration only — do NOT base a "
                               "novelty claim/limitation on the matched quantity; rebase novelty on "
                               "what the oracle does NOT do, keep the match in the spec as corroboration"))
    return out


# ---- paper gates (D1, D2, D4) ---------------------------------------------

def map_contributions_to_evidence(contributions: list[dict]) -> list[Finding]:
    """D1: every headline contribution must map to >=1 VALIDATED datum; self-reported-only
    headline contributions are hard-flagged. contributions=[{name,headline,evidence_grade}]."""
    out: list[Finding] = []
    for c in contributions:
        grade = (c.get("evidence_grade") or "").lower()
        if c.get("headline") and grade != "validated":
            out.append(Finding("D1", "blocker", c.get("name", "?"),
                               f"headline contribution '{c.get('name')}' is supported only by "
                               f"'{grade or 'self-reported'}' evidence — demote to a capability or add "
                               f"an oracle/baseline validation before it can be a headline claim"))
    return out


def lint_venue_experiments(venue: str, marketed_claims: list[str],
                           planned_experiments: list[str], venue_requirements: dict) -> list[Finding]:
    """D2: a venue's required experiments must be planned for the claims being marketed."""
    out: list[Finding] = []
    req = []
    for k, v in (venue_requirements or {}).items():   # case/format-insensitive venue match
        if _norm(k) == _norm(venue):
            req = v
            break
    have = " ".join(planned_experiments).lower()
    for r in req:
        trigger, need = r.get("if_claim", ""), r.get("require", "")
        if any(_norm(trigger) in _norm(m) for m in marketed_claims) and _norm(need) not in _norm(have):
            out.append(Finding("D2", "blocker", venue,
                               f"venue {venue} requires '{need}' for a '{trigger}' claim, but it is not "
                               f"in the planned experiments — add it or drop the claim"))
    return out


def lint_precision(values: list[dict]) -> list[Finding]:
    """D4: exploration-grade numbers must print at ~2 sig figs / as ratios, not 5-6 sig figs.
    values=[{value:'2214.6', grade:'exploration'|'validated'}]."""
    out: list[Finding] = []
    for v in values:
        if (v.get("grade") or "").lower() == "exploration":
            digits = re.sub(r"[^\d]", "", str(v.get("value") or "").lstrip("0"))
            if len(digits) > 2:
                out.append(Finding("D4", "major", v.get("value", "?"),
                                   f"exploration-grade value '{v.get('value')}' printed at "
                                   f"{len(digits)} sig figs — round to ~2 sig figs or express as a ratio"))
    return out


# ---- cross-cutting: ai-review validity (R*) --------------------------------

_SENTINELS = {"test", "todo", "tbd", "placeholder", "stub", "xxx", "n/a", "na", "lorem",
              "foo", "bar", "sample", "example", "dummy", "..."}
# Placeholder content that isn't an exact sentinel token ("TODO: fill in", "TBD later", ...).
_PLACEHOLDER_RE = re.compile(
    r"^\s*(?:todo|tbd|tba|tbc|fixme|xxx+|placeholder|stub|n/?a|lorem|fill[ -]?in|"
    r"to be (?:determined|added|written|filled|done)|coming soon|same as above|"
    r"\.{2,}|-{2,}|\?{2,})\b", re.I)


def validate_review(review: dict, required_subscores: list[str] | None = None) -> list[Finding]:
    """Reject a placeholder/stub reviewer output (the Loom run accepted a review whose every
    field was the literal 'test'). Detects sentinel/placeholder strings, empty/duplicated
    fields, and missing per-autorater sub-scores."""
    out: list[Finding] = []
    required_subscores = required_subscores or ["novelty", "soundness", "clarity", "significance"]
    text_fields = _collect_strings(review)
    for path, val in text_fields:
        s = val.strip()
        # strip a trailing enumerator ('1', '#2', 'one', 'two', 'a', 'i') so 'test one' -> 'test'
        base = re.sub(r"(?:[\s\d.:#()\-]|\b(?:one|two|three|four|five|a|b|c|d|i{1,3}|iv|x)\b)+$",
                      "", s, flags=re.I).strip().lower()
        if s.lower() in _SENTINELS or base in _SENTINELS or _PLACEHOLDER_RE.match(s):
            out.append(Finding("R1", "blocker", path, f"placeholder/stub value {val!r} in review field"))
    nonempty = [v for _, v in text_fields if v.strip()]
    if len(text_fields) >= 3 and len(set(v.strip().lower() for _, v in text_fields)) == 1:
        out.append(Finding("R2", "blocker", "review", "every review field is identical — stub output"))
    if not nonempty:
        out.append(Finding("R2", "blocker", "review", "review has no non-empty fields"))
    scores = review.get("subscores") or review.get("scores") or {}
    missing = [s for s in required_subscores if s not in {k.lower() for k in scores}]
    if missing:
        out.append(Finding("R3", "major", "subscores", f"missing per-autorater sub-scores: {missing}"))
    return out


# ---- aggregators: readiness verdicts --------------------------------------

def patent_readiness(ledger: dict, evidence_items: list[dict], cited_oracles: list[str],
                     fto_status: str, post_search_status: str, pdf_built: bool,
                     novelty_deltas: dict | None = None, prior_art_capabilities: list[str] | None = None,
                     enabled_scope: list[str] | None = None, claimed_scope: list[str] | None = None) -> dict:
    findings = (lint_patent_claims(ledger, novelty_deltas, prior_art_capabilities, enabled_scope, claimed_scope)
                + lint_evidence_admission(evidence_items, cited_oracles))
    # P12 FTO + post-claim search gates; P11 PDF gate
    if fto_status.lower() not in ("done", "cleared", "passed"):
        findings.append(Finding("P12", "blocker", "fto", f"freedom-to-operate search not run (status={fto_status})"))
    if post_search_status.lower() not in ("done", "cleared", "passed"):
        findings.append(Finding("P12", "blocker", "post_search", f"post-drafting confirmation search not run (status={post_search_status})"))
    if not pdf_built:
        findings.append(Finding("P11", "major", "pdf", "patent PDF (USPTO-style, numbered paragraphs + Drawings) not built"))
    blockers = [f for f in findings if f.severity == "blocker"]
    verdict = "ready-for-attorney" if not blockers else "NOT-filing-ready"
    return {"verdict": verdict, "blockers": len(blockers), "findings": [f.as_dict() for f in findings],
            "human_gate": "attorney review mandatory; Veridraft never files"}


def paper_readiness(contributions: list[dict], values: list[dict], venue: str,
                    marketed_claims: list[str], planned_experiments: list[str],
                    venue_requirements: dict, figure_selfcheck_passed: bool) -> dict:
    findings = (map_contributions_to_evidence(contributions)
                + lint_precision(values)
                + lint_venue_experiments(venue, marketed_claims, planned_experiments, venue_requirements))
    if not figure_selfcheck_passed:
        findings.append(Finding("F1", "major", "figures", "figure self-check (no text outside its box; "
                               "no box touching the canvas edge) did not pass"))
    blockers = [f for f in findings if f.severity == "blocker"]
    verdict = "ready-to-submit" if not blockers else "NOT-ready-to-submit"
    return {"verdict": verdict, "blockers": len(blockers), "findings": [f.as_dict() for f in findings]}


# ---- helpers ---------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _collect_strings(obj, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _collect_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _collect_strings(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        out.append((path, obj))
    return out
