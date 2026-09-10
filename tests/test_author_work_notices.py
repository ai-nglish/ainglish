"""Exact wire contract; no network or automatic writes from read helpers."""
import unittest
from ainglish.client import AinglishClient


class NoticeProbe(AinglishClient):
    def __init__(self):
        super().__init__(use_env=False)
        self.calls = []

    def get(self, path, auth=False):
        self.calls.append(("GET", path, auth))
        return {"active": None}

    def post(self, path, payload, auth=True, idempotency_key=None):
        self.calls.append(("POST", path, payload, auth, idempotency_key))
        return {"notice": {"effect": "advisory_only"}}


class AuthorNoticeTests(unittest.TestCase):
    def test_exact_public_read_and_author_write(self):
        c = NoticeProbe()
        self.assertEqual(c.author_work_notices("a/b"), {"active": None})
        c.set_author_work_notice("a/b", "pause_measurements", "Read the plan.",
                                 expected_content_digest="a" * 64, expected_notice_id=None,
                                 idempotency_key="notice-request")
        self.assertEqual(c.calls, [
            ("GET", "/api/v1/proposals/a%2Fb/work-notices", False),
            ("POST", "/api/v1/proposals/a%2Fb/work-notices", {
                "kind": "pause_measurements", "reason": "Read the plan.",
                "expected_content_digest": "a" * 64, "expected_notice_id": None}, True, "notice-request")])

    def test_invalid_input_cannot_make_a_request(self):
        c = NoticeProbe()
        base = dict(slug="slug", kind="clear", reason="No longer needed.", expected_content_digest="a" * 64,
                    expected_notice_id=None, idempotency_key="notice-request")
        for change in ({"kind": "block_voting"}, {"reason": ""}, {"reason": "x" * 2001},
                       {"expected_content_digest": "wrong"}, {"expected_notice_id": "wrong"},
                       {"idempotency_key": "short"}, {"slug": None}):
            with self.assertRaises(ValueError):
                c.set_author_work_notice(**(base | change))
        self.assertEqual(c.calls, [])
