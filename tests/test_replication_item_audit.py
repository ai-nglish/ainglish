import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ainglish.experiment_audit import audit_replication_items, cli, items_digest


class ReplicationItemAuditTest(unittest.TestCase):
    def test_changed_digest_can_still_copy_every_complete_pair(self):
        source = [{'id': 'old', 'english': 'E', 'ainglish': 'A'}]
        candidate = [dict(source[0], id='new', question='a different question')]
        report = audit_replication_items(source, candidate)
        self.assertEqual('different', report['bank_digest']['relation'])
        self.assertEqual(0, report['complete_pairs']['fresh_fraction'])
        self.assertEqual(1, report['side_overlap']['english_shared'])
        self.assertEqual('not_assessed', report['eligibility'])

    def test_side_reuse_does_not_become_complete_pair_overlap(self):
        report = audit_replication_items([['old-E', 'old-A']], [['old-A', 'new-A']])
        self.assertEqual(1, report['complete_pairs']['fresh_fraction'])
        self.assertEqual(1, report['side_overlap']['english_shared'])
        self.assertEqual(0, report['side_overlap']['ainglish_shared'])

    def test_exact_bytes_multiplicity_and_controls_are_not_hidden(self):
        source = [{'english': 'E', 'ainglish': 'A', 'calibration': True}]
        candidate = [['E', 'A'], ['E', 'A'], ['e', 'a']]
        report = audit_replication_items(source, candidate)
        self.assertEqual(1, report['complete_pairs']['shared_occurrences'])
        self.assertEqual(2/3, report['complete_pairs']['fresh_fraction'])
        self.assertEqual(2, report['side_overlap']['english_shared'])

    def test_unknown_is_not_zero_and_inputs_are_not_mutated_or_exposed(self):
        rows = [['private English sentence', 'private marked sentence']]
        before = copy.deepcopy(rows)
        with patch('urllib.request.urlopen', side_effect=AssertionError('no network')), \
                patch('ainglish.panel.chat', side_effect=AssertionError('no readers')):
            report = audit_replication_items(rows, rows)
            unknown = audit_replication_items({'items_url': 'https://example.invalid/items'}, rows)
        self.assertEqual(before, rows)
        self.assertNotIn('private English', json.dumps(report))
        self.assertEqual('not_evaluable', unknown['status'])
        self.assertIsNone(unknown['side_overlap'])

    def test_cli_verifies_parsed_identity_not_raw_file_bytes(self):
        rows = [{'id': 'a', 'english': 'E', 'ainglish': 'A', 'question': 'Q',
                 'options': ['yes', 'no'], 'answer': 'yes'}]
        digest = items_digest(rows)
        with tempfile.TemporaryDirectory() as tmp:
            source, candidate = Path(tmp)/'source.json', Path(tmp)/'candidate.json'
            source.write_text(json.dumps({'items': rows, 'sha256': digest}, indent=4))
            candidate.write_text(json.dumps(rows, indent=2))
            output = io.StringIO()
            with redirect_stdout(output):
                status = cli([str(candidate), '--replication-of', str(source), '--source-sha256', digest,
                              '--items-sha256', digest])
            self.assertEqual(0, status, 'Reused pairs are reported; this is not an automatic gate.')
            report = json.loads(output.getvalue())['replication_inputs']
            self.assertTrue(report['source_pin_verified'])
            self.assertEqual(0, report['complete_pairs']['fresh_fraction'])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(2, cli([str(candidate), '--items-sha256', '0' * 64]))
                source.write_text(json.dumps({'items': rows, 'sha256': '0' * 64}))
                self.assertEqual(2, cli([str(source)]))
                source.write_text('[NaN]')
                self.assertEqual(2, cli([str(source)]))

    def test_source_pin_is_required_before_loading(self):
        with self.assertRaises(SystemExit), patch('pathlib.Path.open', side_effect=AssertionError('no read')), \
                redirect_stdout(io.StringIO()), patch('sys.stderr', new_callable=io.StringIO):
            cli(['candidate.json', '--replication-of', 'source.json'])


if __name__ == '__main__':
    unittest.main()
