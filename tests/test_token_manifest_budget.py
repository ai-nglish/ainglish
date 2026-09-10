import unittest
from unittest.mock import Mock, patch
from ainglish import estimand, token_measurement
from ainglish.client import AinglishClient, MAX_MANIFEST_BYTES, MAX_INLINE_TOKEN_MANIFEST_BYTES, _canonical_json, _validate_attempt_manifest


class TokenManifestBudgetTest(unittest.TestCase):
    LIMITS = {'kind': 'ainglish.inline-token-limits.v1', 'max_canonical_bytes': 131072}
    def manifest(self):
        return {'metric':'token_delta','models':['cl100k_base','o200k_base'],
            'test_set':[{'english':f'careful complete sentence {i}', 'ainglish':f'marked sentence {i}'} for i in range(4)],
            'estimand_contract':estimand.declaration(unit_span='complete sentence',contrast='marked versus careful',
                population='four test pairs',reducer='least_favourable',aggregation_rule='maximum tokenizer mean')}

    def testFinalEnrichedManifestHonoursExactByteBoundary(self):
        m=self.manifest();m['padding']=''
        small=token_measurement.prepare({'manifest':m})
        size=len(_canonical_json(small['manifest']).encode())
        m['padding']='x'*(MAX_MANIFEST_BYTES-size)
        with patch.object(token_measurement,'token_delta',side_effect=AssertionError('encoding is forbidden during prepare')):
            at_cap=token_measurement.prepare({'manifest':m})
            self.assertEqual(MAX_MANIFEST_BYTES,len(_canonical_json(at_cap['manifest']).encode()))
            m['padding']+='x'
            with self.assertRaisesRegex(ValueError,'inline test_set pairs'):
                token_measurement.prepare({'manifest':m})

    def testUtf8AndMetadataCannotProduceAnUnmintablePreparedPlan(self):
        m=self.manifest();m['padding']='é'*10000
        with self.assertRaisesRegex(ValueError,'20 KB'):
            token_measurement.prepare({'manifest':m})
        m=self.manifest();m['padding']='x'*(MAX_MANIFEST_BYTES-len(_canonical_json(m).encode())-100)
        self.assertLess(len(_canonical_json(m).encode()),MAX_MANIFEST_BYTES)
        with self.assertRaisesRegex(ValueError,'do not truncate pairs'):
            token_measurement.prepare({'manifest':m})

    def testGenericUrlAdviceIsNotGivenForRecountedTokens(self):
        m=self.manifest();m['padding']='x'*MAX_MANIFEST_BYTES
        with self.assertRaisesRegex(ValueError,'items_url is not a supported escape'):
            _validate_attempt_manifest(m)
        m['metric']='comprehension_accuracy_delta'
        with self.assertRaisesRegex(ValueError,'immutable URL'):
            _validate_attempt_manifest(m)

    def testLegacyOversizedPlanStopsBeforeTheEncoderFactoryIsCalled(self):
        m=self.manifest();m['padding']='x'*MAX_MANIFEST_BYTES
        # Reconstruct what the previous prepare implementation allowed: a
        # commitment-consistent plan which nevertheless exceeds the wire cap.
        with patch.object(token_measurement,'_validate_attempt_manifest'):
            legacy=token_measurement.prepare({'manifest':m})
        encoder=Mock(side_effect=AssertionError('No encoding may begin'))
        with self.assertRaisesRegex(ValueError,'inline test_set pairs'):
            token_measurement.run_prepared(legacy,'00000000-0000-4000-8000-000000000001',encoder_factory=encoder)
        encoder.assert_not_called()

    def testExpandedLimitIsExplicitAndDoesNotChangeScientificBytes(self):
        m = self.manifest(); m['padding'] = 'x' * 21000
        with self.assertRaisesRegex(ValueError, '20 KB'):
            token_measurement.prepare({'manifest': m})
        with patch.object(token_measurement, 'token_delta', side_effect=AssertionError('No encoding in prepare')):
            plan = token_measurement.prepare({'manifest': m}, token_limits=self.LIMITS)
        self.assertNotIn('transport_limits', plan['manifest'])
        self.assertEqual(self.LIMITS, plan['transport_limits'])
        with patch.object(token_measurement, '_validate_attempt_manifest'):
            legacy = token_measurement.prepare({'manifest': m})
        self.assertEqual(legacy['manifest'], plan['manifest'])
        self.assertEqual(legacy['manifest_commitment'], plan['manifest_commitment'])
        encoder = Mock(side_effect=AssertionError('Old cached support is not fresh support'))
        with self.assertRaisesRegex(ValueError, '20 KB'):
            token_measurement.run_prepared(plan, '00000000-0000-4000-8000-000000000001', encoder_factory=encoder)
        encoder.assert_not_called()

    def testExpandedCanonicalBoundaryIsStillFiniteAndUtf8Aware(self):
        m = self.manifest(); m['padding'] = ''
        size = len(_canonical_json(m).encode())
        m['padding'] = 'é' * ((MAX_INLINE_TOKEN_MANIFEST_BYTES - size) // 2)
        remaining = MAX_INLINE_TOKEN_MANIFEST_BYTES - len(_canonical_json(m).encode())
        m['padding'] += 'x' * remaining
        self.assertEqual(MAX_INLINE_TOKEN_MANIFEST_BYTES, len(_validate_attempt_manifest(m, token_limits=self.LIMITS)))
        m['padding'] += 'x'
        with self.assertRaisesRegex(ValueError, '131072'):
            _validate_attempt_manifest(m, token_limits=self.LIMITS)
        for bad in [{}, {'kind': 'other', 'max_canonical_bytes': 131072},
                    dict(self.LIMITS, max_canonical_bytes=True), dict(self.LIMITS, max_canonical_bytes='131072')]:
            with self.assertRaises(ValueError):
                _validate_attempt_manifest(m, token_limits=bad)
        with self.assertRaisesRegex(ValueError, '131072'):
            _validate_attempt_manifest(m, token_limits=dict(self.LIMITS, max_canonical_bytes=10**9))

    def testExpandedPreparedPlanCanRunWithoutChangingItsCommitment(self):
        m = self.manifest(); m['padding'] = 'x' * 21000
        plan = token_measurement.prepare({'manifest': m}, token_limits=self.LIMITS)
        class Encoder:
            def encode(self, text, **kwargs):
                return text.split()
        result = token_measurement.run_prepared(plan, '00000000-0000-4000-8000-000000000001',
            encoder_factory=lambda name: Encoder(), token_limits=self.LIMITS)
        self.assertEqual(plan['manifest'], result['payload']['manifest'])
        self.assertEqual(-1, result['payload']['value'])
        self.assertNotIn('transport_limits', result['payload']['manifest'])

    def testClientDiscoversBeforeMintAndOldServersRefuseWithoutPosting(self):
        m = self.manifest(); m['padding'] = 'x' * 21000
        c = AinglishClient(use_env=False)
        c.protocols = Mock(return_value={})
        c.post = Mock(return_value={'attempt': {'state': 'open'}})
        args = ('example', m, 'Test full sentence contrast.', [], {'items': 4})
        with self.assertRaisesRegex(ValueError, '20 KB'):
            c.mint_attempt(*args)
        c.post.assert_not_called()
        c.protocols.return_value = {'measurement_submission': {'manifest': {'token_delta_limits': self.LIMITS}}}
        self.assertEqual('open', c.mint_attempt(*args)['attempt']['state'])
        submitted = c.post.call_args.args[1]
        self.assertEqual(m, submitted['manifest'])
        self.assertNotIn('token_limits', submitted)
        self.assertNotIn('transport_limits', submitted['manifest'])
        c.post.reset_mock(); c.protocols.reset_mock()
        c.preflight_attempt('example', self.manifest(), 'Small ordinary attempt.', [], {'items': 4})
        c.protocols.assert_not_called()
        c.post.assert_called_once()

    def testOldServerRefusesLargeFilingBeforeLocalRecount(self):
        m = self.manifest(); m['padding'] = 'x' * 21000
        plan = token_measurement.prepare({'manifest': m}, token_limits=self.LIMITS)
        c = AinglishClient(use_env=False); c.protocols = Mock(return_value={}); c.post = Mock()
        with patch.object(token_measurement, 'verify_payload', side_effect=AssertionError('No recount allowed')) as verify:
            with self.assertRaisesRegex(ValueError, '20 KB'):
                c.measure('example', {'metric': 'token_delta', 'value': 0, 'manifest': plan['manifest']})
            verify.assert_not_called()
        c.post.assert_not_called()

    def testBudgetReportsFinalUtf8BytesOutsideCommitment(self):
        m = self.manifest(); m['note'] = '界é'
        plan = token_measurement.prepare({'manifest': m}, token_limits=self.LIMITS)
        budget = plan['transport_budget']
        self.assertEqual(len(_canonical_json(plan['manifest']).encode('utf-8')), budget['canonical_bytes'])
        self.assertEqual(131072, budget['max_canonical_bytes'])
        self.assertEqual('explicit_server_capability', budget['limit_source'])
        self.assertNotIn('transport_budget', plan['manifest'])
        offline = token_measurement.prepare({'manifest': m})
        self.assertEqual(plan['manifest_commitment'], offline['manifest_commitment'])
        self.assertEqual(20000, offline['transport_budget']['max_canonical_bytes'])
        self.assertFalse(offline['transport_budget']['run_requires_fresh_explicit_limits'])

    def testOfflineRefusalNamesActualSizeAndBothLiveLimitCallSites(self):
        m = self.manifest(); m['padding'] = 'x' * 21000
        plan = token_measurement.prepare({'manifest': m}, token_limits=self.LIMITS)
        actual = plan['transport_budget']['canonical_bytes']
        with self.assertRaises(ValueError) as refused:
            token_measurement.prepare({'manifest': m})
        message = str(refused.exception)
        self.assertIn('actual %d canonical UTF-8 bytes' % actual, message)
        self.assertIn("client.protocols()['measurement_submission']['manifest']['token_delta_limits']", message)
        self.assertIn('BOTH token_measurement.prepare', message)
        self.assertIn('token_measurement.run_prepared', message)

    def testBudgetIsAdvisoryAndCannotOverrideRunCap(self):
        m = self.manifest(); m['padding'] = 'x' * 21000
        plan = token_measurement.prepare({'manifest': m}, token_limits=self.LIMITS)
        plan['transport_budget']['max_canonical_bytes'] = 10**9
        plan['transport_budget']['canonical_bytes'] = 1
        encoder = Mock(side_effect=AssertionError('must refuse before counting'))
        with self.assertRaisesRegex(ValueError, '20 KB'):
            token_measurement.run_prepared(plan, '00000000-0000-4000-8000-000000000001', encoder_factory=encoder)
        encoder.assert_not_called()

    def testCapabilityAccessorIsAnExplicitReadWithoutPayloadMutationOrCaching(self):
        c = AinglishClient(use_env=False)
        c.get = Mock(return_value={'measurement_submission': {'manifest': {'token_delta_limits': self.LIMITS}}})
        c.post = Mock(side_effect=AssertionError('read only'))
        with patch.object(token_measurement, 'token_delta', side_effect=AssertionError('no encoding')):
            self.assertEqual(self.LIMITS, c.token_delta_limits())
            c.get.assert_called_once_with('/api/v1/protocols')
            c.get.return_value = {}
            self.assertIsNone(c.token_delta_limits())
            self.assertEqual(2, c.get.call_count, 'capability changes must not be cached')
        c.post.assert_not_called()

    def testCapabilityAbsenceIsNotAnInventedDefaultAndFetchErrorsPropagate(self):
        c = AinglishClient(use_env=False)
        for envelope in ({}, {'measurement_submission': {}},
                         {'measurement_submission': {'manifest': {}}}):
            c.protocols = Mock(return_value=envelope)
            self.assertIsNone(c.token_delta_limits())
        c.protocols = Mock(side_effect=OSError('transport failed'))
        with self.assertRaisesRegex(OSError, 'transport failed'):
            c.token_delta_limits()

    def testExplicitOversizeCapStillNamesFreshDiscoveryAndBothPhases(self):
        m = self.manifest(); m['padding'] = 'x' * MAX_INLINE_TOKEN_MANIFEST_BYTES
        with self.assertRaises(ValueError) as refused:
            _validate_attempt_manifest(m, token_limits=self.LIMITS)
        message = str(refused.exception)
        self.assertIn('client.token_delta_limits()', message)
        self.assertIn('BOTH token_measurement.prepare', message)
        self.assertIn('token_measurement.run_prepared', message)
        self.assertIn('missing support is not permission', message)

if __name__=='__main__':unittest.main()
