import copy
import unittest
from unittest.mock import Mock

from ainglish import estimand, token_measurement as token
from ainglish.client import manifest_commitment


class TokenComparisonIdentityTest(unittest.TestCase):
    def manifest(self, prefix="source"):
        return {"metric": "token_delta", "models": ["tok-a", "tok-b"],
                "test_set": [{"id": prefix + str(i), "english": f"Complete English {prefix} {i}",
                              "ainglish": f"Marked {prefix} {i}"} for i in range(4)],
                "estimand_contract": estimand.declaration(unit_span="complete sentence",
                    contrast="marked versus careful", population="four fresh authored frames",
                    reducer="least_favourable", aggregation_rule="maximum tokenizer mean")}

    def replica(self, source):
        manifest = self.manifest("fresh")
        manifest["replicates_hash"] = manifest_commitment(source)
        return token.prepare({"manifest": manifest, "replication_target_manifest": source})

    def testFreshInputsHaveDifferentDigestsAndSameSharedIdentity(self):
        source = token.prepare({"manifest": self.manifest()})["manifest"]
        before = copy.deepcopy(source)
        plan = self.replica(source)
        self.assertNotEqual(source["items_sha256"], plan["manifest"]["items_sha256"])
        self.assertEqual(source["comparison_identity"], plan["manifest"]["comparison_identity"])
        self.assertNotIn("items_sha256", source["comparison_identity"])
        self.assertEqual("matched", plan["comparison_identity_status"]["state"])
        self.assertEqual(source, before)

    def testDifferentInstrumentDeclarationDoesNotManufactureAMatch(self):
        source = token.prepare({"manifest": self.manifest()})["manifest"]
        source["comparison_identity"]["extra_rendering_constraint"] = "preserve brackets"
        plan = self.replica(source)
        self.assertEqual("mismatched", plan["comparison_identity_status"]["state"])
        self.assertNotIn("extra_rendering_constraint", plan["manifest"]["comparison_identity"])

    def testV1TargetIsNotSilentlyUpgradedOrCalledMatched(self):
        source = token.prepare({"manifest": self.manifest()})["manifest"]
        source["comparison_identity"].update(kind="ainglish.token-comparison-identity.v1",
                                              items_sha256=source["items_sha256"])
        before = copy.deepcopy(source)
        plan = self.replica(source)
        self.assertEqual("mismatched", plan["comparison_identity_status"]["state"])
        self.assertEqual("ainglish.token-comparison-identity.v1", plan["comparison_identity_status"]["target_kind"])
        self.assertEqual(before, source)
        draft = self.manifest("fresh")
        draft["comparison_identity"] = source["comparison_identity"]
        with self.assertRaisesRegex(ValueError, "Do not copy a v1"):
            token.prepare({"manifest": draft})

    def testOldConsistentPlanRemainsRunnableWithExactCommitment(self):
        plan = token.prepare({"manifest": self.manifest()})
        plan["manifest"]["comparison_identity"].update(kind="ainglish.token-comparison-identity.v1",
                                                        items_sha256=plan["items_sha256"])
        plan["manifest_commitment"] = manifest_commitment(plan["manifest"])
        plan.pop("comparison_identity_status")
        before = copy.deepcopy(plan)
        result = token.run_prepared(plan, "11111111-2222-4333-8444-555555555555",
                                    encoder_factory=lambda name: token._FakeEncoding(1))
        self.assertEqual(plan, before)
        self.assertEqual(plan["manifest"], result["payload"]["manifest"])
        token.verify_payload(result["payload"], encoder_factory=lambda name: token._FakeEncoding(1))

    def testContradictoryOldPlanAndPayloadRefuseBeforeEncoder(self):
        plan = token.prepare({"manifest": self.manifest()})
        payload = token.run_prepared(plan, "11111111-2222-4333-8444-555555555555",
                                    encoder_factory=lambda name: token._FakeEncoding(1))["payload"]
        for obj in [plan["manifest"], payload["manifest"]]:
            obj["comparison_identity"].update(kind="ainglish.token-comparison-identity.v1",
                                               items_sha256="a" * 64)
        plan["manifest_commitment"] = manifest_commitment(plan["manifest"])
        encoder = Mock(side_effect=AssertionError("no encoding permitted"))
        with self.assertRaisesRegex(ValueError, "conflicts with the frozen design"):
            token.run_prepared(plan, "11111111-2222-4333-8444-555555555555", encoder_factory=encoder)
        with self.assertRaisesRegex(ValueError, "conflicts with the frozen design"):
            token.verify_payload(payload, encoder_factory=encoder)
        encoder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
