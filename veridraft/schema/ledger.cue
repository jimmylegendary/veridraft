// CUE invariants for the claim ledger — OPTIONAL defense-in-depth (ADR-0003).
//
// The Python gate (core/gate.py) is AUTHORITATIVE. If the `cue` binary is installed,
// the harness also runs `cue vet ledger.cue <snapshot>.json` so the hard invariants
// are declared in two places. Definitions (#X) are CLOSED by default in CUE, so an
// unknown evidence kind or claim type is a vet failure (not silently allowed).
//
// Snapshot shape produced by core/gate.py:cue_vet_snapshot:
//   { "claims": [ { "claim_id": "..", "type": "P1", "evidence": [ {id,kind,ref} ] } ] }

#Kind: "caw02_evidence" | "caw01_result" | "source_artifact" | "generated_text" | "prose_note"
#Type: "P1" | "P2" | "P3"

#Evidence: {
	id:   string
	kind: #Kind
	ref:  string
	// Real evidence must carry a non-empty, resolvable ref. Generated/prose text is
	// allowed to appear in the snapshot but is never counted as evidence by the gate.
	if kind == "caw02_evidence" || kind == "caw01_result" || kind == "source_artifact" {
		ref: !=""
	}
}

#Claim: {
	claim_id: string
	type:     #Type
	evidence: [...#Evidence]
}

claims: [...#Claim]
