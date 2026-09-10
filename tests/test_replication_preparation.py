import copy
import unittest
from unittest.mock import Mock

from ainglish.client import AinglishClient, _canonical_json
import hashlib


class ReplicationPreparationTest(unittest.TestCase):
    def setUp(self):
        self.c = AinglishClient(use_env=False)
        self.manifest = {'metric': 'token_delta', 'models': ['cl100k_base'],
                         'test_set': [['complete English', 'marked text']], 'replicates_hash': 'a' * 64}
        self.args = ('test-proposal', self.manifest, 'complete statement contrast', ['fresh inputs'], {'items': 1})
        self.receipt = {'accepted': True, 'manifest_commitment': hashlib.sha256(_canonical_json(self.manifest).encode()).hexdigest(),
                        'replication_preparation': {'kind': 'ainglish.replication-preparation.v1',
                            'replicates_hash': 'a' * 64, 'status': 'no_known_obstruction', 'known_obstructions': []}}
        self.c.post = Mock(return_value=self.receipt)

    def testMissingUnitStopsBeforeMintAndPreservesInput(self):
        before = copy.deepcopy(self.manifest)
        self.receipt['replication_preparation'].update(status='blocked_for_confirmation',
            known_obstructions=[{'key': 'unit', 'reason': 'unit_declared_one_sided'}])
        with self.assertRaisesRegex(ValueError, 'unit_declared_one_sided'):
            self.c.mint_attempt(*self.args, for_confirmation=True)
        self.assertEqual(1, self.c.post.call_count)
        self.assertTrue(self.c.post.call_args.args[0].endswith('/preflight'))
        self.assertEqual(before, self.manifest)

    def testGoodPreparationStillNeedsAnActualMintAndKeepsBytes(self):
        self.c.post.side_effect = [self.receipt, {'attempt': {'attempt_id': 'fixture'}}]
        result = self.c.mint_attempt(*self.args, for_confirmation=True)
        self.assertEqual('fixture', result['attempt']['attempt_id'])
        pre, mint = self.c.post.call_args_list
        self.assertEqual(pre.args[1], mint.args[1])
        self.assertTrue(pre.args[0].endswith('/preflight'))
        self.assertTrue(mint.args[0].endswith('/attempts'))

    def testUnknownServerDistinctAndWrongIdentityCannotMasqueradeAsReady(self):
        for changed in ({}, {'accepted': False}, {'manifest_commitment': 'b' * 64},
                        {'replication_preparation': dict(self.receipt['replication_preparation'], status='distinct_estimands')},
                        {'replication_preparation': dict(self.receipt['replication_preparation'], replicates_hash='b' * 64)}):
            self.c.post.reset_mock()
            self.c.post.return_value = changed if not changed else dict(self.receipt, **changed)
            with self.assertRaises(ValueError):
                self.c.mint_attempt(*self.args, for_confirmation=True)
            self.assertEqual(1, self.c.post.call_count)

    def testDiagnosticPathRemainsWireCompatible(self):
        self.c.post.return_value = {'accepted': True, 'replication_preparation': {'status': 'blocked_for_confirmation'}}
        self.assertEqual(self.c.post.return_value, self.c.preflight_attempt(*self.args))
        self.c.post.reset_mock()
        self.c.mint_attempt(*self.args)
        self.c.post.assert_called_once()
        self.assertTrue(self.c.post.call_args.args[0].endswith('/attempts'))

    def testConfirmationCannotBeUsedForOriginalOrCommitmentOnlyRun(self):
        del self.manifest['replicates_hash']
        with self.assertRaisesRegex(ValueError, 'replicates_hash'):
            self.c.preflight_attempt(*self.args, for_confirmation=True)
        self.c.post.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'retained exact manifest'):
            self.c.mint_attempt(*self.args, for_confirmation=True, store_manifest=False)
        self.c.post.assert_not_called()
