"""The exact-binomial preservation core against the shared vector file (protocol row a-9mvh2ph6g1fnw0a1).

Every expectation in the vector file comes from arithmetic written apart from the module: decimal
bisection for the bounds, integer tail sums for a bound against a threshold, the halving schedule
followed in decimals for two bounds against each other (see tools/gen_preservation_vectors.py).
"""
import copy
import hashlib
import json
import math
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

from ainglish import preservation as P

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "exact-binomial-preservation-v1.json"
VECTORS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def tail_of(text):
    fraction = Fraction(text)
    return fraction.numerator / fraction.denominator


def case_named(name):
    return next(c for c in VECTORS["cases"] if c["name"] == name)


def sum_arguments(row):
    """A delta case as the arguments of compare_lower_sum, and the sign that means the rule holds."""
    (mk, mn), (ek, en), m = row["marked"], row["english"], row["family_size"]
    if row["rule"] == "supports":  # L(marked) - U(English) >= -1/20
        return (mk, mn, en - ek, en, m, 19, 20), (lambda sign: sign >= 0)
    return (mn - mk, mn, ek, en, m, 21, 20), (lambda sign: sign > 0)  # U(marked) - L(English) < -1/20


class DisplayBoundTests(unittest.TestCase):
    """The floating-point bounds a receipt shows. No decision reads them."""

    def test_bounds_match_the_independent_high_precision_inversion(self):
        tolerance = float(VECTORS["bound_tolerance"])
        self.assertGreater(len(VECTORS["bounds"]), 90)
        for row in VECTORS["bounds"]:
            tail = tail_of(row["tail"])
            self.assertAlmostEqual(P.lower(row["k"], row["n"], tail), float(row["lower"]), delta=tolerance, msg=row)
            self.assertAlmostEqual(P.upper(row["k"], row["n"], tail), float(row["upper"]), delta=tolerance, msg=row)

    def test_ceiling_and_floor_bounds_stay_finite(self):
        tail = 1 / 640
        self.assertEqual(P.lower(0, 512, tail), 0.0)
        self.assertEqual(P.upper(512, 512, tail), 1.0)
        self.assertLess(P.lower(512, 512, tail), 1.0)
        self.assertGreater(P.upper(0, 512, tail), 0.0)
        self.assertAlmostEqual(P.lower(512, 512, tail), 0.9874592439262977, delta=1e-14)
        self.assertAlmostEqual(P.upper(0, 512, tail), 0.012540756073702308, delta=1e-14)

    def test_bounds_bracket_the_observed_rate(self):
        for n in (1, 7, 19, 64):
            for k in range(n + 1):
                for tail in (0.025, 1 / 640):
                    self.assertTrue(0.0 <= P.lower(k, n, tail) <= k / n <= P.upper(k, n, tail) <= 1.0, (k, n, tail))

    def test_malformed_marginals_are_refused(self):
        for k, n, tail in ((True, 2, 0.01), (1.0, 2, 0.01), (-1, 2, 0.01), (3, 2, 0.01), (0, 0, 0.01), (1, 2, 0.0), (1, 2, 0.5)):
            with self.assertRaises(P.MalformedPreservation, msg=(k, n, tail)):
                P.lower(k, n, tail)

    def test_cdf_matches_direct_summation(self):
        for n in (3, 9, 20):
            for p in (0.01, 0.17, 0.5, 0.83, 0.99):
                for k in range(n):
                    direct = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k + 1))
                    self.assertAlmostEqual(P.cdf(k, n, p), direct, delta=1e-12)


