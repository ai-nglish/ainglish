"""Exact-binomial preservation profile: the numeric core (protocol row a-9mvh2ph6g1fnw0a1).

This module is arithmetic and nothing else. It reads counts that a caller has already
reconstructed from a frozen inventory and says what the profile `exact-binomial-preservation-v1`
makes of them. It does not parse a contract, mint an attempt, replay a journal or settle a
replication; those boundaries are separate and none of them is wired to this module yet.

The rule, from the row:

* The family is every binomial quantity fixed BEFORE outcomes: for each reader and form, marked
  accuracy, careful-English accuracy and each separately elicited semantic-error endpoint. Its
  size M comes from the frozen inventory, never from the cells that happened to arrive.
* Each marginal Clopper-Pearson bound is taken at tail t = 0.05 / (2M) = 1 / (40M).
* A cell SUPPORTS when the delta lower bound is at least -0.05, both accuracy lower bounds are at
  least 0.90 and every error upper bound is at most 0.05. It OPPOSES when the delta upper bound is
  below -0.05, an accuracy upper bound is below 0.90 or an error lower bound is above 0.05.
  Otherwise it is UNRESOLVED. One opposing cell opposes the profile; every cell must support for
  the profile to support.

How an inequality is decided. No decision reads a floating-point number, and there is no
tolerance anywhere.

* A bound against a fixed threshold never needs the bound. The lower bound of k in n is at least
  t exactly when P(X >= k) at p = t is at most the tail, and with t and the tail both fractions
  that is one comparison of integers. The upper bound of k in n is one minus the lower bound of
  n - k in n, so every such test is a test on a lower bound.
* The delta sets two bounds against each other: L(marked) - U(English) >= -1/20 is the same as
  L(marked) + L(English misses) >= 19/20. A point p that has the first bound at or above it and
  the second at or above 19/20 - p proves the sum is large enough; a point with both below proves
  it is not. The search halves [-1, 3] on a fixed schedule and asks those two exact questions at
  each midpoint, so its path depends on the counts alone.
* If MAX_HALVINGS midpoints separate nothing, or a count is above MAX_OBSERVATIONS, the inequality
  is not certified either way and is reported as undecided (None). A cell or profile whose verdict
  hangs on an undecided inequality is NUMERICALLY UNRESOLVED, a label of its own: it is not the
  statistical "unresolved", and it never hides an inequality that is certainly opposing.

Parity. The register evaluates the same counts in PHP (`ExactBinomialPreservation`) with the same
integer procedure, so the two cannot differ by rounding. The floating-point bounds in the result
are for a human to read; a last-bit difference in them between two math libraries changes nothing.
"""
from __future__ import annotations

import math
import re

ANALYSIS = "exact-binomial-preservation-v1"
FAMILY_ALPHA = 0.05
# The marginal tail is 0.05 / (2M); as a fraction, 1 / (TAIL_DIVISOR * M).
TAIL_DIVISOR = 40
# The rule's thresholds. The plain numbers are what a contract carries and a receipt shows; every decision
# uses the fractions (numerator, denominator), so that none of them reads a float.
DELTA_MARGIN = 0.05
ACCURACY_FLOOR = 0.90
ERROR_CAP = 0.05
EXACT_DELTA_MARGIN = (1, 20)
EXACT_ACCURACY_FLOOR = (9, 10)
EXACT_ERROR_CAP = (1, 20)
# The precision policy: how far the delta search goes, and the largest count it is run on.
MAX_HALVINGS = 64
MAX_OBSERVATIONS = 4096
# The most binary places a point chosen from display bounds may have (see _shortcut).
SHORTCUT_PLACES = 48
BISECTIONS = 60
TOKEN_ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base")
MIN_TOKEN_SAVING = 1

# Outcome names, in the reference checker's order of precedence.
LEGACY_UNCHANGED = "legacy_unchanged"
INVALID_SAMPLING = "invalid_sampling"
REFUSE_MALFORMED = "refuse_malformed"
CONFIRMED_LOSS_VETO = "confirmed_loss_veto"
OPPOSES_PROFILE = "opposes_profile"
UNCONFIRMED = "unconfirmed_or_replica_incomplete"
COMPARATOR_MISMATCH = "comparator_mismatch"
NO_DEMONSTRATED_BENEFIT = "no_demonstrated_benefit"
OTHER_PROMISES_INCOMPLETE = "other_promises_incomplete"
SATISFIED = "profile_and_benefit_satisfied_not_ratified"
UNRESOLVED = "unresolved"
NUMERICALLY_UNRESOLVED = "numerically_unresolved"

