import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ainglish.experiment_audit import audit_items, cli


def case(index, answer=None):
    return {"id": "row-%d" % index, "english": "English case %d" % index,
            "ainglish": "Marked case %d" % index, "question": "What is the answer?",
            "options": ["A", "B", "C"], "answer": answer or "ABC"[index % 3]}


class ExperimentAuditTest(unittest.TestCase):
    def test_balanced_unique_data_without_io_or_mutation(self):
        rows = [case(i) for i in range(6)]
        before = copy.deepcopy(rows)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")), patch("ainglish.panel.chat", side_effect=AssertionError("inference forbidden")):
            report = audit_items(rows, require_balanced=True)
        self.assertTrue(report["ok"])
        self.assertEqual(6, report["evaluation"]["distinct_complete_cases"])
        self.assertEqual(1/3, report["evaluation"]["declared_majority_answer_baseline"])
        self.assertEqual(rows, before)
        self.assertNotIn("English case", json.dumps(report))

    def test_different_ids_do_not_make_identical_cases_distinct(self):
        first = case(0)
        second = dict(first, id="another-id")
        report = audit_items([first, second])
        self.assertFalse(report["ok"])
        self.assertEqual(1, report["evaluation"]["distinct_complete_cases"])
        self.assertEqual(1, report["evaluation"]["duplicate_cases"]["count"])

    def test_same_inputs_cannot_have_conflicting_gold(self):
        r = audit_items([case(0), dict(case(0), id="other", answer="B")])
        self.assertIn("conflicting_gold_for_same_input", [x["code"] for x in r["evaluation"]["errors"]])

    def test_imbalance_is_visible_and_policy_is_explicit(self):
        rows = [case(i, "A") for i in range(6)]
        report = audit_items(rows)
        self.assertTrue(report["ok"])
        self.assertFalse(report["evaluation"]["declared_positions_balanced"])
        self.assertEqual(1, report["evaluation"]["declared_majority_answer_baseline"])
        self.assertFalse(audit_items(rows, require_balanced=True)["ok"])

    def test_training_leakage_survives_ids_and_option_order_changes(self):
        training = [case(0)]
        evaluation = [dict(case(0), id="new-id", options=["C", "B", "A"])]
        report = audit_items(evaluation, training)
        self.assertFalse(report["ok"])
        self.assertEqual(1, report["exact_train_evaluation_overlap"]["count"])

    def test_controls_are_not_counted_as_real_or_gold_balance(self):
        rows = [case(i) for i in range(3)] + [dict(case(5), calibration=True)]
        report = audit_items(rows, require_balanced=True)
        self.assertTrue(report["ok"])
        self.assertEqual(3, report["evaluation"]["real_rows"])
        self.assertEqual(1, report["evaluation"]["control_rows"])
        rows[-1]["ainglish"] = rows[-1]["english"]
        self.assertFalse(audit_items(rows)["ok"])

    def test_malformed_inputs_are_reports_not_reader_calls(self):
        for rows in [None, {}, [], [None], [dict(case(0), options=["A", "a"])],
                     [dict(case(0), answer="D")], [dict(case(0), english="")],
                     [dict(case(0), id=[])], [dict(case(0), calibration="false")]]:
            self.assertFalse(audit_items(rows)["ok"], repr(rows))

    def test_duplicate_ids_do_not_pass(self):
        self.assertFalse(audit_items([case(0), dict(case(1), id="row-0")])["ok"])

    def test_cli_preserves_exit_status_and_emits_only_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "items.jsonl"
            path.write_text("\n".join(json.dumps(case(i)) for i in range(3)))
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli([str(path), "--require-balanced"])
            self.assertEqual(0, code)
            self.assertTrue(json.loads(out.getvalue())["ok"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(2, cli([str(Path(tmp)/"missing")]))


if __name__ == "__main__":
    unittest.main()