class ThresholdTests(unittest.TestCase):
    """A bound against a fixed threshold, decided in integers."""

    def test_every_threshold_case_has_the_sign_of_the_integer_tail_sum(self):
        self.assertGreater(len(VECTORS["threshold_cases"]), 90)
        for row in VECTORS["threshold_cases"]:
            t = Fraction(row["threshold"])
            compare = P.compare_lower if row["bound"] == "lower" else P.compare_upper
            self.assertEqual(compare(row["k"], row["n"], row["family_size"], t.numerator, t.denominator), row["sign"], row)

    def test_the_vector_file_holds_true_boundaries_and_both_sides_of_them(self):
        signs = [row["sign"] for row in VECTORS["threshold_cases"]]
        self.assertEqual(signs.count(0), 4)
        self.assertGreater(signs.count(1), 20)
        self.assertGreater(signs.count(-1), 20)

    def test_a_bound_exactly_on_its_threshold_is_neither_above_nor_below(self):
        # 2 of 2 at a family of 10: the lower bound is (1/400)^(1/2) = 1/20, the error cap itself.
        self.assertEqual(P.compare_lower(2, 2, 10, 1, 20), 0)
        # 0 of 3 at a family of 25: the upper bound is 1 - (1/1000)^(1/3) = 9/10, the accuracy floor itself.
        self.assertEqual(P.compare_upper(0, 3, 25, 9, 10), 0)
        # In the rule: "above the cap" opposes, and equal is not above. One endpoint more or less in the
        # family moves the tail and with it the answer.
        cell = {"reader": "r", "form": "f", "marked": [2, 2], "english": [2, 2]}
        at_ten = {"readers": ["r", "s"], "forms": ["f"], "error_endpoints": ["e1", "e2", "e3"]}
        at_eight = {"readers": ["r", "s"], "forms": ["f"], "error_endpoints": ["e1", "e2"]}
        self.assertEqual((P.family_size(at_ten), P.family_size(at_eight)), (10, 8))
        for inventory, expected in ((at_ten, False), (at_eight, True)):
            cells = [dict(cell, reader=reader, errors={e: [2, 2] if e == "e1" else [0, 2] for e in inventory["error_endpoints"]})
                     for reader in inventory["readers"]]
            result = P.evaluate(inventory, cells)
            self.assertIs(result["cells"][0]["decisions"]["opposes"]["error_cap"], expected, inventory)
            self.assertEqual(result["profile_state"], "opposes" if expected else "unresolved")
        # The floor likewise: "below the floor" opposes, and an upper bound of exactly 9/10 is not below it.
        at_twenty_five = {"readers": ["r1", "r2", "r3", "r4", "r5"], "forms": ["f"], "error_endpoints": ["e1", "e2", "e3"]}
        # A smaller family has a larger tail and so a tighter bound: at 20 the same upper bound is below 9/10.
        at_twenty = dict(at_twenty_five, readers=["r1", "r2", "r3", "r4"])
        self.assertEqual((P.family_size(at_twenty_five), P.family_size(at_twenty)), (25, 20))
        for inventory, expected in ((at_twenty_five, False), (at_twenty, True)):
            cells = [{"reader": reader, "form": "f", "marked": [0, 3], "english": [3, 3], "errors": {e: [0, 3] for e in inventory["error_endpoints"]}}
                     for reader in inventory["readers"]]
            result = P.evaluate(inventory, cells)
            self.assertIs(result["cells"][0]["decisions"]["opposes"]["accuracy_floor"], expected, inventory)
            self.assertEqual(result["profile_state"], "opposes" if expected else "unresolved")

    def test_thresholds_outside_the_unit_interval_and_empty_counts_need_no_arithmetic(self):
        self.assertEqual([P.compare_lower(0, 5, 3, num, 7) for num in (-1, 0, 1)], [1, 0, -1])
        self.assertEqual([P.compare_lower(3, 5, 3, num, 7) for num in (-1, 0, 7, 9)], [1, 1, -1, -1])
        self.assertEqual([P.compare_upper(5, 5, 3, num, 7) for num in (6, 7, 8)], [1, 0, -1])
        self.assertEqual([P.compare_upper(2, 5, 3, num, 7) for num in (-1, 0, 7, 9)], [1, 1, -1, -1])

    def test_a_reducible_threshold_is_the_same_threshold(self):
        for k in range(0, 21):
            self.assertEqual(P.compare_lower(k, 20, 16, 9, 10), P.compare_lower(k, 20, 16, 900, 1000))
            self.assertEqual(P.compare_upper(k, 20, 16, 1, 20), P.compare_upper(k, 20, 16, 64, 1280))

    def test_counts_above_the_ceiling_are_not_compared(self):
        top = P.MAX_OBSERVATIONS
        self.assertEqual(top, VECTORS["precision"]["max_observations"])
        self.assertEqual(P.compare_lower(top, top, 16, 9, 10), 1)
        self.assertIsNone(P.compare_lower(top + 1, top + 1, 16, 9, 10))
        self.assertIsNone(P.compare_upper(0, top + 1, 16, 1, 20))
        self.assertIsNone(P.compare_lower_sum(top + 1, top + 1, 5, 9, 16, 19, 20))
        self.assertIsNone(P.compare_lower_sum(5, 9, 0, top + 1, 16, 19, 20))

    def test_malformed_comparisons_are_refused(self):
        for arguments in ((True, 2, 3, 1, 2), (1, 2, True, 1, 2), (1, 2, 0, 1, 2), (3, 2, 3, 1, 2), (1, 2, 3, 0.5, 2),
                          (1, 2, 3, 1, 0), (1, 2, 3, 1, -2), (1, 2, 3, "1", 2)):
            with self.assertRaises(P.MalformedPreservation, msg=arguments):
                P.compare_lower(*arguments)
            with self.assertRaises(P.MalformedPreservation, msg=arguments):
                P.compare_upper(*arguments)
        for arguments in ((1, 2, 1, 2, 3, -1, 20), (1, 2, 1, 2, 3, 41, 20), (1, 2, 1, 2, 3, 19, 20, -1), (1, 2, 1, 2, 3, 19, 20, 2.0)):
            with self.assertRaises(P.MalformedPreservation, msg=arguments):
                P.compare_lower_sum(*arguments)


