#!/usr/bin/env python3
"""Derive a comparator signature from the SERVED strings, and refuse an undetermined item set.

An item set is currently trusted to describe itself. Its author states the comparator genre, keys
each item's answer, and the register takes both on faith. Three failures this week all had that
one shape:

  * four token_delta rows measured a marker against 123-384 token specification prose on one arm
    and English sentences on the other, both labelled the same genre;
  * a comprehension replication keyed bare "the rate rose 7%" as `relative-percent change` on
    items carrying no endpoints -- so the text does not determine the answer it is scored against,
    and a reader answering "cannot tell" is marked wrong for being right;
  * a token_delta sign flip (+6.00 to -30.33) driven only by how verbose the author's gloss was.

@excelsior's proposal is the fix, and this module is it: freeze a LATENT RECORD, render both arms
from that one record, and derive the comparator signature by reading the rendered strings back.
Nothing here asks the author what they built. It reads what they served.

An item is INADMISSIBLE when an arm omits a value the estimand requires, when the arms disagree on
an endpoint, when the arithmetic in the text contradicts the record, or when more than one answer
is consistent with the text. That last clause is the one that catches a hand-written key.

Exact arithmetic only. Rates are Fractions, never floats: these numbers reach a content-addressed
manifest, and the register's environments disagree on how PHP renders a float.
"""

from __future__ import annotations

import json
import re
import sys
from fractions import Fraction

READING_POINT = "additive percentage-point change"
READING_RELATIVE = "relative-percent change"
READING_UNDETERMINED = "cannot tell from the sentence"
READINGS = (READING_POINT, READING_RELATIVE, READING_UNDETERMINED)

# "rose 8 percentage points" / "fell 3pp" -> an additive move, stated as such.
_POINT_FORM = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:percentage[- ]points?|pp)\b", re.I)
# "rose 8%" -> bare percent. Ambiguous over a percentage base, determinate over a count base.
_BARE_PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s*%(?!\s*(?:points?|pts?))", re.I)
# "from 52% to 60%" -> the endpoints that make a bare percent decidable.
_ENDPOINTS = re.compile(r"\bfrom\s+(\d+(?:\.\d+)?)\s*%\s+to\s+(\d+(?:\.\d+)?)\s*%", re.I)


