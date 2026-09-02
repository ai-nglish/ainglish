"""Build exact, expiry-bound reader qualification receipts for measurement manifests.

Qualification is a positive-control statement about one exact roster id and settings digest. It
does not establish task accuracy, model-family independence, training data, or provider stability.
The register recomputes ``passed`` from integer counts and basis-point thresholds.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re


KIND = "ainglish.reader-qualification.v1"
MANIFEST_KEY = "reader_qualifications"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MODEL_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SCREEN_KIND = "ainglish.reader-qualification-screen.v1"
RUN_KIND = "ainglish.reader-qualification-run.v1"


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
    models = manifest.get("models")
    if not isinstance(models, list):
        raise ValueError(
            "reader qualifications require manifest.models to list every qualified roster_id"
        )
    if any(name not in models for name in roster):
        raise ValueError("every qualification roster_id must name a declared manifest model")
    out = deepcopy(manifest)
    out[MANIFEST_KEY] = normalized
    return out


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_screen(value):
    """Validate a target-independent positive-control screen without making reader calls."""
    if not isinstance(value, dict) or value.get("kind") != SCREEN_KIND:
        raise ValueError("screen must be an %s object" % SCREEN_KIND)
    expected = {
        "kind", "roster_id", "reader", "lineage", "controls", "validity_days",
        "min_gap_bps", "min_recovered_bps",
    }
    unknown = set(value) - expected - {"screen_url"}
    missing = expected - set(value)
    if unknown or missing:
        raise ValueError("screen has missing or unknown fields")
    reader = value["reader"]
    if not isinstance(reader, dict) or not isinstance(reader.get("name"), str):
        raise ValueError("reader must be one panel reader object with a non-empty name")
    for field in ("name", "provider", "model", "precision"):
        _text(reader.get(field), "reader.%s" % field, 160)
    roster = reader["name"] + ("@" + reader["precision"] if reader.get("precision") else "")
    if _text(value["roster_id"], "roster_id", 120) != roster:
        raise ValueError("roster_id must equal reader name@precision exactly (%s)" % roster)
    lineage = value["lineage"]
    if not isinstance(lineage, dict) or set(lineage) != {"key", "basis"}:
        raise ValueError("lineage must contain exactly key and basis")
    _text(lineage.get("key"), "lineage.key", 160)
    _text(lineage.get("basis"), "lineage.basis", 1000)
    days = value["validity_days"]
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 90:
        raise ValueError("validity_days must be an integer from 1 through 90")
    for field in ("min_gap_bps", "min_recovered_bps"):
        threshold = value[field]
        if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 10000:
            raise ValueError("%s must be an integer from 0 through 10000" % field)
    if value.get("screen_url") is not None:
        url = _text(value["screen_url"], "screen_url", 2000)
        if not url.lower().startswith("https://"):
            raise ValueError("screen_url must be an https URL")
    controls = value["controls"]
    if not isinstance(controls, list) or not 4 <= len(controls) <= 100:
        raise ValueError("controls must contain 4-100 target-independent positive controls")
    seen = set()
    for index, control in enumerate(controls):
        if not isinstance(control, dict) or set(control) != {
                "id", "detectable", "other", "question", "options", "answer"}:
            raise ValueError("controls[%d] has missing or unknown fields" % index)
        control_id = _text(control["id"], "controls[%d].id" % index, 160)
        if control_id in seen:
            raise ValueError("control ids must be unique")
        seen.add(control_id)
        detectable = _text(control["detectable"], "controls[%d].detectable" % index, 4000)
        other = _text(control["other"], "controls[%d].other" % index, 4000)
        if detectable == other:
            raise ValueError("each control must have distinct detectable and other texts")
        _text(control["question"], "controls[%d].question" % index, 1000)
        options = control["options"]
        if not isinstance(options, list) or not 2 <= len(options) <= 8 \
                or any(not isinstance(option, str) or not option.strip() for option in options) \
                or len(set(options)) != len(options):
            raise ValueError("each control needs 2-8 unique non-empty string options")
        if control["answer"] not in options:
            raise ValueError("each control answer must exactly equal one option")
    return deepcopy(value)


def run_screen(value, *, ask_fn=None, prepare_fn=None, now=None):
    """Run one frozen screen once and return its cells plus manifest-ready receipt.

    ``ask_fn`` and ``prepare_fn`` exist for hermetic tests. Production defaults route through the
    same bound reader adapters as ``ainglish-panel`` and perform no automatic retries.
    """
    from . import panel

    spec = validate_screen(value)
    ask_fn = ask_fn or panel.ask
    prepare_fn = prepare_fn or panel.prepare_reader_instruments
    prepared = prepare_fn({"panel": [deepcopy(spec["reader"])]})
    reader = prepared["panel"][0]
    instrument = panel.reader_receipt(reader)
    screen_bytes = _canonical({
        "kind": SCREEN_KIND,
        "controls": spec["controls"],
        "ordering": "control order; detectable then other; one call per cell; no retry",
    }).encode("utf-8")
    settings_bytes = _canonical(instrument).encode("utf-8")
    counts = {"detectable_correct": 0, "detectable_total": 0,
              "other_correct": 0, "other_total": 0}
    observations = []
    for control in spec["controls"]:
        for cell in ("detectable", "other"):
            answer = ask_fn(reader, control[cell], control["question"], control["options"])
            normalized = str(answer).strip()
            correct = normalized == control["answer"]
            counts[cell + "_total"] += 1
            counts[cell + "_correct"] += int(correct)
            observations.append({
                "control_id": control["id"], "cell": cell,
                "answer": normalized, "expected": control["answer"], "correct": correct,
            })
    qualified_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    valid_until = qualified_at + timedelta(days=spec["validity_days"])
    reader_data = instrument
    qualification = receipt(
        roster_id=spec["roster_id"],
        provider=reader_data.get("provider", reader.get("provider")),
        model=reader_data.get("model", reader.get("model")),
        precision=reader_data.get("precision", reader.get("precision")),
        lineage_key=spec["lineage"]["key"], lineage_basis=spec["lineage"]["basis"],
        screen_sha256=hashlib.sha256(screen_bytes).hexdigest(),
        settings_sha256=hashlib.sha256(settings_bytes).hexdigest(),
        qualified_at=qualified_at.isoformat(), valid_until=valid_until.isoformat(),
        min_gap_bps=spec["min_gap_bps"], min_recovered_bps=spec["min_recovered_bps"],
        model_digest=reader_data.get("model_digest"),
        digest_source=reader_data.get("digest_source"), screen_url=spec.get("screen_url"),
        **counts,
    )
    return {
        "kind": RUN_KIND,
        "status": "passed" if qualification["result"]["passed"] else "failed",
        "receipt": qualification,
        "observations": observations,
        "instrument": instrument,
        "screen": {
            "kind": SCREEN_KIND,
            "sha256": hashlib.sha256(screen_bytes).hexdigest(),
            "controls": len(spec["controls"]),
            "ordering": "detectable then other; one call per cell; no retry",
        },
        "truth_boundary": "Qualification tests this exact reader/settings combination on target-independent controls. It is not target evidence, model-family independence, or a promise of future provider stability.",
    }


def cli(argv=None):
    parser = argparse.ArgumentParser(prog="ainglish-qualify-reader")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate a screen without inference")
    check.add_argument("screen")
    run = sub.add_parser("run", help="run every frozen control cell once")
    run.add_argument("screen")
    run.add_argument("-o", "--output", required=True)
    args = parser.parse_args(argv)
    try:
        value = json.loads(Path(args.screen).read_text(encoding="utf-8"))
        checked = validate_screen(value)
        if args.command == "check":
            result = {"kind": "ainglish.reader-qualification-check.v1", "status": "ok",
                      "roster_id": checked["roster_id"], "controls": len(checked["controls"]),
                      "reader_calls": 0}
            print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
            return 0
        result = run_screen(checked)
        output = Path(args.output)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                          encoding="utf-8")
        print(json.dumps({"status": result["status"], "output": str(output),
                          "roster_id": result["receipt"]["roster_id"]}, sort_keys=True))
        return 0 if result["status"] == "passed" else 2
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))



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
    try:
        attach({"metric": "comprehension_accuracy_delta"}, [good])
        raise AssertionError("qualification without manifest.models must refuse")
    except ValueError as exc:
        assert "manifest.models" in str(exc)
    bad = deepcopy(good)
    bad["result"]["detectable_correct"] = 3
    try:
        validate(bad)
        raise AssertionError("unsupported claimed pass must refuse")
    except ValueError as exc:
        assert "disagrees" in str(exc)
    screen = {
        "kind": SCREEN_KIND,
        "roster_id": "remote-reader@provider-served",
        "reader": {"name": "remote-reader", "provider": "openai-compatible",
                   "model": "model", "precision": "provider-served",
                   "base_url": "https://example.invalid/v1", "api_key_env": ""},
        "lineage": {"key": "model-family", "basis": "published provider model family"},
        "validity_days": 30, "min_gap_bps": 1250, "min_recovered_bps": 5000,
        "controls": [{"id": "c%d" % i, "detectable": "clear %d" % i,
                      "other": "ambiguous %d" % i, "question": "Which?",
                      "options": ["yes", "unknown"], "answer": "yes"} for i in range(4)],
    }
    def fake_prepare(manifest):
        manifest["panel"][0]["_ainglish_instrument_preparation"] = {
            "entry_point": "selftest", "binding": "provider-opaque"}
        manifest["panel"][0]["model_digest"] = None
        manifest["panel"][0]["digest_source"] = "provider-opaque"
        return manifest
    outcome = run_screen(
        screen,
        ask_fn=lambda _reader, text, _question, _options: "yes" if text.startswith("clear") else "unknown",
        prepare_fn=fake_prepare,
        now=datetime(2026, 9, 2, tzinfo=timezone.utc),
    )
    assert outcome["status"] == "passed"
    assert outcome["receipt"]["result"]["detectable_correct"] == 4
    assert outcome["receipt"]["result"]["other_correct"] == 0
    return {"kind": "ainglish.reader-qualification-selftest.v1", "status": "ok", "checks": 8}


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        print(json.dumps(selftest(), sort_keys=True))
    else:
        raise SystemExit(cli())
