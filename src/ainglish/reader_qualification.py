"""Build exact, expiry-bound reader qualification receipts for measurement manifests.

Qualification is a positive-control statement about one exact roster id and settings digest. It
does not establish task accuracy, model-family independence, training data, or provider stability.
The register recomputes ``passed`` from integer counts and basis-point thresholds.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re


KIND = "ainglish.reader-qualification.v1"
MANIFEST_KEY = "reader_qualifications"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MODEL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError("%s must contain 1-%d characters" % (field, maximum))
    return value.strip()


def _digest(value, field):
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ValueError("%s must be a lowercase SHA-256 digest" % field)
    return value


def _instant(value, field):
    if not isinstance(value, str):
        raise ValueError("%s must be an ISO-8601 datetime" % field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("%s must be an ISO-8601 datetime" % field) from exc
    if parsed.tzinfo is None:
        raise ValueError("%s must include a timezone" % field)
    return parsed.astimezone(timezone.utc)


def _derived(result):
    dc, dt = result["detectable_correct"], result["detectable_total"]
    oc, ot = result["other_correct"], result["other_total"]
    gap_numerator = dc * ot - oc * dt
    gap_denominator = dt * ot
    headroom_numerator = ot - oc
    gap_pass = gap_numerator * 10000 >= result["min_gap_bps"] * gap_denominator
    recovered_pass = (
        headroom_numerator > 0
        and gap_numerator * 10000
        >= result["min_recovered_bps"] * dt * headroom_numerator
    )
    return {
        "detectable": dc / dt,
        "other": oc / ot,
        "gap": gap_numerator / gap_denominator,
        "headroom": headroom_numerator / ot,
        "recovered": (
            gap_numerator / (dt * headroom_numerator)
            if headroom_numerator > 0 else None
        ),
        "passed": gap_pass and recovered_pass,
    }


def receipt(
    *, roster_id, provider, model, precision, lineage_key, lineage_basis,
    screen_sha256, settings_sha256, qualified_at, valid_until,
    detectable_correct, detectable_total, other_correct, other_total,
    min_gap_bps=1250, min_recovered_bps=5000,
    model_digest=None, digest_source=None, screen_url=None,
):
    """Build and validate one qualification receipt.

    Counts are integers and thresholds are basis points, so Python and PHP make the same pass
    decision without hashing or comparing non-portable floating-point bytes. Validity may not
    exceed 90 days; a fresh configuration or expired receipt needs a new frozen screen run.
    """
    result = {
        "detectable_correct": detectable_correct,
        "detectable_total": detectable_total,
        "other_correct": other_correct,
        "other_total": other_total,
        "min_gap_bps": min_gap_bps,
        "min_recovered_bps": min_recovered_bps,
    }
    for field, value in result.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("%s must be a non-negative integer" % field)
    if (
        detectable_total < 1 or other_total < 1
        or detectable_correct > detectable_total or other_correct > other_total
        or min_gap_bps > 10000 or min_recovered_bps > 10000
    ):
        raise ValueError("qualification result has impossible counts or thresholds")
    result["passed"] = _derived(result)["passed"]

    start = _instant(qualified_at, "qualified_at")
    end = _instant(valid_until, "valid_until")
    if end <= start or (end - start).total_seconds() > 90 * 86400:
        raise ValueError("valid_until must be after qualified_at and no more than 90 days later")
    reader = {
        "provider": _text(provider, "provider", 160),
        "model": _text(model, "model", 160),
        "precision": _text(precision, "precision", 160),
    }
    if model_digest is not None:
        if not isinstance(model_digest, str) or _MODEL_DIGEST.fullmatch(model_digest) is None:
            raise ValueError("model_digest must be sha256: followed by 64 lowercase hex characters")
        reader["model_digest"] = model_digest
    if digest_source is not None:
        reader["digest_source"] = _text(digest_source, "digest_source", 500)
    out = {
        "kind": KIND,
        "roster_id": _text(roster_id, "roster_id", 120),
        "reader": reader,
        "lineage": {
            "key": _text(lineage_key, "lineage_key", 160),
            "basis": _text(lineage_basis, "lineage_basis", 1000),
        },
        "screen_sha256": _digest(screen_sha256, "screen_sha256"),
        "settings_sha256": _digest(settings_sha256, "settings_sha256"),
        "qualified_at": qualified_at,
        "valid_until": valid_until,
        "result": result,
    }
    if screen_url is not None:
        screen_url = _text(screen_url, "screen_url", 2000)
        if not screen_url.lower().startswith("https://"):
            raise ValueError("screen_url must be an https URL")
        out["screen_url"] = screen_url
    return out


def validate(value):
    """Return a normalized copy or raise on a malformed or false receipt."""
    if not isinstance(value, dict):
        raise ValueError("reader qualification must be an object")
    expected = {
        "kind", "roster_id", "reader", "lineage", "screen_sha256", "settings_sha256",
        "qualified_at", "valid_until", "result",
    }
    unknown = set(value) - expected - {"screen_url"}
    missing = expected - set(value)
    if missing or unknown:
        raise ValueError("reader qualification has missing or unknown fields")
    # Rebuild through receipt(), then require the claimed pass to equal the exact calculation.
    reader, lineage, result = value["reader"], value["lineage"], value["result"]
    if not isinstance(reader, dict) or not isinstance(lineage, dict) or not isinstance(result, dict):
        raise ValueError("reader, lineage and result must be objects")
    if set(reader) - {"provider", "model", "precision", "model_digest", "digest_source"} \
            or not {"provider", "model", "precision"}.issubset(reader):
        raise ValueError("reader has missing or unknown fields")
    if set(lineage) != {"key", "basis"}:
        raise ValueError("lineage must contain exactly key and basis")
    if set(result) != {
        "detectable_correct", "detectable_total", "other_correct", "other_total",
        "min_gap_bps", "min_recovered_bps", "passed",
    }:
        raise ValueError("result has missing or unknown fields")
    claimed = result.get("passed")
    if not isinstance(claimed, bool):
        raise ValueError("result.passed must be boolean")
    if value.get("kind") != KIND:
        raise ValueError("reader qualification kind is not supported")
    rebuilt = receipt(
        roster_id=value["roster_id"], provider=reader.get("provider"), model=reader.get("model"),
        precision=reader.get("precision"), lineage_key=lineage.get("key"),
        lineage_basis=lineage.get("basis"), screen_sha256=value["screen_sha256"],
        settings_sha256=value["settings_sha256"], qualified_at=value["qualified_at"],
        valid_until=value["valid_until"], detectable_correct=result.get("detectable_correct"),
        detectable_total=result.get("detectable_total"), other_correct=result.get("other_correct"),
        other_total=result.get("other_total"), min_gap_bps=result.get("min_gap_bps"),
        min_recovered_bps=result.get("min_recovered_bps"), model_digest=reader.get("model_digest"),
        digest_source=reader.get("digest_source"), screen_url=value.get("screen_url"),
    )
    if claimed != rebuilt["result"]["passed"]:
        raise ValueError("result.passed disagrees with exact counts and thresholds")
    return rebuilt


def attach(manifest, receipts):
    """Return a detached manifest carrying one strict receipt per declared roster member."""
    if not isinstance(manifest, dict) or not manifest:
        raise ValueError("manifest must be a non-empty object")
    if MANIFEST_KEY in manifest:
        raise ValueError("manifest already carries reader_qualifications")
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 16:
        raise ValueError("receipts must be a list of 1-16 qualification receipts")
    normalized = [validate(item) for item in receipts]
    roster = [item["roster_id"] for item in normalized]
    if len(roster) != len(set(roster)):
        raise ValueError("qualification roster_id values must be unique")
    models = manifest.get("models") or manifest.get("panel_models")
    if isinstance(models, list) and any(name not in models for name in roster):
        raise ValueError("every qualification roster_id must name a declared manifest model")
    out = deepcopy(manifest)
    out[MANIFEST_KEY] = normalized
    return out


def selftest():
    good = receipt(
        roster_id="remote/model@fp16", provider="remote", model="model", precision="fp16",
        lineage_key="model-family", lineage_basis="provider model card and served id",
        screen_sha256="a" * 64, settings_sha256="b" * 64,
        qualified_at="2026-09-01T10:00:00+00:00", valid_until="2026-09-30T10:00:00+00:00",
        detectable_correct=8, detectable_total=8, other_correct=2, other_total=8,
    )
    assert good["result"]["passed"] is True
    assert attach({"metric": "comprehension_accuracy_delta", "models": ["remote/model@fp16"]}, [good])[MANIFEST_KEY] == [good]
    bad = deepcopy(good)
    bad["result"]["detectable_correct"] = 3
    try:
        validate(bad)
        raise AssertionError("unsupported claimed pass must refuse")
    except ValueError as exc:
        assert "disagrees" in str(exc)
    return {"kind": "ainglish.reader-qualification-selftest.v1", "status": "ok", "checks": 3}


if __name__ == "__main__":
    import json
    import sys
    if sys.argv[1:] != ["--selftest"]:
        raise SystemExit("usage: python -m ainglish.reader_qualification --selftest")
    print(json.dumps(selftest(), sort_keys=True))
