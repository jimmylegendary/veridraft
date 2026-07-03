"""Thin CLI over the harness op-manifest (ADR-0001).

The CLI cannot bypass a gate — it only calls the same governed core ops the API/MCP
call. `veridraft run` drives the whole vertical slice: import → gate → assemble → draft
→ review.
"""
from __future__ import annotations

import argparse
import sys

from .config import HarnessConfig, load_venues
from .core.harness import Harness
from .core.models import GateReport, GateStatus


def _harness(args) -> Harness:
    cfg = HarnessConfig.load(getattr(args, "config", None))
    if getattr(args, "data_dir", None):
        cfg.data_dir = args.data_dir
    return Harness(cfg)


def _print_gate(report: GateReport) -> None:
    print(f"gate profile: {report.profile}  bundle: {report.bundle_id}")
    for r in report.results:
        mark = "PASS" if r.status is GateStatus.PASSED else "BLOCK"
        print(f"  [{mark}] {r.claim_id} ({r.type})  admissible_evidence={r.admissible_evidence}")
        for reason in r.reasons:
            print(f"         - {reason}")
    print(f"passed: {report.passed_claim_ids or '(none)'}")
    print(f"blocked: {report.blocked_claim_ids or '(none)'}")


def cmd_import(args) -> int:
    h = _harness(args)
    try:
        res = h.import_bundle(args.ref)
        print(f"imported bundle {res['bundle_id']}: {res['claims']} claims, "
              f"{res['results']} results, digest_ok={res['digest_ok']}")
        return 0
    finally:
        h.close()


def cmd_gate(args) -> int:
    h = _harness(args)
    try:
        _print_gate(h.run_gate(args.bundle_id))
        return 0
    finally:
        h.close()


def cmd_assemble(args) -> int:
    h = _harness(args)
    try:
        m = h.assemble_inputs(args.bundle_id, args.template, args.guidelines, args.title,
                              target_audience=args.audience)
        print(f"assembled inputs at {m['inputs_dir']}  (target={m['target_audience']})")
        print(f"  claims: {m['provenance']['claim_ids']}")
        if m.get("excluded_claims"):
            print(f"  excluded (confidentiality): {m['excluded_claims']}")
        print(f"  result refs: {m['provenance']['result_ids']}")
        print(f"  artifact: {m['artifact_id']}")
        return 0
    finally:
        h.close()


def cmd_draft(args) -> int:
    h = _harness(args)
    try:
        d = h.draft(args.bundle_id)
        print(f"drafted {d['artifact_id']} via engine={d['engine']} renderer={d['renderer']}")
        print(f"  PDF: {d['paper_pdf']}")
        print(f"  TeX: {d['paper_tex']}")
        print(f"  staged to: {d['staged_to']}")
        return 0
    finally:
        h.close()


def cmd_patentability(args) -> int:
    h = _harness(args)
    try:
        s = h.patentability(args.bundle_id)
        print(f"patentability: {s['verdict'].upper()}")
        print(f"  substrate (P1/P2): {s['substrate_claims']}")
        print(f"  projections (P3): {s['projection_claims']}")
        for r in s["rationale"]:
            print(f"  - {r}")
        return 0
    finally:
        h.close()


def cmd_draft_patent(args) -> int:
    h = _harness(args)
    try:
        d = h.draft_patent(args.bundle_id, title=args.title)
        print(f"patent draft {d['artifact_id']} via engine={d['engine']} renderer={d['renderer']} "
              f"patentability={d['patentability']}")
        print(f"  claims: {d['independent_claims']} independent + {d['dependent_claims']} dependent")
        print(f"  PDF: {d['patent_pdf']}")
        print(f"  ⚠️  {d['note']}")
        if d["open_items"]:
            print("  open items (human/attorney must resolve):")
            for o in d["open_items"][:8]:
                print(f"    - {o}")
        return 0
    finally:
        h.close()


def cmd_patent_review(args) -> int:
    h = _harness(args)
    try:
        r = h.patent_review(args.bundle_id)
        print(f"patent review {r['artifact_id']}: {r['verdict']}")
        for c in r["checklist"]:
            print(f"  [{'ok' if c['ok'] else 'x'}] {c['item']}")
        print(f"  {r['note']}")
        return 0
    finally:
        h.close()