class DeltaTests(unittest.TestCase):
    """Two bounds against each other: the fixed halving schedule, and the edge where it stops short."""

    def test_every_delta_case_is_undecided_until_its_halving_and_right_from_then_on(self):
        self.assertGreater(max(row["decided_at"] for row in VECTORS["delta_cases"]), 20)
        for row in VECTORS["delta_cases"]:
            arguments, holds = sum_arguments(row)
            at = row["decided_at"]
            self.assertIsNone(P.compare_lower_sum(*arguments, at - 1), row)
            for limit in (at, at + 1, P.MAX_HALVINGS):
                self.assertIs(holds(P.compare_lower_sum(*arguments, limit)), row["holds"], (row, limit))
            self.assertIs(holds(P.compare_lower_sum(*arguments)), row["holds"], row)

    def test_an_estimate_buys_speed_and_never_an_answer(self):
        nonsense = [(0.25, 0.75), (0.9, 0.05), (2.5, -1.0), (float("nan"), 0.5), (0.5, float("inf")), (0.5, 0.5),
                    (1, 0), ("0.9", "0.05"), (0.9,), None, "nearly", [0.97, 0.0003]]
        for row in VECTORS["delta_cases"]:
            arguments, _ = sum_arguments(row)
            tail = P.family_tail(row["family_size"])
            real = (P.lower(arguments[0], arguments[1], tail), P.lower(arguments[2], arguments[3], tail))
            plain = P.compare_lower_sum(*arguments)
            for estimates in [real, real[::-1]] + nonsense:
                self.assertEqual(P.compare_lower_sum(*arguments, P.MAX_HALVINGS, estimates), plain, (row, estimates))
            # Nor does it reach past a halving limit that the schedule alone cannot reach past.
            for limit in range(0, row["decided_at"] + 2):
                self.assertEqual(P.compare_lower_sum(*arguments, limit, real), P.compare_lower_sum(*arguments, limit), (row, limit))

    def test_a_good_estimate_settles_every_case_at_its_first_point(self):
        for row in VECTORS["delta_cases"]:
            arguments, _ = sum_arguments(row)
            tail = P.family_tail(row["family_size"])
            real = (P.lower(arguments[0], arguments[1], tail), P.lower(arguments[2], arguments[3], tail))
            with mock.patch.object(P, "compare_lower", wraps=P.compare_lower) as counted:
                P.compare_lower_sum(*arguments, P.MAX_HALVINGS, real)
                self.assertEqual(counted.call_count, 2, row)
                counted.reset_mock()
                P.compare_lower_sum(*arguments)
                self.assertEqual(counted.call_count, 2 * row["decided_at"], row)

    def test_an_estimate_that_is_right_still_cannot_reach_past_the_halving_limit(self):
        # Aim an estimate exactly at the midpoint the schedule finds at its halving D. Asked there the answer is
        # certain, but at a limit of D - 1 the schedule cannot reach that point, so the estimate may not either.
        for row in VECTORS["delta_cases"]:
            arguments, _ = sum_arguments(row)
            at, point = row["decided_at"], Fraction(row["point"])
            places = at - 2
            self.assertEqual(point.denominator, 2 ** places, row)
            spread = 3.0 / 2 ** places
            aimed = (float(point) + spread, arguments[5] / arguments[6] - (float(point) - spread))
            self.assertEqual(P._shortcut(aimed, arguments[5], arguments[6], P.MAX_HALVINGS), (point.numerator, point.denominator), row)
            self.assertIsNone(P._shortcut(aimed, arguments[5], arguments[6], at - 1), row)
            self.assertIsNone(P.compare_lower_sum(*arguments, at - 1, aimed), row)
            self.assertEqual(P.compare_lower_sum(*arguments, at, aimed), P.compare_lower_sum(*arguments, at), row)

    def test_a_sum_with_an_empty_count_is_a_plain_threshold_test(self):
        self.assertEqual(P.compare_lower_sum(0, 9, 126, 126, 16, 19, 20, 0), P.compare_lower(126, 126, 16, 19, 20))
        self.assertEqual(P.compare_lower_sum(125, 125, 0, 9, 16, 19, 20, 0), P.compare_lower(125, 125, 16, 19, 20))

    def test_depth_cases_reach_the_verdicts_the_vector_file_names(self):
        self.assertEqual(len(VECTORS["depth_cases"]), 8)
        for case in VECTORS["depth_cases"]:
            result = P.evaluate(case["inventory"], case["cells"], case["max_halvings"])
            self.assertEqual([cell["verdict"] for cell in result["cells"]], case["verdicts"], case["name"])
            self.assertEqual(result["profile_state"], case["profile_state"], case["name"])
            self.assertEqual(result["precision"]["max_halvings"], case["max_halvings"])

    def test_an_undecided_inequality_is_reported_as_none_beside_the_decided_ones(self):
        case = next(c for c in VECTORS["depth_cases"] if c["name"] == "one-halving-short-of-settling-the-delta")
        decisions = P.evaluate(case["inventory"], case["cells"], case["max_halvings"])["cells"][0]["decisions"]
        self.assertEqual(decisions["supports"], {"accuracy_floor": True, "error_cap": True, "delta_margin": None})
        self.assertEqual(decisions["opposes"], {"accuracy_floor": False, "error_cap": False, "delta_margin": False})


