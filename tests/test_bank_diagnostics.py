import copy
import json
import random
import unittest
from unittest.mock import patch

from ainglish import bank_diagnostics as lint
from ainglish.experiment_audit import audit_items


def rotation_bank(n=32):
    rows = []
    for i in range(n):
        options = ["reference-one", "reference-two", "both", "neither reference is specified"]
        shift = i % 4
        rows.append({"id": str(i), "english": "English " + str(i), "ainglish": "Marked " + str(i),
                     "question": "Which reference?", "options": options[shift:] + options[:shift],
                     "answer": "reference-one"})
    return rows


class BankDiagnosticsTest(unittest.TestCase):
    def test_rotation_despite_balanced_positions(self):
        rows = rotation_bank()
        r = lint.audit(rows)["targets"]
        self.assertEqual([8, 8, 8, 8], r["gold_position_counts_by_option_count"]["4"])
        rule = next(x for x in r["rules"] if x["anchor"] == "longest" and x["offset"] == 1)
        self.assertEqual((32, 32, 0), (rule["hits"], rule["denominator"], rule["abstentions"]))
        self.assertIn({"code": "high_message_blind_rule_score", **rule}, r["warnings"])

    def test_predictor_has_no_answer_or_message_and_is_deterministic(self):
        for row in rotation_bank():
            self.assertEqual(row["answer"], row["options"][lint.predict(row["options"], "longest", 1)])
        with self.assertRaises(ValueError):
            lint.predict(["a", "b"], "invented", 0)

    def test_standard_reader_prompt_preserves_the_inspected_order(self):
        from ainglish import panel
        options = ["second", "first", "third"]
        with patch.object(panel, "chat", return_value=("B", False)) as chat:
            result = panel.ask({"name": "offline-diagnostic"}, "message", "question", options, allow_unbound=True)
        self.assertEqual("first", result)
        self.assertIn("A: second\nB: first\nC: third", chat.call_args.args[1])

    def test_ties_abstain_and_do_not_shrink_denominator(self):
        rows = [dict(r, options=["A", "B"], answer="A") for r in rotation_bank(4)]
        r = lint.audit(rows)["targets"]
        rule = next(x for x in r["rules"] if x["anchor"] == "longest" and x["offset"] == 0)
        self.assertEqual((0, 4, 4), (rule["hits"], rule["denominator"], rule["abstentions"]))

    def test_controls_never_inflate_target_score(self):
        rows = rotation_bank(8) + [dict(r, calibration=True) for r in rotation_bank(12)]
        r = lint.audit(rows)
        self.assertEqual(8, r["targets"]["scorable_rows"])
        self.assertEqual(12, r["calibration"]["scorable_rows"])

    def test_metadata_invariance_no_io_or_mutation_or_text_in_output(self):
        rows = rotation_bank(); before = copy.deepcopy(rows)
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")), \
             patch("ainglish.panel.chat", side_effect=AssertionError("no inference")):
            report = lint.audit(rows)
        self.assertEqual(rows, before)
        for row in rows:
            row.update(id="hidden", english="hidden", ainglish="hidden", world_id="hidden")
        self.assertEqual(report, lint.audit(rows))
        self.assertNotIn("reference-one", json.dumps(report))

    def test_invalid_and_empty_inputs_are_explicit(self):
        for data in [None, {}, [], [None], [{"calibration": "false"}]]:
            self.assertEqual("not_evaluable", lint.audit(data)["status"])
        r = lint.audit(rotation_bank(1) + [None, {"options": []}])
        self.assertEqual("partial", r["status"])
        self.assertEqual(1, r["unclassified_rows"])
        self.assertEqual(1, r["targets"]["invalid_rows"])
        for options in [["A", "a"], ["one"], ["a", 2], ["", "b"], [str(i) for i in range(27)]]:
            self.assertEqual(0, lint.audit([dict(rotation_bank(1)[0], options=options)])["targets"]["scorable_rows"])

    def test_mixed_widths_and_unicode_are_explicit(self):
        rows = rotation_bank(1) + [dict(rotation_bank(1)[0], options=["é", "z"], answer="é")]
        r = lint.audit(rows)["targets"]
        self.assertEqual({"4": [1, 0, 0, 0], "2": [1, 0]}, r["gold_position_counts_by_option_count"])
        self.assertEqual(0, lint.predict(["é", "z"], "byte_last", 0))
        self.assertIsNone(lint.predict(["é", "z"], "longest", 0))
        self.assertIsNone(lint.predict(["é", "z"], "position", 3))

    def test_posthoc_fit_is_not_treated_as_predictive_clearance(self):
        rows = [dict(r, question="Unique question " + str(i), answer=r["options"][i % 4])
                for i, r in enumerate(rotation_bank())]
        r = lint.audit(rows)
        self.assertEqual(32, r["targets"]["posthoc_lookup_fits"]["ordered_options"]["singleton_groups"])
        self.assertIn("not executable held-out predictors", r["interpretation"])
        self.assertNotIn("ok", r)

    def test_order_free_group_conflicts_remain_visible(self):
        rows = [dict(rotation_bank(1)[0], options=["A", "B"], answer="A"),
                dict(rotation_bank(1)[0], options=["B", "A"], answer="B")]
        fits = lint.audit(rows)["targets"]["posthoc_lookup_fits"]
        self.assertEqual(2, fits["ordered_options"]["in_sample_majority_hits"])
        self.assertEqual(1, fits["unordered_options"]["in_sample_majority_hits"])
        self.assertEqual(1, fits["unordered_options"]["conflicting_gold_groups"])

    def test_shuffle_breaks_rotation_but_not_content_cues(self):
        rows = rotation_bank(128); rng = random.Random(711)
        for row in rows:
            rng.shuffle(row["options"])
        rules = lint.audit(rows)["targets"]["rules"]
        self.assertLess(next(r["hits"] for r in rules if r["anchor"] == "longest" and r["offset"] == 1), 100)
        # Change the gold to a content-cued label: permutations cannot remove it.
        for row in rows:
            row["answer"] = "neither reference is specified"
        rules = lint.audit(rows)["targets"]["rules"]
        self.assertEqual(128, next(r["hits"] for r in rules if r["anchor"] == "longest" and r["offset"] == 0))

    def test_existing_audit_warning_does_not_set_failure(self):
        report = audit_items(rotation_bank())
        self.assertTrue(report["ok"])
        self.assertTrue(report["evaluation"]["bank_diagnostics"]["targets"]["warnings"])


if __name__ == "__main__":
    unittest.main()
