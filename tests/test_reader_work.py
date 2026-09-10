"""Work discovery must not mistake capped absence, access or source drift for eligibility."""
import copy
import unittest
from unittest.mock import Mock

from ainglish.client import AinglishClient, manifest_commitment

IDS = ["a-0000000000000000", "a-0000000000000001"]


class ReaderWorkTest(unittest.TestCase):
    def setUp(self):
        self.reader = dict(name="cached", provider="ollama", model="cached:7b", api="openai",
            answer_protocol="opaque-choice-v1", max_tokens=64, temperature=0,
            seed="provider-default", top_p="provider-default", top_k="provider-default",
            num_ctx="provider-default", reasoning_effort="provider-default",
            model_digest="sha256:" + "a" * 64, base_url="http://private-host:11434/v1",
            api_key="private-fixture", instrument_preparation={"binding": "ollama:/api/tags"})
        self.manifest = {"metric": "comprehension_accuracy_delta", "models": ["cached"],
                         "readers": [self.reader]}
        self.target = manifest_commitment(self.manifest)
        self.source = dict(manifest=self.manifest, manifest_hash=self.target,
                           metric=self.manifest["metric"], evidence_state="valid", is_replication=False,
                           proposal={"public_id": IDS[0], "slug": "example", "stage": "measured"})
        self.client = AinglishClient(use_env=False)
        self.snapshots = {pid: self.snapshot(pid) for pid in IDS}
        self.client.suggestions = Mock(side_effect=lambda proposal: copy.deepcopy(self.snapshots[proposal]))
        self.client.measurement = Mock(return_value=copy.deepcopy(self.source))
        self.client.post = Mock(side_effect=AssertionError("discovery cannot write"))

    def snapshot(self, pid):
        return {"selection": {"mode": "proposal", "public_id": pid, "display_cap_applied": False},
            "generated_at": "2026-09-09T00:00:00Z", "suggestions": [{"public_id": pid,
            "slug": "example", "stage": "measured", "metric": self.manifest["metric"],
            "replicates_hash": self.target, "progression_effect": {"guarantees_progression": False}}],
            "blocked_suggestions": []}

    def test_exact_queries_find_work_even_without_global_discovery(self):
        second = copy.deepcopy(self.source)
        second["proposal"]["public_id"] = IDS[1]
        self.client.measurement.side_effect = [copy.deepcopy(self.source), second]
        result = self.client.reader_work([self.reader], IDS)
        self.assertEqual([c.kwargs for c in self.client.suggestions.call_args_list],
                         [{"proposal": pid} for pid in IDS])
        self.assertEqual(result["matched_sources"], 2)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["inference_calls"], 0)
        self.client.post.assert_not_called()
        for secret in ("private-host", "private-fixture"):
            self.assertNotIn(secret, str(result))

    def test_all_inputs_checked_before_network(self):
        for inventory, proposals, limit in (([{}], IDS, 20), ([self.reader] * 2, IDS, 20),
                ([], [], 20), ([], IDS * 11, 20), ([], [IDS[0]] * 2, 20), ([], ["slug"], 20),
                ([], IDS, True), ([], IDS, 0), ([], IDS, 101)):
            with self.subTest(proposals=proposals, limit=limit), self.assertRaises(ValueError):
                self.client.reader_work(inventory, proposals, max_sources=limit)
        self.client.suggestions.assert_not_called()

    def test_bound_and_dedup_are_explicit(self):
        self.snapshots[IDS[0]]["suggestions"] *= 2
        result = self.client.reader_work([self.reader], IDS, max_sources=1)
        self.assertEqual(result["sources_checked"], 1)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(len(result["unchecked_sources"]), 1)
        self.assertTrue(result["truncated"])
        self.client.measurement.assert_called_once_with(self.target)

    def test_blocked_nonreader_and_original_work_is_not_fetched(self):
        snap = self.snapshots[IDS[0]]
        blocked = snap["suggestions"].pop()
        blocked["blocked_reason"] = "independence"
        snap["blocked_suggestions"].append(blocked)
        snap["suggestions"] = [dict(blocked, metric="token_delta"), dict(blocked, replicates_hash=None)]
        result = self.client.reader_work([self.reader], [IDS[0]])
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["proposals"][0]["blocked_suggestions"][0]["blocked_reason"], "independence")
        self.client.measurement.assert_not_called()

    def test_missing_and_wrong_echo_fail_closed(self):
        for change in ({}, {"mode": "all"}, {"mode": "proposal", "public_id": IDS[1], "display_cap_applied": False},
                       {"mode": "proposal", "public_id": IDS[0], "display_cap_applied": True}):
            self.snapshots[IDS[0]]["selection"] = change
            with self.assertRaises(ValueError):
                self.client.reader_work([], [IDS[0]])
        self.client.measurement.assert_not_called()

    def test_unknown_access_and_opposing_result_remain_visible(self):
        self.client.measurement.return_value.update(value=-25, stance="opposing")
        self.assertEqual(self.client.reader_work([self.reader], [IDS[0]])["matched_sources"], 1)
        result = self.client.reader_work([], [IDS[0]])
        self.assertEqual(result["candidates"][0]["access"]["status"], "not_in_inventory")
        self.assertIn("No match is not a claim", result["boundary"])

    def test_stale_and_malformed_sources_cannot_match(self):
        for change, status in (({"evidence_state": "invalid"}, "source_changed"),
                ({"retraction": {"reason": "withdrawn"}}, "source_changed"),
                ({"is_replication": True}, "source_changed"), ({"is_replication": None}, "source_changed"),
                ({"confirmed": True}, "source_changed"),
                ({"proposal": {"public_id": IDS[1]}}, "invalid_source"),
                ({"proposal": {"public_id": IDS[0], "slug": "renamed", "stage": "measured"}}, "source_changed"),
                ({"metric": "token_delta"}, "invalid_source"),
                ({"manifest_hash": "b" * 64}, "invalid_source"),
                ({"manifest": dict(self.manifest, metric="learnability")}, "invalid_source")):
            self.client.measurement.return_value = dict(self.source, **change)
            self.assertEqual(self.client.reader_work([self.reader], [IDS[0]])["candidates"][0]["access"]["status"], status)

    def test_network_failure_propagates_without_no_work_verdict(self):
        self.client.measurement.side_effect = RuntimeError("network down")
        with self.assertRaisesRegex(RuntimeError, "network down"):
            self.client.reader_work([], IDS)

    def test_inputs_and_server_snapshots_not_mutated(self):
        before = copy.deepcopy((self.snapshots, self.reader))
        result = self.client.reader_work([self.reader], IDS)
        result["candidates"][0]["progression_effect"]["guarantees_progression"] = True
        self.assertEqual(before, (self.snapshots, self.reader))


if __name__ == "__main__":
    unittest.main()
