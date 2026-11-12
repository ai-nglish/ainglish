"""Write tests/fixtures/exact-binomial-preservation-v1.json: the vectors both implementations read.

Nothing here is produced by `ainglish.preservation`. Each kind of expectation comes from
arithmetic written separately, so the file can catch an error in the module instead of echoing it:

* `bounds` and `minimum_per_cell`: a bisection in 60-digit decimal arithmetic.
* `threshold_cases` (a bound against a fixed threshold): the whole binomial tail summed term by
  term with `math.comb` in integers and compared with the tail. No recurrence, no early stop.
* `delta_cases` (two bounds against each other): the two bounds to 60 digits, then the fixed
  halving schedule followed with those decimal values, which also gives the halving at which the
  schedule first separates them and the midpoint that does it.
* `cases`: 27 of the 28 synthetic acceptance cases of the pack published for protocol row
  a-9mvh2ph6g1fnw0a1 on 2026-11-10 (its non-finite token case has no JSON spelling and is tested
  in code), re-expressed with named readers, forms and endpoints, plus implementer cases. Their
  expected outcomes are written down here by hand, never computed by the module under test.
* `depth_cases` and `context_cases`: written by hand; the halvings the depth cases rest on are
  asserted from the decimal schedule.
* `identity_cases`: written by hand. Names chosen so that a parser which joins a reader and a form
  with a separator, or compares number-like names as numbers, judges them differently from one
  that compares the two names as they are. Complete families and families one cell short.

Run from the repository root:  python3 tools/gen_preservation_vectors.py
The register carries a byte copy of the output (tests/Fixtures/exact-binomial-preservation-v1.json).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "exact-binomial-preservation-v1.json"
PRECISION = 60
HALVINGS = 200
MAX_HALVINGS = 64
MAX_OBSERVATIONS = 4096
SAFE = Decimal("1e-40")  # a decimal comparison closer than this is not trusted
ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base")
INVENTORY = {"readers": ["reader-a", "reader-b"], "forms": ["form-a", "form-b"],
             "error_endpoints": ["false-authority", "false-polarity"]}
# The row's own design: two error families, each elicited on both arms. 2 x 2 x (2 + 4) = 24.
INVENTORY_24 = {"readers": ["reader-a", "reader-b"], "forms": ["form-a", "form-b"],
                "error_endpoints": ["false-authority.marked", "false-authority.english",
                                    "false-polarity.marked", "false-polarity.english"]}
ONE_CELL = {"readers": ["r"], "forms": ["f"], "error_endpoints": ["e"]}
TWO_FORMS = {"readers": ["r"], "forms": ["f", "g"], "error_endpoints": ["e"]}


def upper_tail(k, n, p):
    """P(X >= k) for X ~ Binomial(n, p), exact summation by recurrence in the current decimal context."""
    q = 1 - p
    term = Decimal(math.comb(n, k)) * p ** k * q ** (n - k)
    total = term
    for j in range(k, n):
        term = term * (n - j) / (j + 1) * p / q
        total += term
    return total


def decimal_lower(k, n, tail):
    if k == 0:
        return Decimal(0)
    lo, hi = Decimal(0), Decimal(1)
    for _ in range(HALVINGS):
        p = (lo + hi) / 2
        if upper_tail(k, n, p) < tail:
            lo = p
        else:
            hi = p
    return (lo + hi) / 2


def decimal_upper(k, n, tail):
    return 1 - decimal_lower(n - k, n, tail)


def text(value):
    return format(+value, ".18f")


def size(inventory):
    return len(inventory["readers"]) * len(inventory["forms"]) * (2 + len(inventory["error_endpoints"]))


def integer_tail_sign(k, n, t, m):
    """Sign of P(X >= k | p = t) - 1/(40m): every term from math.comb, summed in integers."""
    a, d = t.numerator, t.denominator
    total = sum(math.comb(n, j) * a ** j * (d - a) ** (n - j) for j in range(k, n + 1))
    return (40 * m * total > d ** n) - (40 * m * total < d ** n)


def lower_sign(k, n, m, t):
    """Sign of (lower bound of k in n) - t, for 0 < t < 1."""
    return -1 if k == 0 else -integer_tail_sign(k, n, t, m)


def upper_sign(k, n, m, t):
    """Sign of (upper bound of k in n) - t, for 0 < t < 1: P(X <= k | p = t) against the tail, summed directly."""
    if k == n:
        return 1
    a, d = t.numerator, t.denominator
    total = sum(math.comb(n, j) * a ** j * (d - a) ** (n - j) for j in range(0, k + 1))
    return (40 * m * total > d ** n) - (40 * m * total < d ** n)


def schedule(first, rest):
    """Follow the fixed halving schedule on [-1, 3] with decimal values of the first bound and of
    (threshold - second bound). Returns the sign of their difference, the halving that separates them and
    the midpoint that did it."""
    lo, hi = Fraction(-1), Fraction(3)
    for halving in range(1, MAX_HALVINGS + 1):
        mid = (lo + hi) / 2
        point = Decimal(mid.numerator) / Decimal(mid.denominator)
        assert abs(point - first) > SAFE and abs(point - rest) > SAFE, (point, first, rest)
        if min(first, rest) < point < max(first, rest):
            return (1 if first > rest else -1), halving, mid
        if point < min(first, rest):
            lo = mid
        else:
            hi = mid
    raise AssertionError("not separated in %d halvings" % MAX_HALVINGS)


def delta(rule, marked, english, m):
    """Does the delta inequality hold, and at which halving is it settled? From decimals only."""
    tail = Decimal(1) / Decimal(40 * m)
    (mk, mn), (ek, en) = marked, english
    if rule == "supports":  # L(marked) - U(English) >= -1/20  <=>  L(mk, mn) + L(en - ek, en) >= 19/20
        first, second, threshold = decimal_lower(mk, mn, tail), decimal_lower(en - ek, en, tail), Decimal(19) / Decimal(20)
    else:                   # U(marked) - L(English) < -1/20   <=>  L(mn - mk, mn) + L(ek, en) > 21/20
        first, second, threshold = decimal_lower(mn - mk, mn, tail), decimal_lower(ek, en, tail), Decimal(21) / Decimal(20)
    sign, halving, point = schedule(first, threshold - second)
    return sign > 0, halving, point


def bank(n=512, inventory=INVENTORY):
    return [{"reader": r, "form": f, "marked": [n, n], "english": [n, n],
             "errors": {e: [0, n] for e in inventory["error_endpoints"]}}
            for r in inventory["readers"] for f in inventory["forms"]]


def means(values):
    return dict(zip(ENCODINGS, values))


DEFAULT_CONTEXT = {"prospective": True, "unique_worlds": True, "confirmed": True, "fresh_replica_pass": True,
                   "confirmed_loss": False, "matched_comparator": True, "all_other_promises": True,
                   "token_means": means((-2, -2, -2)),
                   "token_form_means": {"form-a": means((-2, -2, -2)), "form-b": means((-2, -2, -2))}}


def cases():
    out = []

    def case(name, cells, expected, source="acceptance-pack-2026-11-10", inventory=None, **context):
        merged = copy.deepcopy(DEFAULT_CONTEXT)
        merged.update(context)
        row = {"name": name, "source": source, "cells": cells, "context": merged, "expected": expected}
        if inventory is not None:
            row["inventory"] = inventory
        out.append(row)

    case("complete-large-ceiling", bank(), "profile_and_benefit_satisfied_not_ratified")
    case("two-perfect-is-not-proof", bank(2), "unresolved")
    c = bank(); c[0]["marked"] = [200, 512]; c[1] = bank(2)[1]
    case("harm-takes-precedence-over-other-unresolved-cell", c, "opposes_profile")
    c = bank(); c[0]["errors"]["false-authority"] = [150, 512]
    case("one-separately-elicited-error-fails", c, "opposes_profile")
    c = bank(); c[0]["english"] = [200, 512]
    case("large-positive-delta-does-not-excuse-poor-english-accuracy", c, "opposes_profile")
    case("confirmed-loss-veto", bank(), "confirmed_loss_veto", confirmed_loss=True)
    case("legacy-must-stay-legacy", bank(), "legacy_unchanged", prospective=False)
    case("original-unconfirmed", bank(), "unconfirmed_or_replica_incomplete", confirmed=False)
    case("fresh-replication-not-passing", bank(), "unconfirmed_or_replica_incomplete", fresh_replica_pass=False)
    case("comparator-mismatch", bank(), "comparator_mismatch", matched_comparator=False)
    case("positive-allowance-not-benefit", bank(), "no_demonstrated_benefit", token_means=means((4, 4, 4)))
    case("overall-cost-too-close-to-zero", bank(), "no_demonstrated_benefit", token_means=means((-0.5, -2, -2)))
    case("exactly-one-token-saved", bank(), "profile_and_benefit_satisfied_not_ratified", token_means=means((-1, -1, -1)),
         token_form_means={"form-a": means((-1, -1, -1)), "form-b": means((-1, -1, -1))})
    case("missing-tokenizer", bank(), "no_demonstrated_benefit", token_means=means((-2, -2)))
    case("one-form-loses", bank(), "no_demonstrated_benefit",
         token_form_means={"form-a": means((-2, -2, -2)), "form-b": means((1, -2, -2))})
    case("missing-form-benefit", bank(), "no_demonstrated_benefit", token_form_means={})
    # The pack's non-finite case has no JSON spelling; each implementation tests NaN and infinity in code.
    case("declared-cluster-duplicates", bank(), "invalid_sampling", unique_worlds=False)
    case("other-promises-unfinished", bank(), "other_promises_incomplete", all_other_promises=False)
    c = bank(); c.pop(); case("missing-reader-form", c, "refuse_malformed")
    c = bank(); c[-1] = copy.deepcopy(c[0]); case("duplicate-reader-form", c, "refuse_malformed")
    c = bank(); del c[0]["errors"]["false-polarity"]; case("unasked-endpoint", c, "refuse_malformed")
    c = bank(); c[0]["errors"]["posthoc-error"] = [0, 512]; case("posthoc-endpoint", c, "refuse_malformed")
    for value in (True, 1.5, -1, 513):
        c = bank(); c[0]["marked"][0] = value
        case("bad-count-" + str(value), c, "refuse_malformed")
    c = bank(); c[0]["english"] = [0, 0]; case("zero-denominator", c, "refuse_malformed")
    c = bank(); c[0]["errors"] = [[0, 512], [0, 512]]; case("endpoints-as-a-list-are-not-a-mapping", c, "refuse_malformed", source="implementer")
    c = bank(); c[0]["errors"] = {}; case("empty-endpoint-mapping", c, "refuse_malformed", source="implementer")
    c = bank(); c[0]["marked"] = [512, 512, 512]; case("three-element-count", c, "refuse_malformed", source="implementer")
    c = bank(); c[0]["marked"] = "512/512"; case("count-as-text", c, "refuse_malformed", source="implementer")
    # Named-identity cases: the family is the inventory's, never the received cells'.
    c = bank(); c[3]["reader"] = "reader-c"; case("renamed-reader-is-not-the-inventory-reader", c, "refuse_malformed", source="implementer")
    c = bank(); c.append(dict(copy.deepcopy(c[0]), reader="reader-c")); case("extra-reader-form", c, "refuse_malformed", source="implementer")
    c = bank(); c[2]["marked"] = [200, 512]; del c[2]["errors"]["false-authority"]
    case("deleting-an-endpoint-cannot-hide-a-harmful-cell", c, "refuse_malformed", source="implementer")
    c = bank(126); case("smallest-all-correct-family-that-can-support", c, "profile_and_benefit_satisfied_not_ratified", source="implementer")
    c = bank(125); case("one-observation-short-of-support", c, "unresolved", source="implementer")
    # The delta rule alone. Floors and caps are met in both; only L(marked) - U(English) or U(marked) - L(English) decides.
    c = bank(4000); c[0]["marked"] = [3760, 4000]; c[0]["english"] = [3996, 4000]
    case("delta-lower-short-while-floors-and-caps-pass", c, "unresolved", source="implementer")
    c = bank(4000); c[0]["marked"] = [3640, 4000]; c[0]["english"] = [3996, 4000]
    case("delta-upper-below-margin-while-accuracy-upper-clears-the-floor", c, "opposes_profile", source="implementer")
    # The error cap alone, and the accuracy floor alone: in an all-correct cell the delta rule always fails first,
    # so without these two a broken cap or floor would go unnoticed.
    c = bank(4000); c[0]["marked"] = [3996, 4000]; c[0]["english"] = [3996, 4000]; c[0]["errors"]["false-polarity"] = [190, 4000]
    case("error-upper-above-cap-while-delta-and-floors-pass", c, "unresolved", source="implementer")
    c = bank(4000); c[0]["marked"] = [3640, 4000]; c[0]["english"] = [3640, 4000]
    case("accuracy-lower-below-floor-while-delta-and-caps-pass", c, "unresolved", source="implementer")
    # One arm's floor alone: the other arm, the delta and the caps all pass, so a floor read on one arm only would support.
    c = bank(4000); c[0]["marked"] = [3996, 4000]; c[0]["english"] = [3640, 4000]
    case("english-accuracy-lower-below-floor-alone", c, "unresolved", source="implementer")
    c = bank(4000); c[0]["marked"] = [3640, 4000]; c[0]["english"] = [3680, 4000]
    case("marked-accuracy-lower-below-floor-alone", c, "unresolved", source="implementer")
    # Both arms. The row's design asks each error family on the marked arm and on the English arm, so the
    # inventory names four endpoints and the family is 24, with its own tail and its own minimum.
    case("both-arms-family-of-24-at-its-minimum", bank(134, INVENTORY_24), "profile_and_benefit_satisfied_not_ratified",
         source="implementer", inventory=INVENTORY_24)
    case("both-arms-family-of-24-one-observation-short", bank(133, INVENTORY_24), "unresolved", source="implementer", inventory=INVENTORY_24)
    case("the-minimum-for-16-does-not-carry-to-24", bank(126, INVENTORY_24), "unresolved", source="implementer", inventory=INVENTORY_24)
    case("one-arm-of-endpoints-against-the-both-arms-inventory", bank(134, INVENTORY), "refuse_malformed", source="implementer", inventory=INVENTORY_24)
    # Above the largest count the exact search is run on, nothing is certified and nothing is hidden.
    over = MAX_OBSERVATIONS + 1
    case("observations-above-the-ceiling-are-not-judged", bank(over), "numerically_unresolved", source="implementer")
    case("the-ceiling-itself-is-judged", bank(MAX_OBSERVATIONS), "profile_and_benefit_satisfied_not_ratified", source="implementer")
    c = bank(); c[0] = bank(over)[0]; c[1]["marked"] = [200, 512]
    case("a-certain-opposition-is-not-hidden-by-an-unjudged-cell", c, "opposes_profile", source="implementer")
    case("the-loss-veto-is-not-hidden-by-an-unjudged-cell", bank(over), "confirmed_loss_veto", source="implementer", confirmed_loss=True)
    c = bank(); c[0] = bank(over)[0]; c[1] = bank(2)[1]
    case("an-unjudged-cell-could-still-oppose-so-the-profile-is-not-called-unresolved", c, "numerically_unresolved", source="implementer")
    case("an-unfinished-confirmation-is-reported-before-a-numerical-doubt", bank(over), "unconfirmed_or_replica_incomplete",
         source="implementer", confirmed=False)
    # Number-like FORM names, where the per-form token means are a mapping keyed by form. "1e1" and "10.0" are two
    # forms and one number; a parser that sorts them as numbers cannot match the mapping against the inventory when
    # the two are listed in another order. See identity_cases for the same names on the cells.
    numbered = {"readers": ["r"], "forms": ["1e1", "10.0"], "error_endpoints": ["e"]}
    case("number-like-forms-with-token-means-listed-in-another-order", bank(512, numbered), "profile_and_benefit_satisfied_not_ratified",
         source="implementer", inventory=numbered, token_form_means={"10.0": means((-2, -2, -2)), "1e1": means((-2, -2, -2))})
    case("number-like-forms-with-token-means-for-an-equal-number", bank(512, numbered), "no_demonstrated_benefit",
         source="implementer", inventory=numbered, token_form_means={"1e1": means((-2, -2, -2)), "10.00": means((-2, -2, -2))})
    return out


def context_cases():
    """Calls `outcome` must refuse. A context fact is the service's to derive; a missing one is never a pass."""
    rows = []
    for flag in ("prospective", "unique_worlds", "confirmed", "fresh_replica_pass", "confirmed_loss", "matched_comparator",
                 "all_other_promises", "token_means", "token_form_means"):
        context = copy.deepcopy(DEFAULT_CONTEXT)
        del context[flag]
        rows.append({"name": "missing-" + flag, "context": context})
    for flag, value in (("confirmed", 1), ("confirmed", "true"), ("prospective", None), ("unique_worlds", 1.0),
                        ("confirmed_loss", 0), ("confirmed_loss", None), ("all_other_promises", "yes"), ("matched_comparator", [True])):
        context = copy.deepcopy(DEFAULT_CONTEXT)
        context[flag] = value
        rows.append({"name": "%s-as-%s" % (flag, json.dumps(value)), "context": context})
    context = copy.deepcopy(DEFAULT_CONTEXT)
    context["reviewed"] = True
    rows.append({"name": "an-extra-fact", "context": context})
    rows.append({"name": "no-context-at-all", "context": {}})
    return rows


