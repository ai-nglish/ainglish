"""Pure, advisory matching of source readers to an explicitly supplied local inventory."""
import json
import re

ANSWER_FIELDS = ("provider", "model", "precision", "api", "answer_protocol", "max_tokens",
                 "temperature", "seed", "top_p", "top_k", "num_ctx", "reasoning_effort")
READER_METRICS = frozenset({"comprehension_accuracy_delta", "interpretation_entropy_delta",
                          "robustness_delta"})


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
    if not isinstance(reader_inventory, list) or len(reader_inventory) > 16 \
            or any(not isinstance(r, dict) for r in reader_inventory):
        raise ValueError("reader_inventory must be a list of at most 16 bound reader receipts")

    def roster(reader):
        name, precision = reader.get("name"), reader.get("precision")
        if not isinstance(name, str) or not name:
            return None
        return name + ("@" + precision if isinstance(precision, str) and precision else "")

    local = {}
    for reader in reader_inventory:
        name = roster(reader)
        if name is None or name in local:
            raise ValueError("inventory roster identifiers must be non-empty and unique")
        local[name] = reader
    models, sources = manifest.get("models"), manifest.get("readers")
    complete = (isinstance(models, list) and 0 < len(models) <= 16
                and all(isinstance(m, str) and m for m in models) and len(set(models)) == len(models)
                and isinstance(sources, list) and len(sources) == len(models)
                and all(isinstance(r, dict) for r in sources)
                and [roster(r) for r in sources] == models)
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
