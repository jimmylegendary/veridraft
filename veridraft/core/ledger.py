"""Claim ledger — a SQLite projection over an imported CAW-02 signed bundle.

Veridraft does NOT re-own the knowledge repo (ADR-0003 option C): it verifies the
bundle digest, references claims/evidence by id/URI, and stores only typing + gate
status + draft routing + its own artifacts. The provenance chain
`Artifact → GatedClaimSet → ClaimRef → evidence_refs → result_id` is reconstructable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .models import (
    Boundary,
    Claim,
    ClaimType,
    Evidence,
    EvidenceKind,
    GateStatus,
    InterlockStatus,
    Lifecycle,
    RawBundle,
    ResultRef,
    Visibility,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bundle (
    bundle_id TEXT PRIMARY KEY,
    source_adapter TEXT NOT NULL,
    boundary TEXT NOT NULL,
    digest TEXT,
    digest_ok INTEGER NOT NULL DEFAULT 0,
    signature TEXT,
    provenance_manifest TEXT,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claim (
    claim_id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL REFERENCES bundle(bundle_id),
    type TEXT NOT NULL,
    statement TEXT NOT NULL,
    result_refs TEXT NOT NULL DEFAULT '[]',
    gate_status TEXT NOT NULL DEFAULT 'pending',
    boundary TEXT NOT NULL DEFAULT 'confidential',
    visibility TEXT NOT NULL DEFAULT 'private'
);
CREATE TABLE IF NOT EXISTS evidence (
    id TEXT NOT NULL,
    claim_id TEXT NOT NULL REFERENCES claim(claim_id),
    kind TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    trust REAL,
    note TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (claim_id, id)
);
CREATE TABLE IF NOT EXISTS result_ref (
    result_id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL REFERENCES bundle(bundle_id),
    description TEXT NOT NULL DEFAULT '',
    metrics TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS gated_set (
    id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    claim_ids TEXT NOT NULL,
    gated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    state TEXT NOT NULL,
    gated_set_id TEXT NOT NULL,
    confidentiality_track TEXT NOT NULL DEFAULT 'internal-review-required',
    boundary TEXT NOT NULL DEFAULT 'confidential',
    visibility TEXT NOT NULL DEFAULT 'private',
    engine_run_id TEXT,
    review_id TEXT,
    output_ref TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_event (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT,
    actor TEXT NOT NULL,
    detail TEXT,
    prev_hash TEXT,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS interlock (
    claim_id TEXT PRIMARY KEY,
    patent_first INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'held',
    reason TEXT,
    actor TEXT,
    body_hash TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    venue TEXT,
    reviewer TEXT NOT NULL,
    scores TEXT NOT NULL DEFAULT '{}',
    verdict TEXT,
    overall REAL,
    weaknesses TEXT NOT NULL DEFAULT '[]',
    guidance TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS readiness (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artifact_id TEXT NOT NULL,
    venue TEXT NOT NULL,
    policy_class TEXT,
    blocked INTEGER NOT NULL DEFAULT 0,
    human_signoff_user TEXT,
    human_signoff_at TEXT,
    report TEXT NOT NULL DEFAULT '{}',
    draft_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS engine_run (
    id TEXT PRIMARY KEY,
    engine_adapter TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    outputs TEXT NOT NULL DEFAULT '{}',
    provenance TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def canonical_digest(bundle: RawBundle) -> str:
    """Deterministic sha256 over the claims+evidence+results (bundle integrity)."""
    payload = {
        "bundle_id": bundle.bundle_id,
        "claims": [
            {
                "claim_id": c.claim_id,
                "type": c.type.value,
                "statement": c.statement,
                # The confidentiality labels are load-bearing and never re-derived, so
                # they MUST be covered by the integrity digest — otherwise a flipped
                # boundary/visibility (confidential→public) would import as digest_ok.
                "boundary": c.boundary.value,
                "visibility": c.visibility.value,
                "evidence": [
                    {"id": e.id, "kind": e.kind.value, "ref": e.ref} for e in c.evidence
                ],
                "result_refs": list(c.result_refs),
            }
            for c in bundle.claims
        ],
        "results": [
            {"result_id": r.result_id, "description": r.description, "metrics": r.metrics}
            for r in bundle.results
        ],
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class Ledger:
    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")   # serialize concurrent writers
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ---- import projection -------------------------------------------------
    def import_bundle_projection(self, bundle: RawBundle, now: str) -> tuple[bool, str]:
        """Store the projection. Returns (digest_ok, computed_digest).

        If the bundle declares a digest, it must match; a mismatch is a hard error
        (the bundle was tampered or mis-exported). A missing digest is allowed but
        recorded as unverified (v1 CAW-02 export may not yet sign).
        """
        computed = canonical_digest(bundle)
        digest_ok = True
        if bundle.digest is not None and bundle.digest != computed:
            raise ValueError(
                f"bundle {bundle.bundle_id!r} digest mismatch: "
                f"declared {bundle.digest} != computed {computed}"
            )
        if bundle.digest is None:
            digest_ok = False  # unverified, but importable in v1
        # KNOWN LIMITATION (authenticity): a MISSING digest imports as digest_ok=False and its
        # declared boundary/visibility labels are still trusted at egress, and a PRESENT digest
        # attests only self-consistency, not authenticity (the `signature` field is stored but not
        # cryptographically verified — that needs a CAW-02 signing key / PKI). Production should
        # consume SIGNED CAW-02 exports and gate public egress on a verified signature; until then
        # treat an unsigned bundle's public label as advisory, not authoritative.

        cur = self.conn
        # Clear any prior projection of this bundle in child → parent order so the
        # re-insert never trips a foreign-key constraint (re-import is idempotent).
        cur.execute(
            "DELETE FROM evidence WHERE claim_id IN "
            "(SELECT claim_id FROM claim WHERE bundle_id=?)", (bundle.bundle_id,))
        cur.execute("DELETE FROM claim WHERE bundle_id=?", (bundle.bundle_id,))
        cur.execute("DELETE FROM result_ref WHERE bundle_id=?", (bundle.bundle_id,))
        cur.execute("DELETE FROM bundle WHERE bundle_id=?", (bundle.bundle_id,))

        cur.execute(
            "INSERT INTO bundle "
            "(bundle_id, source_adapter, boundary, digest, digest_ok, signature, "
            " provenance_manifest, imported_at) VALUES (?,?,?,?,?,?,?,?)",
            (bundle.bundle_id, bundle.source_adapter, bundle.boundary, computed,
             int(digest_ok), bundle.signature,
             json.dumps(bundle.provenance_manifest), now),
        )
        for c in bundle.claims:
            # claim_id is a global PK: a collision with ANOTHER bundle would raise a raw
            # IntegrityError — pre-check and fail with a clean, actionable error instead.
            owner = cur.execute("SELECT bundle_id FROM claim WHERE claim_id=?", (c.claim_id,)).fetchone()
            if owner and owner["bundle_id"] != bundle.bundle_id:
                self.conn.rollback()
                raise ValueError(
                    f"claim_id {c.claim_id!r} already belongs to bundle {owner['bundle_id']!r} — "
                    f"claim ids must be globally unique across bundles")
            cur.execute(
                "INSERT INTO claim "
                "(claim_id, bundle_id, type, statement, result_refs, gate_status, "
                " boundary, visibility) VALUES (?,?,?,?,?,?,?,?)",
                (c.claim_id, bundle.bundle_id, c.type.value, c.statement,
                 json.dumps(c.result_refs), c.gate_status.value,
                 c.boundary.value, c.visibility.value),
            )
            self._reconcile_interlock_body(c.claim_id, c.statement, now)
            for e in c.evidence:
                cur.execute(
                    "INSERT INTO evidence (id, claim_id, kind, ref, trust, note) "
                    "VALUES (?,?,?,?,?,?)",
                    (e.id, c.claim_id, e.kind.value, e.ref, e.trust, e.note),
                )
        for r in bundle.results:
            cur.execute(
                "INSERT INTO result_ref "
                "(result_id, bundle_id, description, metrics) VALUES (?,?,?,?)",
                (r.result_id, bundle.bundle_id, r.description, json.dumps(r.metrics)),
            )
        try:
            self.conn.commit()
        except sqlite3.IntegrityError as e:
            self.conn.rollback()
            raise ValueError(
                f"bundle {bundle.bundle_id!r} import conflict (a claim_id/result_id may collide "
                f"with another bundle): {e}") from e
        return digest_ok, computed

    # ---- reads -------------------------------------------------------------
    def get_claims(self, bundle_id: str) -> list[Claim]:
        rows = self.conn.execute(
            "SELECT * FROM claim WHERE bundle_id=? ORDER BY claim_id", (bundle_id,)
        ).fetchall()
        return [self._row_to_claim(r) for r in rows]

    def get_claim(self, claim_id: str) -> Claim | None:
        row = self.conn.execute(
            "SELECT * FROM claim WHERE claim_id=?", (claim_id,)
        ).fetchone()
        return self._row_to_claim(row) if row else None

    def _row_to_claim(self, row: sqlite3.Row) -> Claim:
        ev_rows = self.conn.execute(
            "SELECT * FROM evidence WHERE claim_id=? ORDER BY id", (row["claim_id"],)
        ).fetchall()
        evidence = [
            Evidence(
                id=e["id"],
                kind=EvidenceKind(e["kind"]),
                ref=e["ref"],
                trust=e["trust"],
                note=e["note"],
            )
            for e in ev_rows
        ]
        return Claim(
            claim_id=row["claim_id"],
            type=ClaimType(row["type"]),
            statement=row["statement"],
            evidence=evidence,
            result_refs=json.loads(row["result_refs"]),
            gate_status=GateStatus(row["gate_status"]),
            boundary=Boundary(row["boundary"]),
            visibility=Visibility(row["visibility"]),
        )

    def get_results(self, bundle_id: str) -> list[ResultRef]:
        rows = self.conn.execute(
            "SELECT * FROM result_ref WHERE bundle_id=? ORDER BY result_id", (bundle_id,)
        ).fetchall()
        return [
            ResultRef(result_id=r["result_id"], description=r["description"],
                      metrics=json.loads(r["metrics"]))
            for r in rows
        ]

    def get_result(self, result_id: str) -> ResultRef | None:
        r = self.conn.execute(
            "SELECT * FROM result_ref WHERE result_id=?", (result_id,)
        ).fetchone()
        return ResultRef(result_id=r["result_id"], description=r["description"],
                         metrics=json.loads(r["metrics"])) if r else None

    def get_bundle(self, bundle_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM bundle WHERE bundle_id=?", (bundle_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["provenance_manifest"] = json.loads(d.get("provenance_manifest") or "{}")
        return d

    def bundle_digest_ok(self, bundle_id: str) -> bool:
        r = self.conn.execute(
            "SELECT digest_ok FROM bundle WHERE bundle_id=?", (bundle_id,)
        ).fetchone()
        return bool(r and r["digest_ok"])

    # ---- writes ------------------------------------------------------------
    def set_gate_status(self, claim_id: str, status: GateStatus) -> None:
        self.conn.execute(
            "UPDATE claim SET gate_status=? WHERE claim_id=?", (status.value, claim_id)
        )
        self.conn.commit()

    def create_gated_set(self, set_id: str, bundle_id: str, profile: str,
                         claim_ids: list[str], now: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO gated_set (id, bundle_id, profile, claim_ids, gated_at) "
            "VALUES (?,?,?,?,?)",
            (set_id, bundle_id, profile, json.dumps(claim_ids), now),
        )
        self.conn.commit()

    def get_gated_set(self, set_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM gated_set WHERE id=?", (set_id,)).fetchone()
        if not r:
            return None
        return {"id": r["id"], "bundle_id": r["bundle_id"], "profile": r["profile"],
                "claim_ids": json.loads(r["claim_ids"]), "gated_at": r["gated_at"]}

    def record_engine_run(self, run_id: str, engine_adapter: str, workspace_path: str,
                          outputs: dict, provenance: dict, now: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO engine_run "
            "(id, engine_adapter, workspace_path, outputs, provenance, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, engine_adapter, workspace_path, json.dumps(outputs),
             json.dumps(provenance), now),
        )
        self.conn.commit()

    def upsert_artifact(self, artifact_id: str, type: str, state: Lifecycle,
                        gated_set_id: str, now: str, engine_run_id: str | None = None,
                        review_id: str | None = None, output_ref: str | None = None,
                        confidentiality_track: str | None = None,
                        boundary: str | None = None,
                        visibility: str | None = None) -> None:
        # On a state-only advance, callers omit labels/refs; preserve the prior values
        # so a later transition never silently downgrades the classification or loses
        # the engine output. Fail-closed defaults only when there is no prior row.
        prev = self.get_artifact(artifact_id) or {}

        def _keep(new, key, closed):
            return new if new is not None else prev.get(key, closed)

        self.conn.execute(
            "INSERT OR REPLACE INTO artifact "
            "(id, type, state, gated_set_id, confidentiality_track, boundary, visibility, "
            " engine_run_id, review_id, output_ref, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (artifact_id, type, state.value, gated_set_id,
             _keep(confidentiality_track, "confidentiality_track", "internal-review-required"),
             _keep(boundary, "boundary", "confidential"),
             _keep(visibility, "visibility", "private"),
             _keep(engine_run_id, "engine_run_id", None),
             _keep(review_id, "review_id", None),
             _keep(output_ref, "output_ref", None), now),
        )
        self.conn.commit()

    def get_artifact(self, artifact_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM artifact WHERE id=?", (artifact_id,)).fetchone()
        return dict(r) if r else None

    def set_artifact_state(self, artifact_id: str, state: Lifecycle, now: str) -> None:
        self.conn.execute(
            "UPDATE artifact SET state=?, updated_at=? WHERE id=?",
            (state.value, now, artifact_id),
        )
        self.conn.commit()

    def list_artifacts(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM artifact ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- hash-chained lifecycle event log ----------------------------------
    def append_lifecycle_event(self, artifact_id: str, from_state: str | None,
                               to_state: str, actor: str, now: str,
                               reason: str | None = None, detail: dict | None = None) -> str:
        # Read-prev + insert must be ATOMIC so two writers cannot both chain off the same
        # prev_hash and fork the chain. BEGIN IMMEDIATE takes the write lock up front.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            return self._append_lifecycle_locked(artifact_id, from_state, to_state, actor,
                                                 now, reason, detail)
        except Exception:
            self.conn.rollback()
            raise

    def _append_lifecycle_locked(self, artifact_id, from_state, to_state, actor, now,
                                 reason, detail) -> str:
        row = self.conn.execute(
            "SELECT hash FROM lifecycle_event ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = row["hash"] if row else ""
        detail_json = json.dumps(detail or {}, sort_keys=True)
        # JSON-encode the field vector (not a '|'-join) so a field CONTAINING '|' cannot forge a
        # colliding payload — each field is unambiguously delimited/escaped.
        payload = json.dumps([prev_hash, artifact_id, from_state, to_state, reason, actor,
                              detail_json, now])
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        cur = self.conn.execute(
            "INSERT INTO lifecycle_event "
            "(artifact_id, from_state, to_state, reason, actor, detail, prev_hash, hash, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, from_state, to_state, reason, actor, detail_json, prev_hash, h, now),
        )
        # Persist a HEAD anchor (seq, hash, count) so verify_lifecycle detects tail-truncation
        # or row deletion — a shorter but internally-consistent chain must NOT verify. For
        # strong tamper-evidence against a DB-level attacker, export lifecycle_head_hash() to an
        # append-only external store.
        count = self.conn.execute("SELECT COUNT(*) AS c FROM lifecycle_event").fetchone()["c"]
        self._set_meta("lifecycle_head", json.dumps({"seq": cur.lastrowid, "hash": h, "count": count}))
        self.conn.commit()
        return h

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO ledger_meta (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def _get_meta(self, key: str) -> str | None:
        r = self.conn.execute("SELECT value FROM ledger_meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else None

    def lifecycle_head_hash(self) -> str | None:
        head = self._get_meta("lifecycle_head")
        return json.loads(head)["hash"] if head else None

    def get_lifecycle_events(self, artifact_id: str | None = None) -> list[dict]:
        if artifact_id:
            rows = self.conn.execute(
                "SELECT * FROM lifecycle_event WHERE artifact_id=? ORDER BY seq",
                (artifact_id,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lifecycle_event ORDER BY seq").fetchall()
        return [dict(r) for r in rows]

    def verify_lifecycle(self) -> bool:
        """Recompute the hash chain across all events; True iff intact AND consistent with the
        persisted HEAD anchor (so tail-truncation / row-deletion is detected, not silently
        accepted as a shorter valid chain)."""
        rows = self.conn.execute("SELECT * FROM lifecycle_event ORDER BY seq").fetchall()
        prev_hash = ""
        for r in rows:
            payload = json.dumps([prev_hash, r["artifact_id"], r["from_state"], r["to_state"],
                                  r["reason"], r["actor"], r["detail"], r["created_at"]])
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != r["hash"]:
                return False
            prev_hash = r["hash"]
        head = self._get_meta("lifecycle_head")
        if head is None:
            # no anchor: only an EMPTY log is trustworthy — a populated log whose anchor row was
            # deleted is tampered, not fail-open. KNOWN LIMITATION: a FULL wipe (all events AND the
            # anchor deleted) is indistinguishable in-DB from a never-used ledger, so it returns
            # True here — detecting that requires an EXTERNAL anchor; publish lifecycle_head_hash()
            # to an append-only store and compare out-of-band for strong tamper-evidence.
            return not rows
        h = json.loads(head)
        if len(rows) != h.get("count"):
            return False                   # rows deleted (count mismatch) → tampered
        if rows and (rows[-1]["seq"] != h.get("seq") or rows[-1]["hash"] != h.get("hash")):
            return False                   # tail truncated / rewritten
        if not rows and h.get("count", 0) != 0:
            return False                   # all rows deleted
        # The mutable artifact.state is a projection: it MUST equal the to_state of its latest
        # hash-chained lifecycle event, so a direct UPDATE to artifact.state (bypassing the log)
        # is tamper-evident too. (interlock.status / readiness stay projections — audit their
        # transitions via the corresponding lifecycle events.)
        for a in self.conn.execute("SELECT id, state FROM artifact").fetchall():
            # The current state must equal the LATEST real state-transition logged for it. Audit
            # events (readiness:*, reviewed:*, readiness-signed:*) carry a ':' and are NOT state
            # transitions, so they're skipped; a genuine transition (drafted, in_review, …) has none.
            # This catches both an out-of-band UPDATE to a never-logged state AND a REVERT to an
            # older logged state.
            last_txn = None
            for r in self.conn.execute(
                    "SELECT to_state FROM lifecycle_event WHERE artifact_id=? ORDER BY seq",
                    (a["id"],)).fetchall():
                if r["to_state"] and ":" not in r["to_state"]:
                    last_txn = r["to_state"]
            if last_txn is not None and a["state"] != last_txn:
                return False
        return True

    # ---- patent-first interlock (L3a) --------------------------------------
    def ensure_interlock(self, claim_id: str, now: str, patent_first: bool = True) -> None:
        exists = self.conn.execute(
            "SELECT 1 FROM interlock WHERE claim_id=?", (claim_id,)).fetchone()
        if not exists:
            self.conn.execute(
                "INSERT INTO interlock (claim_id, patent_first, status, updated_at) "
                "VALUES (?,?,?,?)",
                (claim_id, int(patent_first), InterlockStatus.HELD.value, now))
            self.conn.commit()

    def get_interlock_status(self, claim_id: str) -> InterlockStatus:
        r = self.conn.execute(
            "SELECT status FROM interlock WHERE claim_id=?", (claim_id,)).fetchone()
        return InterlockStatus(r["status"]) if r else InterlockStatus.NONE

    def get_interlock(self, claim_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM interlock WHERE claim_id=?", (claim_id,)).fetchone()
        return dict(r) if r else None

    def set_interlock_status(self, claim_id: str, status: InterlockStatus,
                             actor: str, reason: str | None, now: str) -> None:
        # Bind the decision to the CURRENT claim body, so a later re-import with a different
        # body does not inherit this (RELEASED) authorization for a different, unfiled claim.
        self.conn.execute(
            "UPDATE interlock SET status=?, actor=?, reason=?, body_hash=?, updated_at=? "
            "WHERE claim_id=?",
            (status.value, actor, reason, self._claim_body_hash(claim_id), now, claim_id))
        self.conn.commit()

    def _claim_body_hash(self, claim_id: str) -> str | None:
        r = self.conn.execute("SELECT statement FROM claim WHERE claim_id=?", (claim_id,)).fetchone()
        return hashlib.sha256((r["statement"] or "").encode("utf-8")).hexdigest() if r else None

    def _reconcile_interlock_body(self, claim_id: str, new_statement: str, now: str) -> None:
        """If a RELEASED interlock's recorded body differs from a re-imported claim body, VOID the
        release (re-HOLD) — a human release applied to a different claim body, not this one."""
        row = self.conn.execute(
            "SELECT status, body_hash FROM interlock WHERE claim_id=?", (claim_id,)).fetchone()
        if not row or row["status"] != "released" or row["body_hash"] is None:
            return
        new_bh = hashlib.sha256((new_statement or "").encode("utf-8")).hexdigest()
        if row["body_hash"] != new_bh:
            self.conn.execute(
                "UPDATE interlock SET status='held', reason=?, updated_at=? WHERE claim_id=?",
                ("claim body changed on re-import — prior release voided", now, claim_id))

    def list_interlocks(self, bundle_id: str | None = None) -> list[dict]:
        if bundle_id:
            rows = self.conn.execute(
                "SELECT i.* FROM interlock i JOIN claim c ON c.claim_id=i.claim_id "
                "WHERE c.bundle_id=? ORDER BY i.claim_id", (bundle_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM interlock ORDER BY claim_id").fetchall()
        return [dict(r) for r in rows]

    # ---- AI review / quality assessment (governed, audited artifact) --------
    def record_review(self, artifact_id: str, venue: str | None, reviewer: str,
                      scores: dict, verdict: str | None, overall: float | None,
                      weaknesses: list, guidance: list, now: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO review (artifact_id, venue, reviewer, scores, verdict, overall, "
            " weaknesses, guidance, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (artifact_id, venue, reviewer, json.dumps(scores), verdict, overall,
             json.dumps(weaknesses), json.dumps(guidance), now))
        self.conn.commit()
        return cur.lastrowid

    def get_reviews(self, artifact_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review WHERE artifact_id=? ORDER BY id", (artifact_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["scores"] = json.loads(d["scores"])
            d["weaknesses"] = json.loads(d["weaknesses"])
            d["guidance"] = json.loads(d["guidance"])
            out.append(d)
        return out

    # ---- submission readiness (venue disclosure + prohibited-venue guard) ---
    def record_readiness(self, artifact_id: str, venue: str, policy_class: str | None,
                         blocked: bool, report: dict, now: str, draft_hash: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO readiness (artifact_id, venue, policy_class, blocked, report, draft_hash, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (artifact_id, venue, policy_class, int(blocked), json.dumps(report), draft_hash, now))
        self.conn.commit()
        return cur.lastrowid

    def get_prohibited_readiness(self, artifact_id: str) -> list[dict]:
        """All PROHIBITED-venue readiness rows for this artifact (signed or not)."""
        rows = self.conn.execute(
            "SELECT * FROM readiness WHERE artifact_id=? AND policy_class='prohibited' ORDER BY id",
            (artifact_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_readiness(self, artifact_id: str, venue: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM readiness WHERE artifact_id=? AND venue=? ORDER BY id DESC LIMIT 1",
            (artifact_id, venue)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["report"] = json.loads(d["report"])
        return d

    def get_blocking_readiness(self, artifact_id: str) -> dict | None:
        """Any recorded prohibited-venue readiness for this artifact that has NOT been signed
        off — so publish() honors it even when no `venue` argument is passed (non-bypassable)."""
        r = self.conn.execute(
            "SELECT * FROM readiness WHERE artifact_id=? AND policy_class='prohibited' "
            "AND (human_signoff_user IS NULL OR human_signoff_user='') ORDER BY id DESC LIMIT 1",
            (artifact_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["report"] = json.loads(d["report"])
        return d

    def set_readiness_signoff(self, artifact_id: str, venue: str, user: str, now: str) -> bool:
        r = self.conn.execute(
            "SELECT id FROM readiness WHERE artifact_id=? AND venue=? ORDER BY id DESC LIMIT 1",
            (artifact_id, venue)).fetchone()
        if not r:
            return False
        self.conn.execute(
            "UPDATE readiness SET human_signoff_user=?, human_signoff_at=? WHERE id=?",
            (user, now, r["id"]))
        self.conn.commit()
        return True