def threshold_cases():
    """A bound against one of the rule's fixed thresholds: the sign of (bound - threshold)."""
    rows = []

    def add(bound, k, n, m, threshold, note=None):
        t = Fraction(threshold)
        row = {"bound": bound, "k": k, "n": n, "family_size": m, "threshold": threshold,
               "sign": lower_sign(k, n, m, t) if bound == "lower" else upper_sign(k, n, m, t)}
        if note:
            row["note"] = note
        rows.append(row)

    # Exact equalities: with these counts and family sizes the bound IS the threshold.
    add("upper", 0, 3, 25, "9/10", "equal: 1 - (1/1000)^(1/3) = 9/10, so the upper bound is not below the floor")
    add("upper", 0, 4, 250, "9/10", "equal: 1 - (1/10000)^(1/4) = 9/10")
    add("lower", 2, 2, 10, "1/20", "equal: (1/400)^(1/2) = 1/20, so the lower bound is not above the cap")
    add("lower", 3, 3, 200, "1/20", "equal: (1/8000)^(1/3) = 1/20")
    for k, n, m in ((0, 2, 25), (0, 4, 25), (0, 3, 24), (0, 3, 26), (1, 3, 25)):
        add("upper", k, n, m, "9/10", "beside the equality at 0 of 3, family 25")
    for k, n, m in ((1, 1, 10), (3, 3, 10), (2, 2, 9), (2, 2, 11), (1, 2, 10)):
        add("lower", k, n, m, "1/20", "beside the equality at 2 of 2, family 10")
    # The smallest all-correct cells that can support, and one observation short of each.
    for m, n in ((8, 113), (16, 126), (20, 131), (24, 134)):
        for size_ in (n - 1, n):
            add("lower", size_, size_, m, "19/20", "all-correct delta: needs L(n, n) >= 19/20")
            add("upper", 0, size_, m, "1/20", "zero-error cap: needs U(0, n) <= 1/20")
            add("lower", size_, size_, m, "9/10")
    # Either side of the count at which each bound crosses each threshold.
    for m in (3, 16, 24):
        for n in (59, 126, 512):
            for bound, threshold in (("lower", "9/10"), ("upper", "9/10"), ("lower", "1/20"), ("upper", "1/20")):
                sign = lower_sign if bound == "lower" else upper_sign
                t = Fraction(threshold)
                lo, hi = 0, n  # sign is non-decreasing in k; find the first k that is not negative
                if sign(lo, n, m, t) >= 0 or sign(hi, n, m, t) < 0:
                    continue
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    if sign(mid, n, m, t) < 0:
                        lo = mid
                    else:
                        hi = mid
                add(bound, lo, n, m, threshold)
                add(bound, hi, n, m, threshold)
    assert [row["sign"] for row in rows[:4]] == [0, 0, 0, 0], rows[:4]
    return rows