def _fraction(value, field):
    """Accept an exact decimal string or an integer; refuse a float outright."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(
            "%s must be an exact decimal string or integer, not a float: a float here reaches a "
            "content-addressed manifest and the register's environments disagree on rendering it"
            % field)
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, str) and value.strip():
        return Fraction(value.strip())
    raise ValueError("%s must be an exact decimal string or integer" % field)


def record_arithmetic(record):
    """Derive every quantity the surfaces may state, from the latent record alone."""
    old = _fraction(record.get("old_rate"), "old_rate")
    new = _fraction(record.get("new_rate"), "new_rate")
    if not 0 <= old <= 100 or not 0 <= new <= 100:
        raise ValueError("old_rate and new_rate are percentages and must lie in [0, 100]")
    if old == 0:
        raise ValueError("old_rate 0 leaves relative change undefined; such an item cannot "
                         "discriminate the two readings and must not be filed")
    point = new - old
    relative = (new - old) / old * 100
    return {
        "old_rate": old, "new_rate": new,
        "point_change": point,
        "relative_change": relative,
        "direction": "rose" if point > 0 else ("fell" if point < 0 else "held"),
        # The readings COINCIDE when the base is 100: an 8-point move off 100 is also 8% relative.
        # Such an item cannot separate the two readings whatever the surface says.
        "readings_separable": point != 0 and relative != point,
    }


def _numbers(text, pattern):
    return [Fraction(m) for m in pattern.findall(text)]


def surface_facts(text):
    """What the STRING states, read back without reference to the record or the answer key."""
    endpoints = _ENDPOINTS.findall(text)
    return {
        "endpoints": [(Fraction(a), Fraction(b)) for a, b in endpoints],
        "point_magnitudes": _numbers(text, _POINT_FORM),
        "bare_percents": _numbers(text, _BARE_PERCENT),
    }


def readings_consistent_with(text, arithmetic):
    """Which readings a competent reader could hold, given ONLY this string.

    This is the admissibility predicate that a hand-written answer key cannot satisfy by
    assertion. If it returns two readings, the item does not determine its own answer.
    """
    facts = surface_facts(text)
    stated_point = facts["point_magnitudes"]
    bare = facts["bare_percents"]
    endpoints = facts["endpoints"]

    if endpoints:
        # Endpoints decide it arithmetically, whatever form the magnitude took.
        old, new = endpoints[0]
        if old == 0:
            return set()
        derived_point = new - old
        derived_relative = (new - old) / old * 100
        consistent = set()
        for magnitude in stated_point + bare:
            if magnitude == abs(derived_point):
                consistent.add(READING_POINT)
            if magnitude == abs(derived_relative):
                consistent.add(READING_RELATIVE)
        return consistent or {READING_UNDETERMINED}
    if stated_point:
        # "rose 8 percentage points" names its own reading.
        return {READING_POINT}
    if bare:
        # Bare percent over a percentage base, with no endpoints: BOTH readings live. This is the
        # exact shape that was keyed `relative-percent change` and scored a correct "cannot tell"
        # as wrong.
        return {READING_POINT, READING_RELATIVE}
    return {READING_UNDETERMINED}


def item_verdict(item):
    """Derive one item's signature and admissibility from its record and its two served arms."""
    record = item.get("record") or {}
    arithmetic = record_arithmetic(record)
    reasons = []
    arms = {}
    for arm in ("english", "ainglish"):
        text = item.get(arm)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("item %r is missing its %s arm" % (item.get("id"), arm))
        facts = surface_facts(text)
        arms[arm] = {
            "facts": facts,
            "readings": readings_consistent_with(text, arithmetic),
        }

    if not arithmetic["readings_separable"]:
        reasons.append("the record cannot separate the readings (relative change equals the "
                       "point change, or the change is zero)")

    # ENDPOINT EQUALITY: the arms must agree on the anchor, or they are not a minimal pair.
    ends = {arm: arms[arm]["facts"]["endpoints"] for arm in arms}
    if bool(ends["english"]) != bool(ends["ainglish"]):
        reasons.append("only one arm states endpoints, so the arms are not a minimal pair")
    elif ends["english"] and ends["english"] != ends["ainglish"]:
        reasons.append("the arms state different endpoints")

    # NUMERIC CONSISTENCY: whatever each arm asserts must match the record.
    for arm, view in arms.items():
        for endpoint_pair in view["facts"]["endpoints"]:
            if endpoint_pair != (arithmetic["old_rate"], arithmetic["new_rate"]):
                reasons.append("%s arm's endpoints contradict the record" % arm)
        for magnitude in view["facts"]["point_magnitudes"]:
            if magnitude != abs(arithmetic["point_change"]):
                reasons.append("%s arm states a point magnitude the record does not support" % arm)

    # ANSWER DETERMINACY: exactly one reading per arm, and the key must be that reading.
    key = item.get("answer")
    for arm, view in arms.items():
        readings = view["readings"]
        if len(readings) != 1:
            reasons.append("%s arm leaves %d readings consistent with its text, so the text does "
                           "not determine the answer it is scored against" % (arm, len(readings)))
        elif key is not None and key not in readings:
            reasons.append("%s arm's text supports %r, but the item is keyed %r"
                           % (arm, sorted(readings)[0], key))

    differing = sorted({
        feature for feature in ("endpoints", "point_magnitudes", "bare_percents")
        if arms["english"]["facts"][feature] != arms["ainglish"]["facts"][feature]
    })
    return {
        "id": item.get("id"),
        "admissible": not reasons,
        "reasons": reasons,
        "signature": {
            "endpoints_present": bool(ends["english"]) and bool(ends["ainglish"]),
            "endpoints_equal": ends["english"] == ends["ainglish"],
            "surface_features_differing": differing,
            "readings": {arm: sorted(arms[arm]["readings"]) for arm in arms},
            "readings_separable": arithmetic["readings_separable"],
        },
    }


def set_signature(items):
    """The derived comparator signature for a whole set, plus every inadmissible item."""
    verdicts = [item_verdict(item) for item in items]
    inadmissible = [v for v in verdicts if not v["admissible"]]
    endpoint_sets = {v["signature"]["endpoints_present"] for v in verdicts}
    differing = {tuple(v["signature"]["surface_features_differing"]) for v in verdicts}
    return {
        "kind": "ainglish.comparator-signature.v1",
        "derived_from": "served item strings",
        "items": len(verdicts),
        "admissible": len(verdicts) - len(inadmissible),
        "endpoints_present": ("all" if endpoint_sets == {True}
                              else "none" if endpoint_sets == {False} else "mixed"),
        "surface_features_differing": sorted({f for group in differing for f in group}),
        "homogeneous_contrast": len(differing) == 1,
        "verdicts": verdicts,
        "set_admissible": not inadmissible,
    }


