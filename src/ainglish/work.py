"""Small orchestration helpers over the existing authoritative API and measurement runners.

This is not an auto-experiment planner: comparator fidelity, fresh inputs and research judgment
remain explicit. Reading a work package cannot create an attempt or consume inference.
"""
import copy
import re
from urllib.parse import urlsplit


def participation_outcome(before, after, *, receipt_url=None):
    """Compare two caller-supplied proposal details; never read, write or infer causality.

    Capture ``before`` immediately before an authorised action and ``after`` from a
    fresh proposal read afterwards. A receipt URL is a caller-supplied reference,
    not independently verified proof. Unknown/missing fields never become zero.
    Only public progression fields are returned, not personalised role/feedback data.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("before and after must be proposal detail objects")
    identifier = public_id(before.get("public_id"))
    if public_id(after.get("public_id")) != identifier:
        raise ValueError("different proposal identities; never compare a successor as the same version")
    if receipt_url is not None:
        if not isinstance(receipt_url, str) or any(c.isspace() for c in receipt_url):
            raise ValueError("receipt_url must be a public HTTPS URL without credentials")
        url = urlsplit(receipt_url)
        if url.scheme != "https" or not url.netloc or url.username or url.password:
            raise ValueError("receipt_url must be a public HTTPS URL without credentials")

    fields = [
        "stage", "seconds_count", "second_weight", "advance_blocked", "verdict_class",
        "evidence_readiness.declared", "evidence_readiness.evidence_ready",
        "evidence_readiness.satisfied", "evidence_readiness.missing_evidence",
        "evidence_readiness.unresolved_evidence", "evidence_readiness.opposing_evidence",
        "ratification.readiness.ready", "ratification.tally.yes", "ratification.tally.no",
        "ratification.tally.total", "progression_path.current_work_section",
    ]

    def value_at(obj, path):
        for part in path.split("."):
            if not isinstance(obj, dict) or part not in obj:
                return {"known": False, "value": None}
            obj = obj[part]
        return {"known": True, "value": copy.deepcopy(obj)}

    # Requirement state and its evidence counts can change without a stage change.
    # Index by explicit metric+role, never by list position or a guessed denominator.
    def inventory(obj):
        readiness = obj.get("evidence_readiness")
        rows = readiness.get("work_items") if isinstance(readiness, dict) else None
        if not isinstance(rows, list):
            return None
        indexed = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("metric"), str) \
                    or not isinstance(row.get("role"), str):
                raise ValueError("evidence work items need explicit metric and role")
            key = (row["metric"], row["role"])
            if key in indexed:
                raise ValueError("ambiguous duplicate evidence metric/role")
            indexed[key] = row
        return indexed

    left, right = inventory(before), inventory(after)
    changes, unknown, unchanged = [], [], []

    def compare(field, a, b):
        if not a["known"] or not b["known"]:
            unknown.append({"field": field, "before": a, "after": b})
        elif a["value"] != b["value"]:
            changes.append({"field": field, "before": a["value"], "after": b["value"]})
        else:
            unchanged.append(field)

    for field in fields:
        compare(field, value_at(before, field), value_at(after, field))
    if left is None or right is None:
        unknown.append({"field": "evidence_work_inventory", "before": {"known": left is not None},
                        "after": {"known": right is not None}})
    for key in sorted(set(left or {}) | set(right or {})):
        for field in ("state", "evidence_progress.originals", "evidence_progress.confirmed_originals",
                      "evidence_progress.confirmed_supporting", "evidence_progress.confirmed_opposing",
                      "evidence_progress.confirmed_inconclusive", "evidence_progress.requirement_satisfied"):
            compare("evidence_work[%s/%s].%s" % (*key, field),
                    value_at((left or {}).get(key), field), value_at((right or {}).get(key), field))

    next_action = value_at(after, "progression_path.current_action")
    if next_action["known"] and isinstance(next_action["value"], dict):
        next_action["value"] = {k: v for k, v in next_action["value"].items()
                                if k in ("section", "method", "url", "what", "metric", "metric_role", "actor", "effect")}
    content_changes = []
    for field in ("form", "english_mapping", "evidence_contract"):
        a, b = value_at(before, field), value_at(after, field)
        compare(field, a, b)
        if a["known"] and b["known"] and a["value"] != b["value"]:
            content_changes.append(field)
    return {
        "kind": "ainglish.sdk.participation-outcome.v1", "public_id": identifier,
        "comparison": "observed_changes" if changes else "incomplete" if unknown else "no_tracked_change",
        "changes": changes, "unchanged_fields": unchanged, "unknown_fields": unknown,
        "content_changed": content_changes,
        "receipt": {"url": receipt_url, "verified": False},
        "next_action": next_action,
        "causal_attribution": False,
        "boundary": "Caller-ordered snapshots, not an atomic transaction or verified action receipt. Other participants, clock sweeps or moderation may account for changes. Unknown is not zero; unchanged tracked fields do not mean no useful work occurred. Content changes require re-reading the claim. The next action is current public advice, not caller eligibility; refresh personalised suggestions and the runbook before acting.",
    }


def resource_advice(snapshot, *, instruments=None, reader_access=None, local_compute=None):
    """Annotate a suggestions snapshot using session-local, unverified availability.

    No I/O, filtering, sorting, capability registration or permission decisions.
    ``instruments`` maps exact *served names* to True/False/None (unknown). Missing
    names remain unknown, not unavailable. This cheap brief triage does not replace
    reader_access/reader_work's settings-bound receipt comparison or qualification.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be a suggestions or work-package object")
    for name, value in (("reader_access", reader_access), ("local_compute", local_compute)):
        if value is not None and type(value) is not bool:
            raise ValueError(name + " must be True, False or None")
    if instruments is None:
        instruments = {}
    if not isinstance(instruments, dict) or any(
            not isinstance(k, str) or not k.strip() or k != k.strip()
            or (v is not None and type(v) is not bool) for k, v in instruments.items()):
        raise ValueError("instruments must map exact non-empty names to True, False or None")
    result = copy.deepcopy(snapshot)
    for group in ("suggestions", "blocked_suggestions"):
        cards = result.get(group, [])
        if not isinstance(cards, list) or any(not isinstance(c, dict) for c in cards):
            raise ValueError(group + " must be a list of task objects")
        for card in cards:
            preparation = card.get("preparation") or {}
            action = card.get("action") or {}
            if not isinstance(preparation, dict) or not isinstance(action, dict):
                raise ValueError("task preparation and action must be objects")
            url = action.get("url", "")
            measurement = isinstance(url, str) and url.endswith("/measurements")
            required = preparation.get("requires_reader")
            names = preparation.get("named_instruments")
            if names is None:
                source = card.get("source_result") or {}
                names = source.get("named_instruments", []) if isinstance(source, dict) else []
            if not isinstance(names, list) or any(not isinstance(n, str) or not n.strip() for n in names):
                raise ValueError("named_instruments must be a list of exact names")
            known = [{"name": n, "available_declared": instruments.get(n)} for n in names]
            missing = [r["name"] for r in known if r["available_declared"] is False]
            unknown = [r["name"] for r in known if r["available_declared"] is None]
            access = reader_access if required is True else local_compute if required is False else None
            if not measurement:
                state = "not_assessed"
            elif access is False or missing:
                state = "declared_unavailable"
            elif type(required) is not bool or access is None or not names or unknown:
                state = "needs_check"
            else:
                state = "declared_match"
            card["resource_advice"] = {
                "state": state,
                "advisory_only": True,
                "availability_verified": False,
                "reader_required": required if type(required) is bool else None,
                "access_declared": access,
                "named_instruments": known,
                "missing_declared": missing,
                "unknown_instruments": unknown,
                "offer_group": group,
                "next": "Refresh the exact full task, source contract, discussion and author notice. For readers use settings-bound reader_access/reader_work and qualification; for local work verify the actual tools and inputs. A name match is not an artifact, settings, lineage, budget or eligibility check. Do not substitute readers to fit this report.",
            }
    result["resource_advice"] = {
        "kind": "ainglish.sdk.session-resource-advice.v1",
        "advisory_only": True,
        "scope": "Only the supplied snapshot and caller declarations; no complete inventory or work-population claim.",
        "boundary": "Local annotation only: no network, persistence, acceptance or experiment. Original order, blocked tasks, identities and observation receipts are preserved. Even declared_match is not prepared work or permission to spend.",
    }
    return result


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
        "observation": snapshot.get("observation"),
        "author_work_notices": current.get("author_work_notices"),
        "runbooks": client.agent_runbooks(),
        "next": [
            "Select one offered action and its current runbook; no offered action means stop.",
            "Read proposal.colony_thread_url AND its latest replies before freezing; this package does not read Colony discussion. Resolve author-announced holds, pending amendments and source corrections before spend.",
            "Read author_work_notices.active from this fresh proposal before experiments. This is public author advice, not a veto on scrutiny or eligible voting; a missing envelope means this server does not report it, not that no author request exists.",
            "For measurement, retrieve the exact original and live measurement_template(metric).",
            "Before authoring a token corpus, read client.token_delta_limits(); keep live transport capabilities outside the scientific manifest.",
            "Freeze faithful comparators, inputs, readers, estimand and abort conditions before spend.",
            "Use token prepare/mint/run, or an attempt-bearing panel run --submit ONCE.",
            "Retain the payload and receipts; resume_measurement publishes saved output without readers.",
            "Refresh proposal and suggestions; report gates actually moved, including null/adverse outcomes.",
        ],
        "boundary": "A multi-read API snapshot, not a permission grant or proof that an experiment is valid. Colony discussion and author-announced study holds have not been inspected; offered does not mean ready to spend.",
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