# (family size, rule, marked, english). The near-ties were found by a float search over all pairs of counts;
# what is published about them below comes from the decimal schedule, not from that search.
DELTA_CASES = (
    (16, "supports", [510, 512], [509, 512]),
    (16, "opposes", [510, 512], [509, 512]),
    (24, "supports", [494, 512], [492, 512]),
    (24, "supports", [20, 512], [18, 512]),
    (16, "supports", [354, 512], [315, 512]),
    (16, "opposes", [163, 512], [255, 512]),
    (16, "opposes", [456, 1000], [600, 1000]),
    (24, "supports", [102, 134], [74, 134]),
    (16, "supports", [3760, 4000], [3996, 4000]),
    (16, "opposes", [3640, 4000], [3996, 4000]),
)


def delta_cases():
    rows = []
    for m, rule, marked, english in DELTA_CASES:
        holds, halving, point = delta(rule, marked, english, m)
        rows.append({"family_size": m, "rule": rule, "marked": marked, "english": english, "holds": holds, "decided_at": halving,
                     "point": "%d/%d" % (point.numerator, point.denominator)})
    assert max(row["decided_at"] for row in rows) >= 20 and {row["holds"] for row in rows} == {True, False}
    return rows


def depth_cases():
    """Evaluations under a halving limit, to reach the edge where the search stops short."""
    cell = {"reader": "r", "form": "f", "marked": [510, 512], "english": [509, 512], "errors": {"e": [0, 512]}}
    rows = []
    for inventory in (ONE_CELL, TWO_FORMS):
        m = size(inventory)
        # Everything except the delta is settled without a search, and settled in favour.
        assert lower_sign(510, 512, m, Fraction(9, 10)) >= 0 and lower_sign(509, 512, m, Fraction(9, 10)) >= 0
        assert upper_sign(0, 512, m, Fraction(1, 20)) <= 0 and upper_sign(510, 512, m, Fraction(9, 10)) >= 0
    m = size(ONE_CELL)
    holds, settled, _ = delta("supports", [510, 512], [509, 512], m)
    opposed, ruled_out, _ = delta("opposes", [510, 512], [509, 512], m)
    assert holds and not opposed and 1 <= ruled_out < settled, (holds, settled, opposed, ruled_out)

    def row(name, inventory, cells, limit, verdicts, state):
        rows.append({"name": name, "inventory": inventory, "cells": cells, "max_halvings": limit, "verdicts": verdicts, "profile_state": state})

    row("the-delta-is-settled-at-exactly-this-halving", ONE_CELL, [cell], settled, ["supports"], "supports")
    row("one-halving-short-of-settling-the-delta", ONE_CELL, [cell], settled - 1, ["numerically_unresolved"], "numerically_unresolved")
    row("one-halving-short-of-ruling-out-opposition", ONE_CELL, [cell], ruled_out - 1, ["numerically_unresolved"], "numerically_unresolved")
    row("no-halvings-at-all", ONE_CELL, [cell], 0, ["numerically_unresolved"], "numerically_unresolved")
    harmful = copy.deepcopy(cell); harmful["errors"]["e"] = [150, 512]
    assert lower_sign(150, 512, m, Fraction(1, 20)) > 0
    row("a-certain-error-opposition-needs-no-halvings", ONE_CELL, [harmful], 0, ["opposes"], "opposes")
    # With an all-correct English arm the supporting delta is a plain threshold test, and once it holds the
    # opposing delta cannot: nothing here may wait on a search that was never needed.
    implied = {"reader": "r", "form": "f", "marked": [509, 512], "english": [512, 512], "errors": {"e": [0, 512]}}
    assert lower_sign(509, 512, m, Fraction(19, 20)) >= 0 and lower_sign(509, 512, m, Fraction(9, 10)) >= 0
    row("a-supporting-delta-rules-out-the-opposing-one-without-a-search", ONE_CELL, [implied], 0, ["supports"], "supports")
    # Two cells. The second can never support and never opposes, and none of its tests needs a search.
    m = size(TWO_FORMS)
    holds, settled, _ = delta("supports", [510, 512], [509, 512], m)
    opposed, ruled_out, _ = delta("opposes", [510, 512], [509, 512], m)
    assert holds and not opposed and 1 <= ruled_out < settled
    first = dict(cell, form="f")
    second = {"reader": "r", "form": "g", "marked": [2, 2], "english": [2, 2], "errors": {"e": [0, 2]}}
    assert lower_sign(2, 2, m, Fraction(19, 20)) < 0 and lower_sign(2, 2, m, Fraction(9, 10)) < 0
    row("a-doubt-that-cannot-change-the-profile-is-not-reported", TWO_FORMS, [first, second], settled - 1,
        ["numerically_unresolved", "unresolved"], "unresolved")
    row("a-doubt-that-could-be-an-opposition-is-reported", TWO_FORMS, [first, second], ruled_out - 1,
        ["numerically_unresolved", "unresolved"], "numerically_unresolved")
    return rows


