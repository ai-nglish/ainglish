"""Ballot discovery must not silently collapse to recommended work or mutate evidence."""
import unittest
from unittest.mock import patch
from ainglish.client import AinglishClient


class BallotsTest(unittest.TestCase):
    def test_preserves_unresolved_and_recommended_entries_in_one_public_read(self):
        envelope = {'kind': 'ainglish.ballot-desk.v1', 'entries': [
            {'public_id': 'a-exact', 'recommended_voting_work': False,
             'primary_work': {'section': 'needs_evidence_completion'}, 'tally': {'yes': 0, 'no': 3}},
            {'public_id': 'a-ready', 'recommended_voting_work': True,
             'primary_work': {'section': 'needs_vote'}, 'tally': {'yes': 2, 'no': 1}}],
            'counts': {'total': 2, 'recommended_voting': 1, 'evidence_priority': 1}}
        c = AinglishClient(use_env=False)
        with patch.object(c, 'get', return_value=envelope) as read, patch.object(c, 'post') as write:
            self.assertIs(c.ballots(), envelope)
            read.assert_called_once_with('/api/v1/ballots')
            write.assert_not_called()

    def test_server_failure_is_not_an_empty_or_narrower_result(self):
        c = AinglishClient(use_env=False)
        error = RuntimeError('deployment does not have the endpoint yet')
        with patch.object(c, 'get', side_effect=error) as read, patch.object(c, 'queue') as queue:
            with self.assertRaises(RuntimeError) as raised:
                c.ballots()
            self.assertIs(raised.exception, error)
            read.assert_called_once_with('/api/v1/ballots')
            queue.assert_not_called()


if __name__ == '__main__':
    unittest.main()
