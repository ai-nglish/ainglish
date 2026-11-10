"""Write tests/fixtures/exact-binomial-preservation-v1.json: the vectors both implementations read.

The bounds here are NOT produced by `ainglish.preservation`. They come from a separately written
inversion in 60-digit decimal arithmetic, so the file can catch an error in the double-precision
bisection instead of echoing it. The cases are 27 of the 28 synthetic acceptance cases of the
pack published for protocol row a-9mvh2ph6g1fnw0a1 on 2026-11-10 (its non-finite token case has no
JSON spelling and is tested in code), re-expressed with named readers, forms and endpoints, plus
five named-identity and boundary cases; their expected outcomes are written down here by
hand from the pack, never computed by the module under test.

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
ENCODINGS = ("cl100k_base", "o200k_base", "p50k_base")
INVENTORY = {"readers": ["reader-a", "reader-b"], "forms": ["form-a", "form-b"],
             "error_endpoints": ["false-authority", "false-polarity"]}


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


def bank(n=512):
    return [{"reader": r, "form": f, "marked": [n, n], "english": [n, n],
             "errors": {e: [0, n] for e in INVENTORY["error_endpoints"]}}
            for r in INVENTORY["readers"] for f in INVENTORY["forms"]]


def means(values):
    return dict(zip(ENCODINGS, values))


DEFAULT_CONTEXT = {"prospective": True, "unique_worlds": True, "confirmed": True, "fresh_replica_pass": True,
                   "confirmed_loss": False, "matched_comparator": True, "all_other_promises": True,
                   "token_means": means((-2, -2, -2)),
                   "token_form_means": {"form-a": means((-2, -2, -2)), "form-b": means((-2, -2, -2))}}


def cases():
    out = []

    def case(name, cells, expected, source="acceptance-pack-2026-11-10", **context):
        merged = copy.deepcopy(DEFAULT_CONTEXT)
        merged.update(context)
        out.append({"name": name, "source": source, "cells": cells, "context": merged, "expected": expected})

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
    return out


def inventory_cases():
    """Inventories both parsers must judge alike. A bare-integer name is refused because PHP would
    turn it into an integer array key; a JSON list of endpoints must never pass for a mapping."""
    good = INVENTORY
    rows = [("the-shared-inventory", good, True, 16),
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
        # The two values the acceptance pack publishes for the 512-observation ceiling at tail 1/640.
        t640 = Decimal(1) / Decimal(640)
        assert abs(decimal_lower(512, 512, t640) - Decimal("0.9874592439262977")) < Decimal("1e-15")
        assert abs(decimal_upper(0, 512, t640) - Decimal("0.012540756073702308")) < Decimal("1e-15")
    report = {"kind": "ainglish.exact-binomial-preservation-vectors.v1", "analysis": "exact-binomial-preservation-v1",
              "generated_by": {"tool": "tools/gen_preservation_vectors.py",
                               "tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                               "method": "decimal bisection of the exact binomial tail, %d digits, %d halvings; "
                                         "independent of ainglish.preservation" % (PRECISION, HALVINGS),
                               "python": "%d.%d" % sys.version_info[:2]},
              "note": "Synthetic software vectors. Not language evidence, not a measurement, not a profile pass.",
              "bound_tolerance": "1e-10", "tie_band": "1e-9", "token_encodings": list(ENCODINGS),
              "bounds": bounds, "minimum_per_cell": minima, "inventory": INVENTORY, "inventory_cases": inventory_cases(),
              "cases": cases()}
    OUT.write_text(json.dumps(report, indent=1, allow_nan=False) + "\n", encoding="utf-8")
    print("wrote", OUT.relative_to(ROOT), "|", len(bounds), "bounds |", len(report["cases"]), "cases |", hashlib.sha256(OUT.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
