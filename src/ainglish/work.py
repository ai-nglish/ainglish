"""Small orchestration helpers over the existing authoritative API and measurement runners.

This is not an auto-experiment planner: comparator fidelity, fresh inputs and research judgment
remain explicit. Reading a work package cannot create an attempt or consume inference.
"""
import copy
import re


def public_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"a-[0-9a-hjkmnp-tv-z]{16}", value.lower()):
        raise ValueError("proposal must be an immutable a- public_id (not a slug or URL)")
    return value.lower()


def current_proposal(client, identifier):
    # Detail routes still take slugs. The namespace endpoint accepts public IDs and aliases;
    # resolving it explicitly avoids pretending that /proposals/{public_id} is a wire contract.
    namespace = client.proposal_slug_history(identifier)
    current = client.proposal(namespace['current_slug'], authenticated=True)
    if current.get('public_id') != namespace.get('proposal_public_id'):
        raise ValueError('proposal identity changed during namespace resolution; refresh the task')
    return current


def inspect_work(client, proposal, metric=None, replicates_hash=None):
    proposal = public_id(proposal)
    if metric is not None and (not isinstance(metric, str) or not metric.strip()):
        raise ValueError("metric must be a non-empty string")
    if replicates_hash is not None and (not isinstance(replicates_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", replicates_hash)):
        raise ValueError("replicates_hash must be the full lowercase manifest hash")
    snapshot = client.suggestions(proposal=proposal)
    selection = snapshot.get("selection", {})
    if (selection.get("mode"), selection.get("public_id"), selection.get("display_cap_applied")) \
            != ("proposal", proposal, False):
        raise ValueError("server did not acknowledge exact-target lookup; do not infer eligibility")
    current = current_proposal(client, proposal)
    if current.get("public_id") != proposal:
        raise ValueError("proposal identity changed during inspection; refresh the task")

    def matches(card):
        work = card.get("evidence_work") or {}
        return (card.get("public_id") == proposal
                and (metric is None or (card.get("metric") or work.get("metric")) == metric)
                and (replicates_hash is None or card.get("replicates_hash") == replicates_hash
                     or replicates_hash in work.get("target_hashes", [])))

    cards = [c for c in snapshot["suggestions"] if matches(c)]
    blocked = [c for c in snapshot["blocked_suggestions"] if matches(c)]
    stale = any(c.get("stage") != current.get("stage") or c.get("slug") != current.get("slug")
                for c in cards + blocked)
    return copy.deepcopy({
        "kind": "ainglish.sdk.work-package.v1",
        "status": "stale" if stale else "offered" if cards else "blocked" if blocked else "not_offered",
        "generated_at": snapshot["generated_at"],
        "proposal": current,
        "suggestions": [] if stale else cards,
        "blocked_suggestions": blocked,
        "budgets": snapshot["budgets"],
        "runbooks": client.agent_runbooks(),
        "next": [
            "Select one offered action and its current runbook; no offered action means stop.",
            "For measurement, retrieve the exact original and live measurement_template(metric).",
            "Freeze faithful comparators, inputs, readers, estimand and abort conditions before spend.",
            "Use token prepare/mint/run, or an attempt-bearing panel run --submit ONCE.",
            "Retain the payload and receipts; resume_measurement publishes saved output without readers.",
            "Refresh proposal and suggestions; report gates actually moved, including null/adverse outcomes.",
        ],
        "boundary": "A multi-read snapshot, not a permission grant or proof that an experiment is valid.",
    })


def resume_measurement(client, proposal, payload):
    from ainglish.client import _attempt_id, manifest_commitment

    payload = copy.deepcopy(payload)
    if not isinstance(payload, dict) or not isinstance(payload.get("manifest"), dict):
        raise ValueError("saved payload must contain the exact measurement manifest")
    attempt_id = _attempt_id(payload.get("attempt_id"))
    current = current_proposal(client, proposal)
    state = client.attempt(attempt_id)
    pin = state.get("pin", {})
    if state.get("attempt_id") != attempt_id \
            or state.get("proposal") != current.get("slug") \
            or pin.get("manifest_commitment") != manifest_commitment(payload["manifest"]):
        raise ValueError("saved payload does not match the attempt's proposal and manifest pin")
    owner = state.get("minter", {}).get("sub")
    if not isinstance(owner, str) or not owner or owner != client.whoami().get("sub"):
        raise ValueError("only the attempt's author may resume its saved payload")
    if state.get("state") == "completed":
        return {"attempt": state, "already_completed": True,
                "note": "No POST was made. Inspect measurement_ref for the authoritative filed result."}
    if state.get("state") != "open":
        raise ValueError("attempt is not open; do not rerun or overwrite a terminal result")
    response = client.measure(current["slug"], payload)
    receipt = client.attempt(attempt_id)
    if receipt.get("state") != "completed" or not receipt.get("measurement_ref") \
            or receipt.get("attempt_id") != attempt_id \
            or receipt.get("pin", {}).get("manifest_commitment") != pin["manifest_commitment"]:
        raise ValueError("submission returned but completion is not verified; inspect attempt before retrying")
    return {"submission": response, "attempt": receipt, "already_completed": False}
