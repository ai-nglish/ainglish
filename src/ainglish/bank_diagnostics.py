"""Offline, report-only diagnostics of the question/options shown to a reader.

Predictors never receive messages, metadata, row IDs or keys. Keys score their
predictions afterward. A selected best rule is exploratory, not a statistical
test or evidence that a model used it. No input is changed and no gate is set.
"""
from collections import Counter, defaultdict


def predict(options, anchor, offset):
    """One fixed, within-item rule; None means an absent or tied anchor.

Offsets wrap cyclically for anchors, not for absolute positions. Length counts
Unicode code points of the exact displayed string; lexical order is UTF-8 bytes.
"""
    if anchor == "position":
        return offset if offset < len(options) else None
    values = ([len(s) for s in options] if anchor in ("longest", "shortest")
              else [s.encode("utf-8") for s in options])
    if anchor not in ("longest", "shortest", "byte_first", "byte_last"):
        raise ValueError("Unknown diagnostic anchor")
    target = (max(values) if anchor in ("longest", "byte_last") else min(values))
    found = [i for i, value in enumerate(values) if value == target]
    return (found[0] + offset) % len(options) if len(found) == 1 else None


def _row(item):
    if not isinstance(item, dict) or type(item.get("calibration", False)) is not bool:
        return None
    options, answer, question = item.get("options"), item.get("answer"), item.get("question")
    if not isinstance(options, list) or not 2 <= len(options) <= 26 or any(
            not isinstance(s, str) or not s.strip() for s in options):
        return None
    if not isinstance(question, str) or not question.strip() or not isinstance(answer, str):
        return None
    folded = [s.strip().casefold() for s in options]
    if len(set(folded)) != len(folded) or answer.strip().casefold() not in folded:
        return None
    return question, options, folded.index(answer.strip().casefold())


def _phase(items):
    rows = [row for item in items if (row := _row(item)) is not None]
    n = len(rows)
    rules, warnings = [], []
    if n:
        for anchor in ("position", "longest", "shortest", "byte_first", "byte_last"):
            for offset in range(max(len(options) for _, options, _ in rows)):
                hits = abstentions = 0
                for _, options, gold in rows:
                    prediction = predict(options, anchor, offset)
                    abstentions += prediction is None
                    hits += prediction == gold
                rules.append({"anchor": anchor, "offset": offset, "hits": hits,
                              "denominator": n, "abstentions": abstentions})
        for rule in rules:
            if rule["hits"] * 10 >= 9 * n:
                warnings.append({"code": "high_message_blind_rule_score", **rule})
    positions = {}
    for _, options, gold in rows:
        counts = positions.setdefault(str(len(options)), [0] * len(options))
        counts[gold] += 1
    fits = {}
    for ordered in (True, False):
        groups = defaultdict(Counter)
        for question, options, gold in rows:
            key = (question, tuple(options if ordered else sorted(options)))
            groups[key][options[gold]] += 1
        fits["ordered_options" if ordered else "unordered_options"] = {
            "groups": len(groups), "singleton_groups": sum(sum(c.values()) == 1 for c in groups.values()),
            "conflicting_gold_groups": sum(len(c) > 1 for c in groups.values()),
            "in_sample_majority_hits": sum(max(c.values()) for c in groups.values()),
            "denominator": n,
        }
    return {"rows": len(items), "scorable_rows": n, "invalid_rows": len(items) - n,
            "status": "not_evaluable" if not n else "partial" if len(items) != n else "evaluated",
            "small_sample": n < 20, "rules_tested": len(rules), "rules": rules,
            "gold_position_counts_by_option_count": positions, "warnings": warnings,
            "posthoc_lookup_fits": fits}


def audit(items):
    """Separate targets and calibration controls; never silently discard bad rows.

The <=26-option scope matches the standard panel. No heuristic aliases for
calibration or gold are inferred. Reports contain counts and rule names, never
source texts, keys or identifiers. Invalid flags have an explicit denominator.
"""
    report = {"kind": "ainglish.bank-diagnostics.v1", "report_only": True,
              "reader_calls": 0, "api_calls": 0, "tokenizer_calls": 0,
              "eligibility": "not_assessed", "source_text_included": False,
              "status": "not_evaluable", "unclassified_rows": 0,
              "targets": None, "calibration": None,
              "interpretation": (
                  "Finite exploratory rules using one item's displayed options only. Keys are used only "
                  "to score predictions. >=90% is a review warning, not a significance test; many rules "
                  "and small samples can produce high scores by chance. Ties abstain and remain in the "
                  "denominator. Best-rule selection is post hoc and does not prove model use. The lookup "
                  "fits are trained and scored on this bank, not executable held-out predictors or "
                  "universal message-blind bounds. No warning is not clearance. Neither these diagnostics "
                  "nor shuffling certify semantics, qualification, independence or admissibility.")}
    if not isinstance(items, list):
        return report
    phases = {False: [], True: []}
    for item in items:
        if not isinstance(item, dict) or type(item.get("calibration", False)) is not bool:
            report["unclassified_rows"] += 1
        else:
            phases[item.get("calibration", False)].append(item)
    report.update(targets=_phase(phases[False]), calibration=_phase(phases[True]))
    total = report["targets"]["scorable_rows"] + report["calibration"]["scorable_rows"]
    report["status"] = "not_evaluable" if not total else "partial" if total != len(items) else "evaluated"
    return report
