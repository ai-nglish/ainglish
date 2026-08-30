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

import hashlib
import json
import re
import sys
import textwrap
from fractions import Fraction

READING_POINT = "additive percentage-point change"
READING_RELATIVE = "relative-percent change"
READING_UNDETERMINED = "cannot tell from the sentence"
READINGS = (READING_POINT, READING_RELATIVE, READING_UNDETERMINED)

# "rose 8 percentage points" / "fell 3pp" -> an additive move, stated as such.
_POINT_FORM = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:percentage[- ]points?|pp)\b", re.I)
# "rose 8%" -> bare percent. Ambiguous over a percentage base, determinate over a count base.
_BARE_PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s*%(?!\s*(?:points?|pts?))", re.I)
# "from 52% to 60%" -> the endpoints that make a bare percent decidable. Their spans are MASKED
# before magnitudes are read: an endpoint is an anchor, never a change magnitude, and conflating
# the two let "rose 3%, from 20% to 40%" be rescued by its own endpoint 20 matching the record's
# 20-point move (@dexagon-ai, #123).
_ENDPOINTS = re.compile(r"\bfrom\s+(\d+(?:\.\d+)?)\s*%\s+to\s+(\d+(?:\.\d+)?)\s*%", re.I)
_DIRECTIONS = (
    ("rose", ("rose", "rise", "rises", "increased", "increases", "grew", "grows", "climbed", "up by")),
    ("fell", ("fell", "fall", "falls", "decreased", "decreases", "dropped", "drops", "declined", "down by")),
    ("held", ("held", "holds", "unchanged", "stayed", "flat")),
)


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
    """What the STRING states, read back without reference to the record or the answer key.

    Endpoint spans are removed before magnitudes are read. An endpoint anchors the change; it is
    not a candidate for the change's size, and treating it as one lets a wrong magnitude be
    rescued by an unrelated coincidence with the record.
    """
    endpoints = [(Fraction(a), Fraction(b)) for a, b in _ENDPOINTS.findall(text)]
    magnitude_text = _ENDPOINTS.sub(" ", text)
    lowered = text.lower()
    directions = sorted({
        name for name, words in _DIRECTIONS if any(word in lowered for word in words)
    })
    return {
        "endpoints": endpoints,
        "point_magnitudes": _numbers(magnitude_text, _POINT_FORM),
        "bare_percents": _numbers(magnitude_text, _BARE_PERCENT),
        "directions": directions,
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

    # MAGNITUDE OMISSION: a magnitude in one arm and none in the other is not a minimal pair.
    stated = {arm: view["facts"]["point_magnitudes"] + view["facts"]["bare_percents"]
              for arm, view in arms.items()}
    if bool(stated["english"]) != bool(stated["ainglish"]):
        reasons.append("only one arm states a change magnitude, so the arms are not a minimal pair")

    # NUMERIC CONSISTENCY: whatever each arm asserts must match the record. Endpoints, the point
    # magnitude, AND the bare magnitude -- the last of which went unchecked, so an arm could state
    # any percentage at all as long as some OTHER number in the sentence happened to match.
    supported = {abs(arithmetic["point_change"]), abs(arithmetic["relative_change"])}
    for arm, view in arms.items():
        for endpoint_pair in view["facts"]["endpoints"]:
            if endpoint_pair != (arithmetic["old_rate"], arithmetic["new_rate"]):
                reasons.append("%s arm's endpoints contradict the record" % arm)
        for magnitude in view["facts"]["point_magnitudes"]:
            if magnitude != abs(arithmetic["point_change"]):
                reasons.append("%s arm states a point magnitude the record does not support" % arm)
        for magnitude in view["facts"]["bare_percents"]:
            if magnitude not in supported:
                reasons.append("%s arm states %s%%, which is neither the record's point change "
                               "nor its relative change" % (arm, magnitude))

    # DIRECTION: derived from the record and read back from BOTH strings. A pair that says "fell"
    # while the record rises contradicts it in both arms, and no reader could detect that.
    for arm, view in arms.items():
        directions = view["facts"]["directions"]
        if len(directions) > 1:
            reasons.append("%s arm states more than one direction (%s)"
                           % (arm, ", ".join(directions)))
        elif directions and directions[0] != arithmetic["direction"]:
            reasons.append("%s arm says %r while the record %s"
                           % (arm, directions[0], arithmetic["direction"]))
        elif not directions:
            reasons.append("%s arm states no direction, so its change is not readable" % arm)

    # ANSWER DETERMINACY: exactly one reading per arm, and the key is DERIVED, never trusted.
    # A supplied key is checked against the derivation rather than believed; an item with no key
    # is not thereby admissible, because determinacy is still required of both arms.
    key = item.get("answer")
    derived_key = None
    for arm, view in arms.items():
        readings = view["readings"]
        if len(readings) != 1:
            reasons.append("%s arm leaves %d readings consistent with its text, so the text does "
                           "not determine the answer it is scored against" % (arm, len(readings)))
            continue
        only = sorted(readings)[0]
        if arm == "ainglish":
            derived_key = only
        if key is not None and only != key:
            reasons.append("%s arm's text supports %r, but the item is keyed %r"
                           % (arm, only, key))

    differing = sorted({
        feature for feature in ("endpoints", "point_magnitudes", "bare_percents")
        if arms["english"]["facts"][feature] != arms["ainglish"]["facts"][feature]
    })
    # BYTE-IDENTICAL ARMS carry no contrast at all: there is nothing for the comparator to hold
    # fixed and nothing for it to vary, so such a pair cannot evidence a surface difference.
    if item.get("english") == item.get("ainglish"):
        reasons.append("the two arms are byte-identical, so the item carries no contrast")

    return {
        "id": item.get("id"),
        "admissible": not reasons,
        "reasons": reasons,
        "derived_answer": derived_key,
        "supplied_answer": key,
        "signature": {
            "endpoints_present": bool(ends["english"]) and bool(ends["ainglish"]),
            "endpoints_equal": ends["english"] == ends["ainglish"],
            "surface_features_differing": differing,
            "readings": {arm: sorted(arms[arm]["readings"]) for arm in arms},
            "readings_separable": arithmetic["readings_separable"],
        },
    }


_PREDICATE_FUNCTIONS = ("record_arithmetic", "surface_facts", "readings_consistent_with",
                       "item_verdict")


def predicate_digest():
    """A digest of the admissibility PREDICATE ITSELF, so a receipt is bound to its rule.

    @dantic's requirement on the thread: a version string is not enough if the implementation can
    change while the string stays `v1`. A frozen record re-keyed under a revised predicate would
    then be indistinguishable from one keyed under the original. Hashing the parsed source of the
    functions that decide admissibility makes any behavioural revision change every receipt it
    produces, automatically and without anyone remembering to bump a number.

    The AST is hashed rather than the text, so reformatting and comments do not churn the digest
    while a changed comparison or threshold does.
    """
    import ast as _ast
    import inspect as _inspect
    module = sys.modules[__name__]
    dumps = []
    for name in _PREDICATE_FUNCTIONS:
        source = _inspect.getsource(getattr(module, name))
        tree = _ast.parse(textwrap.dedent(source))
        dumps.append(_ast.dump(tree, annotate_fields=True, include_attributes=False))
    return hashlib.sha256("\n".join(dumps).encode("utf-8")).hexdigest()


def set_signature(items):
    """The derived comparator signature for a whole set, plus every inadmissible item."""
    verdicts = [item_verdict(item) for item in items]
    inadmissible = [v for v in verdicts if not v["admissible"]]
    endpoint_sets = {v["signature"]["endpoints_present"] for v in verdicts}
    differing = {tuple(v["signature"]["surface_features_differing"]) for v in verdicts}
    features = sorted({f for group in differing for f in group})
    return {
        "kind": "ainglish.comparator-signature.v1",
        "predicate_sha256": predicate_digest(),
        "derived_from": "served item strings",
        "items": len(verdicts),
        "admissible": len(verdicts) - len(inadmissible),
        "endpoints_present": ("all" if endpoint_sets == {True}
                              else "none" if endpoint_sets == {False} else "mixed"),
        "surface_features_differing": features,
        # A contrast is homogeneous when every item varies the SAME feature and at least one
        # feature varies at all. One shape shared by every item, where that shape is "nothing
        # differs", is not a homogeneous contrast; it is no contrast.
        "homogeneous_contrast": len(differing) == 1 and bool(features),
        "verdicts": verdicts,
        # An empty set determines nothing, so it cannot be admissible. Vacuous truth here would
        # have let a set that failed to load report itself as clean.
        "set_admissible": bool(verdicts) and not inadmissible,
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

    # (7) @dexagon-ai's #123 counterexamples. Each of these was ADMITTED by the first version.
    # An endpoint value was being collected as a change magnitude, so a wrong "3%" was rescued by
    # the unrelated coincidence of the endpoint 20 matching the record's 20-point move.
    collision = [{
        "id": "endpoint-collision",
        "record": {"old_rate": "20", "new_rate": "40"},
        "english": "The rate rose 3%, from 20% to 40%.",
        "ainglish": "The rate rose 20 percentage points, from 20% to 40%.",
        "answer": READING_POINT,
    }]
    verdict = set_signature(collision)["verdicts"][0]
    assert not verdict["admissible"], "an endpoint must never be read as a change magnitude"
    assert any("neither the record's point change" in r for r in verdict["reasons"]), \
        verdict["reasons"]
    assert surface_facts("The rate rose 3%, from 20% to 40%.")["bare_percents"] == [Fraction(3)], \
        "endpoint spans must be masked before magnitudes are read"

    # Direction was derived from the record and never read back from either string, so a pair
    # could contradict the record in BOTH arms and pass.
    reversed_pair = [{
        "id": "direction-reversed",
        "record": {"old_rate": "52", "new_rate": "60"},
        "english": "The rate fell 8%, from 52% to 60%.",
        "ainglish": "The rate fell 8 percentage points, from 52% to 60%.",
        "answer": READING_POINT,
    }]
    reasons = " | ".join(set_signature(reversed_pair)["verdicts"][0]["reasons"])
    assert "says 'fell' while the record rose" in reasons, reasons

    # Set-level fail-open edges: an empty set determined nothing and reported itself clean;
    # byte-identical arms claimed a homogeneous CONTRAST while nothing differed.
    assert set_signature([])["set_admissible"] is False, "an empty set determines nothing"
    identical = [{
        "id": "identical-arms",
        "record": {"old_rate": "52", "new_rate": "60"},
        "english": "The rate rose 8 percentage points, from 52% to 60%.",
        "ainglish": "The rate rose 8 percentage points, from 52% to 60%.",
        "answer": READING_POINT,
    }]
    flat = set_signature(identical)
    assert not flat["set_admissible"] and not flat["homogeneous_contrast"], \
        "identical arms carry no contrast and must not be labelled a homogeneous one"

    # An item with NO supplied key is not admissible by omission: determinacy is still required,
    # and the key is derived and emitted so the set can be scored from the derivation.
    keyless = dict(good[0])
    keyless.pop("answer")
    derived = set_signature([keyless])["verdicts"][0]
    assert derived["admissible"] and derived["derived_answer"] == READING_POINT, derived
    assert derived["supplied_answer"] is None
    undetermined = dict(drifted[0])
    undetermined.pop("answer")
    assert not set_signature([undetermined])["verdicts"][0]["admissible"], \
        "dropping the key must not rescue an item whose text determines nothing"

    # The receipt is bound to the predicate that produced it, not to a version string anyone can
    # keep while changing the rule underneath it (@dantic).
    digest = predicate_digest()
    assert len(digest) == 64 and set_signature(good)["predicate_sha256"] == digest
    assert digest == predicate_digest(), "the digest must be stable across calls"
    # …and it must actually be DERIVED from the predicate source. A hardcoded constant would be
    # stable and 64 characters too, and would satisfy every assertion above while binding nothing.
    global _PREDICATE_FUNCTIONS
    original = _PREDICATE_FUNCTIONS
    try:
        _PREDICATE_FUNCTIONS = ("record_arithmetic",)
        assert predicate_digest() != digest, \
            "the digest must change with the predicate it covers, or it binds nothing"
    finally:
        _PREDICATE_FUNCTIONS = original
    assert predicate_digest() == digest, "and must return to the original once restored"

    # (8) The set-level signature reports the contrast rather than asserting it.
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


def cli():
    """Console entry point: `ainglish-latent items.json`. Exit 1 when the set is inadmissible."""
    raise SystemExit(main(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
