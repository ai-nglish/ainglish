"""Local, report-only checks on prospective panel items; never calls a reader or API."""

import argparse
from collections import Counter, defaultdict
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import re

from . import panel


MAX_FILE_BYTES = 20 * 1024 * 1024


def _visible_arm_conflicts(items):
    """Compare what ONE arm exposes, not the hidden counterpart or option order.

    This is a review warning: deliberately ambiguous baselines can be legitimate,
    but a paired score then need not estimate meaning-matched comprehension.
    Controls and scientific targets are separate populations.
    """
    findings = []
    for phase in (False, True):
        for arm in ("english", "ainglish"):
            groups, answers = defaultdict(list), defaultdict(set)
            for item in items:
                if bool(item.get("calibration", False)) != phase:
                    continue
                body = [item[arm], item["question"], sorted(option.strip().casefold() for option in item["options"])]
                digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                groups[digest].append(item["id"])
                answers[digest].add(str(item["answer"]).strip().casefold())
            for digest in groups:
                if len(answers[digest]) > 1:
                    findings.append({"arm": arm, "phase": "calibration" if phase else "target",
                                     "sha256": digest, "ids": groups[digest][:20],
                                     "ids_truncated": len(groups[digest]) > 20})
    return {"count": len(findings), "shown": findings[:20], "truncated": len(findings) > 20}


def _answer_copy_controls(items):
    # Explicit answer announcements only; ordinary mention of an option is not
    # evidence of leakage. Even these can be legitimate transport-only controls.
    findings = []
    for item in items:
        if not item.get("calibration", False):
            continue
        for arm in ("english", "ainglish"):
            answer = re.escape(str(item["answer"]).strip())
            pattern = r"\b(?:(?:correct\s+)?answer\s*(?:is\s*|[:=]\s*)|(?:select|return|output)\s+exactly\s+)[\"']?" + answer + r"(?=$|[\s.,;!?'\"])"
            if re.search(pattern, item[arm], re.IGNORECASE):
                findings.append({"id": item["id"], "arm": arm})
    return {"count": len(findings), "shown": findings[:20], "truncated": len(findings) > 20}


def _fingerprint(item, option_order=True):
    body = {key: item[key] for key in ("english", "ainglish", "question", "options")}
    if not option_order:
        body["options"] = sorted(body["options"])
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _groups(groups):
    """Bounded diagnostics: never echo the source utterances or full item objects."""
    repeated = [(digest, ids) for digest, ids in groups.items() if len(ids) > 1]
    return {"count": len(repeated), "shown": [
        {"sha256": digest, "count": len(ids), "ids": ids[:20], "ids_truncated": len(ids) > 20}
        for digest, ids in repeated[:20]], "groups_truncated": len(repeated) > 20}


