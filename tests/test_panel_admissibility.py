"""Prospective gates must act before spend/mint and preserve every started failure cell."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from ainglish import panel
from ainglish.client import manifest_commitment


POLICY = dict(kind="ainglish.panel.admissibility.v1", per_reader_calibration=True,
              max_off_option_cells=0, max_absent_cells=0, max_truncated_cells=0,
              max_transport_fault_cells=0)


def design():
    controls = [dict(id=f"ctl-{i}", calibration=True, english="control absent",
                     ainglish="control present", question="control?", options=["yes", "no"],
                     answer="yes") for i in range(8)]
    real = [dict(id=f"item-{i}", english="clear English", ainglish="clear marked text",
                 question="real?", options=["yes", "no"], answer="yes") for i in range(64)]
    return dict(construct="test", metric="comprehension_accuracy_delta",
                comparator={"kind": "complete-careful-english-v1"},
                seed=42, items=controls + real, panel=[dict(name="r1"), dict(name="r2")],
                panel_neff=1, admissibility=dict(POLICY))


def reader(ep, text, question, options):
    return "no" if text == "control absent" else "yes"


class Client:
    def __init__(self):
        self.events = []

    def mint_attempt(self, slug, manifest, **kwargs):
        self.events.append(("mint", copy.deepcopy(manifest), kwargs))
        return {"attempt": {"attempt_id": "test-attempt"}}

    def measure(self, slug, measurement):
        self.events.append(("measure", measurement))
        return {"ok": True}

    def abort_attempt(self, attempt, **kwargs):
        self.events.append(("abort", kwargs))
        return {"ok": True}


class AdmissibilityTests(unittest.TestCase):
    def run_panel(self, manifest=None, ask=reader, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return panel.run_panel(manifest or design(), ask_fn=ask, **kwargs)

    def test_policy_binds_identity_and_observations_do_not(self):
        strict = self.run_panel()
        relaxed_manifest = design()
        relaxed_manifest["admissibility"]["max_off_option_cells"] = 1
        relaxed = self.run_panel(relaxed_manifest)
        historical_manifest = design()
        del historical_manifest["admissibility"]
        historical = self.run_panel(historical_manifest)
        self.assertNotEqual(manifest_commitment(strict["manifest"]),
                            manifest_commitment(relaxed["manifest"]))
        self.assertNotIn("admissibility", historical["manifest"])
        self.assertEqual(strict["value"], historical["value"])
        self.assertEqual(strict["manifest"]["admissibility"], POLICY)
        self.assertEqual(strict["calibration"]["admissibility"]["counts"],
                         {key: 0 for key in panel._ADMISSIBILITY_LIMITS})
        self.assertEqual(set(strict["calibration"]["by_reader"]), {"r1", "r2"})

    def test_invalid_policy_refuses_before_any_reader_or_mint(self):
        invalid = [None, {}, dict(POLICY, kind="v2"), dict(POLICY, surprise=1),
                   dict(POLICY, per_reader_calibration=1)]
        for key in panel._ADMISSIBILITY_LIMITS:
            invalid.extend(dict(POLICY, **{key: value})
                           for value in (True, -1, 0.0, "0", float("nan")))
        for raw in invalid:
            manifest = design()
            manifest["admissibility"] = raw
            def forbidden(*args):
                self.fail("reader called for malformed policy")
            self.assertIsNone(self.run_panel(manifest, forbidden))
            client = Client()
            with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
                panel._run_preregistered_panel(manifest, self.spec(), forbidden, client)
            self.assertEqual(client.events, [])

    def test_one_off_option_stops_and_keeps_the_offending_cell(self):
        calls = []
        def faulty(ep, text, question, options):
            calls.append(question)
            return "D: explanation instead of an option" if question == "real?" else reader(
                ep, text, question, options)
        cells = []
        result = self.run_panel(ask=faulty, cell_results=cells)
        self.assertTrue(panel._is_panel_refusal(result))
        self.assertEqual(calls.count("real?"), 1)
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0]["answer"], "D: explanation instead of an option")
        self.assertEqual(result["details"]["exceeded"], ["max_off_option_cells"])

    def test_budget_counts_calibration_and_real_together(self):
        manifest = design()
        manifest["admissibility"]["max_off_option_cells"] = 1
        calls = []
        def two_faults(ep, text, question, options):
            calls.append(question)
            if len(calls) == 1 or question == "real?":
                return "off option"
            return reader(ep, text, question, options)
        result = self.run_panel(manifest, two_faults)
        self.assertTrue(panel._is_panel_refusal(result))
        obs = result["details"]["admissibility"]
        self.assertEqual(obs["counts"]["max_off_option_cells"], 2)
        self.assertEqual(obs["by_stage"]["calibration"]["max_off_option_cells"], 1)
        self.assertEqual(obs["by_stage"]["real"]["max_off_option_cells"], 1)

    def test_pooled_success_cannot_rescue_failed_reader_when_declared(self):
        def uneven(ep, text, question, options):
            if ep["name"] == "r2" and question == "control?":
                return "no"
            return reader(ep, text, question, options)
        cells = []
        failed = self.run_panel(ask=uneven, cell_results=cells)
        self.assertEqual(cells, [])
        self.assertEqual(failed["details"]["failed_readers"], ["r2"])
        historical = design()
        del historical["admissibility"]
        self.assertFalse(panel._is_panel_refusal(self.run_panel(historical, uneven)))

    def test_absence_truncation_and_transport_are_distinct(self):
        for failure, expected, gate_kind in (
            (panel.Absent("empty_stop"), {"max_absent_cells"}, "harness_refuse"),
            (panel.Absent("truncated"), {"max_absent_cells", "max_truncated_cells"},
             "harness_refuse"),
            (panel.TransportFault("timeout"), {"max_absent_cells", "max_transport_fault_cells"},
             "reader_timeout"),
        ):
            def fail(ep, text, question, options):
                if question == "real?":
                    if isinstance(failure, Exception):
                        raise failure
                    return failure
                return reader(ep, text, question, options)
            result = self.run_panel(ask=fail)
            self.assertEqual(set(result["details"]["exceeded"]), expected)
            self.assertEqual(panel._panel_refusal_failed_gate_kind(result), gate_kind)

    def test_unsupported_quartet_path_refuses_not_ignores_policy(self):
        manifest = design()
        manifest["metric"] = "robustness_delta"
        self.assertIsNone(self.run_panel(manifest, lambda *args: self.fail("reader spend")))

    @staticmethod
    def spec():
        return dict(slug="test", attempt=dict(estimand="prospective test",
                    admissibility_gates=["test only"], planned_sample={"real": 64}))

    def test_preregistration_freezes_rule_and_aborts_without_filing(self):
        client = Client()
        def faulty(ep, text, question, options):
            return "off option" if question == "real?" else reader(ep, text, question, options)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            result = panel._run_preregistered_panel(
                design(), self.spec(), faulty, client, receipt_dir=directory)
            self.assertIsNone(result)
            self.assertEqual([event[0] for event in client.events], ["mint", "abort"])
            self.assertEqual(client.events[0][1]["admissibility"], POLICY)
            self.assertIn(panel.admissibility_gate_statement(design()),
                          client.events[0][2]["admissibility_gates"])
            self.assertEqual(len(list(Path(directory).glob("*.abort.json"))), 1)
            cells = json.loads(next(Path(directory).glob("*.cells.json")).read_text())
            self.assertGreater(len(cells), 0)
            real_path = Path(directory) / "runspec.attempt-test-attempt.cells.json"
            real = json.loads(real_path.read_text())
            self.assertEqual(real["real_cells_recorded"], 1)

    def test_successful_preregistered_run_has_stable_clean_commitment(self):
        client = Client()
        with contextlib.redirect_stdout(io.StringIO()):
            panel._run_preregistered_panel(design(), self.spec(), reader, client)
        self.assertEqual([event[0] for event in client.events], ["mint", "measure"])
        self.assertEqual(manifest_commitment(client.events[0][1]),
                         manifest_commitment(client.events[1][1]["manifest"]))

    def test_concurrent_abort_drains_started_cells_without_buying_more(self):
        manifest = design()
        manifest["concurrency"] = dict(max_in_flight=2,
            per_reader_max_in_flight={"r1": 1, "r2": 1})
        rendezvous = threading.Barrier(2, timeout=5)
        def faulty(ep, text, question, options):
            if question == "real?":
                rendezvous.wait()
                return "off option"
            return reader(ep, text, question, options)
        cells = []
        result = self.run_panel(manifest, faulty, cell_results=cells)
        self.assertTrue(panel._is_panel_refusal(result))
        self.assertEqual(len(cells), 2)
        self.assertEqual(result["details"]["admissibility"]["counts"]["max_off_option_cells"], 2)
        self.assertEqual(result["details"]["concurrency_execution"]["started"], 2)
        self.assertEqual(result["details"]["concurrency_execution"]["not_started"], 126)


if __name__ == "__main__":
    unittest.main()