class FamilyTests(unittest.TestCase):
    def test_family_size_and_tail_come_from_the_inventory(self):
        self.assertEqual(P.family_size(VECTORS["inventory"]), 16)
        self.assertEqual(P.tail_fraction(16), "1/640")
        self.assertAlmostEqual(P.family_tail(16), 1 / 640, delta=1e-18)
        both_arms = case_named("both-arms-family-of-24-at-its-minimum")["inventory"]
        self.assertEqual((P.family_size(both_arms), P.tail_fraction(24)), (24, "1/960"))

    def test_minimum_per_cell_matches_the_published_minima(self):
        for row in VECTORS["minimum_per_cell"]:
            self.assertEqual(P.minimum_per_cell(row["family_size"]), row["n"], row)

    def test_shared_inventory_cases_are_judged_as_the_vector_file_says(self):
        self.assertEqual(len(VECTORS["inventory_cases"]), 16)
        for case in VECTORS["inventory_cases"]:
            if case["accepted"]:
                self.assertEqual(P.family_size(case["inventory"]), case["family_size"], case["name"])
            else:
                with self.assertRaises(P.MalformedPreservation, msg=case["name"]):
                    P.family_size(case["inventory"])

    def test_inventory_must_be_complete_and_unique(self):
        good = VECTORS["inventory"]
        for bad in ({}, dict(good, readers=[]), dict(good, forms=["form-a", "form-a"]), dict(good, error_endpoints=[]),
                    dict(good, extra=1), dict(good, readers=["reader-a", 2])):
            with self.assertRaises(P.MalformedPreservation, msg=bad):
                P.family_size(bad)


