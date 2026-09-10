import unittest
from unittest.mock import Mock
from ainglish.client import AinglishClient


class AmendmentProblemTest(unittest.TestCase):
    def setUp(self):
        self.client = AinglishClient(use_env=False)
        self.current = {
            'title': 'A short title', 'problem': 'The separate original problem statement.',
            'kind': 'protocol', 'origin': 'prospective', 'rationale': 'A reason',
            'form': 'protocol', 'english_mapping': 'A mapping',
            'predicted_measurement': 'Refuted by an unclaimed change.',
            'colony_thread_url': 'https://thecolony.ai/post/example',
            'protocol_meta': {'retroactive': False}, 'stage': 'seconded',
        }

    def testPreservesSeparateProblemDuringDeploymentPinAmendment(self):
        self.client.proposal = Mock(return_value=self.current)
        self.client.amend = Mock(return_value={'valid': True, 'would_carry': True})
        self.client.amend_current('example', protocol_meta={'retroactive': False, 'deployed_ref': 'commit'})
        fields = self.client.amend.call_args.kwargs
        self.assertEqual(self.current['problem'], fields['problem'])
        self.assertEqual(self.current['title'], fields['title'])
        self.assertTrue(fields['dry_run'])
        self.assertNotIn('stage', fields)
        self.assertNotIn('deployed_ref', self.current['protocol_meta'])

    def testExplicitProblemEditIsAllowedWithoutChangingTitle(self):
        payload = self.client.prepare_amendment(self.current, problem='A deliberate new explanation.')
        self.assertEqual('A deliberate new explanation.', payload['problem'])
        self.assertEqual(self.current['title'], payload['title'])
        self.assertEqual('The separate original problem statement.', self.current['problem'])

    def testOldServerWithoutProblemDoesNotInventOne(self):
        old = dict(self.current); del old['problem']
        self.assertNotIn('problem', self.client.prepare_amendment(old, title='Edited title'))

    def testCustodialOverrideRemainsNarrow(self):
        self.client.proposal = Mock(return_value=self.current)
        self.client.post = Mock()
        with self.assertRaises(ValueError):
            self.client.custodial_amend_current('example', 'Reason', problem='Not a custody surface')
        self.client.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
