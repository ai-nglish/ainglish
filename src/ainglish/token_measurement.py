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
from ainglish.client import _canonical_json, manifest_commitment
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
    manifest[estimand.MANIFEST_KEY] = declaration

    sample_exception = None
    if not _power_of_two(len(rows)):
        sample_exception = _inherited_size_exception(spec, manifest, rows, declaration)
        manifest["sample_size_exception"] = sample_exception
    elif "sample_size_exception" in manifest:
        raise ValueError("remove manifest.sample_size_exception from a power-of-two sample")
    elif "replication_target_manifest" in spec or "inherited_non_power_of_two_rationale" in spec:
        raise ValueError(
            "remove inherited-size operator inputs from a power-of-two run specification"
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
        "items_sha256": items_sha256,
        "pair_count": len(rows),
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
    member_rows, means = [], []
    for name in models:
        mean = counted["by_tokenizer"][name]["mean"]
        if not math.isfinite(mean):
            raise ValueError("non-finite tokenizer mean for %s" % name)
        means.append(mean)
        member_rows.append({"model": name, "value": mean})

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
    return {
        "kind": "ainglish.token-measurement-result.v1",
        "state": "computed_not_submitted",
        "payload": payload,
        "audit": {
            "manifest_commitment": plan["manifest_commitment"],
            "items_sha256": plan["items_sha256"],
            "pair_count": len(rows),
            "by_tokenizer": [
                {"model": row["model"], "mean": row["value"]}
                for row in member_rows
            ],
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
