import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ainglish.experiment_audit import audit_declarations, audit_items, cli


def declaration(**extra):
    return {'kind': 'ainglish.study-declarations.v1', **extra}


def rows():
    # Mirrors the reviewed 3+/1- versus 2+/2- population shape, not source prose.
    return [{'id': str(i), 'english': 'Complete ordinary message %d.' % i,
             'ainglish': 'Complete marked message %d.' % i,
             'population_cell': ('stat-' if i < 4 else 'practical-') + ('negative' if i in (3, 5, 7) else 'positive'),
             'settlement_stratum': 'statistical' if i < 4 else 'practical',
             'world_id': 'world-%d' % i, 'template_id': 'template-%d' % (i % 2),
             'reference_ids': []} for i in range(8)]


class StudyDeclarationsTest(unittest.TestCase):
    def test_population_mismatch_without_spend_mutation_or_prose_inference(self):
        data = rows()
        spec = declaration(expected_target_rows=8, counts={'population_cell': {
            'stat-positive': 3, 'stat-negative': 1, 'practical-positive': 3, 'practical-negative': 1}})
        before = copy.deepcopy((data, spec))
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')), patch('ainglish.panel.chat', side_effect=AssertionError('reader')), patch('ainglish.measure.token_delta', side_effect=AssertionError('tokenizer')):
            result = audit_declarations(data, spec)
        self.assertEqual(before, (data, spec))
        self.assertFalse(result['count_checks']['population_cell']['matches'])
        self.assertEqual(2, result['count_checks']['population_cell']['actual']['practical-negative'])
        self.assertNotIn('Complete ordinary', json.dumps(result))
        self.assertEqual(8, result['declared_clusters']['world_id']['distinct_labels'])
        self.assertEqual(2, result['declared_clusters']['template_id']['distinct_labels'])

    def test_controls_are_separate_and_strata_order_weight_retained(self):
        strata = [{'id': 'practical', 'count': 4, 'weight': 2}, {'id': 'statistical', 'count': 4, 'weight': 1}]
        data = rows() + [dict(rows()[0], calibration=True)]
        result = audit_declarations(data, declaration(expected_target_rows=8, expected_control_rows=1, strata=strata))
        self.assertEqual([], result['warnings'])
        self.assertEqual(strata, result['declared_strata'])
        self.assertTrue(result['stratum_check']['matches'])

    def test_missing_unknown_labels_and_count_mismatch_are_visible(self):
        data = rows(); del data[0]['settlement_stratum']; data[1]['settlement_stratum'] = 'unplanned'
        result = audit_declarations(data, declaration(expected_target_rows=9, strata=[{'id':'statistical','count':8,'weight':1}]))
        self.assertEqual(1, result['stratum_check']['missing_or_invalid'])
        self.assertEqual(1, result['stratum_check']['actual']['unplanned'])
        self.assertIn('declared_row_count_mismatch', [w['code'] for w in result['warnings']])

    def test_zero_expected_labels_need_not_occur(self):
        self.assertTrue(audit_declarations(rows(), declaration(counts={'settlement_stratum': {'statistical':4, 'practical':4, 'absent':0}}))['count_checks']['settlement_stratum']['matches'])

    def test_reference_status_is_explicit_no_fetch_and_aliases_need_bindings(self):
        data = rows(); data[0]['reference_ids'] = ['alias-A', 'B', 'C', 'missing']
        spec = declaration(reference_bindings={
            'A': {'status':'resolved', 'locator':'retained/context#A', 'aliases':['alias-A']},
            'B': {'status':'deliberately_unresolved'}, 'C': {'status':'unknown'}})
        result = audit_declarations(data, spec)
        self.assertEqual({'resolved':1,'deliberately_unresolved':1,'unknown':1,'missing_binding':1}, result['reference_status_counts'])
        self.assertEqual(2, result['warnings'][0]['count'])

    def test_alias_collision_includes_other_canonical_id(self):
        data = rows(); data[0]['reference_ids'] = ['B']
        r = audit_declarations(data, declaration(reference_bindings={
            'A': {'status':'resolved','locator':'A','aliases':['B']},
            'B': {'status':'resolved','locator':'B'}}))
        self.assertEqual(1, r['reference_status_counts']['ambiguous_binding'])

    def test_context_not_rendered_and_bad_reference_lists_are_reported(self):
        data = rows(); data[0]['context'] = 'Hidden fact'; data[0]['reference_ids'] = 'not-a-list'
        r = audit_declarations(data, declaration())
        self.assertIn('context_metadata_not_served', [w['code'] for w in r['warnings']])
        self.assertNotIn('Hidden fact', json.dumps(r))
        self.assertEqual(1, r['reference_status_counts']['invalid_reference_list'])

    def test_missing_usage_is_not_assumed_zero(self):
        data = rows(); del data[0]['reference_ids']
        r = audit_declarations(data, declaration(reference_bindings={'A':{'status':'unknown'}}))
        self.assertIn('reference_usage_undeclared', [w['code'] for w in r['warnings']])

    def test_simple_pairs_are_not_fabricated_metadata(self):
        self.assertEqual('not_evaluable', audit_declarations([['ordinary','marked']], declaration())['status'])
        self.assertEqual('not_evaluable', audit_declarations([{'calibration':'false'}], declaration())['status'])

    def test_bad_declarations_refuse_instead_of_skipping(self):
        for spec in [None, {}, declaration(typo=1), declaration(expected_target_rows=True),
                     declaration(counts=[]), declaration(counts={'form':{'x':-1}}),
                     declaration(strata=[{'id':'x','count':1,'weight':float('inf')}]),
                     declaration(strata=[{'id':'x','count':1,'weight':True}]),
                     declaration(strata=[{'id':'x','count':1,'weight':1}]*2),
                     declaration(reference_bindings={'A':{'status':'resolved'}}),
                     declaration(reference_bindings={'A':{'status':'unknown','aliases':'B'}})]:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                audit_declarations(rows(), spec)

    def test_complete_inputs_with_changed_order_still_warn_on_contradictory_gold(self):
        first = dict(rows()[0], question='Which?', options=['yes','no'], answer='yes')
        second = dict(first, id='other', ainglish='Different hidden marked arm', options=['no','yes'], answer='no')
        self.assertEqual(1, audit_items([first, second])['evaluation']['visible_arm_conflicts']['count'])

    def test_cli_warning_keeps_ok_exit_and_malformed_sidecar_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            bank=Path(tmp)/'bank.json'; spec=Path(tmp)/'spec.json'
            bank.write_text(json.dumps(rows())); spec.write_text(json.dumps(declaration(expected_target_rows=99)))
            out=io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, cli([str(bank),'--token-pairs','--declarations',str(spec)]))
            self.assertTrue(json.loads(out.getvalue())['ok'])
            self.assertTrue(json.loads(out.getvalue())['declarations']['warnings'])
            spec.write_text('{"kind":"misspelled"}')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(2, cli([str(bank),'--token-pairs','--declarations',str(spec)]))
