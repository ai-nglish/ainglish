"""Resource triage must not manufacture access, independence or executable work."""
import copy
import unittest

from ainglish.work import resource_advice


def snapshot(reader=True, names=None):
    card = {"task_key": "task-a", "public_id": "a-0000000000000000",
            "replicates_hash": "b" * 64, "executable_now": True,
            "action": {"url": "/api/v1/proposals/example/measurements"},
            "preparation": {"requires_reader": reader,
                            "named_instruments": ["Reader-Flash", "Reader-Pro"] if names is None else names}}
    blocked = copy.deepcopy(card)
    blocked.update(task_key="task-b", executable_now=False, blocked_by="daily_budget")
    return {"suggestions": [card], "blocked_suggestions": [blocked],
            "selection": {"display_cap_applied": True},
            "brief": {"presentation_truncated": True},
            "observation": {"receipt_id": "private-receipt"}}


class ResourceAdviceTest(unittest.TestCase):
    def advice(self, data=None, **kwargs):
        return resource_advice(snapshot() if data is None else data, **kwargs)["suggestions"][0]["resource_advice"]

    def test_default_and_partial_inventory_are_unknown_not_unavailable(self):
        self.assertEqual(self.advice()["state"], "needs_check")
        advice = self.advice(reader_access=True, instruments={"Reader-Flash": True})
        self.assertEqual(advice["state"], "needs_check")
        self.assertEqual(advice["unknown_instruments"], ["Reader-Pro"])
        self.assertEqual(advice["missing_declared"], [])

    def test_exact_names_no_family_alias_or_lineage_inference(self):
        advice = self.advice(reader_access=True, instruments={"reader-flash": True, "Reader-Pro": False})
        self.assertEqual(advice["state"], "declared_unavailable")
        self.assertEqual(advice["unknown_instruments"], ["Reader-Flash"])
        advice = self.advice(reader_access=True, instruments={"Reader-Flash": True, "Reader-Pro": True})
        self.assertEqual(advice["state"], "declared_match")
        self.assertFalse(advice["availability_verified"])
        self.assertIn("lineage", advice["next"])

    def test_reader_and_local_access_are_not_interchangeable(self):
        self.assertEqual(self.advice(reader_access=False, local_compute=True)["state"], "declared_unavailable")
        token = snapshot(False, ["cl100k_base"])
        self.assertEqual(self.advice(token, local_compute=True, instruments={"cl100k_base": True})["state"], "declared_match")
        self.assertEqual(self.advice(token, reader_access=True)["state"], "needs_check")

    def test_unknown_metric_missing_preparation_and_original_roster_are_not_cpu_promises(self):
        for data in (snapshot(None), snapshot(True, []), snapshot(False, [])):
            self.assertEqual(self.advice(data, reader_access=True, local_compute=True)["state"], "needs_check")
        data = snapshot()
        del data["suggestions"][0]["preparation"]
        self.assertEqual(self.advice(data, reader_access=True, local_compute=True)["state"], "needs_check")

    def test_snapshot_order_blocked_tasks_hashes_receipts_and_caps_are_preserved(self):
        before = snapshot()
        result = resource_advice(before, reader_access=True, instruments={"Reader-Flash": True, "Reader-Pro": True})
        for group in ("suggestions", "blocked_suggestions"):
            for i, card in enumerate(result[group]):
                advice = card.pop("resource_advice")
                self.assertEqual(advice["offer_group"], group)
                self.assertEqual(card, before[group][i])
        del result["resource_advice"]
        self.assertEqual(result, before)
        result["observation"]["receipt_id"] = "changed"
        self.assertEqual(before["observation"]["receipt_id"], "private-receipt")

    def test_non_measurement_work_is_not_eligible_by_virtue_of_resources(self):
        data = snapshot()
        data["suggestions"][0]["action"]["url"] = "/api/v1/proposals/example/votes"
        self.assertEqual(self.advice(data)["state"], "not_assessed")

    def test_bad_declarations_fail_without_io(self):
        for kwargs in ({"reader_access": 1}, {"local_compute": "yes"}, {"instruments": []},
                       {"instruments": {"x": 1}}, {"instruments": {" x": True}},
                       {"instruments": {None: True}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                resource_advice(snapshot(), **kwargs)
        for data in (None, {"suggestions": {}}, {"suggestions": [None]}):
            with self.assertRaises(ValueError):
                resource_advice(data)


if __name__ == "__main__":
    unittest.main()
