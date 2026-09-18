import contextlib
import copy
import io
import unittest

from ainglish import panel

SOURCE = "a" * 64
LINKS = {"correction_of": SOURCE, "legacy_contract_repair_of": SOURCE}


def panel_design():
    controls = [dict(id=f"ctl-{i}", calibration=True, english="unclear " + str(i), ainglish="explicit " + str(i),
                     question="Which?", options=["yes", "no"], answer="yes") for i in range(16)]
    real = [dict(id=f"real-{i}", english="careful " + str(i), ainglish="marked " + str(i),
                 question="Which?", options=["yes", "no"], answer="yes" if i % 2 else "no") for i in range(32)]
    return dict(construct="test", metric="comprehension_accuracy_delta", seed=41,
                comparator={"kind": "complete-careful-english-v1"},
                panel=[dict(name="fixture-north"), dict(name="fixture-south")], items=controls + real, **LINKS)


def robustness_design():
    calibration = [dict(id=f"cal-{i}", english="control absent " + str(i), ainglish="control present " + str(i),
                        question="control?", options=["yes", "no"], answer="yes") for i in range(8)]
    items = [dict(id=f"item-{i}", english="clear English " + str(i), ainglish="clear marked text " + str(i),
                  question="real?", options=["yes", "no"], answer="yes") for i in range(8)]
    return dict(construct="test", metric="robustness_delta", seed=7, panel_neff=1,
                comparator={"kind": "complete-careful-english-v1"},
                panel=[dict(name="r1"), dict(name="r2")], items=items, calibration_items=calibration,
                corruption={"channel": "drop_token"}, **LINKS)


def robustness_reader(ep, text, question, options):
    # Planted effect at baseline: the marked arm is read, the English control is not.
    if question == "control?":
        return "yes" if text.startswith("control present") else "no"
    # Real cells: intact text reads correctly; a corrupted English cell is lost, a corrupted marked
    # cell survives, so the differential is nonzero and no item sits at the floor in both arms.
    if text.startswith("clear English ") and text.split()[-1].isdigit() and len(text.split()) == 3:
        return "yes"
    if "marked" in text:
        return "yes"
    return "no"


class CorrectionIdentityTest(unittest.TestCase):
    def test_panel_planned_and_observed_manifests_keep_the_correction_link(self):
        design = panel_design()
        before = copy.deepcopy(design)
        with contextlib.redirect_stdout(io.StringIO()):
            planned = panel._planned_panel_manifest(design)
            observed = panel.run_panel(design, ask_fn=panel.dry_reader(design["items"], design))
        self.assertIsNotNone(observed)
        for manifest in (planned, observed["manifest"]):
            self.assertEqual(SOURCE, manifest["correction_of"])
            self.assertEqual(SOURCE, manifest["legacy_contract_repair_of"])
        self.assertEqual(before, design, "the design is read, never mutated")

    def test_panel_without_a_link_emits_none(self):
        design = panel_design()
        del design["correction_of"]
        del design["legacy_contract_repair_of"]
        with contextlib.redirect_stdout(io.StringIO()):
            observed = panel.run_panel(design, ask_fn=panel.dry_reader(design["items"], design))
        self.assertIsNotNone(observed)
        self.assertNotIn("correction_of", observed["manifest"])
        self.assertNotIn("legacy_contract_repair_of", observed["manifest"])

    def test_robustness_observed_manifest_keeps_the_correction_link(self):
        design = robustness_design()
        before = copy.deepcopy(design)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            observed = panel.run_robustness(design, ask_fn=robustness_reader)
        self.assertIsNotNone(observed, out.getvalue())
        self.assertEqual(SOURCE, observed["manifest"]["correction_of"])
        self.assertEqual(SOURCE, observed["manifest"]["legacy_contract_repair_of"])
        self.assertEqual(before, design)


if __name__ == "__main__":
    unittest.main()
