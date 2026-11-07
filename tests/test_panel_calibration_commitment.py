"""A valid decimal calibration gate must not fail commitment before reader spend."""
import contextlib
import copy
import io
import hashlib
import math
import unittest

from ainglish import panel
from ainglish.client import manifest_commitment
from test_panel_admissibility import Client, design, reader


class CalibrationCommitmentTests(unittest.TestCase):
    def test_portable_historical_values_keep_their_numeric_identity(self):
        for value in [0, 0.125, 0.25, 0.5, 0.75, 1]:
            out = panel._portable_calibration_threshold(value)
            self.assertEqual(out, value)
            self.assertIsInstance(out, float)
            self.assertEqual(manifest_commitment({'gate': value}), manifest_commitment({'gate': out}))

    def test_nonportable_thresholds_keep_exact_digits_not_rounded_acceptance(self):
        for value in [0.1, 0.95, 0.99999, 0.00001]:
            out = panel._portable_calibration_threshold(value)
            self.assertEqual(out, repr(value))
            self.assertEqual(float(out), value)
            manifest_commitment({'gate': out})
        for value in [math.inf, -math.inf, math.nan]:
            with self.assertRaises(ValueError): panel._portable_calibration_threshold(value)

    def test_exact_point_ninety_five_mints_before_calls_and_closes_same_manifest(self):
        manifest = design()
        manifest.update(calibration_min_gap=0.1, calibration_min_recovered=0.95)
        client = Client()
        def observed_reader(*args):
            self.assertEqual('mint', client.events[0][0])
            return reader(*args)
        spec = {'slug': 'synthetic', 'attempt': {'estimand': 'synthetic test only',
            'admissibility_gates': ['retain the actual declared thresholds'], 'planned_sample': {'items': 64}}}
        with contextlib.redirect_stdout(io.StringIO()):
            result = panel._run_preregistered_panel(manifest, spec, observed_reader, client)
        self.assertIsNotNone(result)
        self.assertEqual(['mint', 'measure'], [e[0] for e in client.events])
        minted = client.events[0][1]
        self.assertEqual('0.1', minted['calibration']['min_gap'])
        self.assertEqual('0.95', minted['calibration']['min_recovered'])
        self.assertEqual(manifest_commitment(minted), manifest_commitment(result['manifest']))
        self.assertEqual(0.95, result['calibration']['min_recovered'], 'Observed gate semantics stay numeric and unchanged.')

    def test_legacy_rule_still_has_no_recovery_threshold(self):
        manifest = design(); manifest['calibration_min_gap'] = 0.1
        with contextlib.redirect_stdout(io.StringIO()):
            emitted = panel._planned_panel_manifest(manifest)
        self.assertEqual('0.1', emitted['calibration']['min_gap'])
        self.assertIsNone(emitted['calibration']['min_recovered'])
        manifest_commitment(emitted)

    def test_learnability_plan_accepts_the_unchanged_decimal_gate(self):
        manifest = design(); manifest.update(metric='learnability', slug='synthetic-marker', form='synthetic-marker',
            calibration_min_gap=0.1, calibration_min_recovered=0.95)
        text = 'synthetic-marker means a visible test property.'
        manifest['entry'] = {'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest(),
            'source_url': 'https://example.invalid/frozen-entry', 'proposal_revision': manifest['slug']}
        for item in manifest['items']:
            if item.get('calibration'):
                item.update(calibration_scope='target-independent', calibration_construct='unrelated-test-control')
            else: item['english'] = item['ainglish']
        with contextlib.redirect_stdout(io.StringIO()):
            emitted = panel._planned_panel_manifest(manifest)
        self.assertEqual('0.95', emitted['calibration']['min_recovered'])
        manifest_commitment(emitted)

if __name__ == '__main__': unittest.main()