def cmd_publish(args) -> int:
    h = _harness(args)
    try:
        r = h.publish(args.bundle_id, target_audience=args.audience,
                      venue=getattr(args, "venue", None))
        if r.get("reason") == "READINESS":
            print(f"BLOCKED {r['artifact_id']} — venue {r['venue']} ({r['policy_class']}): {r['detail']}")
            print("  run: sign-off-readiness <bundle> --venue <v> --user <name>  (human authorship sign-off)")
            return 3
        if r.get("reason") == "INTERLOCK":
            print(f"BLOCKED {r['artifact_id']} — patent-first interlock: held claim(s) {r['held_claims']}")
            print(f"  {r['detail']}")
            print("  run: release-interlock <claim_id>  (only after the patent/provisional is filed)")
            return 3
        if r["published"]:
            print(f"PUBLISHED {r['artifact_id']} → audience={r['audience']}  dest={r['dest']}")
            print(f"  (egress: confidentiality decide() + redaction re-sweep passed; "
                  f"ruleset={r['ruleset_version']})")
            return 0
        print(f"BLOCKED {r['artifact_id']} at egress ({r['reason']}) → audience={r['audience']}")
        print(f"  decision: {r['decision']}")
        if r["redaction_hits"]:
            print(f"  redaction hits ({r['ruleset_version']}):")
            for hit in r["redaction_hits"]:
                print(f"    - {hit['type']}: {hit['span']!r}")
        if r.get("unscannable"):
            print(f"  unscannable deliverables (fail-closed): {r['unscannable']}")
        if r.get("nothing_to_sweep"):
            print("  nothing to sweep (fail-closed): refused to publish unswept")
        return 3
    finally:
        h.close()


def cmd_review(args) -> int:
    h = _harness(args)
    try:
        r = h.run_review(args.bundle_id)
        print(f"review {r['artifact_id']}: verdict={r['verdict']}")
        for c in r["checklist"]:
            print(f"  [{'ok' if c['ok'] else 'x'}] {c['item']}")
        print(f"  {r['note']}")
        return 0
    finally:
        h.close()


def cmd_status(args) -> int:
    h = _harness(args)
    try:
        arts = h.get_lifecycle()
        if not arts:
            print("(no artifacts yet)")
        for a in arts:
            print(f"  {a['id']}  state={a['state']:16} track={a.get('confidentiality_track')} "
                  f"(b={a.get('boundary')},v={a.get('visibility')})  output={a.get('output_ref')}")
        return 0
    finally:
        h.close()


def cmd_events(args) -> int:
    h = _harness(args)
    try:
        events = h.ledger.get_lifecycle_events()
        for e in events:
            reason = f" reason={e['reason']}" if e["reason"] else ""
            print(f"  #{e['seq']} {e['artifact_id']}  {e['from_state']}→{e['to_state']}  "
                  f"actor={e['actor']}{reason}")
        print(f"\nhash chain intact: {h.ledger.verify_lifecycle()}")
        return 0
    finally:
        h.close()


def cmd_venues(args) -> int:
    reg = load_venues()
    for domain, names in reg.get("domains", {}).items():
        print(f"\n[{domain}]")
        for n in names:
            v = reg["venues"].get(n, {})
            print(f"  {n:12} bar={v.get('empirical_bar','?'):7} {v.get('full_name','')}")
    print("\nUse a venue with the AI reviewer: it applies that venue's rubric/bar.")
    return 0


def cmd_reviews(args) -> int:
    h = _harness(args)
    try:
        reviews = h.get_reviews(args.bundle_id)
        if not reviews:
            print("(no reviews captured — run the AI reviewer pipeline to produce one)")
        for r in reviews:
            print(f"\n=== review #{r['id']}  venue={r['venue']}  reviewer={r['reviewer']} ===")
            print(f"  verdict: {r['verdict']}   overall: {r['overall']}")
            if r["scores"]:
                print("  scores: " + ", ".join(f"{k}={v}" for k, v in r["scores"].items()))
            for w in r["weaknesses"]:
                print(f"  - weakness: {w}")
            for g in r["guidance"]:
                if isinstance(g, dict):
                    print(f"  → do: {g.get('action')}  | gain: {g.get('quality_gain')}  | benefit: {g.get('benefit')}")
                else:
                    print(f"  → {g}")
        return 0
    finally:
        h.close()