class IdentityTests(unittest.TestCase):
    """Two cells are one cell only when both of their names are the same names. Found in review of
    the register's twin (pull request 745): a parser that joins a reader and a form into one key can
    lose a cell, and one that compares number-like names as numbers can lose an endpoint."""

    def test_identity_cases_are_judged_as_the_vector_file_says(self):
        rows = VECTORS["identity_cases"]
        self.assertEqual((len(rows), sum(row["accepted"] for row in rows)), (17, 5))
        for case in rows:
            if case["accepted"]:
                result = P.evaluate(case["inventory"], case["cells"])
                self.assertEqual((result["family_size"], len(result["cells"]), result["profile_state"]),
                                 (case["family_size"], case["cells_evaluated"], case["profile_state"]), case["name"])
                self.assertEqual([(cell["reader"], cell["form"]) for cell in result["cells"]],
                                 [(r, f) for r in case["inventory"]["readers"] for f in case["inventory"]["forms"]], case["name"])
                self.assertEqual({tuple(cell["errors"]) for cell in result["cells"]}, {tuple(case["inventory"]["error_endpoints"])}, case["name"])
            else:
                with self.assertRaises(P.MalformedPreservation, msg=case["name"]):
                    P.evaluate(case["inventory"], case["cells"])

    def test_the_names_in_the_identity_cases_really_do_collide_for_a_careless_parser(self):
        # These cases test nothing unless a join or a numeric comparison would merge the names; pin that here.
        by_name = {row["name"]: row for row in VECTORS["identity_cases"]}
        for name, key in (("nul-names-complete", lambda r, f: r + "\x00" + f), ("colon-names-complete", lambda r, f: r + ":" + f),
                          ("length-names-complete", lambda r, f: str(len(r)) + r + f)):
            inventory = by_name[name]["inventory"]
            pairs = [(r, f) for r in inventory["readers"] for f in inventory["forms"]]
            self.assertEqual((len(set(pairs)), len({key(*pair) for pair in pairs})), (4, 3), name)
            # The key the register uses: the reader's length, a colon, then the two names.
            self.assertEqual(len({"%d:%s%s" % (len(r.encode("utf-8")), r, f) for r, f in pairs}), 4, name)
        endpoints = by_name["number-like-names-complete"]["inventory"]["error_endpoints"]
        self.assertEqual((len(set(endpoints)), {float(e) for e in endpoints}), (3, {10.0}))
        swapped = by_name["number-like-endpoints-one-replaced-by-an-equal-number"]["cells"][0]["errors"]
        self.assertEqual(({float(e) for e in swapped}, set(swapped) == set(endpoints)), ({10.0}, False))
        reordered = [list(cell["errors"]) for cell in by_name["number-like-endpoints-in-another-order"]["cells"]]
        self.assertTrue(all(order != endpoints and sorted(order) == sorted(endpoints) for order in reordered))
        swapped_forms = case_named("number-like-forms-with-token-means-listed-in-another-order")
        self.assertEqual((list(swapped_forms["context"]["token_form_means"]), swapped_forms["inventory"]["forms"]), (["10.0", "1e1"], ["1e1", "10.0"]))

    def test_a_family_one_cell_short_is_refused_whatever_the_names(self):
        # The reviewer's own reproducer: three of four cells, 200 of 200 on both arms, no errors.
        inventory = {"readers": ["a", "a\x00b"], "forms": ["b\x00c", "c"], "error_endpoints": ["e"]}
        cell = lambda r, f: {"reader": r, "form": f, "marked": [200, 200], "english": [200, 200], "errors": {"e": [0, 200]}}
        with self.assertRaises(P.MalformedPreservation):
            P.evaluate(inventory, [cell("a", "c"), cell("a\x00b", "b\x00c"), cell("a\x00b", "c")])
        whole = P.evaluate(inventory, [cell(r, f) for r in inventory["readers"] for f in inventory["forms"]])
        self.assertEqual((whole["family_size"], len(whole["cells"]), whole["profile_state"]), (12, 4, "supports"))


