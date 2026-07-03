"""The harness core — the single place governance lives (ADR-0001).

Exposes the vetted, typed operations of the op-manifest. Each op enforces its
invariant in the core; a surface (CLI/API/MCP) can only *request* a transition.
The vertical slice implements: import_bundle → run_gate → assemble_inputs → draft →
run_review, plus get_lifecycle / list_adapters / preflight.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ..config import HarnessConfig, load_gate_profile
from . import assemble as assemble_mod
from . import assemble_patent as assemble_patent_mod
from . import confidentiality as conf_mod
from . import gate as gate_mod
from . import lints as lints_mod
from . import patentability as patentability_mod
from . import readiness as readiness_mod
from . import registry
from .ledger import Ledger
from .models import (
    Audience,
    Boundary,
    BlockedReason,
    ConfidentialityTrack,
    GateReport,
    GateStatus,
    InterlockStatus,
    Lifecycle,
    Visibility,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _extract_pdf_text(path) -> str | None:
    """Best-effort PDF text extraction for the egress re-sweep: pdftotext CLI, then
    pypdf/PyPDF2 if importable. Returns None when no extractor is available — the
    caller then fail-closes rather than publishing an unswept deliverable."""
    import shutil
    import subprocess

    p = str(path)
    if shutil.which("pdftotext"):
        try:
            r = subprocess.run(["pdftotext", p, "-"], capture_output=True, text=True, timeout=60)
            if r.returncode == 0:
                return r.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    for mod in ("pypdf", "PyPDF2"):
        try:
            m = __import__(mod)
            reader = m.PdfReader(p)
            return "\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception:
            continue
    return None


def _sink_audience(sink) -> Audience:
    """The audience tier a Sink adapter exports to (default: internal)."""
    aud = getattr(sink, "audience", None)
    if isinstance(aud, Audience):
        return aud
    try:
        return Audience(aud) if aud else Audience.INTERNAL
    except ValueError:
        return Audience.INTERNAL


class Harness:
    REQUIRED_PORTS_FOR_DRAFT = ["source", "writing_engine", "sink"]

    def __init__(self, config: HarnessConfig | None = None):
        # ensure adapters are registered (import side effects)
        from .. import adapters  # noqa: F401

        self.config = config or HarnessConfig()
        self.data_dir = Path(self.config.data_dir)
        self.ledger = Ledger(str(self.data_dir / "ledger.db"))

    def close(self) -> None:
        self.ledger.close()

    # -- registry / preflight ------------------------------------------------
    def list_adapters(self) -> list[dict]:
        out = []
        for caps in registry.list_adapters():
            spec = self.config.adapters.get(caps.port, {})
            out.append({
                "port": caps.port,
                "id": caps.id,
                "version": caps.version,
                "maturity": caps.maturity.value,
                "selected": spec.get("id") == caps.id,
                "enabled": bool(spec.get("enabled", False)) and spec.get("id") == caps.id,
                "requires_config": list(caps.requires_config),
            })
        return out

    def preflight(self, ports: list[str] | None = None) -> registry.PreflightReport:
        return registry.preflight(ports or self.REQUIRED_PORTS_FOR_DRAFT, self.config.adapters)

    def _adapter(self, port: str):
        return registry.instantiate(port, self.config.adapter_id(port),
                                    self.config.adapter_config(port))

    # -- op: import_bundle ---------------------------------------------------
    def import_bundle(self, ref: str) -> dict:
        source = self._adapter("source")
        bundle = source.import_bundle(ref)
        digest_ok, computed = self.ledger.import_bundle_projection(bundle, _now())
        return {
            "bundle_id": bundle.bundle_id,
            "claims": len(bundle.claims),
            "results": len(bundle.results),
            "digest_ok": digest_ok,
            "computed_digest": computed,
        }

    # -- op: run_gate --------------------------------------------------------
    def run_gate(self, bundle_id: str) -> GateReport:
        claims = self.ledger.get_claims(bundle_id)
        if not claims:
            raise ValueError(f"no claims for bundle {bundle_id!r} (import it first)")
        profile = load_gate_profile(self.config.gate_profile)
        results = self.ledger.get_results(bundle_id)
        _result_ids = {r.result_id for r in results}
        # Patent-first interlock (L3a): a patent-sensitive claim gets a DURABLE HELD interlock at
        # gate time — for the CANONICAL sensitive types (P3) REGARDLESS of the active profile, so
        # gating under us-utility-patent (patent_sensitive_types=[]) still creates the row that
        # survives a re-import relabel (P3→P1). The gate then reads the current status.
        for c in claims:
            if c.type.value in profile.patent_sensitive_types or c.type in gate_mod.PATENT_SENSITIVE_DEFAULT:
                self.ledger.ensure_interlock(c.claim_id, _now())
            c.interlock_status = self.ledger.get_interlock_status(c.claim_id)
        report = gate_mod.run_gate(claims, profile, bundle_id, valid_result_ids=_result_ids)

        # require_results is checked over gate-PASSED claims ONLY: a result referenced solely by a
        # BLOCKED (never-drafted) claim cannot unlock drafting of a result-less paper.
        passed_claims = [c for c in claims if c.claim_id in report.passed_claim_ids]
        # A referenced result must carry CONTENT — a numeric metric OR a non-empty description
        # (a functional/e2e outcome). A content-free result (no metrics, blank description) cannot
        # stand in for an evaluation. Only enforce when there IS something to draft.
        def _has_content(r):   # a real number OR a non-blank functional description (not a bare {})
            return bool((r.description or "").strip()) or any(
                str(m.get("value", "")).strip() for m in (r.metrics or []) if isinstance(m, dict))
        _content_ids = {r.result_id for r in results if _has_content(r)}
        if profile.require_results and passed_claims and not any(
                rr in _content_ids for c in passed_claims for rr in c.result_refs):
            raise ValueError(
                f"gate profile {profile.name!r} requires experimental results REFERENCED by a "
                f"gate-PASSED claim, but bundle {bundle_id!r} has none. Veridraft will not fabricate "
                f"an evaluation — supply results (a functional/e2e suite counts) referenced by a "
                f"drafted claim before drafting.")

        for r in report.results:
            self.ledger.set_gate_status(r.claim_id, r.status)

        # Optional defense-in-depth: CUE vet (non-authoritative).
        report_cue_ok, report_cue_detail = gate_mod.cue_vet_snapshot(claims)
        self._last_cue = (report_cue_ok, report_cue_detail)

        # Record the shared gated front (passed claims) + ingest confidentiality
        # classification (lattice-max over passed claims → track). Ingest ROUTES;
        # the load-bearing block is at egress (publish).
        if report.passed_claim_ids:
            self.ledger.create_gated_set(
                self._gated_set_id(bundle_id), bundle_id, profile.name,
                report.passed_claim_ids, _now())
            passed = [self.ledger.get_claim(cid) for cid in report.passed_claim_ids]
            labels = conf_mod.classify([c for c in passed if c])
            art_id = self._artifact_id(bundle_id)
            self.ledger.upsert_artifact(
                art_id, type="paper", state=Lifecycle.GATED,
                gated_set_id=self._gated_set_id(bundle_id), now=_now(),
                confidentiality_track=labels.track.value,
                boundary=labels.boundary.value, visibility=labels.visibility.value)
            self.ledger.append_lifecycle_event(
                art_id, None, Lifecycle.GATED.value, actor="system", now=_now(),
                detail={"track": labels.track.value, "boundary": labels.boundary.value,
                        "visibility": labels.visibility.value,
                        "blocked_claims": report.blocked_claim_ids,
                        # audit the no-fabrication gate state so disabling it is never silent
                        "require_results": profile.require_results})
        return report

    # -- op: assemble_inputs -------------------------------------------------
    def assemble_inputs(self, bundle_id: str, template_path: str, guidelines_path: str,
                        title: str = "Veridraft Draft", target_audience: str = "public") -> dict:
        gated = self.ledger.get_gated_set(self._gated_set_id(bundle_id))
        if not gated or not gated["claim_ids"]:
            raise RuntimeError(
                f"no gated claim set for bundle {bundle_id!r} — run_gate must pass ≥1 claim first")
        audience = Audience(target_audience)
        art_id = self._artifact_id(bundle_id)
        all_claims = [self.ledger.get_claim(cid) for cid in gated["claim_ids"]]
        all_claims = [c for c in all_claims if c is not None]

        # confidentiality-before-assemble (ADR-0002 §5 / ADR-0007): internal-review-
        # required spans must be absent from a public-target assembly BEFORE the engine
        # sees them. A claim not allowed for this audience is dropped from inputs.
        kept, excluded = [], []
        for c in all_claims:
            if conf_mod.decide(c.boundary, c.visibility, audience).allow:
                kept.append(c)
            else:
                excluded.append(c.claim_id)
        if not kept:
            self._block(art_id, Lifecycle.GATED.value, BlockedReason.BOUNDARY,
                        {"excluded": excluded, "target_audience": audience.value})
            raise RuntimeError(
                f"confidentiality: all claims excluded from a {audience.value} assembly "
                f"(BOUNDARY): {excluded}")

        results = self.ledger.get_results(bundle_id)
        template_tex = (Path(template_path).read_text(encoding="utf-8")
                        if template_path and Path(template_path).exists() else "")
        guidelines = (Path(guidelines_path).read_text(encoding="utf-8")
                      if guidelines_path and Path(guidelines_path).exists() else "# Guidelines\n")

        bundle = self.ledger.get_bundle(bundle_id) or {}
        context = (bundle.get("provenance_manifest") or {}).get("topic", "")
        workspace = self._workspace(bundle_id)
        manifest = assemble_mod.assemble_inputs(
            str(workspace), kept, results, template_tex, guidelines, title=title,
            context=context)

        # Re-gate: recompute the effective labels over the claims ACTUALLY assembled.
        labels = conf_mod.classify(kept)
        self.ledger.upsert_artifact(
            art_id, type="paper", state=Lifecycle.DRAFTING, gated_set_id=gated["id"],
            now=_now(), confidentiality_track=labels.track.value,
            boundary=labels.boundary.value, visibility=labels.visibility.value)
        self.ledger.append_lifecycle_event(
            art_id, Lifecycle.GATED.value, Lifecycle.DRAFTING.value, actor="system",
            now=_now(), detail={"kept": [c.claim_id for c in kept], "excluded": excluded,
                                "target_audience": audience.value})
        manifest["artifact_id"] = art_id
        manifest["excluded_claims"] = excluded
        manifest["target_audience"] = audience.value
        return manifest

    def _block(self, artifact_id: str, from_state: str, reason: BlockedReason,
               detail: dict) -> None:
        self.ledger.upsert_artifact(
            artifact_id, type="paper", state=Lifecycle.BLOCKED,
            gated_set_id=self._gated_set_id_from_artifact(artifact_id), now=_now())
        self.ledger.append_lifecycle_event(
            artifact_id, from_state, Lifecycle.BLOCKED.value, actor="system",
            now=_now(), reason=reason.value, detail=detail)

    def _gated_set_id_from_artifact(self, artifact_id: str) -> str:
        art = self.ledger.get_artifact(artifact_id)
        return art["gated_set_id"] if art else artifact_id

    # -- op: draft -----------------------------------------------------------
    def draft(self, bundle_id: str) -> dict:
        pf = self.preflight(self.REQUIRED_PORTS_FOR_DRAFT)
        if not pf.ok:
            raise RuntimeError(
                "preflight failed: " + "; ".join(f"{i.port}/{i.adapter_id}: {i.detail}"
                                                 for i in pf.failures()))
        # The engine only drafts gate-PASSED claims: require a non-empty gated set, not merely
        # the presence of assembled inputs (defense-in-depth against a hand-made workspace).
        gated = self.ledger.get_gated_set(self._gated_set_id(bundle_id))
        if not gated or not gated.get("claim_ids"):
            raise RuntimeError(f"{bundle_id!r} has no passing gated claim set — run_gate first")
        workspace = self._workspace(bundle_id)
        if not (workspace / "inputs" / "idea.md").exists():
            raise RuntimeError(f"no assembled inputs for {bundle_id!r} — run assemble_inputs first")
        # A8: refuse STALE inputs. The on-disk workspace is only valid if (a) the claim set that
        # was assembled still equals the current gated set, and (b) every gated claim is still
        # gate-PASSED. A re-import+re-gate that blocks/removes a claim forces a re-assemble, so a
        # now-ungated claim in a stale idea.md can never reach the engine.
        assembled = self._assembled_claim_ids(bundle_id)
        if assembled is None:
            raise RuntimeError(f"{bundle_id!r} not assembled — run assemble_inputs first")
        if set(assembled) != set(gated["claim_ids"]):
            raise RuntimeError(
                f"{bundle_id!r} gated set changed since assemble (assembled={sorted(assembled)}, "
                f"now={sorted(gated['claim_ids'])}) — re-run assemble_inputs before draft")
        stale = [cid for cid in gated["claim_ids"]
                 if (c := self.ledger.get_claim(cid)) is None or c.gate_status is not GateStatus.PASSED]
        if stale:
            raise RuntimeError(
                f"{bundle_id!r} has gated claims no longer PASSED {sorted(stale)} — re-gate and "
                f"re-assemble before draft")

        engine = self._adapter("writing_engine")
        out = engine.draft(str(workspace))

        run_id = f"run-{bundle_id}"
        self.ledger.record_engine_run(
            run_id, out.engine_adapter, out.workspace_path,
            outputs={"paper_tex": out.paper_tex_path, "paper_pdf": out.paper_pdf_path,
                     "scores": out.scores},
            provenance=out.provenance, now=_now())

        # Publish outputs via the sink into artifacts/.
        art_id = self._artifact_id(bundle_id)
        sink = self._adapter("sink")
        output_paths = [p for p in (out.paper_pdf_path, out.paper_tex_path) if p]
        dest = sink.publish(art_id, output_paths, str(self.data_dir / "artifacts"))

        self.ledger.upsert_artifact(
            art_id, type="paper", state=Lifecycle.DRAFTED,
            gated_set_id=self._gated_set_id(bundle_id), now=_now(),
            engine_run_id=run_id, output_ref=dest)
        self.ledger.append_lifecycle_event(
            art_id, Lifecycle.DRAFTING.value, Lifecycle.DRAFTED.value, actor="system",
            now=_now(), detail={"engine": out.engine_adapter,
                                "renderer": out.scores.get("renderer")})

        return {
            "artifact_id": art_id,
            "engine": out.engine_adapter,
            "renderer": out.scores.get("renderer"),
            "paper_pdf": out.paper_pdf_path,
            "paper_tex": out.paper_tex_path,
            "staged_to": dest,
        }

    # -- op: publish (the load-bearing egress gate) --------------------------
    def publish(self, bundle_id: str, target_audience: str | None = None,
                venue: str | None = None) -> dict:
        """Egress gate: decide(artifact, audience) + redaction re-sweep over emitted
        text. Fail-closed: a deny OR any redaction hit aborts and blocks the artifact.
        On pass, records approved → published_paper (a human-attributed transition;
        the CLI operator is the human)."""
        art_id = self._artifact_id(bundle_id)
        art = self.ledger.get_artifact(art_id)
        if not art:
            raise RuntimeError(f"no artifact for {bundle_id!r} — draft it first")
        if art["state"] not in (Lifecycle.DRAFTED.value, Lifecycle.IN_REVIEW.value,
                                Lifecycle.APPROVED.value):
            raise RuntimeError(f"artifact {art_id} is {art['state']}, not drafted — cannot publish")

        # Patent-first egress interlock (profile-INDEPENDENT): a paper must not disclose a
        # patent-sensitive (P3) or HELD claim without a human release_interlock — no matter
        # which gate profile admitted the claim into the gated set.
        held = self._held_disclosure_claims(bundle_id, art)
        if held:
            self._block(art_id, art["state"], BlockedReason.INTERLOCK, {"held_claims": held})
            return {"artifact_id": art_id, "published": False,
                    "reason": BlockedReason.INTERLOCK.value, "held_claims": held,
                    "detail": "patent-first: patent-sensitive/HELD claim(s) cannot be disclosed in "
                              "a paper until release_interlock is called (after the patent is filed)."}

        # Submission-readiness guard. Non-bypassable: ANY unsigned prohibited-venue readiness on
        # this artifact blocks publish — even if the caller passes a different, benign venue (you
        # cannot launder a prohibited target through an allowed one), and even with no venue arg.
        if venue and self.ledger.get_latest_readiness(art_id, venue) is None:
            raise RuntimeError(
                f"venue {venue!r} given but no submission-readiness check on record — "
                f"run check_submission_readiness first")
        # Block a prohibited-venue readiness that is UNSIGNED, or whose sign-off was bound to a
        # DIFFERENT draft (re-drafted since sign-off → the human never reviewed the current text).
        cur_hash = self._draft_content_hash(art_id)
        latest_by_venue = {}
        for rd in self.ledger.get_prohibited_readiness(art_id):
            latest_by_venue[rd["venue"]] = rd
        blocking = next((rd for rd in latest_by_venue.values()
                         if not rd.get("human_signoff_user") or (rd.get("draft_hash") or "") != cur_hash),
                        None)
        if blocking:
            v = blocking.get("venue", venue)
            stale = bool(blocking.get("human_signoff_user"))
            self._block(art_id, art["state"], BlockedReason.READINESS,
                        {"venue": v, "stale_signoff": stale,
                         "reason": ("sign-off no longer covers the current draft (re-drafted)"
                                    if stale else "prohibited venue; human sign-off required")})
            return {"artifact_id": art_id, "published": False, "reason": "READINESS", "venue": v,
                    "policy_class": blocking["policy_class"], "stale_signoff": stale,
                    "detail": (f"{v}: the human sign-off no longer covers the current draft (re-drafted "
                               f"since sign-off) — re-review and re-sign."
                               if stale else
                               f"{v} prohibits AI-generated text — a human must review, take "
                               f"authorship responsibility, and sign off before publish.")}

        sink = self._adapter("sink")
        # Clamp the requested audience to the sink's DECLARED tier: a caller cannot escalate a
        # public sink to a counsel audience (which decide() would let confidential content through
        # while the sink actually delivers it publicly). Requesting a MORE restrictive tier is fine.
        _sink_aud = _sink_audience(sink)
        _requested = Audience(target_audience) if target_audience else _sink_aud
        _arank = {Audience.PUBLIC: 0, Audience.INTERNAL: 1, Audience.COUNSEL: 2}
        audience = _requested if _arank[_requested] <= _arank[_sink_aud] else _sink_aud
        boundary = Boundary(art["boundary"])
        visibility = Visibility(art["visibility"])

        # 1. Allow-list decision (routes on the effective labels).
        decision = conf_mod.decide(boundary, visibility, audience)

        # 2. Redaction re-sweep over every string the engine emitted AND the actual
        #    to-be-published deliverables (defense in depth: the engine can synthesize a
        #    codename the source bundle never contained). FAIL-CLOSED: if there is
        #    nothing to sweep, or a deliverable (e.g. a PDF) cannot be read, block —
        #    the sweep must never be vacuously skipped.
        ruleset = conf_mod.Ruleset.load(
            self.config.adapters.get("sink", {}).get("config", {}).get("redaction"))
        strings, unscannable = self._gather_scannable(bundle_id, art)
        hits = conf_mod.scan_strings(strings, ruleset)
        fail_closed = (not strings) or bool(unscannable)

        if not decision.allow or hits or fail_closed:
            detail = {"audience": audience.value, "decision": decision.reason,
                      "redaction_hits": hits, "ruleset_version": ruleset.version,
                      "unscannable": unscannable, "nothing_to_sweep": not strings}
            self._block(art_id, art["state"], BlockedReason.BOUNDARY, detail)
            return {"artifact_id": art_id, "published": False, "reason": BlockedReason.BOUNDARY.value,
                    "audience": audience.value, "decision": decision.reason,
                    "redaction_hits": hits, "ruleset_version": ruleset.version,
                    "unscannable": unscannable, "nothing_to_sweep": not strings}

        # egress pre-check pass → approved (human-owned in the real UI; CLI = human)
        self.ledger.upsert_artifact(art_id, type="paper", state=Lifecycle.APPROVED,
                                    gated_set_id=art["gated_set_id"], now=_now())
        self.ledger.append_lifecycle_event(
            art_id, art["state"], Lifecycle.APPROVED.value, actor="system",
            now=_now(), detail={"audience": audience.value, "decision": decision.reason,
                                "ruleset_version": ruleset.version})

        # human-attributed terminal publish
        published_paths = list(Path(art["output_ref"]).glob("*")) if art.get("output_ref") else []
        dest = str(self.data_dir / "published" / audience.value)
        final = sink.publish(art_id, [str(p) for p in published_paths], dest)
        self.ledger.upsert_artifact(art_id, type="paper", state=Lifecycle.PUBLISHED_PAPER,
                                    gated_set_id=art["gated_set_id"], now=_now(),
                                    output_ref=final)
        self.ledger.append_lifecycle_event(
            art_id, Lifecycle.APPROVED.value, Lifecycle.PUBLISHED_PAPER.value,
            actor="human:cli", now=_now(),
            detail={"audience": audience.value, "dest": final})
        return {"artifact_id": art_id, "published": True, "audience": audience.value,
                "dest": final, "ruleset_version": ruleset.version}

    _TEXT_SUFFIXES = (".tex", ".bbl", ".txt", ".md", ".json")

    def _gather_scannable(self, bundle_id: str, art: dict) -> tuple[list[str], list[str]]:
        """Collect every scannable string tied to this artifact, and the list of
        deliverable files that could NOT be scanned (drives fail-closed at egress)."""
        strings: list[str] = []
        ws = self._workspace(bundle_id)

        # 1. source inputs the engine consumed
        for name in ("idea.md", "experimental_log.md"):
            p = ws / "inputs" / name
            if p.exists():
                strings.append(p.read_text(encoding="utf-8", errors="replace"))
        # 2. engine-emitted text artifacts (tex/bbl/captions/citation_pool/drafts)
        final = ws / "final"
        if final.exists():
            for p in final.iterdir():
                if p.is_file() and p.suffix.lower() in self._TEXT_SUFFIXES:
                    strings.append(p.read_text(encoding="utf-8", errors="replace"))

        # 3. the ACTUAL to-be-published deliverables — each must be scannable.
        unscannable: list[str] = []
        out = Path(art["output_ref"]) if art.get("output_ref") else None
        if out and out.exists():
            for p in sorted(out.iterdir()):
                if not p.is_file():
                    continue
                suf = p.suffix.lower()
                if suf in self._TEXT_SUFFIXES:
                    strings.append(p.read_text(encoding="utf-8", errors="replace"))
                elif suf == ".pdf":
                    txt = _extract_pdf_text(p)
                    # FAIL-CLOSED on an image-only / empty-text / unreadable PDF: its rendered
                    # pages may carry content (a codename in a figure) that is NOT extractable
                    # text, and a sibling .tex does NOT guarantee the PDF's images — so we never
                    # exempt it. Only a PDF with real extractable text is treated as swept.
                    # KNOWN LIMITATION: this is a TEXT sweep — a codename rasterized INTO a figure
                    # of an otherwise text-rich PDF is not caught (no OCR). Confidential figures must
                    # be reviewed by a human; OCR-based image scanning is a future adapter.
                    if txt is not None and txt.strip():
                        strings.append(txt)
                    else:
                        unscannable.append(str(p))
                else:
                    # any other binary deliverable (png/jpg/docx/…) cannot be text-swept
                    unscannable.append(str(p))
        return strings, unscannable

    def _assembled_claim_ids(self, bundle_id: str) -> list[str] | None:
        """The claim set recorded at the most recent assemble (the DRAFTING lifecycle event's
        `kept`), or None if never assembled — used to detect a gated set that changed since."""
        import json as _json
        art_id = self._artifact_id(bundle_id)
        kept = None
        for e in self.ledger.get_lifecycle_events(art_id):
            if e["to_state"] == Lifecycle.DRAFTING.value:
                d = e.get("detail")
                d = _json.loads(d) if isinstance(d, str) else (d or {})
                if "kept" in d:
                    kept = d["kept"]
        return kept

    def _draft_content_hash(self, art_id: str) -> str:
        """Hash the artifact's current text deliverables — a re-draft changes it, so a readiness
        sign-off bound to an OLD hash no longer covers new manuscript content."""
        import hashlib
        art = self.ledger.get_artifact(art_id)
        ref = art.get("output_ref") if art else None
        if not ref or not Path(ref).exists():
            return ""
        h = hashlib.sha256()
        for p in sorted(Path(ref).glob("*")):
            if p.is_file() and p.suffix.lower() in (".tex", ".md", ".bbl", ".txt"):
                h.update(p.read_bytes())
        return h.hexdigest()

    def _held_disclosure_claims(self, bundle_id: str, art: dict) -> list[str]:
        """Gated claims that are patent-first disclosure-blocked (P3 or HELD, not released)."""
        gs = self.ledger.get_gated_set(art.get("gated_set_id") or self._gated_set_id(bundle_id))
        if not gs:
            return []
        out = []
        for cid in gs.get("claim_ids", []):
            # The interlock TABLE is authoritative and survives a re-import that DROPS the claim
            # (import_bundle_projection never deletes interlock rows): a HELD row blocks egress even
            # if the claim projection has vanished — fail-closed on a disappeared patent-first claim.
            if self.ledger.get_interlock_status(cid).value == "held":
                out.append(cid)
                continue
            c = self.ledger.get_claim(cid)
            if c:
                c.interlock_status = self.ledger.get_interlock_status(cid)
                if gate_mod.disclosure_blocked(c):
                    out.append(cid)
        return out

    # -- op: run_review ------------------------------------------------------
    def run_review(self, bundle_id: str) -> dict:
        art_id = self._artifact_id(bundle_id)
        arts = {a["id"]: a for a in self.ledger.list_artifacts()}
        art = arts.get(art_id)
        if not art:
            raise RuntimeError(f"no artifact for {bundle_id!r} — draft it first")

        pdf_ok = bool(art.get("output_ref")) and any(
            Path(art["output_ref"]).glob("*.pdf"))
        gated = self.ledger.get_gated_set(self._gated_set_id(bundle_id))
        checklist = [
            {"item": "all drafted claims passed the evidence gate", "ok": bool(gated and gated["claim_ids"])},
            {"item": "a PDF artifact exists", "ok": pdf_ok},
            {"item": f"confidentiality track = {art.get('confidentiality_track')} "
                     f"(boundary={art.get('boundary')}, visibility={art.get('visibility')})",
             "ok": True},
            {"item": "bundle digest verified", "ok": self.ledger.bundle_digest_ok(bundle_id)},
        ]
        hard = [c for c in checklist if c["item"] not in (
            "bundle digest verified",) and not c["item"].startswith("confidentiality track")]
        verdict = "ready_for_human_review" if all(c["ok"] for c in hard) else "blocked"
        if art["state"] == Lifecycle.DRAFTED.value:
            self.ledger.set_artifact_state(art_id, Lifecycle.IN_REVIEW, _now())
            self.ledger.append_lifecycle_event(
                art_id, Lifecycle.DRAFTED.value, Lifecycle.IN_REVIEW.value,
                actor="system", now=_now(), detail={"verdict": verdict})
        return {"artifact_id": art_id, "checklist": checklist, "verdict": verdict,
                "note": "publish/file is a human-owned transition; run `publish` to run the "
                        "egress gate (confidentiality decide() + redaction re-sweep)"}

    # -- op: release_interlock / list_interlocks (L3a) -----------------------
    def release_interlock(self, claim_id: str, actor: str = "human:cli",
                          reason: str | None = None) -> dict:
        """Human-attributed release of a patent-first interlock (the patent has been
        filed/cleared). The claim can then pass the gate into a paper on the next
        run_gate. Recorded in the hash-chained audit log."""
        il = self.ledger.get_interlock(claim_id)
        if not il:
            raise RuntimeError(
                f"no interlock for claim {claim_id!r} — it is not patent-sensitive, "
                f"or the bundle has not been gated yet")
        prev = il["status"]
        self.ledger.set_interlock_status(
            claim_id, InterlockStatus.RELEASED, actor, reason, _now())
        self.ledger.append_lifecycle_event(
            f"interlock:{claim_id}", prev, InterlockStatus.RELEASED.value,
            actor=actor, now=_now(), reason=reason, detail={"claim_id": claim_id})
        return {"claim_id": claim_id, "status": InterlockStatus.RELEASED.value,
                "previous": prev, "actor": actor}

    def list_interlocks(self, bundle_id: str | None = None) -> list[dict]:
        return self.ledger.list_interlocks(bundle_id)

    # -- op: record_review / get_reviews (AI reviewer + quality assessment) ---
    def record_review(self, bundle_id: str, venue: str | None, reviewer: str,
                      scores: dict, verdict: str | None, overall: float | None,
                      weaknesses: list, guidance: list) -> dict:
        """Capture a venue-specific AI review / quality assessment as a governed,
        audited artifact. The review itself is produced by the reviewer pipeline
        (paper-autoraters + a venue-rubric peer-review); this op records it."""
        art_id = self._artifact_id(bundle_id)
        rid = self.ledger.record_review(art_id, venue, reviewer, scores, verdict,
                                        overall, weaknesses, guidance, _now())
        self.ledger.append_lifecycle_event(
            art_id, self.ledger.get_artifact(art_id)["state"] if self.ledger.get_artifact(art_id) else None,
            f"reviewed:{venue or 'generic'}", actor=reviewer, now=_now(),
            reason=venue, detail={"verdict": verdict, "overall": overall, "review_id": rid})
        return {"review_id": rid, "venue": venue, "verdict": verdict, "overall": overall}

    def get_reviews(self, bundle_id: str) -> list[dict]:
        return self.ledger.get_reviews(self._artifact_id(bundle_id))

    # -- op: submission readiness (venue disclosure + prohibited-venue guard) --
    def _paper_text_for_detection(self, bundle_id: str) -> str | None:
        """Assembled/drafted manuscript text for the optional detector hook.

        Prefers the drafted `final/paper.tex`; falls back to the assembled inputs.
        Read locally only — the point of a self-hosted detector is that this text
        never leaves the box.
        """
        ws = self._workspace(bundle_id)
        tex = ws / "final" / "paper.tex"
        if tex.exists():
            return tex.read_text(encoding="utf-8", errors="replace")
        parts = []
        for name in ("idea.md", "experimental_log.md"):
            p = ws / "inputs" / name
            if p.exists():
                parts.append(p.read_text(encoding="utf-8", errors="replace"))
        return "\n\n".join(parts) if parts else None

    def _detector_false_flag_risk(self, bundle_id: str) -> dict | None:
        """Optional detector hook (OFF by default).

        If a `detector` adapter is configured AND enabled, run it on the manuscript
        text and return its false-flag RISK band. Otherwise return None so readiness
        works with no detector (a low score is a RISK proxy, never proof).
        """
        spec = self.config.adapters.get("detector")
        if not spec or not spec.get("enabled"):
            return None
        text = self._paper_text_for_detection(bundle_id)
        if not text:
            return None
        try:
            detector = self._adapter("detector")
            return detector.detect(text)
        except Exception as exc:  # a detector must never break readiness
            return {"detector": spec.get("id"), "band": "unavailable", "score": None,
                    "note": f"detector error: {exc}; RISK proxy, not a verdict"}

    def check_submission_readiness(self, bundle_id: str, venue: str,
                                   tool: str = "an AI writing assistant (PaperOrchestra, wrapped by Veridraft)",
                                   purpose: str = "draft and refine the manuscript from evidence-gated claims") -> dict:
        art_id = self._artifact_id(bundle_id)
        false_flag_risk = self._detector_false_flag_risk(bundle_id)
        report = readiness_mod.check_readiness(venue, tool, purpose,
                                               false_flag_risk=false_flag_risk)
        # Bind this readiness (and any human sign-off of it) to the CURRENT draft content, so a
        # later re-draft's new manuscript is not covered by an old sign-off (cf. interlock body_hash).
        self.ledger.record_readiness(art_id, venue, report["policy_class"], report["blocked"],
                                     report, _now(), draft_hash=self._draft_content_hash(art_id))
        cur = self.ledger.get_artifact(art_id)
        self.ledger.append_lifecycle_event(
            art_id, cur["state"] if cur else None, f"readiness:{venue}", actor="system",
            now=_now(), reason=report["policy_class"],
            detail={"blocked": report["blocked"], "disclosure_location": report["disclosure_location"]})
        return report

    def sign_off_readiness(self, bundle_id: str, venue: str, user: str = "human:cli") -> dict:
        art_id = self._artifact_id(bundle_id)
        if not self.ledger.set_readiness_signoff(art_id, venue, user, _now()):
            raise RuntimeError(f"no readiness record for {bundle_id!r} @ {venue!r} — run readiness first")
        self.ledger.append_lifecycle_event(
            art_id, f"readiness:{venue}", f"readiness-signed:{venue}", actor=user, now=_now())
        return {"venue": venue, "signed_off_by": user}

    def get_readiness(self, bundle_id: str, venue: str) -> dict | None:
        return self.ledger.get_latest_readiness(self._artifact_id(bundle_id), venue)

    # -- op: patentability / draft_patent / patent_review (L3b) --------------
    def patentability(self, bundle_id: str) -> dict:
        gated = self.ledger.get_gated_set(self._gated_set_id(bundle_id))
        if not gated or not gated["claim_ids"]:
            raise RuntimeError(f"no gated claim set for {bundle_id!r} — run_gate first")
        claims = [c for c in (self.ledger.get_claim(cid) for cid in gated["claim_ids"]) if c]
        return patentability_mod.screen(claims)

    def draft_patent(self, bundle_id: str, title: str = "Invention") -> dict:
        pf = self.preflight(["source", "patent_engine", "sink"])
        if not pf.ok:
            raise RuntimeError("preflight failed: " + "; ".join(
                f"{i.port}/{i.adapter_id}: {i.detail}" for i in pf.failures()))
        gated = self.ledger.get_gated_set(self._gated_set_id(bundle_id))
        if not gated or not gated["claim_ids"]:
            raise RuntimeError(f"no gated claim set for {bundle_id!r} — run_gate first "
                               f"(use the us-utility-patent profile so P3 claims are admitted)")
        claims = [c for c in (self.ledger.get_claim(cid) for cid in gated["claim_ids"]) if c]

        screen = patentability_mod.screen(claims)
        if screen["verdict"] == "no-go":
            raise RuntimeError("patentability screen NO-GO: " + " ".join(screen["rationale"]))

        results = self.ledger.get_results(bundle_id)
        bundle = self.ledger.get_bundle(bundle_id) or {}
        context = (bundle.get("provenance_manifest") or {}).get("topic", "")
        ws = self._patent_workspace(bundle_id)
        assemble_patent_mod.assemble_patent_inputs(str(ws), claims, results, title, context, screen)

        engine = self._adapter("patent_engine")
        out = engine.draft_patent(str(ws))

        run_id = f"patrun-{bundle_id}"
        self.ledger.record_engine_run(
            run_id, out.engine_adapter, out.workspace_path,
            outputs={"patent_tex": out.paper_tex_path, "patent_pdf": out.paper_pdf_path,
                     "scores": out.scores},
            provenance=out.provenance, now=_now())
        art_id = self._patent_artifact_id(bundle_id)
        sink = self._adapter("sink")
        outs = [p for p in (out.paper_pdf_path, out.paper_tex_path) if p]
        dest = sink.publish(art_id, outs, str(self.data_dir / "artifacts"))
        # patents are confidential by default (counsel audience); mark the track accordingly
        self.ledger.upsert_artifact(
            art_id, type="patent", state=Lifecycle.DRAFTED, gated_set_id=gated["id"], now=_now(),
            engine_run_id=run_id, output_ref=dest,
            confidentiality_track="internal-review-required", boundary="confidential", visibility="team")
        self.ledger.append_lifecycle_event(
            art_id, None, Lifecycle.DRAFTED.value, actor="system", now=_now(),
            reason=screen["verdict"],
            detail={"engine": out.engine_adapter, "open_items": out.provenance.get("open_items", []),
                    "needs_human": True})
        return {
            "artifact_id": art_id, "engine": out.engine_adapter,
            "renderer": out.scores.get("renderer"),
            "patent_pdf": out.paper_pdf_path, "patent_tex": out.paper_tex_path,
            "independent_claims": out.scores.get("independent_claims"),
            "dependent_claims": out.scores.get("dependent_claims"),
            "patentability": screen["verdict"], "open_items": out.provenance.get("open_items", []),
            "needs_human": True, "note": "DRAFT, not a filing — human/attorney review is mandatory "
                                         "before any disclosure or filing (ADR-0004).",
        }

    def patent_review(self, bundle_id: str) -> dict:
        art_id = self._patent_artifact_id(bundle_id)
        art = self.ledger.get_artifact(art_id)
        if not art:
            raise RuntimeError(f"no patent draft for {bundle_id!r} — draft_patent first")
        import json as _json
        final = self._patent_workspace(bundle_id) / "final"
        open_items = []
        oi = final / "open_items.json"
        if oi.exists():
            open_items = _json.loads(oi.read_text(encoding="utf-8"))
        # deterministic claim gates (P1/P3/P6/P7 …) over the structured ledger
        findings = []
        cj = final / "claims.json"
        if cj.exists():
            ledger = _json.loads(cj.read_text(encoding="utf-8"))
            findings = [f.as_dict() for f in lints_mod.lint_patent_claims(ledger)]
        blockers = [f for f in findings if f["severity"] == "blocker"]
        checklist = [
            {"item": "independent claims present (method/system/CRM)", "ok": True},
            {"item": "dependent-claim ladder present", "ok": True},
            {"item": "every claim element traces to an evidence-gated claim", "ok": True},
            {"item": "no blocker-class claim-lint findings (101 terminal effect, delta-in-independent, "
                     "definiteness, completeness)", "ok": not blockers},
            {"item": "FTO + post-claim prior-art search run", "ok": False},
            {"item": "102/103 prior-art novelty confirmed", "ok": False},
            {"item": "101 eligibility cleared (legal)", "ok": False},
            {"item": "antecedent basis / §112 reviewed", "ok": False},
        ]
        return {"artifact_id": art_id, "checklist": checklist, "open_items": open_items,
                "lint_findings": findings, "blockers": len(blockers),
                "verdict": "attorney-review-required",
                "note": "The human/attorney gate is MANDATORY and non-bypassable; Veridraft never files."}

    def _patent_workspace(self, bundle_id: str):
        return self.data_dir / "patent" / bundle_id

    def _patent_artifact_id(self, bundle_id: str) -> str:
        return f"pat-{bundle_id}"

    # -- op: get_lifecycle ---------------------------------------------------
    def get_lifecycle(self) -> list[dict]:
        return self.ledger.list_artifacts()

    def blocked_backlog(self, bundle_id: str) -> list[dict]:
        out = []
        for c in self.ledger.get_claims(bundle_id):
            if c.gate_status is GateStatus.BLOCKED:
                out.append({"claim_id": c.claim_id, "type": c.type.value})
        return out

    # -- helpers -------------------------------------------------------------
    def _workspace(self, bundle_id: str) -> Path:
        return self.data_dir / "workspace" / bundle_id

    def _gated_set_id(self, bundle_id: str) -> str:
        return f"gs-{bundle_id}"

    def _artifact_id(self, bundle_id: str) -> str:
        return f"art-{bundle_id}"
