"""One fixture, two implementations. tests/fixtures/message-blind-floor-parity-v1.json is byte-identical to the register's
tests/Fixtures/message-blind-floor-parity-v1.json (ai-nglish/ainglish-symfony #683): banks `_validate_item_block` accepts and
the block `message_blind_floor` returned for each when the fixture was generated. The register asserts its
`MessageBlindFloor::compute` against the same file, so a drift in either side's key normalisation (answer text vs index,
`str.strip` vs `trim`, casefold vs lowercase) is a failing test here or there, not a 422 after reader spend.

The fixture header pins the sha256 of the panel.py that produced every expected block, and the pin is a GATE, not
provenance prose: the test below hashes the panel.py under test and requires equality. Any edit to panel.py fails here
until the blocks are regenerated or confirmed and the pin is bumped (re-pin: run message_blind_floor over each case's real
items, confirm or update `expected`, write the new sha256 into generated_by.panel_py_sha256 and the commit into
generated_by.commit, and copy the file byte-for-byte to the register). A pin checked only for shape let a one-line
edit pass with a stale producer hash (Skitter's mutation, Colony 654d61ed, 2026-10-16)."""
import hashlib
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
        self.assertEqual(6, len(self.fixture["cases"]))
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
        # Provenance, not a gate (mindGrapez on Colony 654d61ed, 2026-10-16): the digest attests the producer's code,
        # not the Unicode tables str.strip and str.casefold read, which move between Python releases (3.12 ships
        # unidata 15.0, 3.13 ships 15.1). Recording both lets a future disagreement be attributed; asserting
        # equality here would fail the suite on every interpreter other than the producer's, so it is not asserted.
        self.assertRegex(generated["python"], r"^\d+\.\d+\.\d+$")
        self.assertRegex(generated["unidata_version"], r"^\d+\.\d+\.\d+$")

    def test_the_pinned_producer_is_the_panel_under_test(self):
        # The hash is read from the fixture and compared with the file under test at run time; a second literal
        # here would only move the drift one hop (fixture-vs-file becomes literal-vs-fixture).
        with open(panel.__file__, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        self.assertEqual(self.fixture["generated_by"]["panel_py_sha256"], actual,
                         "panel.py differs from the file that produced the expected blocks: regenerate or confirm the six blocks and re-pin")


if __name__ == "__main__":
    unittest.main()
