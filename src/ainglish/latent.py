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


def _direction_pattern(words):
    """Boundary-aware matcher for one direction's vocabulary.

    Plain substring containment was a FAIL-OPEN parser, not merely a noisy one: `"rose" in "prose"`
    made `The prose metric changed 8%, from 52% to 60%.` report a direction its text never states,
    and `"held" in "withheld"` manufactured a second one (@dexagon-ai, #123). An invented direction
    does not just cause a false refusal — it lets a pair whose arms state NO direction satisfy the
    direction check and be admitted.

    Multiword entries ("up by") are matched as phrases with flexible internal whitespace, so the
    boundary lands at the ends of the phrase rather than inside it.
    """
    alternatives = "|".join(
        r"\s+".join(re.escape(part) for part in word.split())
        for word in words
    )
    return re.compile(r"\b(?:%s)\b" % alternatives, re.I)


_DIRECTION_PATTERNS = tuple((name, _direction_pattern(words)) for name, words in _DIRECTIONS)


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
    directions = sorted({
        name for name, pattern in _DIRECTION_PATTERNS if pattern.search(text) is not None
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
    singletons = {}
    for arm, view in arms.items():
        readings = view["readings"]
        if len(readings) != 1:
            reasons.append("%s arm leaves %d readings consistent with its text, so the text does "
                           "not determine the answer it is scored against" % (arm, len(readings)))
            continue
        only = sorted(readings)[0]
        singletons[arm] = only
        if key is not None and only != key:
            reasons.append("%s arm's text supports %r, but the item is keyed %r"
                           % (arm, only, key))

    # BOTH ARMS MUST DERIVE THE SAME READING. Each arm being individually determinate is not
    # enough: a minimal pair asks ONE question, and two arms that determine DIFFERENT answers are
    # two questions wearing one id.
    #
    # Without this the module inverted its own purpose (@dexagon-ai, #123). Reproduced on the
    # previous head: record 50 -> 60, english "rose 10 percentage points", ainglish "rose 20%",
    # no `answer`. Each arm is a clean singleton, so the loop above raised nothing and the item was
    # ADMITTED with derived_answer "relative-percent change" — while the same pair WITH a hand key
    # was correctly refused by the mismatch check. Omitting the key rescued a contradiction, which
    # is exactly the move deriving the key instead of trusting it is supposed to make impossible.
    if len(singletons) == 2:
        if singletons["english"] != singletons["ainglish"]:
            reasons.append("the arms derive different answers (english supports %r, ainglish "
                           "supports %r), so the pair is not one question over a minimal contrast"
                           % (singletons["english"], singletons["ainglish"]))
        else:
            # Only an agreed reading may be published as the derived key: reporting one arm's
            # answer while the other contradicts it would carry the contradiction downstream.
            derived_key = singletons["ainglish"]

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


# THE BEHAVIOURAL CLOSURE the receipt commits to. Getting this list wrong is the whole failure
# mode: the first version named four functions and omitted `set_signature` itself, both helpers,
# every regex, the direction lexicon and the reading constants. @dexagon-ai demonstrated the
# consequence on #123 -- replacing `_ENDPOINTS` with a never-matching pattern flipped a verdict
# from admissible to inadmissible while the digest stayed byte-identical. A digest that misses the
# thing it certifies is worse than none, because it advertises a guarantee it does not hold.
_PREDICATE_FUNCTIONS = (
    "_fraction", "_numbers", "_direction_pattern", "record_arithmetic", "surface_facts",
    "readings_consistent_with", "item_verdict", "set_signature",
)
# Pinned so that (a) a predicate change forces a deliberate acknowledgement rather than a silent
# re-key, and (b) CI running the selftest on 3.9 and 3.12 proves the digest is interpreter-
# independent instead of asserting it. Update it only when the predicate genuinely changed.
PREDICATE_SHA256 = "48d59ca6fbb6dc7f8df1ea104aebf210152a25f6a3ee3ae519940bf0e5488eb7"
_PREDICATE_PATTERNS = ("_POINT_FORM", "_BARE_PERCENT", "_ENDPOINTS")
_PREDICATE_CONSTANTS = ("READING_POINT", "READING_RELATIVE", "READING_UNDETERMINED",
                        "READINGS", "_DIRECTIONS")


def _inspect_source(name):
    """The source of one closure member, for tests that need to inspect what is hashed."""
    import inspect as _i
    return textwrap.dedent(_i.getsource(getattr(sys.modules[__name__], name)))


def predicate_digest():
    """A digest of the admissibility PREDICATE ITSELF, so a receipt is bound to its rule.

    @dantic's requirement: a version string binds nothing if the implementation can change while
    the string stays `v1`. A frozen record re-keyed under a revised predicate would then be
    indistinguishable from one keyed under the original.

    Two properties this has to have, and the first version had neither:

    COMPLETE. Everything the verdict depends on is hashed -- every function in the closure
    including `set_signature`, both helpers, each regex's live pattern AND flags, the direction
    lexicon, and the reading constants. Hashing the LIVE regex objects rather than only their
    source also catches substitution at runtime, which is how the gap was demonstrated.

    INTERPRETER-INDEPENDENT. Function SOURCE TEXT is hashed, not an AST dump. `ast.dump()` is not
    a documented cross-version canonical form, and this package supports 3.9 through 3.12; a
    digest that differed by interpreter would make two honest agents produce different receipts
    for the same rule. Source text is file bytes and cannot vary that way. The selftest pins the
    expected digest, so CI running both interpreters proves the parity mechanically rather than
    asserting it.

    The cost is that a comment edit inside a hashed function changes the digest. That is the right
    direction to be wrong in: a conservative digest raises a false alarm, an incomplete one grants
    a false assurance. The earlier version chose convenience and missed a regex.
    """
    import inspect as _inspect
    module = sys.modules[__name__]
    parts = ["ainglish.comparator-signature.v1"]
    for name in _PREDICATE_FUNCTIONS:
        parts.append("def:" + name)
        parts.append(textwrap.dedent(_inspect.getsource(getattr(module, name))))
    for name in _PREDICATE_PATTERNS:
        pattern = getattr(module, name)
        parts.append("re:%s:%s:%d" % (name, pattern.pattern, int(pattern.flags)))
    for name in _PREDICATE_CONSTANTS:
        parts.append("const:%s:%s" % (
            name, json.dumps(getattr(module, name), sort_keys=True, default=str)))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


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
        # RECORDED, deliberately not hashed. @dexagon-ai's #123 review offered two ways to be
        # safe across interpreters: be independent of them, or bind and expose them. The digest
        # takes the first route -- it hashes source text, not an AST -- and PREDICATE_SHA256 plus
        # CI on 3.9 and 3.12 is the proof. This field takes the second route for PROVENANCE only:
        # if a cross-version discrepancy is ever found, every receipt already says which
        # interpreter produced it. Hashing it instead would make two honest agents on different
        # Pythons produce different receipts for the same rule, which is the failure the field
        # exists to detect.
        "predicate_python": "%d.%d" % sys.version_info[:2],
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

    # (5) KEYLESS ARMS THAT DERIVE OPPOSITE ANSWERS. @dexagon-ai's exact counterexample (#123):
    # each arm is individually determinate, so every per-arm check passes, and the previous head
    # ADMITTED the pair with derived_answer "relative-percent change". The same pair WITH a hand key
    # was refused -- so dropping the key rescued a contradiction, inverting the whole point of
    # deriving the key rather than trusting it.
    keyless_contradiction = {
        "id": "keyless-opposite-readings",
        "record": {"old_rate": "50", "new_rate": "60"},
        "english": "The rate rose 10 percentage points, from 50% to 60%.",
        "ainglish": "The rate rose 20%, from 50% to 60%.",
    }
    verdict = item_verdict(keyless_contradiction)
    assert not verdict["admissible"], \
        "two arms deriving different answers are two questions, and omitting the key cannot hide it"
    assert verdict["derived_answer"] is None, \
        "no agreed reading exists, so none may be published as the derived key"
    assert any("derive different answers" in reason for reason in verdict["reasons"]), verdict
    # With the key supplied the pair must still refuse: the two routes agree on the outcome.
    assert not item_verdict(dict(keyless_contradiction, answer="point"))["admissible"]
    # And an agreeing keyless pair is still admissible, so this is not a blanket refusal.
    agreeing = dict(good[0])
    agreeing.pop("answer", None)
    assert item_verdict(agreeing)["admissible"], \
        "a keyless pair whose arms agree must remain admissible"

    # (6) DIRECTION MATCHING IS BOUNDARY-AWARE. Substring containment was FAIL-OPEN: it invented a
    # direction from an unrelated word, which let a pair stating no direction satisfy the direction
    # check and be admitted (@dexagon-ai, #123).
    assert surface_facts("The rate rose 8%, from 52% to 60%.")["directions"] == ["rose"]
    assert surface_facts("The prose metric changed 8%, from 52% to 60%.")["directions"] == [], \
        "'rose' must not match inside 'prose'"
    assert surface_facts("The rate held at 8%, from 52% to 52%.")["directions"] == ["held"]
    assert surface_facts("The value withheld 8%, from 52% to 60%.")["directions"] == [], \
        "'held' must not match inside 'withheld'"
    # Multiword entries stay matchable as phrases, with flexible internal whitespace.
    assert surface_facts("The rate went up by 8 percentage points, from 52% to 60%.")["directions"] == ["rose"]
    assert surface_facts("The rate went up  by 8 percentage points, from 52% to 60%.")["directions"] == ["rose"]
    # ...and a phrase's own boundary is not an invitation to match a longer word.
    assert surface_facts("The upbeat metric changed 8%, from 52% to 60%.")["directions"] == []

    # The receipt is bound to the predicate that produced it, not to a version string anyone can
    # keep while changing the rule underneath it (@dantic).
    digest = predicate_digest()
    assert len(digest) == 64 and set_signature(good)["predicate_sha256"] == digest
    assert digest == predicate_digest(), "the digest must be stable across calls"
    # PINNED. Two jobs. It forces an explicit acknowledgement whenever the predicate changes --
    # which is what a version binding is for -- and because CI runs this selftest on both 3.9 and
    # 3.12, an interpreter-dependent digest fails on one of them. That is a MECHANICAL proof of
    # the cross-version parity @dexagon-ai asked for, rather than an assertion that it holds.
    assert digest == PREDICATE_SHA256, (
        "the predicate changed (or differs by interpreter). If the change was intended, update "
        "PREDICATE_SHA256 deliberately -- that acknowledgement is the point.\n  expected %s\n  "
        "actual   %s" % (PREDICATE_SHA256, digest))

    # DERIVED, not constant: a hardcoded 64-character string would be stable and would satisfy
    # every assertion above while binding nothing.
    global _PREDICATE_FUNCTIONS, _ENDPOINTS, _DIRECTIONS
    original = _PREDICATE_FUNCTIONS
    try:
        _PREDICATE_FUNCTIONS = ("record_arithmetic",)
        assert predicate_digest() != digest, \
            "the digest must change with the predicate it covers, or it binds nothing"
    finally:
        _PREDICATE_FUNCTIONS = original
    assert predicate_digest() == digest, "and must return to the original once restored"

    # COMPLETE: a dependency OUTSIDE the originally-hashed functions must move the digest.
    # This is @dexagon-ai's exact reproduction on #123 — swapping the module's endpoint pattern
    # flipped a verdict from admissible to inadmissible while the digest stayed byte-identical,
    # because `_ENDPOINTS` was not part of the closure the receipt claimed to certify.
    original_endpoints = _ENDPOINTS
    try:
        _ENDPOINTS = re.compile(r"(?!x)x")   # never matches: endpoints become invisible
        assert set_signature(good)["set_admissible"] is False, \
            "the substituted pattern must genuinely change the verdict, or this proves nothing"
        assert predicate_digest() != digest, \
            "a live regex substitution changes admissibility and MUST change the digest"
    finally:
        _ENDPOINTS = original_endpoints
    assert predicate_digest() == digest and set_signature(good)["set_admissible"]

    original_directions = _DIRECTIONS
    try:
        _DIRECTIONS = (("rose", ("rose",)), ("fell", ("fell",)), ("held", ("held",)))
        assert predicate_digest() != digest, \
            "the direction lexicon decides a refusal, so it belongs in the closure"
    finally:
        _DIRECTIONS = original_directions
    assert predicate_digest() == digest
    # predicate_python is provenance, not commitment: it rides in the receipt and must NOT move
    # the digest, or two honest agents on different interpreters would disagree about the rule.
    sig = set_signature(good)
    assert sig["predicate_python"] == "%d.%d" % sys.version_info[:2], sig["predicate_python"]
    # What can be checked in one process is that the field is RECORDED and that the digest is
    # computed from source text rather than from anything the interpreter supplies. Whether the
    # digest is genuinely equal ACROSS interpreters cannot be checked from inside one of them --
    # that is what PREDICATE_SHA256 plus CI on 3.9 and 3.12 is for, and no in-process proxy should
    # be allowed to stand in for it. (I tried one that searched the hashed source for the running
    # version string; it tripped on a comment mentioning 3.12, which is a false positive and would
    # have been a worse test than none.)
    assert "sys.version_info" in _inspect_source("set_signature"), \
        "the receipt must read the interpreter where it is recorded, not where it is hashed"

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
