"""The mint-time inventory check against the shared vector file (protocol row a-9mvh2ph6g1fnw0a1).

The register runs the same file through ``PreservationInventory::check``. Every expectation in it was
written by hand: the exact set of problem codes for each manifest, and the family size, tail and
arithmetic floor wherever the feasibility check runs. Synthetic software fixtures; nothing here is a
measurement.
"""
import copy
import hashlib
import json
import unittest
from fractions import Fraction
from pathlib import Path

from ainglish import preservation as P
from ainglish import preservation_inventory as I

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "exact-binomial-preservation-inventory-v1.json"
# The register pins the same digest (tests/PreservationInventoryTest.php): one file, two readers.
FIXTURE_SHA256 = "497a962670619c2ad56cf0e15dd69513990e1abc3b45e15aee6be109c7fa6975"
VECTORS = json.loads(FIXTURE.read_text(encoding="utf-8"))
# Written out here and not collected from the module, so a code the module never emits is noticed.
CODES = {
    "analysis", "inventory_missing", "inventory_shape", "inventory_keys", "kind",
    "readers_shape", "reader_name", "lineage_name", "reader_duplicate", "lineages_fewer_than_two",
    "readers_differ_from_manifest", "manifest_readers",
    "forms_shape", "form_name", "form_duplicate", "forms_differ_from_strata", "manifest_strata",
    "endpoints_shape", "endpoint_id", "endpoint_family", "endpoint_arm", "endpoint_key", "endpoint_duplicate",
    "endpoint_pair_duplicate", "family_one_armed",
    "sampling_shape", "sampling_unit", "sampling_cluster_field", "per_cell_type", "per_cell_above_ceiling",
    "per_cell_infeasible",
}


def case_named(name):
    return next(c for c in VECTORS["cases"] if c["name"] == name)


def codes_of(result):
    return sorted({p["code"] for p in result["problems"]})


class SharedVectorTests(unittest.TestCase):
    def test_the_file_is_the_shared_one_and_says_what_it_holds(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(), FIXTURE_SHA256,
                         "the vector file changed: the register's copy and this constant change with it")
        self.assertEqual(VECTORS["kind"], "ainglish.preservation-inventory-vectors.v1")
        self.assertEqual(VECTORS["analysis"], P.ANALYSIS)
        valid = [c for c in VECTORS["cases"] if c["valid"]]
        self.assertEqual((VECTORS["valid"], VECTORS["refused"]), (len(valid), len(VECTORS["cases"]) - len(valid)))
        self.assertGreaterEqual(len(valid), 5, "refusals alone would pass a check that refuses everything")
        self.assertGreaterEqual(len(VECTORS["cases"]) - len(valid), 40)
        for case in VECTORS["cases"]:
            self.assertEqual(case["valid"], case["codes"] == [], case["name"])

    def test_every_vector_is_read_as_written(self):
        for case in VECTORS["cases"]:
            with self.subTest(case=case["name"]):
                before = copy.deepcopy(case["manifest"])
                result = I.check(case["manifest"])
                self.assertEqual(case["manifest"], before, "the check must not change what it is given")
                self.assertEqual(codes_of(result), case["codes"], "%s (%s): %r" % (case["name"], case["why"], result["problems"]))
                self.assertEqual(result["family"], case["family"])
                for problem in result["problems"]:
                    self.assertTrue(problem["message"].strip(), "a problem says what is wrong")
                if not case["valid"]:
                    self.assertIsNone(result["inventory"], "a refused identity yields no inventory")
                    continue
                # A valid inventory is one the numeric core counts the same way.
                self.assertEqual(result["inventory"], case["manifest"]["preservation_inventory"])
                self.assertEqual(list(result["inventory"]), list(I.KEYS))
                core = I.core_inventory(result["inventory"])
                self.assertEqual(P.family_size(core), case["family"]["family_size"])

    def test_every_code_is_exercised_by_some_vector_and_no_other_code_exists(self):
        seen = {code for case in VECTORS["cases"] for code in case["codes"]}
        self.assertEqual(seen, CODES)
        # Each code also has a case in which it is the only problem, so no code is tested only in company.
        alone = {case["codes"][0] for case in VECTORS["cases"] if len(case["codes"]) == 1}
        self.assertEqual(alone, CODES - {"endpoint_pair_duplicate"})  # one family twice on an arm leaves the other arm empty too

    def test_the_floors_of_the_vectors_agree_with_a_direct_computation(self):
        """Written by hand in the file; recomputed here apart from the core's own routine."""
        seen = {}
        for case in VECTORS["cases"]:
            if case["family"] is None:
                continue
            m, n = case["family"]["family_size"], 1
            while Fraction(19, 20) ** n > Fraction(1, 40 * m):
                n += 1
            self.assertEqual(case["family"]["minimum_per_cell"], n, case["name"])
            self.assertEqual(case["family"]["tail_fraction"], "1/%d" % (40 * m), case["name"])
            seen[m] = n
        self.assertEqual(seen, {16: 126, 24: 134, 36: 142, 192: 175, 264: 181})

    def test_the_largest_admitted_family_still_has_a_floor_under_the_ceiling(self):
        m = I.MAX_READERS * I.MAX_FORMS * (2 + I.MAX_ERROR_ENDPOINTS)
        self.assertEqual(m, 67584)
        self.assertEqual(P.minimum_per_cell(m), 289)


