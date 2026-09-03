#!/usr/bin/env python3
"""Prepare and run canonical ``token_delta`` measurements without hand arithmetic.

This is deliberately a two-phase instrument. ``prepare`` freezes and commits the complete
manifest without loading a tokenizer. The caller then mints that exact manifest through the
authenticated client. Only ``run --attempt-id ...`` imports tiktoken, computes every declared
tokenizer cell, and emits the ready-to-submit payload bound to that attempt.
"""

import argparse
import copy
from fractions import Fraction
import hashlib
import importlib.metadata
import json
import math
import pathlib
import re
import sys

from ainglish import estimand
from ainglish.client import (
    _canonical_json,
    _settlement_strata_contract,
    _validate_measurement_strata,
    manifest_commitment,
)
from ainglish.measure import token_delta


PLAN_KIND = "ainglish.token-measurement-plan.v1"
ATTEMPT_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
PROTECTED_COMPARISON_FIELDS = (
    "kind", "items_sha256", "item_count", "tokenizer_roster", "comparator",
    "population", "aggregation", "unit_span",
)


def _digest(value):
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _nonempty(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string" % field)
    return value.strip()


def _power_of_two(value):
    return value > 0 and value & (value - 1) == 0


def _test_set(manifest):
    if "pairs" in manifest:
        raise ValueError(
            "manifest.pairs is not accepted by the canonical runner; use one manifest.test_set "
            "so the sampled-input identity has a single carrier"
        )
    rows = manifest.get("test_set")
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest.test_set must be a non-empty list")
    if len(rows) > 512:
        raise ValueError("manifest.test_set may contain at most 512 pairs")
    seen = set()
    clean = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError("manifest.test_set[%d] must be an object" % index)
        english = _nonempty(row.get("english"), "manifest.test_set[%d].english" % index)
        ainglish = _nonempty(row.get("ainglish"), "manifest.test_set[%d].ainglish" % index)
        if english == ainglish:
            raise ValueError("manifest.test_set[%d] has identical English and Ainglish arms" % index)
        identity = (english, ainglish)
        if identity in seen:
            raise ValueError("manifest.test_set[%d] duplicates an earlier complete pair" % index)
        seen.add(identity)
        fixed = copy.deepcopy(row)
        fixed["english"], fixed["ainglish"] = english, ainglish
        clean.append(fixed)
    return clean


def _models(manifest):
    models = manifest.get("models")
    if not isinstance(models, list) or not 2 <= len(models) <= 16:
        raise ValueError("manifest.models must name 2–16 tokenizer encodings")
    clean = [_nonempty(model, "manifest.models entry") for model in models]
    if len(set(clean)) != len(clean):
        raise ValueError("manifest.models must not repeat a tokenizer encoding")
    if any(len(model) > 80 for model in clean):
        raise ValueError("manifest.models entries must be at most 80 characters")
    return clean


def _target_estimand_matches(target, declaration):
    """Compare a target's declaration, with one narrow legacy-read compatibility adapter."""
    if estimand.MANIFEST_KEY in target:
        return estimand.validate(target[estimand.MANIFEST_KEY]) == declaration
    legacy = target.get("estimand")
    if not isinstance(legacy, dict):
        return False
    return (
        legacy.get("comparator") == declaration["contrast"]
        and legacy.get("population") == declaration["population"]
        and legacy.get("aggregation") == declaration["aggregation"]["rule"]
    )


def _inherited_size_exception(spec, manifest, rows, declaration):
    target = spec.get("replication_target_manifest")
    rationale = spec.get("inherited_non_power_of_two_rationale")
    if not isinstance(target, dict) or not isinstance(rationale, str) or not rationale.strip():
        raise ValueError(
            "non-power-of-two samples are refused unless a replication supplies "
            "replication_target_manifest and inherited_non_power_of_two_rationale"
        )
    target_hash = manifest.get("replicates_hash")
    if not isinstance(target_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", target_hash):
        raise ValueError("the inherited-size exception requires manifest.replicates_hash")
    if manifest_commitment(target) != target_hash:
        raise ValueError("replication_target_manifest does not hash to manifest.replicates_hash")
    target_rows = _test_set(target)
    if len(target_rows) != len(rows):
        raise ValueError("the replication sample count must exactly match the target sample count")
    if not _target_estimand_matches(target, declaration):
        raise ValueError(
            "the inherited-size exception requires an estimand_contract exactly matching the "
            "target (or an exact mapping to its legacy comparator/population/aggregation)"
        )
    return {
        "kind": "inherited-replication-sample-size-v1",
        "target_manifest_hash": target_hash,
        "target_item_count": len(target_rows),
        "rationale": rationale.strip(),
    }


def _replication_target(spec, manifest):
    """Return the verified target manifest for a replication, or None for an original."""
    target = spec.get("replication_target_manifest")
    target_hash = manifest.get("replicates_hash")
    if target_hash is None:
        if target is not None:
            raise ValueError(
                "replication_target_manifest requires manifest.replicates_hash; an original has no target"
            )
        return None
    if not isinstance(target_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", target_hash):
        raise ValueError("manifest.replicates_hash must be the target's 64-hex manifest commitment")
    if not isinstance(target, dict):
        raise ValueError(
            "a replication requires spec.replication_target_manifest: the target's exact manifest "
            "(client.measurement(replicates_hash)['manifest']). The runner verifies it hashes to "
            "manifest.replicates_hash and decides from it whether an estimand_contract may be attached"
        )
    if manifest_commitment(target) != target_hash:
        raise ValueError("replication_target_manifest does not hash to manifest.replicates_hash")
    return target


def _stratification(manifest, rows, target=None):
    """Validate and freeze deterministic row strata, including replication identity."""
    contract = _settlement_strata_contract(manifest)
    target_contract = _settlement_strata_contract(target) if target is not None else None
    if target is not None:
        current_identity = None if contract is None else [
            (ident, weight) for ident, weight, _share in contract
        ]
        target_identity = None if target_contract is None else [
            (ident, weight) for ident, weight, _share in target_contract
        ]
        if current_identity != target_identity:
            raise ValueError(
                "a replication must preserve the target's settlement_strata ids, order, "
                "and weights exactly"
            )
    if contract is None:
        return None

    declared = {ident for ident, _weight, _share in contract}
    counts = {ident: 0 for ident in declared}
    for index, row in enumerate(rows):
        ident = row.get("stratum")
        if not isinstance(ident, str) or ident not in declared:
            raise ValueError(
                "manifest.test_set[%d].stratum must name exactly one declared settlement stratum"
                % index
            )
        counts[ident] += 1
    missing = [ident for ident, _weight, _share in contract if counts[ident] == 0]
    if missing:
        raise ValueError("settlement strata with no test_set rows: %s" % ", ".join(missing))
    return [
        {"id": ident, "weight": weight, "share": share, "item_count": counts[ident]}
        for ident, weight, share in contract
    ]


def _contract_policy(target, declaration):
    """Decide whether the design declaration is written into the committed manifest.

    The register's commensurability gate holds a ``unit_span`` declared on one side only
    (``incommensurable hold: unit``, ainglish#144). A replication therefore carries an
    ``estimand_contract`` exactly when its target does, and then the same one.
    """
    if target is None:
        return {
            "attached": True,
            "reason": "original study: the declaration is this row's own estimand_contract",
        }
    if estimand.MANIFEST_KEY not in target:
        legacy = target.get("estimand")
        return {
            "attached": False,
            "register_gate": "unit_declared_one_sided",
            "reason": (
                "the replication target declares no estimand_contract; a one-sided unit_span is "
                "held by the register (incommensurable hold: unit), so the design declaration is "
                "kept in plan.design_declaration and the mint estimand, not in the manifest"
            ),
            "target_legacy_estimand_matches": (
                _target_estimand_matches(target, declaration) if isinstance(legacy, dict) else None
            ),
        }
    try:
        target_declaration = estimand.validate(target[estimand.MANIFEST_KEY])
    except ValueError as exc:
        raise ValueError(
            "the replication target's estimand_contract is malformed (%s); the runner cannot "
            "match it, so replicate this target by hand or ask its author to retract and refile" % exc
        ) from None
    if target_declaration.get("kind") != estimand.KIND_V1:
        raise ValueError(
            "the replication target declares a %s estimand_contract; the token runner produces "
            "only %s declarations, so this target cannot be replicated with the runner"
            % (target_declaration.get("kind"), estimand.KIND_V1)
        )
    if target_declaration != declaration:
        raise ValueError(
            "manifest.estimand_contract must equal the target's declaration exactly: a replication "
            "answers the target's question, and the register holds any unit_span difference "
            "(target unit_span %r, yours %r)"
            % (target_declaration.get("unit_span"), declaration.get("unit_span"))
        )
    return {
        "attached": True,
        "reason": "the replication target declares the same estimand_contract",
    }


def prepare(spec):
    """Return a frozen, mint-ready plan without importing or loading a tokenizer."""
    if not isinstance(spec, dict):
        raise ValueError("the run specification must be a JSON object")
    if "manifest" not in spec:
        raise ValueError(
            "the run specification must wrap the committed object under spec.manifest; "
            "replication_target_manifest and rationale are operator inputs, not manifest fields"
        )
    unknown_wrapper = sorted(
        set(spec) - {"manifest", "replication_target_manifest", "inherited_non_power_of_two_rationale"}
    )
    if unknown_wrapper:
        raise ValueError("unknown run-specification field(s): %s" % ", ".join(unknown_wrapper))
    source = spec.get("manifest")
    if not isinstance(source, dict):
        raise ValueError("spec.manifest must be a JSON object")
    manifest = copy.deepcopy(source)
    if manifest.get("metric") != "token_delta":
        raise ValueError("manifest.metric must be token_delta")
    models = _models(manifest)
    rows = _test_set(manifest)
    if len(rows) < 4:
        raise ValueError("token_delta requires at least four complete pairs")
    manifest["models"] = models
    manifest["test_set"] = rows
    target = _replication_target(spec, manifest)
    strata = _stratification(manifest, rows, target=target)

    if "estimand" in manifest:
        raise ValueError(
            "manifest.estimand is a retired duplicate vocabulary; declare the quantity once "
            "under manifest.estimand_contract using ainglish.estimand.declaration()"
        )
    declaration = estimand.validate(manifest.get(estimand.MANIFEST_KEY))
    if declaration["aggregation"]["reducer"] != "least_favourable":
        raise ValueError(
            "manifest.estimand_contract.aggregation.reducer must be least_favourable for the "
            "maximum-tokenizer headline"
        )
    policy = _contract_policy(target, declaration)
    if policy["attached"]:
        manifest[estimand.MANIFEST_KEY] = declaration
    else:
        del manifest[estimand.MANIFEST_KEY]

    sample_exception = None
    if not _power_of_two(len(rows)):
        sample_exception = _inherited_size_exception(spec, manifest, rows, declaration)
        manifest["sample_size_exception"] = sample_exception
    elif "sample_size_exception" in manifest:
        raise ValueError("remove manifest.sample_size_exception from a power-of-two sample")
    elif "inherited_non_power_of_two_rationale" in spec:
        raise ValueError(
            "remove inherited_non_power_of_two_rationale from a power-of-two run specification"
        )

    comparator = declaration["contrast"]
    population = declaration["population"]
    aggregation = declaration["aggregation"]["rule"]
    if "maximum tokenizer mean" not in aggregation.lower() \
            and "least-favourable" not in aggregation.lower() \
            and "least_favourable" not in aggregation.lower():
        raise ValueError(
            "manifest.estimand_contract.aggregation.rule must name the maximum tokenizer mean "
            "or least-favourable rule"
        )

    items_sha256 = _digest(rows)
    supplied_digest = manifest.get("items_sha256")
    if supplied_digest is not None and supplied_digest != items_sha256:
        raise ValueError("manifest.items_sha256 does not match canonical manifest.test_set")
    manifest["items_sha256"] = items_sha256

    expected_identity = {
        "kind": "ainglish.token-comparison-identity.v1",
        "items_sha256": items_sha256,
        "item_count": len(rows),
        "tokenizer_roster": models,
        "comparator": comparator,
        "population": population,
        "aggregation": aggregation,
        "unit_span": declaration["unit_span"],
    }
    supplied_identity = manifest.get("comparison_identity", {})
    if not isinstance(supplied_identity, dict):
        raise ValueError("manifest.comparison_identity must be an object when supplied")
    for key in PROTECTED_COMPARISON_FIELDS:
        if key in supplied_identity and supplied_identity[key] != expected_identity[key]:
            raise ValueError("manifest.comparison_identity.%s conflicts with the frozen design" % key)
    manifest["comparison_identity"] = dict(copy.deepcopy(supplied_identity), **expected_identity)

    if manifest.get("interval_kind") not in (None, "member_span"):
        raise ValueError("manifest.interval_kind conflicts with the token runner's member_span interval")
    manifest["interval_kind"] = "member_span"

    try:
        tiktoken_version = importlib.metadata.version("tiktoken")
    except importlib.metadata.PackageNotFoundError:
        raise ValueError(
            'tiktoken is required before prepare; install "ainglish[tokens]". Version discovery '
            "does not load a tokenizer or expose the frozen items to one"
        ) from None
    provenance = {
        "kind": "ainglish.tiktoken-provenance.v1",
        "library": "tiktoken",
        "library_version": tiktoken_version,
        "encodings": models,
    }
    supplied_provenance = manifest.get("tokenizer_provenance")
    if supplied_provenance is not None and supplied_provenance != provenance:
        raise ValueError("manifest.tokenizer_provenance conflicts with the local runner provenance")
    manifest["tokenizer_provenance"] = provenance

    commitment = manifest_commitment(manifest)
    mint_estimand = (
        "token_delta over %s: %s; population: %s; aggregation: %s"
        % (declaration["unit_span"], declaration["contrast"],
           declaration["population"], declaration["aggregation"]["rule"])
    )
    if len(mint_estimand) > 2000:
        raise ValueError("the estimand_contract renders a mint estimand longer than 2000 characters")
    return {
        "kind": PLAN_KIND,
        "state": "prepared_not_run",
        "manifest": manifest,
        "manifest_commitment": commitment,
        "estimand_contract_policy": policy,
        "design_declaration": declaration,
        "replication_target": None if target is None else {
            "manifest_hash": manifest["replicates_hash"],
            "estimand_contract_declared": estimand.MANIFEST_KEY in target,
        },
        "items_sha256": items_sha256,
        "pair_count": len(rows),
        **({"settlement_strata": strata} if strata is not None else {}),
        "sample_size_rule": sample_exception or {
            "kind": "power-of-two-v1", "item_count": len(rows), "passed": True,
        },
        "mint": {
            "estimand": mint_estimand,
            "admissibility_gates": [
                "every declared tiktoken encoding loads",
                "every frozen English and Ainglish string is countable",
            ],
            "planned_sample": {"items": len(rows), "tokenizers": len(models)},
        },
        "next": "Mint plan.manifest with client.mint_attempt(...) before running this plan.",
    }


def verify_payload(payload, encoder_factory=None):
    """Recompute a canonical token payload from its final manifest before submission.

    The manifest is the experiment; headline and member rows are only claims about that
    experiment. This verifier loads the declared encodings, repeats every count from the frozen
    ``test_set``, and refuses if any submitted result differs. It deliberately supports only the
    canonical runner's digest-pinned tiktoken payloads: hand-authored legacy evidence remains a
    server concern and is never given a misleading local verification receipt.
    """
    if not isinstance(payload, dict) or payload.get("metric") != "token_delta":
        raise ValueError("token payload verification requires metric token_delta")
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("metric") != "token_delta":
        raise ValueError("token payload verification requires manifest.metric token_delta")
    rows, models = _test_set(manifest), _models(manifest)
    if manifest.get("items_sha256") != _digest(rows):
        raise ValueError("manifest.items_sha256 does not match canonical manifest.test_set")
    if manifest.get("interval_kind") != "member_span":
        raise ValueError("canonical token payloads require manifest.interval_kind member_span")

    provenance = manifest.get("tokenizer_provenance")
    if not isinstance(provenance, dict) \
            or provenance.get("kind") != "ainglish.tiktoken-provenance.v1" \
            or provenance.get("library") != "tiktoken" \
            or provenance.get("encodings") != models:
        raise ValueError("canonical token payload verification requires matching tiktoken provenance")
    if encoder_factory is None:
        try:
            import tiktoken
        except ImportError:
            raise ValueError(
                'tiktoken is required to verify this payload before submission; install "ainglish[tokens]"'
            ) from None
        installed = getattr(tiktoken, "__version__", importlib.metadata.version("tiktoken"))
        if provenance.get("library_version") != installed:
            raise ValueError(
                "cannot verify a payload produced with tiktoken %s using installed version %s"
                % (provenance.get("library_version"), installed)
            )
        encoder_factory = tiktoken.get_encoding

    counted = token_delta(
        [(row["english"], row["ainglish"]) for row in rows],
        models,
        encoder_factory=encoder_factory,
    )
    strata = _settlement_strata_contract(manifest)
    expected_members = []
    expected_strata = {}
    for model in models:
        raw = counted["by_tokenizer"][model]
        if strata is None:
            mean = raw["mean"]
        else:
            cells = {}
            for ident, _weight, share in strata:
                values = [
                    delta for row, delta in zip(rows, raw["per_pair"])
                    if row.get("stratum") == ident
                ]
                if not values:
                    raise ValueError("manifest settlement stratum %r has no test_set rows" % ident)
                cells[ident] = sum(values) / len(values)
            unknown = {row.get("stratum") for row in rows} - {row[0] for row in strata}
            if unknown:
                raise ValueError("manifest.test_set carries unknown settlement strata: %s" %
                                 sorted(map(repr, unknown)))
            mean = sum(share * cells[ident] for ident, _weight, share in strata)
            expected_strata[model] = cells
        if not math.isfinite(mean):
            raise ValueError("non-finite recomputed tokenizer mean for %s" % model)
        expected_members.append({"model": model, "value": mean})

    def same(actual, expected, field):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) \
                or not math.isfinite(float(actual)) \
                or not math.isclose(float(actual), float(expected), rel_tol=0, abs_tol=1e-12):
            raise ValueError("%s does not match the value recomputed from manifest.test_set" % field)

    if payload.get("panel_models") != models:
        raise ValueError("panel_models must exactly match manifest.models for a canonical token payload")
    members = payload.get("per_member")
    if not isinstance(members, list) or len(members) != len(expected_members):
        raise ValueError("per_member must report every manifest.models encoding exactly once")
    for index, (actual, expected) in enumerate(zip(members, expected_members)):
        if not isinstance(actual, dict) or actual.get("model") != expected["model"]:
            raise ValueError("per_member order and model ids must exactly match manifest.models")
        same(actual.get("value"), expected["value"], "per_member[%d].value" % index)

    means = [row["value"] for row in expected_members]
    headline = max(means)
    same(payload.get("value"), headline, "value")
    same(payload.get("value_lo"), min(means), "value_lo")
    same(payload.get("value_hi"), headline, "value_hi")
    target = manifest.get("replicates_hash")
    if (target is None and "replicates_hash" in payload) \
            or (target is not None and payload.get("replicates_hash") != target):
        raise ValueError("top-level replicates_hash must exactly match manifest.replicates_hash")

    if strata is not None:
        results = payload.get("stratum_results")
        if not isinstance(results, list) or [row.get("id") for row in results if isinstance(row, dict)] \
                != [row[0] for row in strata]:
            raise ValueError("stratum_results must follow manifest.settlement_strata order")
        cells = expected_strata[models[means.index(headline)]]
        for index, row in enumerate(results):
            same(row.get("value"), cells[row["id"]], "stratum_results[%d].value" % index)
    elif "stratum_results" in payload:
        raise ValueError("unstratified canonical token payload must not carry stratum_results")
    _validate_measurement_strata(payload)
    return {
        "kind": "ainglish.token-measurement-integrity.v1",
        "verified": True,
        "items_sha256": manifest["items_sha256"],
        "pair_count": len(rows),
        "headline_model": models[means.index(headline)],
        "headline_value": headline,
    }


def run_prepared(plan, attempt_id, encoder_factory=None):
    """Count a previously prepared plan and return a complete measurement payload plus audit."""
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND \
            or plan.get("state") != "prepared_not_run":
        raise ValueError("input must be an unmodified prepared token-measurement plan")
    if not isinstance(attempt_id, str) or ATTEMPT_ID.fullmatch(attempt_id) is None:
        raise ValueError("attempt_id must be the full UUID returned by mint_attempt")
    manifest = copy.deepcopy(plan.get("manifest"))
    if not isinstance(manifest, dict) or manifest_commitment(manifest) != plan.get("manifest_commitment"):
        raise ValueError("prepared manifest no longer matches its commitment; do not run it")
    rows, models = _test_set(manifest), _models(manifest)
    if _digest(rows) != plan.get("items_sha256"):
        raise ValueError("prepared test_set no longer matches items_sha256; do not run it")

    if encoder_factory is None:
        try:
            import tiktoken
        except ImportError:
            raise ValueError('tiktoken is required for run; install "ainglish[tokens]"') from None
        installed = getattr(tiktoken, "__version__", importlib.metadata.version("tiktoken"))
        declared = manifest["tokenizer_provenance"]["library_version"]
        if declared != installed:
            raise ValueError(
                "tiktoken version changed after prepare (%s -> %s); prepare and mint a new manifest"
                % (declared, installed)
            )
        encoder_factory = tiktoken.get_encoding

    counted = token_delta(
        [(row["english"], row["ainglish"]) for row in rows],
        models,
        encoder_factory=encoder_factory,
    )
    strata = _stratification(manifest, rows)
    member_rows, means, member_audit = [], [], []
    for name in models:
        raw = counted["by_tokenizer"][name]
        cells = None
        if strata is None:
            mean = raw["mean"]
        else:
            cells = []
            for stratum in strata:
                values = [
                    delta for row, delta in zip(rows, raw["per_pair"])
                    if row["stratum"] == stratum["id"]
                ]
                cell_mean = sum(values) / len(values)
                cells.append({
                    "id": stratum["id"],
                    "weight": stratum["weight"],
                    "share": stratum["share"],
                    "item_count": len(values),
                    "value": cell_mean,
                })
            mean = sum(cell["share"] * cell["value"] for cell in cells)
        if not math.isfinite(mean):
            raise ValueError("non-finite tokenizer mean for %s" % name)
        means.append(mean)
        member_rows.append({"model": name, "value": mean})
        member_audit.append({
            "model": name,
            "mean": mean,
            **({"strata": cells} if cells is not None else {}),
        })

    value = max(means)
    payload = {
        "metric": "token_delta",
        "value": value,
        "value_lo": min(means),
        "value_hi": value,
        "panel_models": models,
        "per_member": member_rows,
        "manifest": manifest,
        "attempt_id": attempt_id,
    }
    if manifest.get("replicates_hash") is not None:
        # The register classifies a row as a replication from the TOP-LEVEL payload field; the
        # copy inside the manifest is identity only. Without this line the documented
        # client.measure(slug, result["payload"]) path files an intended replication as a new
        # original — two live rows were misfiled that way on 2026-09-02 (@dexagon-ai, #147 review).
        payload["replicates_hash"] = manifest["replicates_hash"]
    if strata is not None:
        headline = member_audit[means.index(value)]
        payload["stratum_results"] = [
            {"id": cell["id"], "value": cell["value"]}
            for cell in headline["strata"]
        ]
    _validate_measurement_strata(payload)
    verify_payload(payload, encoder_factory=encoder_factory)
    return {
        "kind": "ainglish.token-measurement-result.v1",
        "state": "computed_not_submitted",
        "payload": payload,
        "audit": {
            "manifest_commitment": plan["manifest_commitment"],
            "items_sha256": plan["items_sha256"],
            "pair_count": len(rows),
            "by_tokenizer": member_audit,
            "headline_rule": "maximum tokenizer mean (least favourable)",
            "headline_model": models[means.index(value)],
        },
        "next": "Submit result.payload unchanged with client.measure(slug, result['payload']).",
    }


class _FakeEncoding:
    def __init__(self, multiplier):
        self.multiplier = multiplier

    def encode(self, text):
        return list(range(len(text.split()) * self.multiplier))


def selftest():
    manifest = {
        "metric": "token_delta",
        "construct": "selftest",
        "models": ["tok-a", "tok-b"],
        "test_set": [
            {"english": "one two three", "ainglish": "one two"},
            {"english": "one two three four", "ainglish": "one"},
            {"english": "one two", "ainglish": "one"},
            {"english": "one two three", "ainglish": "one"},
        ],
        "estimand_contract": estimand.declaration(
            unit_span="complete message",
            contrast="Ainglish form versus complete careful English",
            population="four frozen selftest pairs",
            reducer="least_favourable",
            aggregation_rule="equal item mean, then maximum tokenizer mean",
        ),
    }
    plan = prepare({"manifest": manifest})
    assert plan["pair_count"] == 4 and plan["sample_size_rule"]["passed"] is True
    assert plan["manifest"]["comparison_identity"]["items_sha256"] == plan["items_sha256"]
    result = run_prepared(
        plan, "11111111-2222-4333-8444-555555555555",
        encoder_factory=lambda name: _FakeEncoding(1 if name == "tok-a" else 2),
    )
    assert result["payload"]["value"] == -1.75
    assert [row["value"] for row in result["payload"]["per_member"]] == [-1.75, -3.5]
    assert result["payload"]["panel_models"] == plan["manifest"]["models"]
    assert result["payload"]["manifest"]["interval_kind"] == "member_span"
    assert result["payload"]["manifest"]["estimand_contract"]["governance_effect"] == "report_only"
    assert "replicates_hash" not in result["payload"], "an original carries no top-level replicates_hash"
    receipt = verify_payload(result["payload"], encoder_factory=lambda name: _FakeEncoding(
        1 if name == "tok-a" else 2))
    assert receipt["verified"] is True and receipt["headline_model"] == "tok-a"
    for label, mutate, expected in (
        ("headline", lambda value: value.update(value=value["value"] + 1), "value does not match"),
        ("lower bound", lambda value: value.update(value_lo=value["value_lo"] + 1),
         "value_lo does not match"),
        ("member", lambda value: value["per_member"][0].update(
            value=value["per_member"][0]["value"] + 1), "per_member[0].value"),
        ("model order", lambda value: value["panel_models"].reverse(), "panel_models"),
        ("replication role", lambda value: value.update(replicates_hash="0" * 64),
         "replicates_hash"),
        ("frozen input", lambda value: value["manifest"]["test_set"][0].update(
            english="changed after counting"), "items_sha256"),
    ):
        corrupt = copy.deepcopy(result["payload"])
        mutate(corrupt)
        try:
            verify_payload(corrupt, encoder_factory=lambda name: _FakeEncoding(
                1 if name == "tok-a" else 2))
        except ValueError as exc:
            assert expected in str(exc), (label, exc)
        else:
            raise AssertionError("%s corruption passed deterministic verification" % label)
    assert "stratum_results" not in result["payload"], "an unstratified run invents no result cells"

    stratified = copy.deepcopy(manifest)
    stratified["settlement_strata"] = [
        {"id": "common", "weight": 1},
        {"id": "edge", "weight": 3},
    ]
    for index, row in enumerate(stratified["test_set"]):
        row["stratum"] = "common" if index < 3 else "edge"
    stratified_plan = prepare({"manifest": stratified})
    stratified_result = run_prepared(
        stratified_plan, "22222222-3333-4444-8555-666666666666",
        encoder_factory=lambda name: _FakeEncoding(1 if name == "tok-a" else 2),
    )
    assert "replicates_hash" not in stratified_result["payload"]
    cells = stratified_result["payload"]["stratum_results"]
    assert [row["id"] for row in cells] == ["common", "edge"]
    assert abs(sum(weight * row["value"] for weight, row in zip((0.25, 0.75), cells))
               - stratified_result["payload"]["value"]) < 1e-12
    assert all(len(row["strata"]) == 2
               for row in stratified_result["audit"]["by_tokenizer"])

    for label, mutate, expected in (
        ("missing", lambda value: value["test_set"][0].pop("stratum"), "must name"),
        ("unknown", lambda value: value["test_set"][0].update(stratum="other"), "must name"),
        ("empty", lambda value: [row.update(stratum="common") for row in value["test_set"]],
         "no test_set rows"),
        ("duplicate", lambda value: value["settlement_strata"].append(
            {"id": "common", "weight": 1}), "duplicate"),
        ("bad-weight", lambda value: value["settlement_strata"][0].update(weight=0),
         "positive"),
    ):
        invalid = copy.deepcopy(stratified)
        mutate(invalid)
        try:
            prepare({"manifest": invalid})
        except ValueError as exc:
            assert expected in str(exc), (label, exc)
        else:
            raise AssertionError("%s stratification was accepted" % label)

    dyadic = copy.deepcopy(manifest)
    dyadic["test_set"] = [
        ({"english": "left right e%03d" % index,
          "ainglish": "left a%03d" % index, "id": "d%03d" % index}
         if index < 127 else
         {"english": "left e127", "ainglish": "right a127", "id": "d127"})
        for index in range(128)
    ]
    dyadic_plan = prepare({"manifest": dyadic})
    dyadic_result = run_prepared(
        dyadic_plan, "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
        encoder_factory=lambda name: _FakeEncoding(1 if name == "tok-a" else 2),
    )
    exact = dyadic_result["payload"]["per_member"][0]["value"]
    assert Fraction(exact) == Fraction(-127, 128) and exact != round(exact, 6)
    _canonical_json(dyadic_result["payload"])

    bad = copy.deepcopy(manifest)
    bad["models"] = ["tok-a", "tok-b", "tok-c"]
    bad["test_set"].append({"english": "one two three four five", "ainglish": "one two"})
    try:
        prepare({"manifest": bad})
    except ValueError as exc:
        assert "non-power-of-two" in str(exc)
    else:
        raise AssertionError("unjustified non-power-of-two sample was accepted")

    # Replications: the estimand_contract follows the TARGET (ainglish#144). The register's unit
    # gate holds any one-sided unit_span, so a runner replication of a contract-less legacy original
    # must not carry one; a replication of a declared original must carry the same one.
    fake = lambda name: _FakeEncoding(1 if name == "tok-a" else 2)
    legacy_target = {
        "metric": "token_delta", "models": ["tok-a", "tok-b"],
        "test_set": [{"english": "alpha beta gamma %d" % i, "ainglish": "alpha %d" % i} for i in range(4)],
    }
    replication = copy.deepcopy(manifest)
    replication["test_set"] = [
        {"english": "delta epsilon zeta %d" % i, "ainglish": "delta %d" % i} for i in range(4)
    ]
    replication["replicates_hash"] = manifest_commitment(legacy_target)
    try:
        prepare({"manifest": replication})
    except ValueError as exc:
        assert "replication_target_manifest" in str(exc), exc
    else:
        raise AssertionError("a replication without its target manifest was accepted")
    try:
        prepare({"manifest": manifest, "replication_target_manifest": legacy_target})
    except ValueError as exc:
        assert "replicates_hash" in str(exc), exc
    else:
        raise AssertionError("an original carrying a target manifest was accepted")
    legacy_plan = prepare({"manifest": replication, "replication_target_manifest": legacy_target})
    assert estimand.MANIFEST_KEY not in legacy_plan["manifest"], "one-sided contract would hold at the unit gate"
    assert legacy_plan["estimand_contract_policy"]["attached"] is False
    assert legacy_plan["estimand_contract_policy"]["register_gate"] == "unit_declared_one_sided"
    assert legacy_plan["design_declaration"]["unit_span"] == "complete message"
    assert legacy_plan["replication_target"]["estimand_contract_declared"] is False
    assert legacy_plan["manifest"]["comparison_identity"]["comparator"] == manifest["estimand_contract"]["contrast"]
    assert legacy_plan["manifest"]["replicates_hash"] == replication["replicates_hash"]
    assert "complete message" in legacy_plan["mint"]["estimand"]
    legacy_result = run_prepared(legacy_plan, "11111111-2222-4333-8444-555555555555", encoder_factory=fake)
    assert estimand.MANIFEST_KEY not in legacy_result["payload"]["manifest"]
    assert legacy_result["payload"]["value"] == -2.0
    # The register routes a row as a replication from the TOP-LEVEL payload field, never from the
    # manifest copy; two live rows were misfiled as originals on 2026-09-02 for exactly this gap.
    assert legacy_result["payload"]["replicates_hash"] == legacy_plan["manifest"]["replicates_hash"]
    assert "stratum_results" not in legacy_result["payload"]

    declared_target = copy.deepcopy(legacy_target)
    declared_target["estimand_contract"] = copy.deepcopy(manifest["estimand_contract"])
    declared_replication = copy.deepcopy(replication)
    declared_replication["replicates_hash"] = manifest_commitment(declared_target)
    declared_plan = prepare({"manifest": declared_replication, "replication_target_manifest": declared_target})
    assert declared_plan["manifest"][estimand.MANIFEST_KEY] == estimand.validate(manifest["estimand_contract"])
    assert declared_plan["estimand_contract_policy"]["attached"] is True
    assert declared_plan["replication_target"]["estimand_contract_declared"] is True

    stratified_target = copy.deepcopy(stratified)
    stratified_replication = copy.deepcopy(stratified)
    stratified_replication["test_set"] = [
        {"english": "fresh english words %d" % index,
         "ainglish": "fresh ainglish %d" % index,
         "stratum": "common" if index < 3 else "edge"}
        for index in range(4)
    ]
    stratified_replication["replicates_hash"] = manifest_commitment(stratified_target)
    stratified_replication_plan = prepare({
        "manifest": stratified_replication,
        "replication_target_manifest": stratified_target,
    })
    stratified_replication_result = run_prepared(
        stratified_replication_plan, "33333333-4444-4555-8666-777777777777",
        encoder_factory=fake,
    )
    assert (stratified_replication_result["payload"]["replicates_hash"]
            == stratified_replication["replicates_hash"])
    assert [row["id"] for row in stratified_replication_result["payload"]["stratum_results"]] \
        == ["common", "edge"]

    drifted = copy.deepcopy(stratified_replication)
    drifted["settlement_strata"] = list(reversed(drifted["settlement_strata"]))
    try:
        prepare({"manifest": drifted, "replication_target_manifest": stratified_target})
    except ValueError as exc:
        assert "ids, order, and weights" in str(exc), exc
    else:
        raise AssertionError("a replication with target stratum-contract drift was accepted")

    mismatched = copy.deepcopy(declared_replication)
    mismatched["estimand_contract"] = estimand.declaration(
        unit_span="single clause",
        contrast="Ainglish form versus complete careful English",
        population="four frozen selftest pairs",
        reducer="least_favourable",
        aggregation_rule="equal item mean, then maximum tokenizer mean",
    )
    try:
        prepare({"manifest": mismatched, "replication_target_manifest": declared_target})
    except ValueError as exc:
        assert "unit_span" in str(exc) and "target" in str(exc), exc
    else:
        raise AssertionError("a replication whose contract differs from the target's was accepted")

    wrong_target = copy.deepcopy(declared_target)
    wrong_target["models"] = ["tok-a"]
    try:
        prepare({"manifest": declared_replication, "replication_target_manifest": wrong_target})
    except ValueError as exc:
        assert "does not hash" in str(exc), exc
    else:
        raise AssertionError("a target manifest that does not hash to replicates_hash was accepted")
    print("token_measurement selftest: canonical carrier, provenance, aggregation and refusal gates OK")


def _read(path):
    text = sys.stdin.read() if path == "-" else pathlib.Path(path).read_text(encoding="utf-8")
    return json.loads(text)


def _write(value, path):
    text = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path == "-":
        sys.stdout.write(text)
    else:
        pathlib.Path(path).write_text(text, encoding="utf-8")


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    sub = parser.add_subparsers(dest="command")
    prepare_parser = sub.add_parser("prepare", help="freeze a mint-ready manifest without loading a tokenizer")
    prepare_parser.add_argument("spec")
    prepare_parser.add_argument("-o", "--output", default="-")
    run_parser = sub.add_parser("run", help="run an already-minted prepared plan")
    run_parser.add_argument("plan")
    run_parser.add_argument("--attempt-id", required=True)
    run_parser.add_argument("-o", "--output", default="-")
    args = parser.parse_args(argv)
    try:
        if args.selftest:
            selftest()
            return 0
        if args.command == "prepare":
            _write(prepare(_read(args.spec)), args.output)
            return 0
        if args.command == "run":
            _write(run_prepared(_read(args.plan), args.attempt_id), args.output)
            return 0
        parser.error("choose prepare, run, or --selftest")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, "REFUSING: %s\n" % exc)


if __name__ == "__main__":
    raise SystemExit(cli())