def inventory_cases():
    """Inventories both parsers must judge alike. A bare-integer name is refused because PHP would
    turn it into an integer array key; a JSON list of endpoints must never pass for a mapping."""
    good = INVENTORY
    rows = [("the-shared-inventory", good, True, 16),
            ("two-error-families-on-both-arms", INVENTORY_24, True, 24),
            ("one-reader-one-form-one-endpoint", {"readers": ["r"], "forms": ["f"], "error_endpoints": ["e"]}, True, 3),
            ("no-readers", dict(good, readers=[]), False, None),
            ("no-error-endpoints", dict(good, error_endpoints=[]), False, None),
            ("repeated-form", dict(good, forms=["form-a", "form-a"]), False, None),
            ("repeated-endpoint", dict(good, error_endpoints=["e", "e"]), False, None),
            ("non-string-reader", dict(good, readers=["reader-a", 2]), False, None),
            ("empty-name", dict(good, forms=["form-a", ""]), False, None),
            ("bare-integer-endpoint-zero", dict(good, error_endpoints=["0", "1"]), False, None),
            ("bare-integer-form", dict(good, forms=["form-a", "12"]), False, None),
            ("negative-integer-reader", dict(good, readers=["reader-a", "-5"]), False, None),
            ("leading-zero-is-a-name-not-an-integer", dict(good, forms=["form-a", "007"]), True, 16),
            ("extra-key", dict(good, extra=1), False, None),
            ("missing-key", {"readers": ["r"], "forms": ["f"]}, False, None),
            ("readers-as-mapping", dict(good, readers={"a": "reader-a"}), False, None)]
    return [{"name": n, "inventory": inv, "accepted": ok, "family_size": m} for n, inv, ok, m in rows]


