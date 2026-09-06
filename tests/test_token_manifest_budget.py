import copy
import unittest
from unittest.mock import patch
from ainglish import estimand, token_measurement
from ainglish.client import MAX_MANIFEST_BYTES, _canonical_json, _validate_attempt_manifest


class TokenManifestBudgetTest(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