# The facts `outcome` reads beside the counts. Every one is required and none has a default.
CONTEXT_FLAGS = ("prospective", "unique_worlds", "confirmed", "fresh_replica_pass", "confirmed_loss",
                 "matched_comparator", "all_other_promises")


class MalformedPreservation(ValueError):
    """The inventory or the counts are not a complete, well-formed family. Never a null result."""


class ContextRefused(TypeError):
    """`outcome` was called without one of its facts, or with one that is not a plain boolean.

    This is the calling service's error, not a property of the evidence, so it is raised instead
    of being turned into an outcome name. A fact that is missing is never read as a pass.
    """


_LOG_FACTORIAL = [0.0, 0.0]
_BARE_INTEGER = re.compile(r"-?(0|[1-9][0-9]*)")


def _log_factorial(n):
    while len(_LOG_FACTORIAL) <= n:
        _LOG_FACTORIAL.append(_LOG_FACTORIAL[-1] + math.log(len(_LOG_FACTORIAL)))
    return _LOG_FACTORIAL[n]


def _count(value, what):
    if type(value) is not int:  # bool is a subclass of int and is refused here
        raise MalformedPreservation("%s must be an integer count" % what)
    return value


def _marginal(k, n, family_size):
    _count(k, "k"), _count(n, "n")
    if type(family_size) is not int or family_size < 1:
        raise MalformedPreservation("family size must be a positive integer")
    if not (n >= 1 and 0 <= k <= n):
        raise MalformedPreservation("invalid marginal: k=%r n=%r" % (k, n))


def _fraction(num, den):
    if type(num) is not int or type(den) is not int or den < 1:
        raise MalformedPreservation("a threshold must be an integer numerator over a positive integer denominator")


# --- Display values. Double precision, for a reader of the receipt. No decision uses them. ---

def cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p), summed from its largest term downwards. Display only."""
    if k < 0:
        return 0.0
    if k >= n or p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    if k >= n * p:
        return max(0.0, 1.0 - cdf(n - k - 1, n, 1.0 - p))
    term = math.exp(_log_factorial(n) - _log_factorial(k) - _log_factorial(n - k)
                    + k * math.log(p) + (n - k) * math.log1p(-p))
    first = term
    total = term
    ratio = (1.0 - p) / p
    for j in range(k, 0, -1):
        term *= j / (n - j + 1) * ratio
        total += term
        if term < first * 1e-17:
            break
    return min(1.0, total)


def lower(k, n, tail):
    """Clopper-Pearson lower bound at one-sided tail `tail` for k events in n observations. Display only."""
    _count(k, "k"), _count(n, "n")
    if not (n >= 1 and 0 <= k <= n and 0.0 < tail < 0.5):
        raise MalformedPreservation("invalid marginal: k=%r n=%r tail=%r" % (k, n, tail))
    if k == 0:
        return 0.0
    if k == n:
        return tail ** (1.0 / n)
    failures = n - k
    lo, hi = 0.0, 1.0
    for _ in range(BISECTIONS):
        p = (lo + hi) / 2.0
        if cdf(failures, n, p) > tail:
            lo = p
        else:
            hi = p
    return 1.0 - (lo + hi) / 2.0


def upper(k, n, tail):
    """Clopper-Pearson upper bound at one-sided tail `tail`. Display only."""
    _count(k, "k"), _count(n, "n")
    if not (n >= 1 and 0 <= k <= n):
        raise MalformedPreservation("invalid marginal: k=%r n=%r" % (k, n))
    return 1.0 - lower(n - k, n, tail)


# --- Decisions. Integers only. ---

def _tail_sign(k, n, a, d, family_size):
    """Sign of P(X >= k) - 1/(40 * family_size) for X ~ Binomial(n, a/d), 1 <= k <= n, 0 < a < d.

    Every term is an integer over the common denominator d**n, each made from its neighbour by
    an exact integer step. The sum starts at k or at the mode, whichever is later, so that terms
    only shrink from there, and it stops as soon as the sign is settled: what has been summed
    already exceeds the tail, or (when nothing below the start is missing) what has been summed
    plus a geometric bound on everything still to come falls short of it. If neither happens the
    sum runs to the end and the totals are compared. Stopping early changes the cost, never the sign.
    """
    b = d - a
    whole = d ** n
    scale = TAIL_DIVISOR * family_size
    mode = max(0, (n * a - b) // d + 1)  # the first j with term j+1 smaller than term j
    start = max(k, mode)
    first = math.comb(n, start) * a ** start * b ** (n - start)
    term = total = first
    j = start
    while True:
        if scale * total > whole:
            return 1
        if j == n:
            break
        if start == k:
            # Later terms shrink at least as fast as this ratio, so they sum to at most term * rest_num / rest_den.
            rest_num = (n - j) * a
            rest_den = (j + 1) * b - rest_num
            if scale * (total * rest_den + term * rest_num) < whole * rest_den:
                return -1
        term = term * ((n - j) * a) // ((j + 1) * b)
        total += term
        j += 1
    term = first
    for j in range(start - 1, k - 1, -1):  # the terms below the mode, when the upper ones did not settle it
        term = term * ((j + 1) * b) // ((n - j) * a)
        total += term
        if scale * total > whole:
            return 1
    scaled = scale * total
    return (scaled > whole) - (scaled < whole)


def compare_lower(k, n, family_size, num, den):
    """Sign of (lower bound of k in n) - num/den: 1, 0 or -1, exactly. None above MAX_OBSERVATIONS."""
    _marginal(k, n, family_size), _fraction(num, den)
    if n > MAX_OBSERVATIONS:
        return None
    if k == 0:  # the bound is exactly 0
        return (num < 0) - (num > 0)
    if num <= 0:  # the bound is above 0
        return 1
    if num >= den:  # and below 1
        return -1
    g = math.gcd(num, den)
    return -_tail_sign(k, n, num // g, den // g, family_size)


def compare_upper(k, n, family_size, num, den):
    """Sign of (upper bound of k in n) - num/den, exactly. None above MAX_OBSERVATIONS."""
    _marginal(k, n, family_size), _fraction(num, den)
    sign = compare_lower(n - k, n, family_size, den - num, den)
    return None if sign is None else -sign


def _shortcut(estimates, num, den, max_halvings):
    """A point (x, scale) worth asking about first, from two display bounds, or None.

    The point lies between where the first bound and (threshold - second bound) are thought to be,
    with no more binary places than the gap needs. It only chooses where to look. It has at most
    max_halvings - 2 binary places, and the fixed schedule reaches every such point that separates
    the bounds, so asking here first never certifies anything the schedule alone would not.
    """
    if (not isinstance(estimates, (list, tuple)) or len(estimates) != 2
            or not all(type(value) is float and math.isfinite(value) for value in estimates)):
        return None
    target = num / den - estimates[1]
    gap, middle = abs(estimates[0] - target), (estimates[0] + target) / 2.0
    if not gap > 0.0 or not -1.0 < middle < 3.0:
        return None
    places = 0
    while places <= SHORTCUT_PLACES and 2.0 ** -places > gap / 4.0:
        places += 1
    if places > min(SHORTCUT_PLACES, max_halvings - 2):
        return None
    return math.floor(middle * 2.0 ** places + 0.5), 2 ** places


def compare_lower_sum(ka, na, kb, nb, family_size, num, den, max_halvings=MAX_HALVINGS, estimates=None):
    """Sign of (lower bound of ka in na) + (lower bound of kb in nb) - num/den, for 0 <= num/den <= 2.

    1, 0 or -1 when a point separates the two bounds from the threshold; None when `max_halvings`
    midpoints separate nothing or a count is above MAX_OBSERVATIONS.

    `estimates`, when given, is the two bounds as display floats. They buy speed and nothing else:
    one point between them is asked first, with the same two exact questions as any midpoint, and
    if it does not separate the bounds the fixed schedule runs as if no estimate had been given.
    Wrong, swapped or non-finite estimates therefore cannot change the answer.
    """
    _marginal(ka, na, family_size), _marginal(kb, nb, family_size), _fraction(num, den)
    if not 0 <= num <= 2 * den or type(max_halvings) is not int or max_halvings < 0:
        raise MalformedPreservation("the sum threshold must lie in [0, 2] and the halving limit must be a count")
    if na > MAX_OBSERVATIONS or nb > MAX_OBSERVATIONS:
        return None
    if ka == 0:
        return compare_lower(kb, nb, family_size, num, den)
    if kb == 0:
        return compare_lower(ka, na, family_size, num, den)

    def ask(x, scale):
        """At p = x / scale: 1, 0 or -1 if p separates the bounds, else which side of both p lies on."""
        first = compare_lower(ka, na, family_size, x, scale)
        second = compare_lower(kb, nb, family_size, num * scale - x * den, den * scale)
        if first >= 0 and second >= 0:
            return 1 if first + second > 0 else 0
        if first <= 0 and second <= 0:
            return -1
        return "below" if first > 0 else "above"

    point = _shortcut(estimates, num, den, max_halvings)
    if point is not None:
        answer = ask(*point)
        if answer not in ("below", "above"):
            return answer
    # p = x / scale. `lo` is below both the first bound and (threshold - second bound); `hi` is above both.
    lo, hi, scale = -1, 3, 1
    for _ in range(max_halvings):
        if (hi - lo) % 2:
            lo, hi, scale = 2 * lo, 2 * hi, 2 * scale
        mid = (lo + hi) // 2
        answer = ask(mid, scale)
        if answer == "below":
            lo = mid
        elif answer == "above":
            hi = mid
        else:
            return answer
    return None


def _all(values):
    values = list(values)
    if any(v is False for v in values):
        return False
    return None if any(v is None for v in values) else True


def _any(values):
    values = list(values)
    if any(v is True for v in values):
        return True
    return None if any(v is None for v in values) else False


def _is(sign, test):
    return None if sign is None else test(sign)


def _state(opposes, supports):
    """One label from the two three-valued facts. A certain opposition is read first."""
    if opposes is True:
        return "opposes"
    if opposes is None:
        return "numerically_unresolved"
    if supports is True:
        return "supports"
    return "numerically_unresolved" if supports is None else "unresolved"


def _names(values, what, minimum=1):
    if not isinstance(values, (list, tuple)) or len(values) < minimum:
        raise MalformedPreservation("inventory %s must list at least %d name(s)" % (what, minimum))
    for value in values:
        # A canonical integer string would become an integer array key in PHP and stay a string
        # here; refusing it on both sides keeps the two parsers identical.
        if not isinstance(value, str) or value == "" or _BARE_INTEGER.fullmatch(value):
            raise MalformedPreservation("inventory %s holds a name that is empty, not a string, or a bare integer" % what)
    if len(set(values)) != len(values):
        raise MalformedPreservation("inventory %s repeats a name" % what)
    return list(values)


def inventory_family(inventory):
    """(readers, forms, error endpoints) of a frozen inventory, validated. Order is preserved."""
    if not isinstance(inventory, dict) or set(inventory) != {"readers", "forms", "error_endpoints"}:
        raise MalformedPreservation("inventory must hold exactly readers, forms and error_endpoints")
    return (_names(inventory["readers"], "readers"), _names(inventory["forms"], "forms"),
            _names(inventory["error_endpoints"], "error_endpoints"))


def family_size(inventory):
    """M: every binomial quantity the inventory fixes before outcomes.

    An error endpoint is one separately elicited question on one arm, so a design with two error
    families on both arms lists four. This function counts what the inventory names; refusing an
    inventory whose error families are not on both arms is the mint's job, where each endpoint
    carries its family and its arm.
    """
    readers, forms, endpoints = inventory_family(inventory)
    return len(readers) * len(forms) * (2 + len(endpoints))


def family_tail(m):
    """The marginal tail 0.05 / (2M), as a float for display."""
    if type(m) is not int or m < 1:
        raise MalformedPreservation("family size must be a positive integer")
    return FAMILY_ALPHA / (2 * m)


def tail_fraction(m):
    """The same tail as the exact fraction every decision uses: 0.05 / (2M) = 1 / (40M)."""
    family_tail(m)
    return "1/%d" % (TAIL_DIVISOR * m)


def minimum_per_cell(m):
    """Smallest n at which an all-correct, zero-error cell supports the profile for family M. Exact."""
    family_tail(m)
    margin_num, margin_den = EXACT_DELTA_MARGIN
    n = 1
    while not (compare_lower(n, n, m, margin_den - margin_num, margin_den) >= 0
               and compare_lower(n, n, m, *EXACT_ACCURACY_FLOOR) >= 0
               and compare_upper(0, n, m, *EXACT_ERROR_CAP) <= 0):
        n += 1
        if n > MAX_OBSERVATIONS:
            raise MalformedPreservation("no cell of at most %d observations can support a family of %d" % (MAX_OBSERVATIONS, m))
    return n


def _pair(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MalformedPreservation("%s must be [events, observations]" % what)
    k, n = _count(value[0], what), _count(value[1], what)
    if n < 1 or not 0 <= k <= n:
        raise MalformedPreservation("%s has counts outside 0 <= events <= observations, observations >= 1" % what)
    return k, n


def _quantity(k, n, tail):
    """Counts with their display bounds; no bound is computed above MAX_OBSERVATIONS."""
    shown = n <= MAX_OBSERVATIONS
    return {"events": k, "observations": n, "lower": lower(k, n, tail) if shown else None,
            "upper": upper(k, n, tail) if shown else None}


def evaluate(inventory, cells, max_halvings=MAX_HALVINGS):
    """Bounds, decisions and verdict for every cell of a complete family. Raises MalformedPreservation otherwise.

    `cells` is a list of {reader, form, marked: [k, n], english: [k, n], errors: {endpoint: [k, n]}}
    holding exactly one cell for every reader and form of the inventory and exactly the inventory's
    error endpoints in each. A missing, duplicated or extra cell or endpoint is malformed: the
    family is never shrunk to what arrived.

    Each cell carries `decisions`, the six inequalities of the rule as True, False or None
    (undecided), and a verdict of supports, opposes, unresolved or numerically_unresolved.
    """
    readers, forms, endpoints = inventory_family(inventory)
    m = len(readers) * len(forms) * (2 + len(endpoints))
    tail = family_tail(m)
    if not isinstance(cells, (list, tuple)):
        raise MalformedPreservation("cells must be a list")
    by_identity = {}
    for cell in cells:
        if not isinstance(cell, dict) or set(cell) != {"reader", "form", "marked", "english", "errors"}:
            raise MalformedPreservation("a cell must hold exactly reader, form, marked, english and errors")
        identity = (cell["reader"], cell["form"])
        if not all(isinstance(part, str) for part in identity):
            raise MalformedPreservation("a cell's reader and form must be strings")
        if identity in by_identity:
            raise MalformedPreservation("reader %r form %r appears twice" % identity)
        by_identity[identity] = cell
    expected = [(reader, form) for reader in readers for form in forms]
    if set(by_identity) != set(expected):
        raise MalformedPreservation("the cells do not cover exactly the inventory's readers and forms")
    counted = []
    for reader, form in expected:
        cell = by_identity[(reader, form)]
        errors = cell["errors"]
        if not isinstance(errors, dict) or set(errors) != set(endpoints):
            raise MalformedPreservation("reader %r form %r does not hold exactly the inventory's error endpoints" % (reader, form))
        counted.append((reader, form, _pair(cell["marked"], "marked"), _pair(cell["english"], "english"),
                        [(endpoint, _pair(errors[endpoint], "error endpoint %r" % endpoint)) for endpoint in endpoints]))
    floor, cap = EXACT_ACCURACY_FLOOR, EXACT_ERROR_CAP
    margin_num, margin_den = EXACT_DELTA_MARGIN
    out, cell_opposes, cell_supports = [], [], []
    for reader, form, (mk, mn), (ek, en), error_counts in counted:
        marked, english = _quantity(mk, mn, tail), _quantity(ek, en, tail)
        error_rows = {endpoint: _quantity(k, n, tail) for endpoint, (k, n) in error_counts}
        shown = None not in (marked["lower"], english["lower"])
        supports = {
            "accuracy_floor": _all(_is(compare_lower(k, n, m, *floor), lambda s: s >= 0) for k, n in ((mk, mn), (ek, en))),
            "error_cap": _all(_is(compare_upper(k, n, m, *cap), lambda s: s <= 0) for _, (k, n) in error_counts),
            # L(marked) - U(English) >= -margin, written on two lower bounds.
            "delta_margin": _is(compare_lower_sum(mk, mn, en - ek, en, m, margin_den - margin_num, margin_den, max_halvings,
                                                  (marked["lower"], 1.0 - english["upper"]) if shown else None), lambda s: s >= 0),
        }
        opposes = {
            "accuracy_floor": _any(_is(compare_upper(k, n, m, *floor), lambda s: s < 0) for k, n in ((mk, mn), (ek, en))),
            "error_cap": _any(_is(compare_lower(k, n, m, *cap), lambda s: s > 0) for _, (k, n) in error_counts),
            # U(marked) - L(English) < -margin. It cannot hold when the supporting delta test does.
            "delta_margin": False if supports["delta_margin"] is True else _is(
                compare_lower_sum(mn - mk, mn, ek, en, m, margin_den + margin_num, margin_den, max_halvings,
                                  (1.0 - marked["upper"], english["lower"]) if shown else None), lambda s: s > 0),
        }
        cell_opposes.append(_any(opposes.values()))
        cell_supports.append(_all(supports.values()))
        out.append({"reader": reader, "form": form, "marked": marked, "english": english, "errors": error_rows,
                    "delta_lower": marked["lower"] - english["upper"] if shown else None,
                    "delta_upper": marked["upper"] - english["lower"] if shown else None,
                    "decisions": {"supports": supports, "opposes": opposes},
                    "verdict": _state(cell_opposes[-1], cell_supports[-1])})
    return {"analysis": ANALYSIS, "family_size": m, "tail": tail, "tail_fraction": tail_fraction(m),
            "precision": {"decisions": "exact integer comparison of binomial tails; no tolerance",
                          "max_halvings": max_halvings, "max_observations": MAX_OBSERVATIONS,
                          "bounds": "double precision, for display; never read by a decision"},
            "cells": out, "profile_state": _state(_any(cell_opposes), _all(cell_supports))}


def token_benefit(forms, token_means, token_form_means):
    """True only when every encoding saves at least one token overall and in every form.

    `token_means` maps each encoding to the mean of (marked - English) tokens, so a saving is
    negative; `token_form_means` maps each form to the same mapping. A missing or extra encoding,
    a missing form, a boolean, a non-finite value or any mean above -1 is no demonstrated benefit.
    """
    def row_ok(row):
        if not isinstance(row, dict) or set(row) != set(TOKEN_ENCODINGS):
            return False
        for value in row.values():
            if type(value) not in (int, float) or not math.isfinite(value) or value > -MIN_TOKEN_SAVING:
                return False
        return True

    if not row_ok(token_means):
        return False
    if not isinstance(token_form_means, dict) or set(token_form_means) != set(forms) or not forms:
        return False
    return all(row_ok(row) for row in token_form_means.values())


def outcome(inventory, cells, *, prospective, unique_worlds, confirmed, fresh_replica_pass, confirmed_loss,
            matched_comparator, token_means, token_form_means, all_other_promises):
    """One outcome name for a whole evidence situation, in the reference checker's order.

    Every keyword is required. Each is a fact the SERVICE derives at its own boundary (the mint's
    identity and reviewed declarations, the sampling review, settlement, the standing loss veto,
    the token carrier, the proposal's other promises); none is a field a submitter fills in, and
    none has a default, because a default would be a pass nobody checked. A missing fact, or one
    that is not a plain boolean, raises ContextRefused. `token_means` and `token_form_means` may
    be None, which is read as no demonstrated benefit.

    This function only fixes the order in which the facts are read, so a real failure is never
    hidden behind a merely unfinished one. A profile that is numerically unresolved is reported
    as such only when nothing earlier in the order applies.
    """
    flags = {"prospective": prospective, "unique_worlds": unique_worlds, "confirmed": confirmed,
             "fresh_replica_pass": fresh_replica_pass, "confirmed_loss": confirmed_loss,
             "matched_comparator": matched_comparator, "all_other_promises": all_other_promises}
    for name in CONTEXT_FLAGS:
        if type(flags[name]) is not bool:
            raise ContextRefused("context fact %r must be True or False, got %r" % (name, flags[name]))
    if not prospective:
        return LEGACY_UNCHANGED
    if not unique_worlds:
        return INVALID_SAMPLING
    try:
        result = evaluate(inventory, cells)
        forms = inventory_family(inventory)[1]
    except MalformedPreservation:
        return REFUSE_MALFORMED
    if confirmed_loss:
        return CONFIRMED_LOSS_VETO
    if result["profile_state"] == "opposes":
        return OPPOSES_PROFILE
    if not confirmed or not fresh_replica_pass:
        return UNCONFIRMED
    if not matched_comparator:
        return COMPARATOR_MISMATCH
    if not token_benefit(forms, token_means, token_form_means):
        return NO_DEMONSTRATED_BENEFIT
    if not all_other_promises:
        return OTHER_PROMISES_INCOMPLETE
    return {"supports": SATISFIED, "unresolved": UNRESOLVED,
            "numerically_unresolved": NUMERICALLY_UNRESOLVED}[result["profile_state"]]
