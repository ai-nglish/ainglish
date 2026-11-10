"""Exact-binomial preservation profile: the numeric core (protocol row a-9mvh2ph6g1fnw0a1).

This module is arithmetic and nothing else. It reads counts that a caller has already
reconstructed from a frozen inventory and says what the profile `exact-binomial-preservation-v1`
makes of them. It does not parse a contract, mint an attempt, replay a journal or settle a
replication; those boundaries are separate and none of them is wired to this module yet.

The rule, from the row:

* The family is every binomial quantity fixed BEFORE outcomes: for each reader and form, marked
  accuracy, careful-English accuracy and each separately elicited semantic-error endpoint. Its
  size M comes from the frozen inventory, never from the cells that happened to arrive.
* Each marginal Clopper-Pearson bound is taken at tail t = 0.05 / (2M).
* A cell SUPPORTS when the delta lower bound is at least -0.05, both accuracy lower bounds are at
  least 0.90 and every error upper bound is at most 0.05. It OPPOSES when the delta upper bound is
  below -0.05, an accuracy upper bound is below 0.90 or an error lower bound is above 0.05.
  Otherwise it is UNRESOLVED. One opposing cell opposes the profile; every cell must support for
  the profile to support.

Parity. The register evaluates the same counts in PHP (`ExactBinomialPreservation`). Both sides
use this bisection in double precision, and both read one shared vector file. A bound closer than
`TIE_BAND` to its threshold satisfies neither inequality, so a last-bit difference between two
math libraries can leave a cell unresolved but can never turn one verdict into another.
"""
from __future__ import annotations

import math

ANALYSIS = "exact-binomial-preservation-v1"
FAMILY_ALPHA = 0.05
DELTA_MARGIN = 0.05
ACCURACY_FLOOR = 0.90
ERROR_CAP = 0.05
TIE_BAND = 1e-9
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


class MalformedPreservation(ValueError):
    """The inventory or the counts are not a complete, well-formed family. Never a null result."""


_LOG_FACTORIAL = [0.0, 0.0]


def _log_factorial(n):
    while len(_LOG_FACTORIAL) <= n:
        _LOG_FACTORIAL.append(_LOG_FACTORIAL[-1] + math.log(len(_LOG_FACTORIAL)))
    return _LOG_FACTORIAL[n]


def _count(value, what):
    if type(value) is not int:  # bool is a subclass of int and is refused here
        raise MalformedPreservation("%s must be an integer count" % what)
    return value


def cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p), summed from its largest term downwards."""
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
    """Clopper-Pearson lower bound at one-sided tail `tail` for k events in n observations."""
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
    """Clopper-Pearson upper bound at one-sided tail `tail`."""
    _count(k, "k"), _count(n, "n")
    if not (n >= 1 and 0 <= k <= n):
        raise MalformedPreservation("invalid marginal: k=%r n=%r" % (k, n))
    return 1.0 - lower(n - k, n, tail)


def _names(values, what, minimum=1):
    if not isinstance(values, (list, tuple)) or len(values) < minimum:
        raise MalformedPreservation("inventory %s must list at least %d name(s)" % (what, minimum))
    for value in values:
        if not isinstance(value, str) or value == "":
            raise MalformedPreservation("inventory %s holds a name that is not a non-empty string" % what)
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
    """M: every binomial quantity the inventory fixes before outcomes."""
    readers, forms, endpoints = inventory_family(inventory)
    return len(readers) * len(forms) * (2 + len(endpoints))


def family_tail(m):
    """The marginal tail 0.05 / (2M)."""
    if type(m) is not int or m < 1:
        raise MalformedPreservation("family size must be a positive integer")
    return FAMILY_ALPHA / (2 * m)


def tail_fraction(m):
    """The same tail as an exact fraction, for receipts: 0.05 / (2M) = 1 / (40M)."""
    family_tail(m)
    return "1/%d" % (40 * m)


def minimum_per_cell(m):
    """Smallest n at which an all-correct, zero-error cell can support the profile for family M."""
    tail = family_tail(m)
    n = 1
    while tail ** (1.0 / n) - 1.0 < -DELTA_MARGIN + TIE_BAND or tail ** (1.0 / n) < ACCURACY_FLOOR + TIE_BAND:
        n += 1
    return n


def _pair(value, what):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MalformedPreservation("%s must be [events, observations]" % what)
    k, n = _count(value[0], what), _count(value[1], what)
    if n < 1 or not 0 <= k <= n:
        raise MalformedPreservation("%s has counts outside 0 <= events <= observations, observations >= 1" % what)
    return k, n


def evaluate(inventory, cells):
    """Bounds and verdict for every cell of a complete family. Raises MalformedPreservation otherwise.

    `cells` is a list of {reader, form, marked: [k, n], english: [k, n], errors: {endpoint: [k, n]}}
    holding exactly one cell for every reader and form of the inventory and exactly the inventory's
    error endpoints in each. A missing, duplicated or extra cell or endpoint is malformed: the
    family is never shrunk to what arrived.
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
    out = []
    for reader, form in expected:
        cell = by_identity[(reader, form)]
        errors = cell["errors"]
        if not isinstance(errors, dict) or set(errors) != set(endpoints):
            raise MalformedPreservation("reader %r form %r does not hold exactly the inventory's error endpoints" % (reader, form))
        mk, mn = _pair(cell["marked"], "marked")
        ek, en = _pair(cell["english"], "english")
        marked = {"events": mk, "observations": mn, "lower": lower(mk, mn, tail), "upper": upper(mk, mn, tail)}
        english = {"events": ek, "observations": en, "lower": lower(ek, en, tail), "upper": upper(ek, en, tail)}
        error_rows = {}
        for endpoint in endpoints:
            k, n = _pair(errors[endpoint], "error endpoint %r" % endpoint)
            error_rows[endpoint] = {"events": k, "observations": n, "lower": lower(k, n, tail), "upper": upper(k, n, tail)}
        delta_lower = marked["lower"] - english["upper"]
        delta_upper = marked["upper"] - english["lower"]
        supports = (delta_lower >= -DELTA_MARGIN + TIE_BAND
                    and min(marked["lower"], english["lower"]) >= ACCURACY_FLOOR + TIE_BAND
                    and all(row["upper"] <= ERROR_CAP - TIE_BAND for row in error_rows.values()))
        opposes = (delta_upper < -DELTA_MARGIN - TIE_BAND
                   or min(marked["upper"], english["upper"]) < ACCURACY_FLOOR - TIE_BAND
                   or any(row["lower"] > ERROR_CAP + TIE_BAND for row in error_rows.values()))
        out.append({"reader": reader, "form": form, "marked": marked, "english": english, "errors": error_rows,
                    "delta_lower": delta_lower, "delta_upper": delta_upper,
                    "verdict": "opposes" if opposes else ("supports" if supports else "unresolved")})
    verdicts = [row["verdict"] for row in out]
    state = "opposes" if "opposes" in verdicts else ("supports" if all(v == "supports" for v in verdicts) else "unresolved")
    return {"analysis": ANALYSIS, "family_size": m, "tail": tail, "tail_fraction": tail_fraction(m),
            "cells": out, "profile_state": state}


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


def outcome(inventory, cells, *, prospective=True, unique_worlds=True, confirmed=True, fresh_replica_pass=True,
            confirmed_loss=False, matched_comparator=True, token_means=None, token_form_means=None,
            all_other_promises=True):
    """One outcome name for a whole evidence situation, in the reference checker's order.

    The keyword facts are supplied by the caller's own boundaries (mint identity, sampling review,
    settlement, the standing loss veto, the token carrier). This function only fixes the order in
    which they are read, so a real failure is never hidden behind a merely unfinished one.
    """
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
    return SATISFIED if result["profile_state"] == "supports" else UNRESOLVED
