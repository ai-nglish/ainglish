"""Private diagnostics wrappers: no live credentials or submissions."""
import copy
import unittest
from unittest.mock import Mock

from ainglish.client import AinglishClient
from test_work import Probe, ID

RECEIPT = "00000000-0000-4000-8000-000000000001"
TASK = "a" * 64


class ParticipationDiagnosticsTests(unittest.TestCase):
    def client(self):
        c = AinglishClient(use_env=False)
        c.get = Mock(return_value={"visibility": "admins_only", "groups": []})
        c.post = Mock(return_value={"visibility": "submitter_and_admins", "replayed": False})
        return c

    def test_feedback_is_one_explicit_authenticated_private_write(self):
        c = self.client()
        result = c.suggestion_feedback(RECEIPT, TASK, "blocked", reason="reader_unavailable", detail="No exact reader access.")
        c.post.assert_called_once_with("/api/v1/me/suggestions/feedback", {
            "receipt_id": RECEIPT, "task_key": TASK, "status": "blocked",
            "reason": "reader_unavailable", "detail": "No exact reader access."}, auth=True)
        c.get.assert_not_called()
        self.assertEqual("submitter_and_admins", result["visibility"])

    def test_feedback_validation_precedes_network(self):
        c = self.client()
        base = {"receipt_id": RECEIPT, "task_key": TASK, "status": "accepted"}
        for change in ({"receipt_id": "bad"}, {"task_key": "bad"}, {"status": "completed"},
                       {"status": []}, {"status": "blocked"}, {"reason": "not a code"},
                       {"reason": False}, {"detail": []}, {"detail": "界" * 1001}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    c.suggestion_feedback(**dict(base, **change))
        c.post.assert_not_called()
        c.suggestion_feedback(**base, detail="界" * 1000)
        c.post.assert_called_once()

    def test_admin_read_has_bounded_exact_filters_and_no_fallback(self):
        c = self.client()
        c.participation_diagnostics(days=14, actor="colony:fixture", page=2, page_size=10)
        c.get.assert_called_once_with("/api/v1/admin/participation?days=14&page=2&page_size=10&actor=colony%3Afixture", auth=True)
        c.post.assert_not_called()
        c.get.side_effect = RuntimeError("admin access refused")
        with self.assertRaisesRegex(RuntimeError, "admin access refused"):
            c.participation_diagnostics()
        self.assertEqual(2, c.get.call_count)

    def test_bad_admin_filters_never_make_a_request(self):
        c = self.client()
        for values in ({"days": 0}, {"days": 31}, {"page": True}, {"page": 10001},
                       {"page_size": 51}, {"page_size": "20"}, {"actor": "a&days=30"}, {"actor": []}):
            with self.assertRaises(ValueError):
                c.participation_diagnostics(**values)
        c.get.assert_not_called()

    def test_work_package_preserves_optional_receipt_without_automatic_feedback(self):
        c = Probe()
        self.assertIsNone(c.work_package(ID)["observation"])
        c.snapshot["observation"] = {"recorded": True, "receipt_id": RECEIPT}
        c.card["task_key"] = TASK
        frozen = copy.deepcopy(c.snapshot)
        package = c.work_package(ID)
        self.assertEqual(RECEIPT, package["observation"]["receipt_id"])
        self.assertEqual(TASK, package["suggestions"][0]["task_key"])
        package["observation"]["receipt_id"] = "changed"
        self.assertEqual(frozen, c.snapshot)
        self.assertTrue(all(call[0] == "GET" for call in c.calls))


if __name__ == "__main__":
    unittest.main()
