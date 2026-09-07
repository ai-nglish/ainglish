import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ainglish.experiment_audit import audit_items, audit_token_pairs, cli


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

    def test_visible_arm_conflicts_survive_changed_hidden_arm_and_option_order(self):
        first = case(0)
        second = dict(case(1), english=first['english'], options=['C', 'A', 'B'])
        rows = [first, second]
        before = copy.deepcopy(rows)
        report = audit_items(rows)
        self.assertTrue(report['ok'], 'Ambiguous baseline is a review warning, not automatic rejection')
        conflict = report['evaluation']['visible_arm_conflicts']
        self.assertEqual(1, conflict['count'])
        self.assertEqual('english', conflict['shown'][0]['arm'])
        self.assertEqual(rows, before)
        self.assertNotIn(first['english'], json.dumps(report))

    def test_different_questions_and_separate_phases_do_not_conflict(self):
        first = case(0)
        for second in [dict(case(1), english=first['english'], question='A different question?'),
                       dict(case(1), english=first['english'], calibration=True)]:
            self.assertEqual(0, audit_items([first, second])['evaluation']['visible_arm_conflicts']['count'])

    def test_copy_controls_are_explicit_bounded_warnings(self):
        rows = [case(0), dict(case(1), calibration=True, ainglish='The correct answer is B.')]
        report = audit_items(rows)
        self.assertTrue(report['ok'])
        self.assertEqual(1, report['evaluation']['answer_copy_controls']['count'])
        rows[-1]['ainglish'] = "Control instruction: select exactly 'B'."
        self.assertEqual(1, audit_items(rows)['evaluation']['answer_copy_controls']['count'])
        rows[-1]['ainglish'] = 'B is one possible outcome. The message does not settle it.'
        self.assertEqual(0, audit_items(rows)['evaluation']['answer_copy_controls']['count'])

    def test_warning_diagnostics_are_bounded(self):
        rows = [dict(case(i), english='same visible input') for i in range(80)]
        finding = audit_items(rows)['evaluation']['visible_arm_conflicts']['shown'][0]
        self.assertEqual(20, len(finding['ids']))
        self.assertTrue(finding['ids_truncated'])

    def test_token_heading_audit_does_not_count_or_certify(self):
        pairs = [{'english': 'A definition paragraph. ' * 20, 'ainglish': '<ACTION>, no-undo'}]
        before = copy.deepcopy(pairs)
        with patch('ainglish.measure.token_delta', side_effect=AssertionError('no counting')):
            report = audit_token_pairs(pairs)
        self.assertTrue(report['ok'])
        self.assertEqual('paragraph_vs_placeholder_heading', report['warnings'][0]['code'])
        self.assertEqual(0, report['tokenizer_calls'])
        self.assertEqual(pairs, before)
        self.assertNotIn('definition paragraph', json.dumps(report))

    def test_length_alone_and_instantiated_pairs_do_not_trigger_heading_warning(self):
        for pairs in [[['Long ' * 100, 'A short complete message.']],
                      [{'english': 'This action cannot be undone.', 'ainglish': 'Send it, no-undo.'}]]:
            self.assertEqual([], audit_token_pairs(pairs)['warnings'])
        for pairs in [None, [], ['not a pair'], [['one', 'two', 'three']],
                      [{'english': 'one', 'baseline': 'different', 'ainglish': 'marked'}]]:
            self.assertFalse(audit_token_pairs(pairs)['ok'])

    def test_token_cli_is_report_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'pairs.json'
            path.write_text(json.dumps([['Careful English', 'Marked message']]))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, cli([str(path), '--token-pairs']))
            self.assertEqual('ainglish.token-input-audit.v1', json.loads(out.getvalue())['kind'])

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