def cmd_readiness(args) -> int:
    h = _harness(args)
    try:
        r = h.check_submission_readiness(args.bundle_id, args.venue,
                                         tool=args.tool, purpose=args.purpose)
        cls = r["policy_class"].upper()
        print(f"venue={r['venue']}  policy={cls}  disclosure_required={r['disclosure_required']}")
        if r["blocked"]:
            print("  ⛔ PROHIBITED venue → publish default-denied; human sign-off required (no evasion).")
        print(f"  disclosure ({r['disclosure_location']}):")
        print(f"    {r['disclosure_text']}")
        for g in r["guidance"]:
            print(f"  - {g}")
        return 0
    finally:
        h.close()


def cmd_signoff_readiness(args) -> int:
    h = _harness(args)
    try:
        r = h.sign_off_readiness(args.bundle_id, args.venue, user=f"human:{args.user}")
        print(f"readiness signed off for {args.bundle_id} @ {r['venue']} by {r['signed_off_by']}")
        print("  (records human authorship responsibility; publish for this venue is now permitted)")
        return 0
    finally:
        h.close()


def cmd_interlocks(args) -> int:
    h = _harness(args)
    try:
        rows = h.list_interlocks(args.bundle_id)
        if not rows:
            print("(no interlocks)")
        for r in rows:
            extra = f"  reason={r['reason']}" if r.get("reason") else ""
            print(f"  {r['claim_id']}  status={r['status']}  "
                  f"patent_first={bool(r['patent_first'])}{extra}")
        return 0
    finally:
        h.close()


def cmd_release_interlock(args) -> int:
    h = _harness(args)
    try:
        r = h.release_interlock(args.claim_id, reason=args.reason)
        print(f"interlock released for {r['claim_id']}: {r['previous']} → {r['status']} "
              f"(by {r['actor']})")
        print("  re-run `gate` to let the released claim into a paper")
        return 0
    finally:
        h.close()


def cmd_adapters(args) -> int:
    h = _harness(args)
    try:
        for a in h.list_adapters():
            flags = []
            if a["selected"]:
                flags.append("selected")
            if a["enabled"]:
                flags.append("enabled")
            if a["maturity"] == "stub":
                flags.append("STUB")
            req = f" requires_config={a['requires_config']}" if a["requires_config"] else ""
            print(f"  {a['port']:14} {a['id']:22} v{a['version']:6} "
                  f"[{','.join(flags) or '-'}]{req}")
        pf = h.preflight()
        print(f"\npreflight (draft ports): {'OK' if pf.ok else 'FAILED'}")
        for i in pf.failures():
            print(f"  x {i.port}/{i.adapter_id}: {i.detail}")
        return 0
    finally:
        h.close()