class CaseTests(unittest.TestCase):
    def test_every_shared_case_reaches_its_expected_outcome(self):
        self.assertEqual(len(VECTORS["cases"]), 54)
        for case in VECTORS["cases"]:
            actual = P.outcome(case.get("inventory", VECTORS["inventory"]), case["cells"], **case["context"])
            self.assertEqual(actual, case["expected"], case["name"])

    def test_no_decision_reads_a_display_bound(self):
        # Replace both display bounds with nonsense: every outcome and every verdict must stay where it was.
        expected = [P.evaluate(c["inventory"], c["cells"], c["max_halvings"])["profile_state"] for c in VECTORS["depth_cases"]]
        for wrong in (lambda k, n, tail: 0.5, lambda k, n, tail: float("nan"), lambda k, n, tail: 1.0 - k / n):
            with mock.patch.object(P, "lower", wrong), mock.patch.object(P, "upper", wrong):
                for case in VECTORS["cases"]:
                    actual = P.outcome(case.get("inventory", VECTORS["inventory"]), case["cells"], **case["context"])
                    self.assertEqual(actual, case["expected"], case["name"])
                self.assertEqual([P.evaluate(c["inventory"], c["cells"], c["max_halvings"])["profile_state"]
                                  for c in VECTORS["depth_cases"]], expected)

    def test_a_missing_or_non_boolean_context_fact_is_refused_never_passed(self):
        cells = case_named("complete-large-ceiling")["cells"]
        self.assertEqual(len(VECTORS["context_cases"]), 19)
        for case in VECTORS["context_cases"]:
            with self.assertRaises(TypeError, msg=case["name"]):
                P.outcome(VECTORS["inventory"], cells, **case["context"])
        full = case_named("complete-large-ceiling")["context"]
        self.assertEqual(set(full), set(P.CONTEXT_FLAGS) | {"token_means", "token_form_means"})
        with self.assertRaises(P.ContextRefused):
            P.outcome(VECTORS["inventory"], cells, **dict(full, confirmed=1))
        with self.assertRaises(TypeError):
            P.outcome(VECTORS["inventory"], cells)

    def test_no_token_evidence_is_no_benefit(self):
        case = case_named("complete-large-ceiling")
        context = dict(case["context"], token_means=None, token_form_means=None)
        self.assertEqual(P.outcome(VECTORS["inventory"], case["cells"], **context), P.NO_DEMONSTRATED_BENEFIT)

    def test_non_finite_and_boolean_token_means_are_no_benefit(self):
        case = case_named("complete-large-ceiling")
        for bad in (float("nan"), float("inf"), float("-inf"), True, "-2"):
            context = copy.deepcopy(case["context"])
            context["token_means"]["o200k_base"] = bad
            self.assertEqual(P.outcome(VECTORS["inventory"], case["cells"], **context), P.NO_DEMONSTRATED_BENEFIT, bad)

    def test_evaluation_exposes_counts_bounds_decisions_and_the_precision_policy(self):
        result = P.evaluate(VECTORS["inventory"], case_named("complete-large-ceiling")["cells"])
        self.assertEqual((result["analysis"], result["family_size"], result["tail_fraction"], result["profile_state"]),
                         (P.ANALYSIS, 16, "1/640", "supports"))
        self.assertEqual((result["precision"]["max_halvings"], result["precision"]["max_observations"]),
                         (P.MAX_HALVINGS, P.MAX_OBSERVATIONS))
        self.assertEqual([(c["reader"], c["form"]) for c in result["cells"]],
                         [("reader-a", "form-a"), ("reader-a", "form-b"), ("reader-b", "form-a"), ("reader-b", "form-b")])
        cell = result["cells"][0]
        self.assertEqual((cell["marked"]["events"], cell["marked"]["observations"]), (512, 512))
        self.assertAlmostEqual(100 * cell["delta_lower"], -1.2540756073702308, delta=1e-10)
        self.assertEqual(set(cell["errors"]), {"false-authority", "false-polarity"})
        self.assertEqual(cell["decisions"], {"supports": {"accuracy_floor": True, "error_cap": True, "delta_margin": True},
                                             "opposes": {"accuracy_floor": False, "error_cap": False, "delta_margin": False}})

    def test_a_cell_above_the_ceiling_shows_counts_and_no_bounds(self):
        cells = case_named("observations-above-the-ceiling-are-not-judged")["cells"]
        result = P.evaluate(VECTORS["inventory"], cells)
        cell = result["cells"][0]
        self.assertEqual(cell["marked"], {"events": P.MAX_OBSERVATIONS + 1, "observations": P.MAX_OBSERVATIONS + 1, "lower": None, "upper": None})
        self.assertIsNone(cell["delta_lower"])
        self.assertEqual(set(cell["decisions"]["supports"].values()) | set(cell["decisions"]["opposes"].values()), {None})
        self.assertEqual((cell["verdict"], result["profile_state"]), ("numerically_unresolved", "numerically_unresolved"))

    def test_a_harmful_cell_opposes_whatever_the_pooled_rate_says(self):
        cells = copy.deepcopy(case_named("complete-large-ceiling")["cells"])
        cells[2]["marked"] = [200, 512]
        pooled = sum(c["marked"][0] for c in cells) / sum(c["marked"][1] for c in cells)
        self.assertGreater(pooled, 0.84)
        result = P.evaluate(VECTORS["inventory"], cells)
        self.assertEqual([c["verdict"] for c in result["cells"]], ["supports", "supports", "opposes", "supports"])
        self.assertEqual(result["profile_state"], "opposes")