def _inspect(items, label):
    output = {"rows": len(items) if isinstance(items, list) else None, "errors": [], "warnings": []}
    diagnostic = io.StringIO()
    with redirect_stdout(diagnostic):
        valid = panel._validate_item_block(items, label)
    if not valid:
        output["errors"].append({"code": "invalid_panel_items", "detail": diagnostic.getvalue().strip()})
        return output, set()
    if not items:
        output["errors"].append({"code": "empty_item_set"})
        return output, set()
    ids = [item["id"] for item in items]
    if any(not isinstance(value, str) or not value.strip() for value in ids):
        output["errors"].append({"code": "invalid_item_id", "detail": "Use non-empty string item identifiers."})
        return output, set()
    duplicate_ids = [key for key, count in Counter(ids).items() if count > 1]
    if duplicate_ids:
        output["errors"].append({"code": "duplicate_ids", "ids": duplicate_ids[:20], "count": len(duplicate_ids)})
    if any("calibration" in item and not isinstance(item["calibration"], bool) for item in items):
        output["errors"].append({"code": "non_boolean_calibration_flag"})
    blank = [item["id"] for item in items if any(not item[key].strip() for key in ("english", "ainglish", "question"))]
    if blank:
        output["errors"].append({"code": "blank_arm_or_question", "ids": blank[:20], "count": len(blank)})
    real = [item for item in items if not item.get("calibration", False)]
    controls = [item for item in items if item.get("calibration", False)]
    inert = panel._same_arm_calibration_ids(controls)
    if inert:
        output["errors"].append({"code": "identical_control_arms", "ids": inert[:20], "count": len(inert)})
    if not real:
        output["errors"].append({"code": "no_real_items"})
    conflicts_by_arm = _visible_arm_conflicts(items)
    copy_controls = _answer_copy_controls(items)
    if conflicts_by_arm["count"]:
        output["warnings"].append({"code": "conflicting_gold_for_visible_arm", "count": conflicts_by_arm["count"]})
    if copy_controls["count"]:
        output["warnings"].append({"code": "explicit_answer_copy_control", "count": copy_controls["count"]})
    output["visible_arm_conflicts"] = conflicts_by_arm
    output["answer_copy_controls"] = copy_controls
    groups, meanings = defaultdict(list), defaultdict(set)
    for item in real:
        fingerprint = _fingerprint(item)
        groups[fingerprint].append(item["id"])
        meanings[fingerprint].add(str(item["answer"]).strip().casefold())
    duplicates = _groups(groups)
    if duplicates["count"]:
        output["errors"].append({"code": "repeated_complete_cases", "groups": duplicates["count"]})
    conflicts = [key for key, answers in meanings.items() if len(answers) > 1]
    if conflicts:
        output["errors"].append({"code": "conflicting_gold_for_same_input", "sha256": conflicts[:20], "count": len(conflicts)})
    # These are DECLARED option positions, not the SDK's opaque served choice codes.
    positions = Counter()
    answers = Counter()
    option_sets = set()
    for item in real:
        normalized = [option.strip().casefold() for option in item["options"]]
        answer = str(item["answer"]).strip().casefold()
        positions[normalized.index(answer)] += 1
        answers[answer] += 1
        option_sets.add(tuple(normalized))
    widths = sorted({len(item["options"]) for item in real})
    balanced = None
    if len(widths) == 1:
        counts = [positions[index] for index in range(widths[0])]
        balanced = max(counts) - min(counts) <= 1
        if not balanced:
            output["warnings"].append({"code": "declared_answer_positions_imbalanced", "counts": counts})
    elif widths:
        output["warnings"].append({"code": "mixed_option_widths", "widths": widths})
    strata = Counter(str(item.get("settlement_stratum", "undeclared")) for item in real)
    output.update({
        "real_rows": len(real), "control_rows": len(controls), "distinct_complete_cases": len(groups),
        "duplicate_cases": duplicates, "declared_option_position_counts": dict(sorted(positions.items())),
        "declared_positions_balanced": balanced, "declared_answer_counts": dict(answers),
        "declared_majority_answer_baseline": max(answers.values()) / len(real) if real else None,
        "uniform_random_option_baseline": sum(1 / len(item["options"]) for item in real) / len(real) if real else None,
        "one_common_ordered_option_set": len(option_sets) == 1,
        "settlement_stratum_counts": dict(strata),
        "declared_boundary_rows": sum(item.get("boundary_case") is True for item in real),
    })
    return output, {_fingerprint(item, option_order=False) for item in real}


