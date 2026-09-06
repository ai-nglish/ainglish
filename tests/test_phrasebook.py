import copy
import hashlib
import json
import unittest
from unittest.mock import patch
from ainglish.phrasebook import phrasebook


def entry(slug="we", **changes):
    row={"slug":slug,"public_id":"a-example","form":"we-including-you / we-excluding-you",
         "status":"current","ratified_version":"0.10.0","english_mapping":"Includes or excludes the reader. It grants no authority.",
         "slot":{"we-including-you":"included","we-excluding-you":"excluded"}}
    return dict(row,**changes)


def build(rows,selectors,**kwargs):
    raw=json.dumps({"version":"example-v1","entries":rows}).encode()
    return phrasebook(raw,selectors,source_url="https://ainglish.org/releases/example/register.json",
                      expected_sha256=hashlib.sha256(raw).hexdigest(),**kwargs)


class PhrasebookTest(unittest.TestCase):
    def test_whole_mapping_and_pins_without_io_or_mutation(self):
        rows=[entry()];before=copy.deepcopy(rows)
        with patch("urllib.request.urlopen",side_effect=AssertionError("network forbidden")):
            r=build(rows,["we-including-you"])
        self.assertTrue(r['complete']);self.assertEqual(rows,before)
        self.assertIn(rows[0]['english_mapping'],r['reference'])
        self.assertEqual(len(r['reference'].encode()),r['reference_bytes'])
        self.assertEqual(hashlib.sha256(rows[0]['english_mapping'].encode()).hexdigest(),r['selected'][0]['mapping_sha256'])

    def test_multiple_selectors_of_one_entry_do_not_duplicate_it(self):
        self.assertEqual(1,len(build([entry()],["we","we-including-you","we-excluding-you","we"])['selected']))

    def test_proposals_and_retired_language_are_not_promoted(self):
        for changes in [{"status":"proposed"},{"ratified_version":None},{"status":"deprecated","stage":"ratified"},{"status":"current","stage":"superseded"},{"status":"current","stage":"proposed"}]:
            r=build([entry(**changes)],["we"])
            self.assertFalse(r['complete']);self.assertEqual([],r['selected'])

    def test_ambiguous_source_is_not_first_match_wins(self):
        r=build([entry(),entry(slug="other")],["we-including-you"])
        self.assertEqual('ambiguous_in_source',r['omitted'][0]['reason'])

    def test_budget_omits_whole_mapping_not_its_restrictions(self):
        mapping='Meaning. '+('Important restriction. '*80)
        r=build([entry(english_mapping=mapping)],["we"],max_reference_bytes=600)
        self.assertFalse(r['complete']);self.assertNotIn('Meaning.',r['reference'])
        self.assertEqual('whole_mapping_exceeds_budget',r['omitted'][0]['reason'])
        self.assertLessEqual(r['reference_bytes'],600)

    def test_unicode_budget_counts_utf8_bytes(self):
        r=build([entry(english_mapping='意味 — '+('é'*100))],["we"])
        self.assertGreater(r['reference_bytes'],len(r['reference']))

    def test_wrong_pin_is_not_silently_recomputed(self):
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            phrasebook(b'{}',["we"],source_url='https://ainglish.org/x',expected_sha256='0'*64)

    def test_invalid_shape_selection_budget_and_url_are_refused(self):
        for selectors in [[],[None],[""],["we"]*33]:
            with self.assertRaises(ValueError):build([entry()],selectors)
        for budget in [True,0,255,100001]:
            with self.assertRaises(ValueError):build([entry()],["we"],max_reference_bytes=budget)
        for url in ['http://ainglish.org/x','https://user:secret@ainglish.org/x','https://ainglish.org/x?key=secret']:
            with self.assertRaises(ValueError):phrasebook(b'{}',["we"],source_url=url,expected_sha256=hashlib.sha256(b'{}').hexdigest())


if __name__=='__main__':unittest.main()
