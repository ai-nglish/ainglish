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

if __name__ == '__main__': unittest.main()