class PythonSideTests(unittest.TestCase):
    """Where Python and PHP read JSON differently, the check must still land on the same code."""

    def base(self):
        return copy.deepcopy(case_named("complete_family_of_24")["manifest"])

    def test_true_is_not_a_count_and_a_float_is_not_an_integer(self):
        for value in (True, False, 512.0, "512", None, [512]):
            m = self.base()
            m["preservation_inventory"]["sampling"]["per_cell"] = value
            self.assertEqual(codes_of(I.check(m)), ["per_cell_type"], repr(value))

    def test_values_of_the_wrong_type_are_refused_and_never_raise(self):
        for path, value, code in (
            (("kind",), ["ainglish.preservation-inventory.v1"], "kind"),
            (("sampling", "unit"), ["world"], "sampling_unit"),
            (("sampling", "cluster_field"), {"name": "template_cluster"}, "sampling_cluster_field"),
            (("error_endpoints", 0, "arm"), ["english"], "endpoint_arm"),
            (("error_endpoints", 0, "arm"), {"english": True}, "endpoint_arm"),
            (("error_endpoints", 0, "scoring_key_sha256"), 0, "endpoint_key"),
            (("error_endpoints", 0, "family"), ["false-authority"], "endpoint_family"),
            (("readers", 0, "name"), {"n": 1}, "reader_name"),
            (("readers", 0, "lineage"), ["lineage-x"], "lineage_name"),
            (("forms", 0), ["form-a"], "form_name"),
        ):
            m = self.base()
            target = m["preservation_inventory"]
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = value
            self.assertIn(code, codes_of(I.check(m)), path)
        m = self.base()
        m[I.ANALYSIS_KEY] = ["exact-binomial-preservation-v1"]
        self.assertEqual(codes_of(I.check(m)), ["analysis"])

    def test_an_empty_object_and_a_list_are_both_not_the_inventory(self):
        for value in ({}, [], [{}], "x", 0, None):
            m = self.base()
            m["preservation_inventory"] = value
            self.assertEqual(codes_of(I.check(m)), ["inventory_shape"], repr(value))

    def test_a_manifest_that_is_not_an_object_is_a_caller_error(self):
        with self.assertRaises(TypeError):
            I.check([])

    def test_stratum_contract_follows_the_registers_rule(self):
        good = self.base()
        self.assertEqual(I._stratum_ids(good), ["form-a", "form-b"])
        for edit in (
            lambda m: m.pop("settlement_strata"),
            lambda m: m.__setitem__("settlement_strata", []),
            lambda m: m.__setitem__("settlement_strata", {"form-a": 1}),
            lambda m: m["settlement_strata"][0].__setitem__("weight", True),
            lambda m: m["settlement_strata"][0].__setitem__("weight", float("inf")),
            lambda m: m["settlement_strata"][0].__setitem__("weight", -1),
            lambda m: m["settlement_strata"][0].__setitem__("id", "Form-A"),
            lambda m: m["settlement_strata"][0].__setitem__("id", "form-b"),
            lambda m: m["settlement_strata"][0].__setitem__("extra", 1),
            lambda m: m.__setitem__("settlement_strata", [{"id": "s%d" % i, "weight": 1} for i in range(65)]),
        ):
            m = self.base()
            edit(m)
            self.assertIsNone(I._stratum_ids(m))
            self.assertEqual(codes_of(I.check(m)), ["manifest_strata"])

    def test_no_value_anywhere_in_the_manifest_makes_the_check_raise(self):
        """Dexagon's review of pull request 240: a 401-digit integer weight raised OverflowError.

        Every node of the complete manifest below its root is replaced in turn by each odd value, and
        the answer must be the envelope every time. The long integers are valid JSON; the non-finite
        floats are what Python's own ``json`` hands over for ``1e400`` and for ``NaN``.
        """
        odd = [None, True, False, 0, -1, 1, 1.5, 512.0, 1e308, float("inf"), float("-inf"), float("nan"),
               2**53 - 1, 2**53, 2**63 - 1, 2**63, 10**400, -(10**400), "", "x", "7", "form-a", [], [[]], {}, {"k": "v"},
               [10**400], {"id": "form-a", "weight": 10**400}]

        def paths(node, at=()):
            if at:
                yield at
            if isinstance(node, dict):
                for key, value in node.items():
                    yield from paths(value, at + (key,))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    yield from paths(value, at + (index,))

        every = list(paths(self.base()))
        self.assertGreater(len(every), 40)
        checks = 0
        for at in every:
            for value in odd:
                m = self.base()
                target = m
                for step in at[:-1]:
                    target = target[step]
                target[at[-1]] = copy.deepcopy(value)
                try:
                    result = I.check(m)
                except Exception as error:  # the finding: an exception where an answer was promised
                    self.fail("%r at %r raised %r" % (value, at, error))
                self.assertEqual(set(result), {"problems", "inventory", "family"}, (at, value))
                for problem in result["problems"]:
                    self.assertIn(problem["code"], CODES, (at, value))
                self.assertEqual(result["inventory"] is None, bool(result["problems"]), (at, value))
                checks += 1
        self.assertEqual(checks, len(every) * len(odd))

    def test_a_long_integer_is_refused_with_the_registers_code(self):
        m = self.base()
        m["settlement_strata"][0]["weight"] = 10**400
        self.assertIsNone(I._stratum_ids(m))
        self.assertEqual(codes_of(I.check(m)), ["manifest_strata"])
        self.assertIsNone(I.check(m)["family"])
        m = self.base()
        m["settlement_strata"][0]["weight"] = 10**308  # long, and still a finite float: a weight like any other
        self.assertEqual(I._stratum_ids(m), ["form-a", "form-b"])
        self.assertEqual(I.check(m)["problems"], [])
        for value, code in ((I.PORTABLE_INTEGER_MAX, "per_cell_above_ceiling"), (I.PORTABLE_INTEGER_MAX + 1, "per_cell_type"),
                            (2**63 - 1, "per_cell_type"), (2**63, "per_cell_type"), (10**400, "per_cell_type")):
            m = self.base()
            m["preservation_inventory"]["sampling"]["per_cell"] = value
            self.assertEqual(codes_of(I.check(m)), [code], value)
        self.assertEqual(I.PORTABLE_INTEGER_MAX, 9007199254740991)


if __name__ == "__main__":
    unittest.main()
