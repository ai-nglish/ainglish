"""A qualification for one instrument must not buy cells or mint for another."""
import contextlib
import copy
import hashlib
import io
import json
import unittest
from unittest.mock import Mock, patch

from ainglish import panel, reader_qualification as qualification


def settings_digest(reader):
    return hashlib.sha256(json.dumps(reader, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def reader_config(precision="q4_k_m", top_p=1.0):
    reader = dict(name="reader", provider="openai-compatible", model="example-model",
                  base_url="http://localhost:9999/v1", api="openai", api_key_env="",
                  seed=2026091451, temperature=0, max_tokens=64, top_p=top_p,
                  model_digest="sha256:" + "a" * 64, digest_source="provider-opaque",
                  _ainglish_instrument_preparation={
                      "entry_point": "prepare_reader_instruments", "binding": "provider-opaque"})
    if precision is not None:
        reader["precision"] = precision
    return reader


def receipt(reader):
    instrument = panel.reader_receipt(reader)
    roster = reader["name"] + ("@" + reader["precision"] if reader.get("precision") else "")
    return qualification.receipt(
        roster_id=roster, provider=reader["provider"], model=reader["model"],
        precision=reader.get("precision", "Q4_K_M"), lineage_key="test/example",
        lineage_basis="Synthetic unit fixture, not a real qualification.",
        screen_sha256="b" * 64, settings_sha256=settings_digest(instrument),
        qualified_at="2026-10-01T00:00:00Z", valid_until="2026-11-01T00:00:00Z",
        detectable_correct=8, detectable_total=8, other_correct=0, other_total=8)


def design(reader=None):
    reader = reader or reader_config()
    controls = [dict(id="control-%d" % i, calibration=True, english="absent", ainglish="present",
                     question="Present?", options=["yes", "no"], answer="yes") for i in range(8)]
    real = [dict(id="real-%d" % i, english="present", ainglish="present",
                 question="Present?", options=["yes", "no"], answer="yes") for i in range(16)]
    return dict(metric="comprehension_accuracy_delta", construct="test", seed=42,
                items=controls + real, panel=[copy.deepcopy(reader)],
                reader_qualifications=[receipt(reader)])


def oracle(_reader, text, _question, _options):
    return "no" if text == "absent" else "yes"


class QualificationSettingsBindingTest(unittest.TestCase):
    def run_quietly(self, candidate, ask):
        with contextlib.redirect_stdout(io.StringIO()):
            return panel.run_panel(candidate, ask_fn=ask)

    def test_exact_instrument_runs_and_preview_preserves_receipt(self):
        for precision, top_p in [(None, 1.0), ("q4_k_m", 1.0), ("q4_k_m", 1)]:
            with self.subTest(precision=precision, top_p=repr(top_p)):
                candidate = design(reader_config(precision, top_p))
                ask = Mock(side_effect=oracle)
                result = self.run_quietly(candidate, ask)
                self.assertIsNotNone(result)
                self.assertGreater(ask.call_count, 0)
                planned = panel._planned_panel_manifest(candidate)
                self.assertEqual(planned["reader_qualifications"], candidate["reader_qualifications"])
                self.assertEqual(settings_digest(planned["readers"][0]),
                                 candidate["reader_qualifications"][0]["settings_sha256"])

    def test_changed_full_settings_refuse_before_cells_and_mint(self):
        changes = {"seed": 2026102701, "temperature": 0.5, "top_p": 0.75,
                   "max_tokens": 128, "model": "different-model",
                   "model_digest": "sha256:" + "c" * 64,
                   "base_url": "http://localhost:9998/v1"}
        for field, value in changes.items():
            with self.subTest(field=field):
                candidate = design()
                candidate["panel"][0][field] = value
                ask = Mock(side_effect=oracle)
                self.assertIsNone(self.run_quietly(candidate, ask))
                ask.assert_not_called()
                client = Mock()
                spec = {"slug": "test", "attempt": {"estimand": "Test settings binding",
                        "admissibility_gates": ["matching qualification"], "planned_sample": {"items": 16}}}
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                    panel._run_preregistered_panel(candidate, spec, ask, client)
                self.assertEqual(client.mock_calls, [])
                ask.assert_not_called()

    def test_check_uses_prepared_instrument(self):
        candidate = design()
        def prepare(manifest):
            manifest["panel"][0]["seed"] = 2026102701
            return manifest
        ask = Mock(side_effect=oracle)
        with patch.object(panel, "ask", ask), patch.object(panel, "prepare_reader_instruments", side_effect=prepare) as binding:
            self.assertIsNone(self.run_quietly(candidate, ask))
        binding.assert_called_once()
        ask.assert_not_called()

    def test_robustness_refuses_before_dispatch(self):
        candidate = design()
        candidate["metric"] = "robustness_delta"
        candidate["panel"][0]["seed"] += 1
        ask = Mock(side_effect=oracle)
        with patch.object(panel, "run_robustness") as dispatch:
            self.assertIsNone(self.run_quietly(candidate, ask))
        dispatch.assert_not_called()
        ask.assert_not_called()

    def test_attach_checks_supplied_reader_receipts_without_mutation(self):
        reader = reader_config()
        item = receipt(reader)
        manifest = {"models": [item["roster_id"]], "readers": [panel.reader_receipt(reader)]}
        before, original = copy.deepcopy(manifest), copy.deepcopy(item)
        attached = qualification.attach(manifest, [item])
        self.assertEqual(attached["reader_qualifications"], [item])
        self.assertEqual(manifest, before)
        self.assertEqual(item, original)
        manifest["readers"][0]["seed"] += 1
        with self.assertRaisesRegex(ValueError, "settings_sha256"):
            qualification.attach(manifest, [item])
        # Roster-only callers retain their documented metadata attachment.
        self.assertEqual(qualification.attach({"models": [item["roster_id"]]}, [item])[
                         "reader_qualifications"], [item])

    def test_missing_ambiguous_or_non_json_reader_receipts_refuse(self):
        reader = reader_config()
        item = receipt(reader)
        instrument = panel.reader_receipt(reader)
        for readers in [None, [], [{}], [dict(instrument, name="other")],
                        [instrument, instrument], [dict(instrument, seed=float("nan"))]]:
            with self.subTest(readers=repr(readers)), self.assertRaises(ValueError):
                qualification.attach({"models": [item["roster_id"]], "readers": readers}, [item])

    def test_no_qualifications_and_secret_only_changes_remain_supported(self):
        candidate = design()
        del candidate["reader_qualifications"]
        candidate["panel"][0]["seed"] += 1
        self.assertIsNotNone(self.run_quietly(candidate, oracle))
        candidate = design()
        candidate["panel"][0]["api_key_env"] = "SYNTHETIC_CREDENTIAL_ENV"
        candidate["panel"][0]["base_url"] += "?token=synthetic#fragment"
        self.assertIsNotNone(self.run_quietly(candidate, oracle))

    def _file_runspec(self):
        """A reader as a runspec FILE carries it: no preparation key, no digest fields. The receipt is
        taken over the PREPARED instrument, exactly as run_screen produces it."""
        file_reader = {k: v for k, v in reader_config().items()
                       if k not in ("_ainglish_instrument_preparation", "model_digest", "digest_source")}
        prepared = copy.deepcopy(file_reader)
        panel.prepare_reader_instruments({"panel": [prepared]})
        self.assertNotEqual(settings_digest(panel.reader_receipt(file_reader)),
                            settings_digest(panel.reader_receipt(prepared)))
        candidate = design(prepared)
        candidate["panel"] = [copy.deepcopy(file_reader)]
        return candidate

    def test_cli_dry_run_on_unprepared_runspec_defers_binding(self):
        candidate = self._file_runspec()
        dry = dict(candidate, _dry_run=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = panel.run_panel(dry, ask_fn=panel.dry_reader(dry["items"], dry))
        self.assertIsNotNone(result)
        self.assertFalse(panel._is_panel_refusal(result))
        self.assertIn("settings binding deferred to spend", out.getvalue())
        self.assertEqual(result["manifest"]["reader_qualifications"], candidate["reader_qualifications"])

    def test_prepared_preview_and_run_still_check_the_same_runspec(self):
        candidate = self._file_runspec()
        panel.prepare_reader_instruments(candidate)
        planned = panel._planned_panel_manifest(candidate)
        self.assertEqual(planned["reader_qualifications"], candidate["reader_qualifications"])
        ask = Mock(side_effect=oracle)
        self.assertIsNotNone(self.run_quietly(candidate, ask))
        self.assertGreater(ask.call_count, 0)
        # Once prepared, a dry run checks too: deferral is only for unprepared entries.
        candidate["panel"][0]["seed"] += 1
        dry = dict(candidate, _dry_run=True)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(panel.run_panel(dry, ask_fn=panel.dry_reader(dry["items"], dry)))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            panel._planned_panel_manifest(candidate)


if __name__ == "__main__":
    unittest.main()
