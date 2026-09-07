import copy
import unittest
from unittest.mock import Mock, patch
from ainglish import estimand, token_measurement as token
from ainglish.client import AinglishClient, manifest_commitment


class ReplicationIntentTest(unittest.TestCase):
    attempt = '12345678-1234-4123-8123-123456789abc'

    def manifest(self, prefix='source'):
        return {'metric': 'token_delta', 'models': ['cl100k_base', 'o200k_base'],
                'test_set': [{'english': f'{prefix} careful complete sentence {i}',
                              'ainglish': f'{prefix} marked sentence {i}'} for i in range(4)],
                'estimand_contract': estimand.declaration(unit_span='complete sentence',
                    contrast='marked versus careful English', population='four balanced pairs',
                    reducer='least_favourable', aggregation_rule='maximum tokenizer mean')}

    def replication(self):
        target = token.prepare({'manifest': self.manifest()})['manifest']
        source_hash = manifest_commitment(target)
        manifest = self.manifest('fresh'); manifest['replicates_hash'] = source_hash
        return {'manifest': manifest, 'replication_target_manifest': target}, source_hash

    def testMissingOrWrongTargetRefusesBeforeDependencyDiscovery(self):
        for supplied in [None, 'b' * 64]:
            m = self.manifest()
            if supplied is not None: m['replicates_hash'] = supplied
            with patch.object(token.importlib.metadata, 'version', side_effect=AssertionError('No dependency discovery')):
                with self.assertRaisesRegex(ValueError, 'replication intent mismatch'):
                    token.prepare({'manifest': m}, expected_replicates_hash='a' * 64)

    def testMalformedExpectedHashIsNotAnOptOut(self):
        for bad in ['', 'abc', True, 42, 'A' * 64]:
            with self.assertRaisesRegex(ValueError, 'exact 64-hex'):
                token.prepare({'manifest': self.manifest()}, expected_replicates_hash=bad)

    def testExplicitIntentChangesNoScientificCommitment(self):
        spec, target = self.replication(); before = copy.deepcopy(spec)
        plain = token.prepare(spec)
        checked = token.prepare(spec, expected_replicates_hash=target)
        self.assertEqual(plain, checked)
        self.assertEqual(before, spec)
        self.assertEqual('replication', checked['intent']['role'])
        self.assertEqual(target, checked['intent']['replicates_hash'])
        self.assertNotIn('intent', checked['manifest'])

    def testWrongRunIntentStopsBeforeEncoderAndDoesNotRelabel(self):
        plan = token.prepare({'manifest': self.manifest()}); before = copy.deepcopy(plan)
        encoder = Mock(side_effect=AssertionError('No encoding'))
        with self.assertRaisesRegex(ValueError, 'replication intent mismatch'):
            token.run_prepared(plan, self.attempt, encoder_factory=encoder,
                               expected_replicates_hash='a' * 64)
        encoder.assert_not_called(); self.assertEqual(before, plan)
        self.assertEqual('original', plan['intent']['role'])

    def testForgedSummaryRefusesAndOldPlansRemainReadable(self):
        plan = token.prepare({'manifest': self.manifest()})
        plan['intent']['role'] = 'replication'
        encoder = Mock(side_effect=AssertionError('No encoding'))
        with self.assertRaisesRegex(ValueError, 'intent summary'):
            token.run_prepared(plan, self.attempt, encoder_factory=encoder)
        encoder.assert_not_called()
        del plan['intent']
        class Encoding:
            def encode(self, text, **kwargs): return text.split()
        result = token.run_prepared(plan, self.attempt, encoder_factory=lambda _: Encoding())
        self.assertNotIn('replicates_hash', result['payload'])

    def testRoleSurvivesPrepareMintRunAndSubmission(self):
        spec, target = self.replication()
        plan = token.prepare(spec, expected_replicates_hash=target)
        c = AinglishClient(use_env=False)
        c.post = Mock(return_value={'attempt': {'attempt_id': self.attempt}})
        c.mint_attempt('fixture', plan['manifest'], **plan['mint'])
        self.assertEqual(target, c.post.call_args.args[1]['manifest']['replicates_hash'])
        class Encoding:
            def encode(self, text, **kwargs): return text.split()
        result = token.run_prepared(plan, self.attempt, encoder_factory=lambda _: Encoding(),
                                    expected_replicates_hash=target)
        payload = result['payload']
        self.assertEqual(target, payload['replicates_hash'])
        self.assertEqual(target, payload['manifest']['replicates_hash'])
        self.assertEqual(plan['manifest_commitment'], manifest_commitment(payload['manifest']))
        # Local encoding fixture, not a scientific result; isolate transport from real recount.
        with patch.object(token, 'verify_payload', return_value={}):
            c.measure('fixture', payload)
        self.assertEqual(target, c.post.call_args.args[1]['replicates_hash'])
        self.assertEqual(target, c.post.call_args.args[1]['manifest']['replicates_hash'])

    def testCliPassesExpectationAtBothBoundaries(self):
        for command in ['prepare', 'run']:
            argv = [command, 'fixture.json', '--expect-replication-of', 'a' * 64]
            method = 'prepare' if command == 'prepare' else 'run_prepared'
            if command == 'run': argv += ['--attempt-id', self.attempt]
            with patch.object(token, '_read', return_value={}), patch.object(token, '_write'), \
                    patch.object(token, method, return_value={}) as operation:
                self.assertEqual(0, token.cli(argv))
                self.assertEqual('a' * 64, operation.call_args.kwargs['expected_replicates_hash'])


if __name__ == '__main__': unittest.main()
