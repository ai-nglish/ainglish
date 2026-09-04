"""Offline workflow boundaries: no credentials, readers or live writes."""
import copy
import io
import json
import unittest
from unittest.mock import patch

from ainglish.client import AinglishClient, manifest_commitment

ID = "a-0000000000000000"
ATTEMPT = "00000000-0000-4000-8000-000000000001"


class Probe(AinglishClient):
    def __init__(self):
        super().__init__(use_env=False)
        self.calls = []
        self.payload = {"attempt_id": ATTEMPT, "metric": "token_delta", "value": 2,
                        "manifest": {"metric": "token_delta", "test_set": [["old", "new"]]}}
        self.state = {"attempt_id": ATTEMPT, "proposal": "current-slug", "state": "open",
                      "pin": {"manifest_commitment": manifest_commitment(self.payload["manifest"])},
                      "minter": {"sub": "me"}, "measurement_ref": None}
        self.card = {"public_id": ID, "slug": "current-slug", "stage": "seconded",
                     "metric": "token_delta", "action": {"method": "POST"}}
        self.snapshot = {"selection": {"mode": "proposal", "public_id": ID,
                                       "display_cap_applied": False},
                         "suggestions": [self.card], "blocked_suggestions": [],
                         "generated_at": "2026-09-04T00:00:00Z", "budgets": {}}

    def get(self, path, auth=False):
        self.calls.append(("GET", path, auth))
        if path.startswith("/api/v1/me/suggestions"):
            return copy.deepcopy(self.snapshot)
        if path.startswith("/api/v1/proposals/"):
            return {"public_id": ID, "slug": "current-slug", "stage": "seconded"}
        if path.startswith("/api/v1/attempts/"):
            return copy.deepcopy(self.state)
        if path == "/api/v1/me":
            return {"sub": "me"}
        return {"kind": "fixture.runbook", "runbooks": []}

    def post(self, path, payload, **kwargs):
        self.calls.append(("POST", path, copy.deepcopy(payload)))
        self.state.update(state="completed", measurement_ref="a" * 64)
        return {"ok": True}


class WorkTests(unittest.TestCase):
    def test_exact_query_and_runbook_methods(self):
        c = Probe()
        c.suggestions()
        c.suggestions(proposal=ID.upper())
        c.agent_runbooks()
        c.agent_runbook("dispute-settlement")
        self.assertEqual(c.calls, [
            ("GET", "/api/v1/me/suggestions", True),
            ("GET", "/api/v1/me/suggestions?proposal=" + ID, True),
            ("GET", "/api/v1/agent-runbooks", False),
            ("GET", "/api/v1/agent-runbooks/dispute-settlement", False)])
        for bad in ("", "slug", {}, "https://ainglish.org/proposals/" + ID):
            with self.assertRaises(ValueError):
                c.suggestions(proposal=bad)
        with self.assertRaises(ValueError):
            c.agent_runbook("../private")

    def test_read_only_package_and_missing_exact_target(self):
        c = Probe()
        p = c.work_package(ID, metric="token_delta")
        self.assertEqual(p["status"], "offered")
        self.assertTrue(all(row[0] == "GET" for row in c.calls))
        p["suggestions"][0]["metric"] = "changed"
        self.assertEqual(c.card["metric"], "token_delta")
        self.assertEqual(c.work_package(ID, metric="comprehension_accuracy_delta")["status"], "not_offered")
        self.assertEqual(c.work_package(ID, replicates_hash="a" * 64)["status"], "not_offered")
        c.snapshot["selection"]["display_cap_applied"] = True
        with self.assertRaises(ValueError):
            c.work_package(ID)

    def test_stale_and_budget_blocked_never_become_offered(self):
        c = Probe()
        c.snapshot["suggestions"] = []
        c.snapshot["blocked_suggestions"] = [c.card]
        self.assertEqual(c.work_package(ID)["status"], "blocked")
        c.card["stage"] = "ratified"
        self.assertEqual(c.work_package(ID)["status"], "stale")

    def test_saved_result_publishes_once_and_recovery_does_not_rerun(self):
        c = Probe()
        original = copy.deepcopy(c.payload)
        with patch("ainglish.panel.run_panel", side_effect=AssertionError("no inference on recovery")):
            result = c.resume_measurement(ID, c.payload)
            self.assertFalse(result["already_completed"])
            self.assertTrue(c.resume_measurement(ID, c.payload)["already_completed"])
        posts = [row for row in c.calls if row[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2], original)
        self.assertEqual(c.payload, original)

    def test_recovery_rejects_mismatched_and_terminal_attempts(self):
        for change in ({"proposal": "another"}, {"state": "aborted"},
                       {"minter": {"sub": "other"}}, {"pin": {"manifest_commitment": "b" * 64}}):
            c = Probe()
            c.state.update(change)
            with self.assertRaises(ValueError):
                c.resume_measurement(ID, c.payload)
            self.assertFalse(any(row[0] == "POST" for row in c.calls))

    def test_submit_saved_cli_never_fetches_items_or_calls_readers(self):
        from ainglish import panel
        c = Probe()
        with patch("ainglish.client.AinglishClient", return_value=c), \
                patch("builtins.open", return_value=io.StringIO(json.dumps(c.payload))), \
                patch("ainglish.panel.fetch_items", side_effect=AssertionError("no item fetch")), \
                patch("ainglish.panel.run_panel", side_effect=AssertionError("no reader run")), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(panel.main(["panel", "submit-saved", ID, "saved.json"]), 0)


if __name__ == "__main__":
    unittest.main()