class FixtureTests(unittest.TestCase):
    def test_fixture_names_the_generator_that_is_committed(self):
        tool = ROOT / VECTORS["generated_by"]["tool"]
        self.assertEqual(hashlib.sha256(tool.read_bytes()).hexdigest(), VECTORS["generated_by"]["tool_sha256"])
        self.assertEqual(VECTORS["analysis"], P.ANALYSIS)
        self.assertEqual(tuple(VECTORS["token_encodings"]), P.TOKEN_ENCODINGS)

    def test_fixture_declares_the_thresholds_and_the_precision_policy_the_module_uses(self):
        self.assertEqual(VECTORS["thresholds"], {"delta_margin": "%d/%d" % P.EXACT_DELTA_MARGIN, "accuracy_floor": "%d/%d" % P.EXACT_ACCURACY_FLOOR,
                                                 "error_cap": "%d/%d" % P.EXACT_ERROR_CAP})
        # The plain numbers a contract carries are the same thresholds, and are never what a decision reads.
        for plain, exact in ((P.DELTA_MARGIN, P.EXACT_DELTA_MARGIN), (P.ACCURACY_FLOOR, P.EXACT_ACCURACY_FLOOR), (P.ERROR_CAP, P.EXACT_ERROR_CAP)):
            self.assertEqual(Fraction(str(plain)), Fraction(*exact))
            self.assertEqual(plain, exact[0] / exact[1])
        self.assertEqual(VECTORS["precision"], {"max_halvings": P.MAX_HALVINGS, "max_observations": P.MAX_OBSERVATIONS})
        self.assertNotIn("tie_band", VECTORS)
        self.assertFalse(hasattr(P, "TIE_BAND"))


if __name__ == "__main__":
    unittest.main()