def identity_cases():
    """Families whose NAMES are the test. Every name is a legal name; what differs between a right and a
    wrong parser is whether two different (reader, form) pairs, or two different endpoint names, are
    kept apart. Each family comes complete (accepted, every cell evaluated) and damaged (refused).

    * nul: ("a", "b\\0c") and ("a\\0b", "c") are one string when the pair is joined with a NUL.
    * colon: ("x", "y:z") and ("x:y", "z") are one string when the pair is joined with a colon.
    * length: ("1c", 20 d's + "f") and ("c" + 20 d's, "f") are one string when the reader's length is written
      in front of the two names with nothing after it: 2 then "1c..." reads the same as 21 then "c...".
    * number-like: "1e1", "10.0" and " 10" are three names and one number. A sort that compares them
      as numbers leaves them in the order they came, so two equal sets can look different.
    Reported on register pull request 745 for the NUL join (review of 2026-11-11)."""
    def cells(inventory, pairs=None, order=None, n=512):
        endpoints = order or inventory["error_endpoints"]
        pairs = pairs if pairs is not None else [(r, f) for r in inventory["readers"] for f in inventory["forms"]]
        return [{"reader": r, "form": f, "marked": [n, n], "english": [n, n], "errors": {e: [0, n] for e in endpoints}} for r, f in pairs]

    nul = {"readers": ["a", "a\x00b"], "forms": ["b\x00c", "c"], "error_endpoints": ["e"]}
    colon = {"readers": ["x", "x:y"], "forms": ["y:z", "z"], "error_endpoints": ["e"]}
    length = {"readers": ["1c", "c" + "d" * 20], "forms": ["d" * 20 + "f", "f"], "error_endpoints": ["e"]}
    numbers = {"readers": ["1e1", "10.0"], "forms": ["f"], "error_endpoints": ["1e1", "10.0", " 10"]}
    for inventory, m in ((nul, 12), (colon, 12), (length, 12), (numbers, 10)):
        assert size(inventory) == m, (inventory, size(inventory))
    rows = []

    def row(name, inventory, given, accepted, note):
        rows.append({"name": name, "note": note, "inventory": inventory, "cells": given, "accepted": accepted,
                     "family_size": size(inventory) if accepted else None,
                     "cells_evaluated": len(inventory["readers"]) * len(inventory["forms"]) if accepted else None,
                     "profile_state": "supports" if accepted else None})

    for label, inv, first, second in (("nul", nul, ("a", "b\x00c"), ("a\x00b", "c")), ("colon", colon, ("x", "y:z"), ("x:y", "z")),
                                      ("length", length, ("1c", "d" * 20 + "f"), ("c" + "d" * 20, "f"))):
        every = [(r, f) for r in inv["readers"] for f in inv["forms"]]
        assert first in every and second in every and first != second
        row(label + "-names-complete", inv, cells(inv), True, "four different cells; a joined key would call two of them one")
        row(label + "-names-without-the-first-of-the-pair", inv, cells(inv, [p for p in every if p != first]), False,
            "three cells; a joined key cannot see that %r is missing" % (first,))
        row(label + "-names-without-the-second-of-the-pair", inv, cells(inv, [p for p in every if p != second]), False,
            "three cells; a joined key cannot see that %r is missing" % (second,))
        row(label + "-names-one-of-the-pair-twice", inv, cells(inv, [p for p in every if p != second] + [first]), False,
            "four cells, one of them given twice and one missing")
    row("number-like-names-complete", numbers, cells(numbers), True, "endpoints in the inventory's order")
    row("number-like-endpoints-in-another-order", numbers, cells(numbers, order=["10.0", " 10", "1e1"]), True,
        "the same three endpoint names, listed in another order in every cell")
    row("number-like-endpoints-one-missing", numbers, cells(numbers, order=["1e1", "10.0"]), False, "two of the three endpoints")
    row("number-like-endpoints-one-replaced-by-an-equal-number", numbers, cells(numbers, order=["1e1", "10.0", "10.00"]), False,
        "10.00 is the same number as the missing name and a different name")
    row("number-like-readers-one-twice", numbers, cells(numbers, [("1e1", "f"), ("1e1", "f")]), False, "one reader given twice, the other missing")
    # 512 of 512 with no errors supports any family this small: the published minimum per cell for 16 is 126.
    assert all(c["marked"] == [512, 512] for r in rows for c in r["cells"])
    return rows


