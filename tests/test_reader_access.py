import copy
import unittest
from unittest.mock import patch
from ainglish.client import AinglishClient, manifest_commitment
from ainglish.reader_access import assess


class ReaderAccessTest(unittest.TestCase):
    def setUp(self):
        self.reader = dict(name="example", provider="ollama", model="example:7b",
            precision="q4_k_m", api="openai", answer_protocol="opaque-choice-v1",
            max_tokens=64, temperature=0, seed="provider-default", top_p="provider-default",
            top_k="provider-default", num_ctx="provider-default", reasoning_effort="provider-default",
            model_digest="sha256:" + "a" * 64, base_url="http://private-host:11434/v1",
            instrument_preparation={"entry_point": "prepare_reader_instruments", "binding": "ollama:/api/tags"})
        self.manifest = {"metric": "comprehension_accuracy_delta", "models": ["example@q4_k_m"],
                         "readers": [self.reader]}
        self.source = self.source_for(self.manifest)

    def source_for(self, manifest):
        return {"metric": manifest["metric"], "manifest": manifest,
                "manifest_hash": manifest_commitment(manifest)}

    def test_matching_is_only_a_supplied_snapshot_and_keeps_secrets_out(self):
        local = dict(self.reader, base_url="http://my-own-host:11435/v1", api_key="secret-fixture")
        result = assess(self.source, [local])
        self.assertEqual(result["status"], "matching_inventory")
        self.assertEqual(result["inference_calls"], 0)
        for text in ("private-host", "my-own-host", "secret-fixture"):
            self.assertNotIn(text, str(result))
        self.assertIn("qualification", result["boundary"])

    def test_every_answer_setting_must_match(self):
        for field, value in (("temperature", 0.7), ("model", "example:8b"), ("max_tokens", 128),
                             ("model_digest", "sha256:" + "b" * 64), ("seed", 7),
                             ("answer_protocol", "free-text"), ("num_ctx", 8192),
                             ("provider", "another-provider"), ("temperature", False)):
            with self.subTest(field=field, value=value):
                result = assess(self.source, [dict(self.reader, **{field: value})])
                self.assertEqual(result["status"], "mismatch")
                self.assertIn(field, result["readers"][0]["fields"])

    def test_model_name_alone_is_not_access(self):
        result = assess(self.source, [{"name": "example", "precision": "q4_k_m"}])
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(assess(self.source, [])["status"], "not_in_inventory")
        self.assertEqual(assess(self.source, [dict(self.reader, instrument_preparation={})])["status"], "mismatch")

    def test_missing_source_fields_cannot_be_inferred(self):
        for field in ("precision", "max_tokens", "temperature", "model"):
            manifest = copy.deepcopy(self.manifest)
            del manifest["readers"][0][field]
            self.assertEqual(assess(self.source_for(manifest), [self.reader])["status"], "source_incomplete")

    def test_unbound_alias_is_explicitly_opaque(self):
        manifest = copy.deepcopy(self.manifest)
        del manifest["readers"][0]["model_digest"]
        self.assertEqual(assess(self.source_for(manifest), [self.reader])["status"], "provider_opaque")

    def test_source_commitment_and_metric_checked(self):
        with self.assertRaises(ValueError):
            assess(dict(self.source, manifest_hash="f" * 64), [self.reader])
        with self.assertRaises(ValueError):
            assess(dict(self.source, metric="token_delta"), [self.reader])

    def test_roster_missing_duplicate_or_reordered_is_not_guessed(self):
        for models in ([], ["another@q4_k_m"], ["example@q4_k_m"] * 2):
            manifest = dict(self.manifest, models=models)
            self.assertEqual(assess(self.source_for(manifest), [self.reader])["status"], "source_incomplete")
        with self.assertRaises(ValueError):
            assess(self.source, [self.reader, self.reader])

    def test_does_not_mutate_either_input(self):
        original = copy.deepcopy((self.source, self.reader))
        assess(self.source, [self.reader])
        self.assertEqual(original, (self.source, self.reader))

    def test_client_fetches_only_exact_source(self):
        client = AinglishClient(use_env=False)
        with patch.object(client, "measurement", return_value=self.source) as get:
            result = client.reader_access(self.source["manifest_hash"], [self.reader])
            get.assert_called_once_with(self.source["manifest_hash"])
            self.assertEqual(result["status"], "matching_inventory")
        with self.assertRaises(ValueError):
            client.reader_access("not-a-commitment", [self.reader])


if __name__ == "__main__":
    unittest.main()