def audit_items(items, training_items=None, require_balanced=False):
    """Audit canonical panel rows without changing them or changing any register gate.

    `ok` reports structural checks plus the caller's optional balance policy. It is
    NOT semantic validation, independence, qualification, preregistration or admission.
    Exact repeats remain visible as errors; planned repetition may be justified in
    a research design but must not be represented as additional distinct cases.
    """
    evaluation, evaluation_keys = _inspect(items, "items")
    report = {"kind": "ainglish.experiment-input-audit.v1", "report_only": True,
              "reader_calls": 0, "api_calls": 0, "source_text_included": False,
              "semantic_equivalence": "not_checked", "evaluation": evaluation,
              "training": None, "exact_train_evaluation_overlap": None,
              "balance_required": bool(require_balanced),
              "interpretation": "Declared answer geometry, not actual opaque choice-code balance. Distinct literal cases are not proof of independent semantic frames."}
    if training_items is not None:
        training, training_keys = _inspect(training_items, "training_items")
        report["training"] = training
        overlap = sorted(evaluation_keys & training_keys)
        report["exact_train_evaluation_overlap"] = {"count": len(overlap), "sha256": overlap[:20], "truncated": len(overlap) > 20}
        if overlap:
            evaluation["errors"].append({"code": "train_evaluation_overlap", "count": len(overlap)})
    blocks = [evaluation] + ([report["training"]] if report["training"] is not None else [])
    if require_balanced:
        for block in blocks:
            if block.get("declared_positions_balanced") is not True:
                block["errors"].append({"code": "required_declared_balance_not_met"})
    report["ok"] = all(not block["errors"] for block in blocks)
    return report


def audit_token_pairs(pairs):
    """Heuristic source audit, not token counting or semantic equivalence validation.

    Accept the same simple complete-pair carriers used by the token runner. A
    length ratio alone never diagnoses a bad comparator. Flag only the narrower
    paragraph-versus-placeholder-heading shape, and keep it report-only.
    """
    report = {"kind": "ainglish.token-input-audit.v1", "report_only": True,
              "reader_calls": 0, "api_calls": 0, "tokenizer_calls": 0,
              "source_text_included": False, "semantic_equivalence": "not_checked",
              "rows": len(pairs) if isinstance(pairs, list) else None,
              "errors": [], "warnings": [], "ok": True}
    if not isinstance(pairs, list) or not pairs:
        report["errors"].append({"code": "nonempty_pair_list_required"})
    else:
        suspect, invalid = [], []
        for index, pair in enumerate(pairs):
            if isinstance(pair, dict):
                english, marked = pair.get("english", pair.get("baseline")), pair.get("ainglish")
                conflict = "english" in pair and "baseline" in pair and pair["english"] != pair["baseline"]
            elif isinstance(pair, list) and len(pair) == 2:
                english, marked = pair
                conflict = False
            else:
                english, marked, conflict = None, None, False
            if conflict or not all(isinstance(text, str) and text.strip() for text in (english, marked)):
                invalid.append(index + 1)
                continue
            placeholder = re.search(r"<[A-Za-z][A-Za-z0-9_ -]*>", marked)
            if placeholder and len(english) >= 240 and len(marked) <= 120 and len(english) >= 4 * len(marked):
                suspect.append(index + 1)
        if invalid:
            report["errors"].append({"code": "invalid_complete_pairs", "positions": invalid[:20], "count": len(invalid)})
        if suspect:
            report["warnings"].append({"code": "paragraph_vs_placeholder_heading", "positions": suspect[:20], "count": len(suspect),
                "interpretation": "Inspect whether both arms instantiate the same message. A documentation-length study may be intentional; this warning is not a semantic verdict."})
    report["ok"] = not report["errors"]
    return report


def _read(path):
    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Input exceeds the 20 MiB local audit limit")
    if str(path).endswith(".jsonl"):
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    return json.loads(raw)


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("items", help="Local JSON list or JSONL of canonical panel items; never a URL")
    parser.add_argument("--training", help="Optional local training item file to check for literal leakage")
    parser.add_argument("--require-balanced", action="store_true", help="Treat declared answer-position imbalance as a local audit error")
    parser.add_argument("--token-pairs", action="store_true", help="Inspect a local complete token-pair list without loading tokenizers")
    args = parser.parse_args(argv)
    if args.token_pairs and (args.training or args.require_balanced):
        parser.error("--token-pairs cannot be combined with panel training or answer-balance options")
    try:
        report = audit_token_pairs(_read(args.items)) if args.token_pairs else audit_items(
            _read(args.items), _read(args.training) if args.training else None, args.require_balanced)
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"kind": "ainglish.experiment-input-audit.error", "error_type": type(error).__name__,
                          "message": "Cannot read valid bounded local item data; no reader or API call occurred."}))
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(cli())