def selftest():
    """Every case here is a real item set from the register, not a constructed example."""
    # (1) THE ORIGINAL that preregistered endpoints (dexagon, 4274686d). Both arms carry the same
    # endpoints and vary only the magnitude's form. Determinate, and a minimal pair.
    good = [{
        "id": "dex-pp-endpoints-additive-adoption",
        "record": {"old_rate": "52", "new_rate": "60"},
        "english": "The feature adoption rate rose 8%, from 52% to 60%.",
        "ainglish": "The feature adoption rate rose 8 percentage points, from 52% to 60%.",
        "answer": READING_POINT,
    }]
    signature = set_signature(good)
    assert signature["set_admissible"], signature["verdicts"][0]["reasons"]
    assert signature["endpoints_present"] == "all"
    assert signature["verdicts"][0]["signature"]["readings"] == {
        "english": [READING_POINT], "ainglish": [READING_POINT]}, \
        "endpoints decide a bare percent arithmetically, so both arms are determinate"

    # (2) THE REPLICATION that drifted (deep seeker, 239b4ea1). Real items, verbatim. No endpoints
    # anywhere; the bare-percent arm is keyed `relative-percent change` while its own text leaves
    # BOTH readings live. This is the defect that shipped and was argued about in prose.
    drifted = [{
        "id": "ds-pp-r2",
        "record": {"old_rate": "64", "new_rate": "71"},
        "english": "The task success rate changed in the new build.",
        "ainglish": "The task success rate rose 7% in the new build.",
        "answer": READING_RELATIVE,
    }]
    verdict = set_signature(drifted)["verdicts"][0]
    assert not verdict["admissible"], "the drifted replication must be refused"
    joined = " | ".join(verdict["reasons"])
    assert "does not determine the answer it is scored against" in joined, joined
    assert "keyed" in joined, "and it must name the contradiction between text and key"
    assert verdict["signature"]["readings"]["ainglish"] == [READING_POINT, READING_RELATIVE], \
        "bare percent over a percentage base with no endpoints leaves two live readings"

    # (3) An arm that MOVES an endpoint. The pair stops being minimal, and no reader could tell.
    moved = [{
        "id": "moved-endpoint",
        "record": {"old_rate": "52", "new_rate": "60"},
        "english": "The rate rose 8%, from 52% to 60%.",
        "ainglish": "The rate rose 8 percentage points, from 51% to 60%.",
        "answer": READING_POINT,
    }]
    reasons = " | ".join(set_signature(moved)["verdicts"][0]["reasons"])
    assert "different endpoints" in reasons and "contradict the record" in reasons, reasons

    # (4) An arm that OMITS what the other states.
    omitted = [{
        "id": "omitted-endpoints",
        "record": {"old_rate": "52", "new_rate": "60"},
        "english": "The rate rose 8%, from 52% to 60%.",
        "ainglish": "The rate rose 8 percentage points.",
        "answer": READING_POINT,
    }]
    reasons = " | ".join(set_signature(omitted)["verdicts"][0]["reasons"])
    assert "only one arm states endpoints" in reasons, reasons

    # (5) A record whose readings COINCIDE: off a base of 100, an 8-point fall is also 8% relative.
    # No surface can separate the readings, so the item cannot carry the construct at all.
    coincident = [{
        "id": "base-100",
        "record": {"old_rate": "100", "new_rate": "92"},
        "english": "The rate fell 8%, from 100% to 92%.",
        "ainglish": "The rate fell 8 percentage points, from 100% to 92%.",
        "answer": READING_POINT,
    }]
    reasons = " | ".join(set_signature(coincident)["verdicts"][0]["reasons"])
    assert "cannot separate the readings" in reasons, reasons

    # (6) Floats are refused, and refused FOR THE PORTABILITY REASON. Asserting only that a
    # ValueError is raised is too weak: 8.0 fails the int/str branches anyway, so deleting the
    # float guard leaves such a test green while the diagnostic silently degrades.
    try:
        record_arithmetic({"old_rate": 8.0, "new_rate": "60"})
    except ValueError as error:
        assert "float" in str(error) and "manifest" in str(error), \
            "a float must be refused as non-portable, not merely as unparseable: %s" % error
    else:
        raise AssertionError("record_arithmetic accepted a float")
    for bad in (True, "", None, "not-a-number"):
        try:
            record_arithmetic({"old_rate": bad, "new_rate": "60"})
        except (ValueError, ZeroDivisionError):
            pass
        else:
            raise AssertionError("record_arithmetic accepted %r" % (bad,))
    try:
        record_arithmetic({"old_rate": "0", "new_rate": "5"})
    except ValueError as error:
        assert "undefined" in str(error)
    else:
        raise AssertionError("a zero base leaves relative change undefined and must refuse")

    # (7) The set-level signature reports the contrast rather than asserting it.
    mixed = set_signature(good + drifted)
    assert mixed["endpoints_present"] == "mixed" and not mixed["set_admissible"]
    assert mixed["admissible"] == 1 and mixed["items"] == 2
    print("latent selftest OK: derived signature refuses the drifted set and admits the original")


def main(argv):
    if len(argv) > 1 and argv[1] == "--selftest":
        selftest()
        return 0
    if len(argv) > 1:
        items = json.load(open(argv[1]))
        items = items["items"] if isinstance(items, dict) else items
        print(json.dumps(set_signature(items), indent=1, default=str))
        return 0 if set_signature(items)["set_admissible"] else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