def main():
    with localcontext() as context:
        context.prec = PRECISION
        bounds = []
        for tail_text in ("1/640", "1/320", "1/40"):
            tail = Decimal(Fraction(tail_text).numerator) / Decimal(Fraction(tail_text).denominator)
            for n in (1, 2, 3, 8, 20, 59, 126, 512):
                for k in sorted({0, 1, n // 2, n - 1, n} & set(range(n + 1))):
                    bounds.append({"k": k, "n": n, "tail": tail_text, "lower": text(decimal_lower(k, n, tail)),
                                   "upper": text(decimal_upper(k, n, tail))})
        minima = []
        for m, published in ((8, 113), (16, None), (20, 131), (24, 134)):
            tail = Decimal(1) / Decimal(40 * m)
            n = 1
            while decimal_lower(n, n, tail) - 1 < Decimal("-0.05"):
                n += 1
            assert published is None or n == published, (m, n, published)
            minima.append({"family_size": m, "n": n,
                           "source": "proposal thread, author's packet of 2026-09-25" if published else "derived here"})
        # Justify the two delta-only cases from the decimal bounds, not from the module under test.
        t640d = Decimal(1) / Decimal(640); margin, floor = Decimal("-0.05"), Decimal("0.90")
        el, eu = decimal_lower(3996, 4000, t640d), decimal_upper(3996, 4000, t640d)
        ml, mu = decimal_lower(3760, 4000, t640d), decimal_upper(3760, 4000, t640d)
        assert ml >= floor and el >= floor and ml - eu < margin and mu - el >= margin and mu >= floor, (ml, mu, el, eu)
        ml, mu = decimal_lower(3640, 4000, t640d), decimal_upper(3640, 4000, t640d)
        assert mu - el < margin and mu >= floor and eu >= floor, (ml, mu, el, eu)
        cap = Decimal("0.05")
        hl, hu = decimal_lower(3996, 4000, t640d), decimal_upper(3996, 4000, t640d)
        xl, xu = decimal_lower(190, 4000, t640d), decimal_upper(190, 4000, t640d)
        assert hl - hu >= margin and hl >= floor and xu > cap and xl <= cap and decimal_upper(0, 4000, t640d) <= cap, (hl, hu, xl, xu)
        fl, fu = decimal_lower(3640, 4000, t640d), decimal_upper(3640, 4000, t640d)
        assert fl < floor and fu >= floor and fl - fu >= margin and fu - fl >= margin, (fl, fu)
        # One arm's floor alone. English short: the marked floor, the delta both ways and the English upper bound all pass.
        assert hl >= floor and fl < floor and fu >= floor and hl - fu >= margin and hu - fl >= margin, (hl, hu, fl, fu)
        # Marked short: 3640 of 4000 marked against 3680 of 4000 English.
        gl, gu = decimal_lower(3680, 4000, t640d), decimal_upper(3680, 4000, t640d)
        assert fl < floor and fu >= floor and gl >= floor and fl - gu >= margin and fu - gl >= margin, (fl, fu, gl, gu)
        # The two values the acceptance pack publishes for the 512-observation ceiling at tail 1/640.
        t640 = Decimal(1) / Decimal(640)
        assert abs(decimal_lower(512, 512, t640) - Decimal("0.9874592439262977")) < Decimal("1e-15")
        assert abs(decimal_upper(0, 512, t640) - Decimal("0.012540756073702308")) < Decimal("1e-15")
        deltas, depths = delta_cases(), depth_cases()
    report = {"kind": "ainglish.exact-binomial-preservation-vectors.v1", "analysis": "exact-binomial-preservation-v1",
              "generated_by": {"tool": "tools/gen_preservation_vectors.py",
                               "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                               "method": "bounds: decimal bisection of the exact binomial tail, %d digits, %d halvings; "
                                         "threshold signs: integer tail sums; delta cases: the halving schedule followed "
                                         "in decimals; all independent of ainglish.preservation" % (PRECISION, HALVINGS),
                               "python": "%d.%d" % sys.version_info[:2]},
              "note": "Synthetic software vectors. Not language evidence, not a measurement, not a profile pass.",
              "bound_tolerance": "1e-10",
              "thresholds": {"delta_margin": "1/20", "accuracy_floor": "9/10", "error_cap": "1/20"},
              "precision": {"max_halvings": MAX_HALVINGS, "max_observations": MAX_OBSERVATIONS},
              "token_encodings": list(ENCODINGS),
              "bounds": bounds, "minimum_per_cell": minima, "threshold_cases": threshold_cases(), "delta_cases": deltas,
              "depth_cases": depths, "inventory": INVENTORY, "inventory_cases": inventory_cases(),
              "identity_cases": identity_cases(),
              "context_cases": context_cases(), "cases": cases()}
    OUT.write_text(json.dumps(report, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), "|", len(bounds), "bounds |", len(report["threshold_cases"]), "threshold cases |",
          len(deltas), "delta cases |", len(depths), "depth cases |", len(report["context_cases"]), "context cases |",
          len(report["cases"]), "cases |", len(report["identity_cases"]), "identity cases |", hashlib.sha256(OUT.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
