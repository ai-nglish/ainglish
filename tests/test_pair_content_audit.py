import copy
import unittest
from unittest.mock import patch

from ainglish.pair_content_audit import audit
from ainglish import token_measurement, estimand


class PairContentAuditTest(unittest.TestCase):
    def testBareFormAndDroppedObjectsAreVisibleWithoutSemanticVerdict(self):
        pairs = [["The job is able to retry the data.", "able-to(retry)."]]
        before = copy.deepcopy(pairs)
        report = audit(pairs, {"forms": [{"literal": "able-to", "followed_by": "whitespace"}],
                               "protected_terms": ["job", "data"]})
        self.assertEqual(before, pairs)
        self.assertEqual(["no_declared_form_match", "unequal_protected_terms", "unequal_protected_terms"],
                         [w["code"] for w in report["warnings"]])
        self.assertEqual("not_checked", report["semantic_equivalence"])
        self.assertNotIn("ok", report)

    def testBareAndPrefixFormsHaveExplicitDifferentMatchingRules(self):
        report = audit([["The job can retry.", "The job able-to retry."]],
                       {"forms": [{"literal": "able-to", "followed_by": "whitespace"}]})
        self.assertEqual(1, report["pairs"][0]["form_matches"][0]["count"])
        self.assertEqual([], report["warnings"])
        prefix = audit([["Every group in G@v2 improved.", "each-group(G@v2): improved."]],
                       {"forms": [{"literal": "each-group("}], "references": ["G@v2"]})
        self.assertEqual([], prefix["warnings"])

    def testReferencesAreBoundaryAwareAndCountsMatter(self):
        report = audit([["G@v2 G@v2 G@v20", "G@v2 G@v20"]], {"references": ["G@v2"]})
        self.assertEqual({"literal": "G@v2", "english": 2, "ainglish": 1}, report["pairs"][0]["references"][0])
        self.assertEqual("unequal_references", report["warnings"][0]["code"])

    def testEqualWordsDoNotCertifyChangedRolesOrScope(self):
        report = audit([["Alice follows Bob.", "Bob follows Alice."]])
        self.assertEqual([], report["warnings"])
        self.assertEqual([], report["pairs"][0]["english_only_words"])
        self.assertEqual("not_assessed", report["eligibility"])
        self.assertEqual("not_declared", report["form_check"])

    def testLiteralsAreNotRegexAndUnicodeBoundariesAreRetained(self):
        r = audit([["α must keep x+y.", "α must keep x+y."]],
                  {"forms": [{"literal": "x+y"}], "protected_terms": ["α", "must"]})
        self.assertEqual(1, r["pairs"][0]["form_matches"][0]["count"])
        self.assertEqual([], r["warnings"])

    def testMalformedDeclarationsRefuseRatherThanPretendToCheck(self):
        for declaration in ({"regex": ".*"}, {"forms": ["able-to"]},
                            {"forms": [{"literal": "able-to", "followed_by": "anything"}]},
                            {"references": [""]}, {"protected_terms": ["must", "must"]}):
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                audit([["a", "b"]], declaration)
        with self.assertRaises(ValueError):
            audit([{"english": "a", "baseline": "b", "ainglish": "c"}])

    def testPrepareDiagnosticsCannotChangeManifestCommitment(self):
        manifest = {"metric": "token_delta", "models": ["cl100k_base", "o200k_base"],
            "test_set": [{"english": "job %d can retry" % i, "ainglish": "job %d able-to retry" % i} for i in range(4)],
            "estimand_contract": estimand.declaration(unit_span="complete message", contrast="equally informative English",
                population="four illustrative jobs", reducer="least_favourable", aggregation_rule="maximum tokenizer mean")}
        with patch("ainglish.token_measurement.importlib.metadata.version", return_value="0.11.0"):
            plain = token_measurement.prepare({"manifest": manifest})
            reviewed = token_measurement.prepare({"manifest": manifest, "input_review": {
                "forms": [{"literal": "wrong-form"}], "protected_terms": ["job"]}})
        self.assertEqual(plain["manifest"], reviewed["manifest"])
        self.assertEqual(plain["manifest_commitment"], reviewed["manifest_commitment"])
        self.assertEqual(4, len(reviewed["input_diagnostics"]["warnings"]))
        self.assertNotIn("input_review", reviewed["manifest"])


if __name__ == "__main__":
    unittest.main()
