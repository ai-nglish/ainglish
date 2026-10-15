"""One fixture, two implementations. tests/fixtures/message-blind-floor-parity-v1.json is byte-identical to the register's
tests/Fixtures/message-blind-floor-parity-v1.json (ai-nglish/ainglish-symfony #683): banks `_validate_item_block` accepts and
the block `message_blind_floor` returned for each when the fixture was generated. The register asserts its
`MessageBlindFloor::compute` against the same file, so a drift in either side's key normalisation (answer text vs index,
`str.strip` vs `trim`, casefold vs lowercase) is a failing test here or there, not a 422 after reader spend."""
import json
import os
import unittest

from ainglish import panel

HERE = os.path.dirname(os.path.abspath(__file__))


class MessageBlindFloorParityTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, "fixtures", "message-blind-floor-parity-v1.json"), encoding="utf-8") as f:
            self.fixture = json.load(f)
        self.assertEqual("ainglish.message-blind-floor-parity.v1", self.fixture["kind"])

    def test_every_bank_validates_and_reproduces_its_block(self):
        self.assertEqual(5, len(self.fixture["cases"]))
        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                self.assertTrue(panel._validate_item_block(case["items"], case["name"]))
                real = [item for item in case["items"] if not item.get("calibration")]
                self.assertEqual(case["expected"], panel.message_blind_floor(real))

    def test_the_fixture_names_the_harness_that_produced_it(self):
        generated = self.fixture["generated_by"]
        self.assertEqual("ainglish.panel.message_blind_floor", generated["function"])
        self.assertEqual("ai-nglish/ainglish", generated["repo"])
        self.assertRegex(generated["commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(generated["panel_py_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
