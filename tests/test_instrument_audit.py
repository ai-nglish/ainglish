import copy
import unittest
from unittest.mock import patch
from ainglish import instrument_audit
from ainglish.panel import arm_for


class InstrumentAuditTest(unittest.TestCase):
    def row(self, iid, **extra):
        return dict(id=iid, english='Careful English explanation.', ainglish='Marked message.',
                    question='Which action follows?', options=['inspect the original ledger','Cannot determine'],
                    answer='inspect the original ledger', settlement_stratum='one', probe='action', domain='lab', **extra)

    def manifest(self, rows):
        return dict(seed=7, items=rows, panel=[{'name':'reader','api_key_env':'DO_NOT_EXPORT'}])

    def codes(self, result):
        return {f['code'] for f in result['findings']}

    def test_report_only_no_network_no_mutation(self):
        m=self.manifest([self.row('a'),self.row('b')]); before=copy.deepcopy(m)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            result=instrument_audit.check(m)
        self.assertEqual(m,before); self.assertTrue(result['report_only'])
        self.assertNotIn('passed',result); self.assertNotIn('DO_NOT_EXPORT',str(result))

    def test_detects_within_cell_confounding_despite_global_balance(self):
        rows=[]
        for probe, arm in [('action','english'),('citation','ainglish')]:
            for i in range(4):
                nonce=0
                while arm_for(7,'reader',f'{probe}-{i}-{nonce}') != arm: nonce+=1
                row=self.row(f'{probe}-{i}-{nonce}'); row['probe']=probe; rows.append(row)
        result=instrument_audit.check(self.manifest(rows))
        self.assertEqual(2,len([f for f in result['findings'] if f['code']=='single_arm_cell']))
        self.assertNotIn('single_arm_reader',self.codes(result))

    def test_empty_crossed_cells_are_visible(self):
        a,b=self.row('a'),self.row('b'); b.update(probe='citation',domain='school')
        result=instrument_audit.check(self.manifest([a,b]))
        self.assertEqual(2,len([f for f in result['findings'] if f['code']=='empty_crossed_cell']))

    def test_missing_axis_is_not_silently_assessed(self):
        row=self.row('a'); del row['domain']
        r=instrument_audit.check(self.manifest([row]))
        self.assertIn('unclassified_axis',self.codes(r)); self.assertNotIn('domain',r['assessed_axes'])

    def test_duplicate_inputs_frames_and_invalid_key(self):
        a=self.row('a',semantic_frame='same'); b=dict(a,id='b'); c=self.row('c'); c['answer']='not offered'
        r=instrument_audit.check(self.manifest([a,b,c]))
        self.assertTrue({'shared_declared_frame','invalid_answer_key'} <= self.codes(r))
        duplicate=instrument_audit.check(self.manifest([a,b]))
        self.assertIn('repeated_complete_cases', {f['code'] for f in duplicate['item_audit']['evaluation']['errors']})

    def test_overlap_is_case_punctuation_normalized_review_not_verdict(self):
        a=self.row('a'); a['english']='First: inspect the ORIGINAL ledger! Then continue.'
        r=instrument_audit.check(self.manifest([a]))
        f=next(f for f in r['findings'] if f['code']=='gold_phrase_in_arm_review')
        self.assertEqual(['english'],f['arms']); self.assertTrue(r['report_only'])

    def test_opaque_and_short_labels_not_false_assurance(self):
        a=self.row('a'); a.update(answer='A',options=['A','B'],english='A')
        r=instrument_audit.check(self.manifest([a])); self.assertNotIn('gold_phrase_in_arm_review',self.codes(r))
        self.assertIn('semantic_independence_unassessed',self.codes(r))

    def test_calibration_not_mixed_into_target_allocation(self):
        a=self.row('a'); a['calibration']=True
        r=instrument_audit.check(self.manifest([a,self.row('b')]))
        self.assertEqual(1,r['target_items'])

    def test_duplicate_id_and_reader_names(self):
        m=self.manifest([self.row('a'),self.row('a')]);m['panel'].append({'name':' READER '})
        r=instrument_audit.check(m)
        self.assertEqual(2,len([f for f in r['findings'] if f['code']=='duplicate_identity']))

    def test_invalid_shapes(self):
        for m in [None,{}, {'items':[1],'panel':[]}]:
            with self.assertRaises(ValueError): instrument_audit.check(m)
        with self.assertRaises(ValueError): instrument_audit.check(self.manifest([]),axes=['probe','probe'])
        self.assertIn('empty_design',self.codes(instrument_audit.check(self.manifest([]))))

    def assigned_row(self, prefix, arm, **extra):
        # Synthetic pre-exposure fixture, not a proposal experiment or reader result.
        for nonce in range(100):
            iid = f'{prefix}-{nonce}'
            if arm_for(7, 'reader', iid) == arm:
                return self.row(iid, **extra)
        self.fail('fixture allocation not found')

    def test_required_rival_options_catch_menu_omission(self):
        a, b = self.row('a'), self.row('b')
        a.update(options=['receipt only', 'neither'], answer='receipt only')
        b.update(options=['receipt and agreement', 'neither'], answer='receipt and agreement')
        requirements = {'required_options': ['receipt only', 'receipt and agreement']}
        report = instrument_audit.check(self.manifest([a, b]), requirements=requirements)
        missing = [f for f in report['findings'] if f['code'] == 'required_option_missing']
        self.assertEqual(2, len(missing))
        self.assertEqual(['receipt and agreement'], missing[0]['missing_options'])
        for row in [a, b]:
            row['options'] = ['receipt only', 'receipt and agreement', 'neither']
        clean = instrument_audit.check(self.manifest([a, b]), requirements=requirements)
        self.assertNotIn('required_option_missing', self.codes(clean))
        self.assertNotIn('passed', clean)

    def test_menu_and_gold_normalization_matches_harness(self):
        a = self.row('a')
        a.update(options=['\u00a0YES ', 'no'], answer='yes')
        report = instrument_audit.check(self.manifest([a]), requirements={'required_options': ['Yes', ' NO ']})
        self.assertNotIn('required_option_missing', self.codes(report))
        self.assertNotIn('invalid_answer_key', self.codes(report))
        a.update(options=['1', '0'], answer=1)
        self.assertNotIn('invalid_answer_key', self.codes(instrument_audit.check(self.manifest([a]))))

    def test_exact_quota_exposes_marginal_mismatch(self):
        rows = [self.assigned_row('e1', 'english'), self.assigned_row('e2', 'english'),
                self.assigned_row('e3', 'english'), self.assigned_row('a1', 'ainglish')]
        req = {'arm_quotas': [{'reader': 'reader', 'where': {'settlement_stratum': 'one'},
                              'english': 2, 'ainglish': 2}]}
        report = instrument_audit.check(self.manifest(rows), requirements=req)
        self.assertIn('arm_quota_mismatch', self.codes(report))
        self.assertEqual({'english': 3, 'ainglish': 1}, report['requirements']['arm_quotas'][0]['observed'])

    def test_balanced_global_quota_does_not_hide_class_or_pair_confound(self):
        rows = []
        for frame, arm in [('outlier', 'english'), ('coincide', 'ainglish')]:
            rows.extend(self.assigned_row(f'{frame}-{i}', arm, frame_class=frame, world_id=frame) for i in range(2))
        req = {'arm_quotas': [{'reader': 'reader', 'where': {}, 'english': 2, 'ainglish': 2}],
               'opposite_arm_frame_axis': 'world_id'}
        report = instrument_audit.check(self.manifest(rows), axes=['frame_class'], requirements=req)
        self.assertNotIn('arm_quota_mismatch', self.codes(report))
        self.assertEqual(2, sum(f['code'] == 'single_arm_cell' for f in report['findings']))
        self.assertEqual(2, sum(f['code'] == 'paired_frame_same_arm' for f in report['findings']))

    def test_opposite_pairs_exclude_calibration_and_report_each_reader(self):
        rows = [self.assigned_row('e', 'english', world_id='one'),
                self.assigned_row('a', 'ainglish', world_id='one')]
        control = self.row('cal', world_id='one'); control['calibration'] = True
        m = self.manifest(rows + [control]); m['panel'].append({'name': 'other-reader'})
        report = instrument_audit.check(m, requirements={'opposite_arm_frame_axis': 'world_id'})
        pairs = report['requirements']['frame_pairs']
        self.assertEqual(2, len(pairs))
        for pair in pairs:
            self.assertEqual(2, pair['english'] + pair['ainglish'])
        first = next(p for p in pairs if p['reader'] == 'reader')
        self.assertEqual((1, 1), (first['english'], first['ainglish']))
        self.assertNotIn('frame_not_pair', self.codes(report))

    def test_missing_metadata_or_unknown_reader_not_zero_observation(self):
        req = {'arm_quotas': [{'reader': 'reader', 'where': {'missing': 'x'}, 'english': 0, 'ainglish': 0},
                              {'reader': 'absent', 'where': {}, 'english': 0, 'ainglish': 0}]}
        report = instrument_audit.check(self.manifest([self.row('a')]), requirements=req)
        self.assertEqual(2, sum(f['code'] == 'arm_quota_unassessed' for f in report['findings']))
        self.assertTrue(all(q['observed'] is None for q in report['requirements']['arm_quotas']))

    def test_empty_selection_does_not_satisfy_zero_quota(self):
        req = {'arm_quotas': [{'reader': 'reader', 'where': {'probe': 'missing'}, 'english': 0, 'ainglish': 0}]}
        report = instrument_audit.check(self.manifest([self.row('a')]), requirements=req)
        self.assertIn('arm_quota_empty_selection', self.codes(report))
        self.assertIsNone(report['requirements']['arm_quotas'][0]['observed'])

    def test_duplicate_id_declines_new_assignment_requirements(self):
        m = self.manifest([self.row('a', world_id='one'), self.row('a', world_id='one')])
        req = {'opposite_arm_frame_axis': 'world_id',
               'arm_quotas': [{'reader': 'reader', 'where': {}, 'english': 1, 'ainglish': 1}]}
        report = instrument_audit.check(m, requirements=req)
        self.assertFalse(report['requirements']['assignment_assessed'])
        self.assertIn('frame_assignment_unassessed', self.codes(report))
        self.assertIn('arm_quota_unassessed', self.codes(report))

    def test_missing_and_nonpaired_frames_are_not_silent(self):
        rows = [self.row('a', world_id='single'), self.row('b')]
        report = instrument_audit.check(self.manifest(rows), requirements={'opposite_arm_frame_axis': 'world_id'})
        self.assertTrue({'frame_identity_missing', 'frame_not_pair'} <= self.codes(report))

    def test_requirements_do_not_fetch_mutate_spend_or_emit_credentials(self):
        m = self.manifest([self.row('a', world_id='one')]); before = copy.deepcopy(m)
        req = {'required_options': ['Cannot determine'], 'opposite_arm_frame_axis': 'world_id'}
        prior = copy.deepcopy(req)
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')), \
                patch('ainglish.panel.ask', side_effect=AssertionError('inference')):
            report = instrument_audit.check(m, requirements=req)
        self.assertEqual(before, m); self.assertEqual(prior, req)
        self.assertNotIn('DO_NOT_EXPORT', str(report))
        self.assertTrue(report['report_only'])

    def test_requirements_shape_checks_and_absence(self):
        m = self.manifest([self.row('a')])
        self.assertIsNone(instrument_audit.check(m)['requirements'])
        for req in [[], {'typo': 1}, {'required_options': ['yes', ' YES ']},
                    {'required_options': [None]}, {'arm_quotas': {}},
                    {'opposite_arm_frame_axis': ''}, {'arm_quotas': [{}]},
                    {'arm_quotas': [{'reader': 'reader', 'where': {}, 'english': True, 'ainglish': 1}]}]:
            with self.subTest(req=req), self.assertRaises(ValueError):
                instrument_audit.check(m, requirements=req)

    def test_committed_readers_and_integer_frame_identity(self):
        rows = [self.assigned_row('e', 'english', frame_cluster=0),
                self.assigned_row('a', 'ainglish', frame_cluster=0)]
        m = self.manifest(rows); m['readers'] = m.pop('panel')
        report = instrument_audit.check(m, requirements={'opposite_arm_frame_axis': 'frame_cluster'})
        self.assertNotIn('frame_identity_missing', self.codes(report))
        self.assertEqual(0, report['requirements']['frame_pairs'][0]['frame'])
        m['panel'] = [{'name': 'different'}]
        with self.assertRaises(ValueError): instrument_audit.check(m)
        report = instrument_audit.check({'items': rows, 'panel': []}, requirements={})
        self.assertFalse(report['requirements']['assignment_assessed'])

if __name__ == '__main__': unittest.main()
