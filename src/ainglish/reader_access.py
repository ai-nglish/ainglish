"""Pure, advisory matching of source readers to an explicitly supplied local inventory."""
import copy
import json
import re

ANSWER_FIELDS = ("provider", "model", "api", "answer_protocol", "max_tokens",
                 "temperature", "seed", "top_p", "top_k", "num_ctx", "reasoning_effort")
READER_METRICS = frozenset({"comprehension_accuracy_delta", "interpretation_entropy_delta",
                          "robustness_delta", "learnability"})


def _roster(reader):
    name, precision = reader.get("name"), reader.get("precision")
    if not isinstance(name, str) or not name:
        return None
    return name + ("@" + precision if isinstance(precision, str) and precision else "")


def _inventory(reader_inventory):
    if not isinstance(reader_inventory, list) or len(reader_inventory) > 16 \
            or any(not isinstance(r, dict) for r in reader_inventory):
        raise ValueError("reader_inventory must be a list of at most 16 bound reader receipts")
    local = {}
    for reader in reader_inventory:
        name = _roster(reader)
        if name is None or name in local:
            raise ValueError("inventory roster identifiers must be non-empty and unique")
        local[name] = reader
    return local


def assess(measurement, reader_inventory):
    """Compare exact receipts without probing endpoints, inference, downloads or writes.

    Inventory entries are ``panel.reader_receipt`` objects obtained after explicitly
    binding the caller's own configured endpoints with ``prepare_reader_instruments``.
    A supplied snapshot is not live access or qualification. Private URLs/credentials
    are neither fetched nor included in this report. Do not pass a model-name list.
    """
    from .client import manifest_commitment

    def literal(value):
        # Inventory is not a scientific manifest: a different, ordinary sampler
        # float must report mismatch, not fail the manifest portability validator.
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    if not isinstance(measurement, dict) or not isinstance(measurement.get("manifest"), dict):
        raise ValueError("measurement must contain the exact source manifest")
    manifest = measurement["manifest"]
    commitment = measurement.get("manifest_hash")
    if not isinstance(commitment, str) or commitment != manifest_commitment(manifest):
        raise ValueError("source manifest does not match its measurement commitment")
    if measurement.get("metric") != manifest.get("metric") or manifest.get("metric") not in READER_METRICS:
        raise ValueError("source must be a committed reader-metric measurement")
    local = _inventory(reader_inventory)
    models, sources = manifest.get("models"), manifest.get("readers")
    complete = (isinstance(models, list) and 0 < len(models) <= 16
                and all(isinstance(m, str) and m for m in models) and len(set(models)) == len(models)
                and isinstance(sources, list) and len(sources) == len(models)
                and all(isinstance(r, dict) for r in sources)
                and [_roster(r) for r in sources] == models)
    rows = []
    if complete:
        for name, source in zip(models, sources):
            missing = [k for k in ANSWER_FIELDS if k not in source or source[k] is None or source[k] == ""]
            if missing:
                rows.append({"roster_id": name, "status": "source_incomplete", "fields": missing})
                continue
            candidate = local.get(name)
            if candidate is None:
                rows.append({"roster_id": name, "status": "not_in_inventory", "fields": []})
                continue
            differences = [k for k in ANSWER_FIELDS if k not in candidate
                           or literal(candidate[k]) != literal(source[k])]
            # A plain-model roster legitimately omits the optional precision label.
            # Exact quantization still rides in the bound weight digest. Never invent
            # a label or confuse its omission with a missing answer-affecting setting.
            if literal(candidate.get("precision")) != literal(source.get("precision")):
                differences.append("precision")
            for k in ("model_catalog", "model_catalog_binding", "credential_boundary"):
                if literal(candidate.get(k)) != literal(source.get(k)):
                    differences.append(k)
            source_digest = source.get("model_digest")
            source_preparation = source.get("instrument_preparation")
            bound = (isinstance(source_digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", source_digest)
                     and isinstance(source_preparation, dict)
                     and source_preparation.get("binding") not in (None, "unbound"))
            if bound and candidate.get("model_digest") != source_digest:
                differences.append("model_digest")
            prepared = candidate.get("instrument_preparation")
            if not isinstance(prepared, dict) or prepared.get("binding") in (None, "unbound"):
                differences.append("instrument_preparation")
            state = "mismatch" if differences else "matching_inventory" if bound else "provider_opaque"
            rows.append({"roster_id": name, "status": state, "fields": differences})
    statuses = {r["status"] for r in rows}
    status = ("source_incomplete" if not complete or "source_incomplete" in statuses
              else "mismatch" if "mismatch" in statuses
              else "not_in_inventory" if "not_in_inventory" in statuses
              else "provider_opaque" if "provider_opaque" in statuses else "matching_inventory")
    return {
        "kind": "ainglish.reader-access-check.v1", "source_manifest_hash": commitment,
        "status": status, "readers": rows, "inventory_basis": "caller-supplied snapshot",
        "inference_calls": 0, "writes": 0,
        "remaining_checks": [
            "Refresh authenticated suggestions and the proposal; confirm independent eligibility.",
            "Verify current access and exact bindings on your own configured endpoints.",
            "Qualify the exact reader configuration on target-independent controls.",
            "Preserve source population, comparator, scoring and strata on wholly fresh inputs.",
            "Check prerequisites, freeze the study, preflight and mint before spend.",
        ],
        "boundary": "A matching supplied inventory is not live access, qualification, scientific validity, "
                    "settlement eligibility or promised progression. Provider-opaque matching leaves weight "
                    "identity unknown; preserve that disclosure and follow the source's provider policy. "
                    "A different model requires a separately scoped original, not silent substitution.",
    }


def discover(client, reader_inventory, proposals, *, max_sources=20):
    """Bounded exact-target lookup, not filtering a capped global suggestion list.

    The caller chooses and orders up to 20 proposal IDs. Only independently offered
    reader replications are inspected. Private inventory endpoints are never fetched.
    Network/server errors propagate; they must not become an empty-work verdict.
    """
    from .work import public_id

    _inventory(reader_inventory)  # Reject invalid input before any network request.
    if not isinstance(proposals, list) or not 1 <= len(proposals) <= 20:
        raise ValueError("proposals must be a list of 1 to 20 explicit public IDs")
    identifiers = [public_id(p) for p in proposals]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("proposals must not contain duplicate public IDs")
    if type(max_sources) is not int or not 1 <= max_sources <= 100:
        raise ValueError("max_sources must be an integer from 1 to 100")
    result = {"kind": "ainglish.reader-work.v1", "scope": identifiers,
              "proposals": [], "candidates": [], "unchecked_sources": [],
              "max_sources": max_sources, "sources_checked": 0, "matched_sources": 0,
              "truncated": False, "inference_calls": 0, "writes": 0,
              "boundary": "Only these caller-selected proposals and this supplied inventory were checked. "
              "No match is not a claim that the project has no work. Matching leaves fresh proposal "
              "inspection, study holds, scientific validity, exact qualification and minting to check. "
              "No source URL, inventory endpoint, model download or inference is accessed."}
    seen = set()
    for identifier in identifiers:
        snapshot = client.suggestions(proposal=identifier)
        selection = snapshot.get("selection") or {}
        if (selection.get("mode"), selection.get("public_id"), selection.get("display_cap_applied")) \
                != ("proposal", identifier, False):
            raise ValueError("server did not acknowledge uncapped exact-target lookup; do not infer eligibility")

        def reader_replication(card):
            metric = card.get("metric") or (card.get("evidence_work") or {}).get("metric")
            target = card.get("replicates_hash")
            return (card.get("public_id") == identifier and metric in READER_METRICS
                    and isinstance(target, str) and re.fullmatch(r"[0-9a-f]{64}", target))

        offered = [c for c in snapshot["suggestions"] if reader_replication(c)]
        blocked = [c for c in snapshot["blocked_suggestions"] if reader_replication(c)]
        # Blocked cards remain visible, but their sources are not fetched as runnable work.
        result["proposals"].append({"public_id": identifier,
            "generated_at": snapshot["generated_at"], "offered_reader_replications": len(offered),
            "blocked_suggestions": copy.deepcopy(blocked)})
        for card in offered:
            target = card["replicates_hash"]
            key = (identifier, target)
            if key in seen:
                continue
            seen.add(key)
            row = {k: copy.deepcopy(card[k]) for k in
                   ("public_id", "slug", "stage", "replicates_hash", "coordination", "progression_effect")
                   if k in card}
            row.update(metric=card.get("metric") or card["evidence_work"]["metric"],
                       generated_at=snapshot["generated_at"])
            if result["sources_checked"] >= max_sources:
                result["unchecked_sources"].append(row)
                result["truncated"] = True
                continue
            measurement = client.measurement(target)
            result["sources_checked"] += 1
            # Never treat a changed/retracted source as a match from an older work card.
            if (measurement.get("manifest_hash") != target
                    or measurement.get("metric") != row["metric"]):
                row["access"] = {"status": "invalid_source", "reason": "source identity or metric differs from the offered task"}
            elif not isinstance(measurement.get("proposal"), dict) \
                    or measurement["proposal"].get("public_id") != identifier:
                row["access"] = {"status": "invalid_source", "reason": "source does not belong to the offered proposal"}
            elif (measurement.get("evidence_state") != "valid" or measurement.get("retraction")
                    or measurement.get("is_replication") is not False or measurement.get("confirmed")
                    or measurement["proposal"].get("slug") != card.get("slug")
                    or measurement["proposal"].get("stage") != card.get("stage")):
                row["access"] = {"status": "source_changed", "reason": "source is no longer an unsettled valid original at the offered stage and slug"}
            else:
                try:
                    row["access"] = assess(measurement, reader_inventory)
                except ValueError:
                    row["access"] = {"status": "invalid_source", "reason": "source commitment or reader contract failed validation"}
            if row["access"]["status"] == "matching_inventory":
                result["matched_sources"] += 1
            row["source_result"] = {k: copy.deepcopy(measurement[k]) for k in
                ("value", "value_lo", "value_hi", "stance", "confirmed", "settlement_state") if k in measurement}
            result["candidates"].append(row)
    return result
