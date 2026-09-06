import contextlib
import copy
import io
import unittest
from unittest.mock import Mock

from ainglish import panel, study_scope


class StudyScopeTest(unittest.TestCase):
    def test_optional_and_detached_without_inventing_scope(self):
        source = {"metric": "token_delta", "test_set": [["English", "marked"]]}
        self.assertEqual("undeclared", study_scope.inspect_manifest(source)["status"])
        result = study_scope.attach(source, purpose="boundary_check", scope="Tests missing scope, not numeric conversion.")
        result["test_set"][0][0] = "changed"
        self.assertEqual("English", source["test_set"][0][0])
        self.assertNotIn("study_purpose", source)
        self.assertTrue(study_scope.inspect_manifest(result)["report_only"])
        with self.assertRaises(ValueError):
            study_scope.attach(result, purpose="claim_test", scope="replace old purpose")

    def test_invalid_scope_refuses_before_any_reader(self):
        bad = [None, "diagnostic", {"study_purpose": "diagnostic"},
               {"study_scope": "only"}, {"study_purpose": [], "study_scope": "scope"},
               {"study_purpose": "proven", "study_scope": "scope"},
               {"study_purpose": "diagnostic", "study_scope": " "},
               {"study_purpose": "diagnostic", "study_scope": "x" * 1001},
               {"study_purpose": "diagnostic", "study_scope": False}]
        for value in bad:
            with self.subTest(value=value):
                self.assertEqual("malformed", study_scope.inspect_manifest(value)["status"])
                reader = Mock(side_effect=AssertionError("reader must not run"))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertIsNone(panel.run_panel(value, ask_fn=reader))
                reader.assert_not_called()

    def test_planned_and_observed_manifest_retain_exact_scope(self):
        spec = {
            "metric": "comprehension_accuracy_delta", "seed": 41,
            "panel": [{"name": "fixture-north"}, {"name": "fixture-south"}],
            "items": [
                {"id": "control-" + str(i), "calibration": True, "english": "unclear " + str(i),
                 "ainglish": "explicit " + str(i), "question": "Which?", "options": ["yes", "no"], "answer": "yes"}
                for i in range(16)
            ] + [
                {"id": "real-" + str(i), "english": "careful " + str(i), "ainglish": "marked " + str(i),
                 "question": "Which?", "options": ["yes", "no"], "answer": "yes" if i % 2 else "no"}
                for i in range(32)
            ],
        }
        note = "  Diagnostic scope: neither numeric conversion nor real-world usage.  "
        scoped = study_scope.attach(spec, purpose="diagnostic", scope=note)
        before = copy.deepcopy(scoped)
        with contextlib.redirect_stdout(io.StringIO()):
            planned = panel._planned_panel_manifest(scoped)
            observed = panel.run_panel(scoped, ask_fn=panel.dry_reader(scoped["items"], scoped))
        self.assertIsNotNone(observed)
        for manifest in [planned, observed["manifest"]]:
            self.assertEqual("diagnostic", manifest["study_purpose"])
            self.assertEqual(note, manifest["study_scope"])
        self.assertEqual(before, scoped)


if __name__ == "__main__":
    unittest.main()