def cmd_run(args) -> int:
    h = _harness(args)
    try:
        imp = h.import_bundle(args.ref)
        bundle_id = imp["bundle_id"]
        print(f"== import ==\nbundle {bundle_id}: {imp['claims']} claims, "
              f"digest_ok={imp['digest_ok']}\n")

        print("== gate ==")
        report = h.run_gate(bundle_id)
        _print_gate(report)
        if not report.passed_claim_ids:
            print("\nNo claim passed the gate — nothing to draft. Slice halts (by design).")
            return 2

        print(f"\n== assemble (target={args.audience}) ==")
        try:
            m = h.assemble_inputs(bundle_id, args.template, args.guidelines, args.title,
                                  target_audience=args.audience)
        except RuntimeError as e:
            print(f"BLOCKED before drafting: {e}")
            return 3
        print(f"inputs: {m['inputs_dir']}  claims={m['provenance']['claim_ids']}")
        if m.get("excluded_claims"):
            print(f"excluded (confidentiality): {m['excluded_claims']}")

        print("\n== draft ==")
        d = h.draft(bundle_id)
        print(f"engine={d['engine']} renderer={d['renderer']}\nPDF: {d['paper_pdf']}")

        print("\n== review ==")
        r = h.run_review(bundle_id)
        print(f"verdict: {r['verdict']}")
        for c in r["checklist"]:
            print(f"  [{'ok' if c['ok'] else 'x'}] {c['item']}")

        print(f"\n== publish (egress gate, audience={args.audience}) ==")
        p = h.publish(bundle_id, target_audience=args.audience)
        if p["published"]:
            print(f"PUBLISHED → {p['dest']}  (decide+redaction passed; ruleset={p['ruleset_version']})")
        else:
            print(f"BLOCKED at egress ({p['reason']}): {p['decision']}")
            for hit in p["redaction_hits"]:
                print(f"  redaction: {hit['type']} {hit['span']!r}")

        backlog = h.blocked_backlog(bundle_id)
        print(f"\nblocked-claim backlog: {backlog or '(none)'}")
        status = "PUBLISHED" if p["published"] else "BLOCKED at egress"
        print(f"\nDONE — gated claim → PDF at: {d['paper_pdf']}  |  egress: {status}")
        return 0 if p["published"] else 3
    finally:
        h.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="veridraft", description="Veridraft Paper & Patent Harness (v1 slice)")
    p.add_argument("--config", help="path to veridraft.config.json")
    p.add_argument("--data-dir", help="override data dir (default .veridraft)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("import-bundle", help="import a CAW-02 bundle")
    s.add_argument("ref"); s.set_defaults(func=cmd_import)

    s = sub.add_parser("gate", help="run the evidence gate")
    s.add_argument("bundle_id"); s.set_defaults(func=cmd_gate)

    s = sub.add_parser("assemble", help="assemble engine inputs from gated claims")
    s.add_argument("bundle_id")
    s.add_argument("--template", required=True)
    s.add_argument("--guidelines", required=True)
    s.add_argument("--title", default="Veridraft Draft")
    s.add_argument("--audience", default="public", choices=["public", "internal", "counsel"])
    s.set_defaults(func=cmd_assemble)

    s = sub.add_parser("draft", help="run the writing engine")
    s.add_argument("bundle_id"); s.set_defaults(func=cmd_draft)

    s = sub.add_parser("patentability", help="patentability screen over gated claims")
    s.add_argument("bundle_id"); s.set_defaults(func=cmd_patentability)

    s = sub.add_parser("draft-patent", help="draft a patent application from gated claims (needs_human)")
    s.add_argument("bundle_id")
    s.add_argument("--title", default="Invention")
    s.set_defaults(func=cmd_draft_patent)

    s = sub.add_parser("patent-review", help="patent-specific review checklist")
    s.add_argument("bundle_id"); s.set_defaults(func=cmd_patent_review)

    s = sub.add_parser("review", help="run the review checklist")
    s.add_argument("bundle_id"); s.set_defaults(func=cmd_review)

    s = sub.add_parser("publish", help="egress gate: confidentiality decide() + redaction re-sweep")
    s.add_argument("bundle_id")
    s.add_argument("--audience", default=None, choices=["public", "internal", "counsel"],
                   help="override the sink's audience tier")
    s.add_argument("--venue", default=None, help="target venue; enforces its LLM policy (prohibited → sign-off)")
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("readiness", help="submission-readiness: venue LLM policy + disclosure text")
    s.add_argument("bundle_id")
    s.add_argument("--venue", required=True)
    s.add_argument("--tool", default="an AI writing assistant (PaperOrchestra, wrapped by Veridraft)")
    s.add_argument("--purpose", default="draft and refine the manuscript from evidence-gated claims")
    s.set_defaults(func=cmd_readiness)

    s = sub.add_parser("sign-off-readiness", help="human authorship sign-off for a prohibited-venue submission")
    s.add_argument("bundle_id")
    s.add_argument("--venue", required=True)
    s.add_argument("--user", default="cli")
    s.set_defaults(func=cmd_signoff_readiness)

    s = sub.add_parser("status", help="artifact lifecycle board")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("events", help="hash-chained lifecycle event log + verify")
    s.set_defaults(func=cmd_events)

    s = sub.add_parser("reviews", help="show captured AI reviews / quality assessments")
    s.add_argument("bundle_id")
    s.set_defaults(func=cmd_reviews)

    s = sub.add_parser("venues", help="list AI-reviewer target venues (rubric/bar per venue)")
    s.set_defaults(func=cmd_venues)

    s = sub.add_parser("interlocks", help="list patent-first interlocks")
    s.add_argument("bundle_id", nargs="?", default=None)
    s.set_defaults(func=cmd_interlocks)

    s = sub.add_parser("release-interlock", help="release a patent-first interlock (human)")
    s.add_argument("claim_id")
    s.add_argument("--reason", default=None)
    s.set_defaults(func=cmd_release_interlock)

    s = sub.add_parser("adapters", help="list registered adapters + preflight")
    s.set_defaults(func=cmd_adapters)

    s = sub.add_parser("run", help="full slice: import→gate→assemble→draft→review→publish")
    s.add_argument("ref")
    s.add_argument("--template", required=True)
    s.add_argument("--guidelines", required=True)
    s.add_argument("--title", default="Veridraft Draft")
    s.add_argument("--audience", default="public", choices=["public", "internal", "counsel"])
    s.set_defaults(func=cmd_run)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:  # surface clean errors to the CLI
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
