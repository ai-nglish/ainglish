"""The exact-binomial preservation core against the shared vector file (protocol row a-9mvh2ph6g1fnw0a1)."""
import copy
import hashlib
import json
import math
import unittest
from fractions import Fraction
from pathlib import Path

from ainglish import preservation as P

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "exact-binomial-preservation-v1.json"
VECTORS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def tail_of(text):
    fraction = Fraction(text)
    return fraction.numerator / fraction.denominator


class BoundTests(unittest.TestCase):
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


class FamilyTests(unittest.TestCase):
    def test_family_size_and_tail_come_from_the_inventory(self):
        self.assertEqual(P.family_size(VECTORS["inventory"]), 16)
        self.assertEqual(P.tail_fraction(16), "1/640")
        self.assertAlmostEqual(P.family_tail(16), 1 / 640, delta=1e-18)

    def test_minimum_per_cell_matches_the_published_minima(self):
        for row in VECTORS["minimum_per_cell"]:
            self.assertEqual(P.minimum_per_cell(row["family_size"]), row["n"], row)

    def test_inventory_must_be_complete_and_unique(self):
        good = VECTORS["inventory"]
        for bad in ({}, dict(good, readers=[]), dict(good, forms=["form-a", "form-a"]), dict(good, error_endpoints=[]),
                    dict(good, extra=1), dict(good, readers=["reader-a", 2])):
            with self.assertRaises(P.MalformedPreservation, msg=bad):
                P.family_size(bad)


class CaseTests(unittest.TestCase):
    def test_every_shared_case_reaches_its_expected_outcome(self):
        self.assertEqual(len(VECTORS["cases"]), 34)
        for case in VECTORS["cases"]:
            actual = P.outcome(VECTORS["inventory"], case["cells"], **case["context"])
            self.assertEqual(actual, case["expected"], case["name"])

    def test_non_finite_and_boolean_token_means_are_no_benefit(self):
        case = next(c for c in VECTORS["cases"] if c["name"] == "complete-large-ceiling")
        for bad in (float("nan"), float("inf"), float("-inf"), True, "-2"):
            context = copy.deepcopy(case["context"])
            context["token_means"]["o200k_base"] = bad
            self.assertEqual(P.outcome(VECTORS["inventory"], case["cells"], **context), P.NO_DEMONSTRATED_BENEFIT, bad)

    def test_evaluation_exposes_counts_and_bounds_for_every_cell(self):
        case = next(c for c in VECTORS["cases"] if c["name"] == "complete-large-ceiling")
        result = P.evaluate(VECTORS["inventory"], case["cells"])
        self.assertEqual((result["analysis"], result["family_size"], result["tail_fraction"], result["profile_state"]),
                         (P.ANALYSIS, 16, "1/640", "supports"))
        self.assertEqual([(c["reader"], c["form"]) for c in result["cells"]],
                         [("reader-a", "form-a"), ("reader-a", "form-b"), ("reader-b", "form-a"), ("reader-b", "form-b")])
        cell = result["cells"][0]
        self.assertEqual((cell["marked"]["events"], cell["marked"]["observations"]), (512, 512))
        self.assertAlmostEqual(100 * cell["delta_lower"], -1.2540756073702308, delta=1e-10)
        self.assertEqual(set(cell["errors"]), {"false-authority", "false-polarity"})

    def test_a_harmful_cell_opposes_whatever_the_pooled_rate_says(self):
        cells = copy.deepcopy(next(c for c in VECTORS["cases"] if c["name"] == "complete-large-ceiling")["cells"])
        cells[2]["marked"] = [200, 512]
        pooled = sum(c["marked"][0] for c in cells) / sum(c["marked"][1] for c in cells)
        self.assertGreater(pooled, 0.84)
        result = P.evaluate(VECTORS["inventory"], cells)
        self.assertEqual([c["verdict"] for c in result["cells"]], ["supports", "supports", "opposes", "supports"])
        self.assertEqual(result["profile_state"], "opposes")

    def test_a_bound_inside_the_tie_band_is_unresolved_not_a_verdict(self):
        inventory = {"readers": ["r"], "forms": ["f"], "error_endpoints": ["e"]}
        cells = [{"reader": "r", "form": "f", "marked": [400, 400], "english": [400, 400], "errors": {"e": [0, 400]}}]
        original = P.ERROR_CAP
        exact_upper = P.evaluate(inventory, cells)["cells"][0]["errors"]["e"]["upper"]
        try:
            P.ERROR_CAP = exact_upper + P.TIE_BAND / 2  # the bound now sits inside the band below the cap
            self.assertEqual(P.evaluate(inventory, cells)["cells"][0]["verdict"], "unresolved")
            P.ERROR_CAP = exact_upper + 3 * P.TIE_BAND  # clear of the band
            self.assertEqual(P.evaluate(inventory, cells)["cells"][0]["verdict"], "supports")
        finally:
            P.ERROR_CAP = original


class FixtureTests(unittest.TestCase):
    def test_fixture_names_the_generator_that_is_committed(self):
        tool = ROOT / VECTORS["generated_by"]["tool"]
        self.assertEqual(hashlib.sha256(tool.read_bytes()).hexdigest(), VECTORS["generated_by"]["tool_sha256"])
        self.assertEqual(VECTORS["analysis"], P.ANALYSIS)
        self.assertEqual(tuple(VECTORS["token_encodings"]), P.TOKEN_ENCODINGS)
        self.assertEqual(float(VECTORS["tie_band"]), P.TIE_BAND)


if __name__ == "__main__":
    unittest.main()
