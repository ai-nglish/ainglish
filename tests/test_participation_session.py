import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('session_example', Path(__file__).parents[1] / 'examples/participation/session.py')
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)
ID = 'a-0000000000000000'
KEY = 'a' * 64


class SessionTests(unittest.TestCase):
    def client(self):
        c = Mock(spec=['whoami', 'suggestions', 'work_package'])
        c.whoami.return_value = {'sub': 'me'}
        card = {'public_id': ID, 'task_key': KEY, 'executable_now': True, 'tier': 'decision_reviews',
                'action': {'method': 'POST', 'url': '/api/v1/proposals/test/vote'}}
        c.suggestions.return_value = {'suggestions': [card], 'blocked_suggestions': [],
                                      'observation': {'receipt_id': 'private'}, 'selection': {'view': 'brief'}}
        c.work_package.return_value = {'status': 'offered', **c.suggestions.return_value}
        return c

    def test_brief_is_only_read_and_never_selects_first(self):
        c = self.client()
        result = session.inspect_session(c, {'reader_access': False, 'local_compute': True})
        c.suggestions.assert_called_once_with(domain='language', view='brief', capability='local')
        c.work_package.assert_not_called()
        self.assertIsNone(result['selected_task'])
        self.assertEqual(result['advice']['observation']['receipt_id'], 'private')
        self.assertNotIn('resource_advice', c.suggestions.return_value)

    def test_explicit_selection_keeps_task_and_is_not_acceptance(self):
        c = self.client()
        before = copy.deepcopy(c.work_package.return_value)
        result = session.inspect_session(c, proposal=ID, task_key=KEY)
        self.assertEqual(result['status'], 'selected_for_inspection')
        self.assertEqual(result['selected_task']['task_key'], KEY)
        self.assertIn('No task accepted', result['boundary'])
        c.suggestions.assert_not_called()
        self.assertEqual(c.work_package.return_value, before)

    def test_unavailable_resources_do_not_hide_or_reauthorise_a_task(self):
        c = self.client()
        card = c.work_package.return_value['suggestions'][0]
        card.update(action={'url': '/api/v1/proposals/test/measurements'}, metric='comprehension_accuracy_delta',
                    preparation={'requires_reader': True, 'named_instruments': ['exact-model']}, replicates_hash='b' * 64)
        result = session.inspect_session(c, {'reader_access': False}, proposal=ID, task_key=KEY)
        self.assertEqual(result['selected_task']['resource_advice']['state'], 'declared_unavailable')
        self.assertEqual(result['selected_task']['replicates_hash'], 'b' * 64)
        self.assertIn('check', result['next'].lower())

    def test_blocked_stale_and_missing_work_do_not_select_substitutes(self):
        for state in ('blocked', 'stale', 'not_offered'):
            c = self.client()
            c.work_package.return_value['status'] = state
            result = session.inspect_session(c, proposal=ID, task_key=KEY)
            self.assertEqual(result['status'], state)
            self.assertIsNone(result['selected_task'])
        c = self.client()
        self.assertEqual(session.inspect_session(c, proposal=ID, task_key='b' * 64)['status'], 'task_changed_or_not_offered')

    def test_duplicate_or_no_longer_executable_task_fails_closed(self):
        c = self.client()
        c.work_package.return_value['suggestions'] *= 2
        self.assertIsNone(session.inspect_session(c, proposal=ID, task_key=KEY)['selected_task'])
        c = self.client()
        c.work_package.return_value['suggestions'][0]['executable_now'] = False
        self.assertIsNone(session.inspect_session(c, proposal=ID, task_key=KEY)['selected_task'])

    def test_bad_config_is_rejected_before_any_request(self):
        for resources in ({'api_key': 'never-echo'}, {'reader_access': 'yes'}, {'instruments': {'x': 1}}):
            c = self.client()
            with self.assertRaises(ValueError):
                session.inspect_session(c, resources)
            self.assertEqual(c.mock_calls, [])
        c = self.client()
        with self.assertRaises(ValueError):
            session.inspect_session(c, proposal=ID, task_key='truncated')
        self.assertEqual(c.mock_calls, [])

    def test_exact_target_without_task_key_stays_an_inspection(self):
        result = session.inspect_session(self.client(), proposal=ID)
        self.assertEqual(result['status'], 'inspect_exact_tasks')
        self.assertIsNone(result['selected_task'])


if __name__ == '__main__':
    unittest.main()
