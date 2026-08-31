"""Small, report-only estimand declarations for measurement manifests.

This module does not change Ainglish settlement.  It gives experimenters one
strict shape for declaring the quantity a design estimates while the register
evaluates whether a governed comparability rule would be worth its complexity.

Input realisation and instrument identity deliberately stay in their existing
manifest fields (``test_set``, ``models``, tokenizer provenance, and so on).
They may change in a useful replication without changing the estimand.
"""

from __future__ import annotations

from copy import deepcopy
import json
import sys


KIND = "ainglish.estimand-shadow.v1"
COMPARISON_KIND = "ainglish.estimand-shadow-comparison.v1"
MANIFEST_KEY = "estimand_contract"
REDUCERS = frozenset({
    "mean",
    "median",
    "minimum",
    "maximum",
    "least_favourable",
    "sum",
    "rate",
    "custom",
})
CORE_FIELDS = ("unit_span", "contrast", "population", "aggregation")
MAX_TEXT = 1000


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be a non-empty string" % field)
    value = value.strip()
    if len(value) > MAX_TEXT:
        raise ValueError("%s must be at most %d characters" % (field, MAX_TEXT))
    return value


def declaration(*, unit_span, contrast, population, reducer, aggregation_rule):
    """Build one strict shadow declaration.

    The vocabulary of ``unit_span``, ``contrast`` and ``population`` remains
    open during shadow use; prematurely closing those taxonomies would merely
    move ambiguity into a misleading enum.  Reducer names are closed, and the
    exact rule is always required so two agents cannot both say ``mean`` while
    applying different weighting or least-favourable logic.
    """
    reducer = _text(reducer, "reducer")
    if reducer not in REDUCERS:
        raise ValueError(
            "reducer must be one of %s" % ", ".join(sorted(REDUCERS))
        )
    result = {
        "kind": KIND,
        "unit_span": _text(unit_span, "unit_span"),
        "contrast": _text(contrast, "contrast"),
        "population": _text(population, "population"),
        "aggregation": {
            "reducer": reducer,
            "rule": _text(aggregation_rule, "aggregation_rule"),
        },
        "governance_effect": "report_only",
    }
    validate(result)
    return result


def validate(value):
    """Return a normalized copy or raise ``ValueError`` on a malformed claim."""
    if not isinstance(value, dict):
        raise ValueError("estimand declaration must be an object")
    expected = {"kind", *CORE_FIELDS, "governance_effect"}
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if missing:
        raise ValueError("estimand declaration is missing: %s" % ", ".join(missing))
    if unknown:
        raise ValueError("estimand declaration has unknown fields: %s" % ", ".join(unknown))
    if value["kind"] != KIND:
        raise ValueError("estimand declaration kind must be %s" % KIND)
    if value["governance_effect"] != "report_only":
        raise ValueError("estimand declaration governance_effect must be report_only")
    normalized = {
        "kind": KIND,
        "unit_span": _text(value["unit_span"], "unit_span"),
        "contrast": _text(value["contrast"], "contrast"),
        "population": _text(value["population"], "population"),
    }
    aggregation = value["aggregation"]
    if not isinstance(aggregation, dict):
        raise ValueError("aggregation must be an object")
    if set(aggregation) != {"reducer", "rule"}:
        raise ValueError("aggregation must contain exactly reducer and rule")
    reducer = _text(aggregation["reducer"], "aggregation.reducer")
    if reducer not in REDUCERS:
        raise ValueError(
            "aggregation.reducer must be one of %s" % ", ".join(sorted(REDUCERS))
        )
    normalized["aggregation"] = {
        "reducer": reducer,
        "rule": _text(aggregation["rule"], "aggregation.rule"),
    }
    normalized["governance_effect"] = "report_only"
    return normalized


def inspect_manifest(manifest):
    """Describe declaration presence without turning absence into a verdict."""
    if not isinstance(manifest, dict):
        return {
            "kind": KIND,
            "status": "malformed_manifest",
            "error": "manifest must be an object",
            "governance_effect": "report_only",
        }
    if MANIFEST_KEY not in manifest:
        return {
            "kind": KIND,
            "status": "undeclared",
            "declaration": None,
            "governance_effect": "report_only",
        }
    try:
        normalized = validate(manifest[MANIFEST_KEY])
    except ValueError as exc:
        return {
            "kind": KIND,
            "status": "malformed_declaration",
            "error": str(exc),
            "governance_effect": "report_only",
        }
    return {
        "kind": KIND,
        "status": "declared",
        "declaration": normalized,
        "governance_effect": "report_only",
    }


def attach(manifest, value):
    """Return a detached manifest carrying ``value``; never overwrite silently."""
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("manifest must be a non-empty object")
    if MANIFEST_KEY in manifest:
        raise ValueError("manifest already carries estimand_contract")
    result = deepcopy(manifest)
    result[MANIFEST_KEY] = validate(value)
    return result


def compare(left, right):
    """Compare two valid declarations, report-only.

    ``same_declared_estimand`` means only that these four declarations match.
    It is not a settlement verdict: item disjointness, instrument scope,
    implementation, and admissibility remain separate evidence.
    """
    left = validate(left)
    right = validate(right)
    differences = [field for field in CORE_FIELDS if left[field] != right[field]]
    return {
        "kind": COMPARISON_KIND,
        "status": "declared_difference" if differences else "same_declared_estimand",
        "different_dimensions": differences,
        "governance_effect": "report_only",
        "does_not_establish": (
            "A matching declaration does not establish same inputs, adequate disjointness, "
            "instrument equivalence, statistical agreement, or settlement eligibility."
        ),
    }


def selftest():
    base = declaration(
        unit_span="complete message",
        contrast="Ainglish form versus the proposal's careful-English mapping",
        population="fresh balanced task messages drawn from the declared generator frame",
        reducer="least_favourable",
        aggregation_rule="per-tokenizer item mean, then maximum across tokenizer lineages",
    )
    assert validate(base) == base
    assert inspect_manifest({})["status"] == "undeclared"
    assert inspect_manifest({MANIFEST_KEY: {}})["status"] == "malformed_declaration"

    raw_first = {
        "metric": "token_delta",
        "models": ["cl100k_base"],
        "test_set": [1, 2],
    }
    first = attach(raw_first, base)
    second = attach(
        {"metric": "token_delta", "models": ["o200k_base"], "test_set": [3, 4]},
        base,
    )
    assert MANIFEST_KEY not in raw_first
    same = compare(first[MANIFEST_KEY], second[MANIFEST_KEY])
    assert same["status"] == "same_declared_estimand"
    assert same["different_dimensions"] == []

    changed = deepcopy(base)
    changed["aggregation"] = {"reducer": "mean", "rule": "mean across every item and lineage"}
    different = compare(base, changed)
    assert different["status"] == "declared_difference"
    assert different["different_dimensions"] == ["aggregation"]

    try:
        attach(first, base)
        raise AssertionError("an overwrite must refuse")
    except ValueError as exc:
        assert "already carries" in str(exc)
    try:
        declaration(
            unit_span="message", contrast="A versus B", population="frame",
            reducer="whatever", aggregation_rule="unspecified",
        )
        raise AssertionError("an unknown reducer must refuse")
    except ValueError as exc:
        assert "reducer must be one of" in str(exc)

    return {
        "kind": "ainglish.estimand-shadow-selftest.v1",
        "status": "ok",
        "checks": 10,
    }


if __name__ == "__main__":
    if sys.argv[1:] != ["--selftest"]:
        raise SystemExit("usage: python -m ainglish.estimand --selftest")
    print(json.dumps(selftest(), sort_keys=True))
