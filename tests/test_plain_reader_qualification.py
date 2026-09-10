import copy
import hashlib
import json
import unittest
from unittest.mock import Mock
from ainglish import panel, reader_qualification as qualification


class PlainQualificationTest(unittest.TestCase):
    def setUp(self):
        self.reader = dict(name="plain-source-reader", provider="ollama", model="cached:7b",
            api="openai", answer_protocol="opaque-choice-v1", max_tokens=64, temperature=0,
            base_url="http://localhost:11434/v1", model_digest="sha256:" + "a" * 64,
            digest_source="ollama:/api/tags",
            _ainglish_instrument_preparation={"entry_point": "prepare_reader_instruments", "binding": "ollama:/api/tags"})
        self.screen = {"kind": qualification.SCREEN_KIND, "roster_id": "plain-source-reader",
            "reader": self.reader, "receipt_precision": "Q4_K_M",
            "lineage": {"key": "example", "basis": "Bound cached quantized weights; not independent training data."},
            "controls": [{"id": str(i), "detectable": "Holder is A.", "other": "Holder is B.",
                "question": "Which holder?", "options": ["A", "B"], "answer": "A"} for i in range(4)],
            "validity_days": 7, "min_gap_bps": 5000, "min_recovered_bps": 10000}

    def test_plain_roster_and_exact_settings_are_preserved(self):
        before = copy.deepcopy(self.screen)
        ask = Mock(side_effect=lambda reader,text,question,options: "A" if text=="Holder is A." else "B")
        prepared = Mock(side_effect=lambda spec: copy.deepcopy(spec))
        result = qualification.run_screen(self.screen, ask_fn=ask, prepare_fn=prepared)
        receipt = result["receipt"]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(receipt["roster_id"], "plain-source-reader")
        self.assertEqual(receipt["reader"]["precision"], "Q4_K_M")
        self.assertNotIn("precision", result["instrument"])
        expected = hashlib.sha256(json.dumps(panel.reader_receipt(self.reader),
            sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(receipt["settings_sha256"], expected)
        self.assertEqual(ask.call_count, 8)
        self.assertEqual(self.screen, before)
        self.assertEqual(qualification.attach({"models": ["plain-source-reader"]}, [receipt])["reader_qualifications"][0], receipt)
        self.assertEqual(panel._attach_reader_qualifications(["plain-source-reader"], [receipt]), [receipt])
        with self.assertRaises(ValueError):
            qualification.validate(dict(receipt, receipt_precision="Q4_K_M"))

    def test_plain_roster_requires_explicit_precision_description_and_weight_pin(self):
        for field in ("receipt_precision", "model_digest"):
            screen = copy.deepcopy(self.screen)
            (screen if field=="receipt_precision" else screen["reader"]).pop(field)
            ask=Mock()
            with self.assertRaises(ValueError):
                qualification.run_screen(screen, ask_fn=ask)
            ask.assert_not_called()

    def test_bad_or_conflicting_labels_refused_before_spend(self):
        for change in ({"receipt_precision": ""}, {"roster_id": "plain-source-reader@Q4_K_M"},
                       {"reader": dict(self.reader, precision="q4_k_m")},
                       {"reader": dict(self.reader, model_digest="unbound")}):
            with self.assertRaises(ValueError):
                qualification.validate_screen(dict(self.screen, **change))

    def test_existing_labelled_roster_unchanged(self):
        screen = copy.deepcopy(self.screen)
        del screen["receipt_precision"]
        screen["reader"]["precision"] = "q4_k_m"
        screen["roster_id"] += "@q4_k_m"
        self.assertEqual(qualification.validate_screen(screen), screen)


if __name__ == "__main__":
    unittest.main()
