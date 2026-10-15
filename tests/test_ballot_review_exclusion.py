import unittest
from ainglish.client import AinglishClient


class Probe(AinglishClient):
    def __init__(self):
        super().__init__(use_env=False)
        self.calls = []

    def _request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"revision": 2, "advisory_only": True}


class ReviewExclusionTests(unittest.TestCase):
    def test_read_and_explicit_write_are_private_and_reference_preserving(self):
        c = Probe()
        self.assertEqual(2, c.ballot_review_exclusion(" a/b ")["revision"])
        c.set_ballot_review_exclusion("a/b", excluded=True, reason="verified_evidence",
                                     detail="I audited this bank off-register.", expected_revision=2)
        self.assertEqual("GET", c.calls[0][0])
        self.assertEqual("PUT", c.calls[1][0])
        for _, path, kwargs in c.calls:
            self.assertEqual("/api/v1/me/ballot-review-exclusions/a%2Fb", path)
            self.assertTrue(kwargs["auth"])
        self.assertEqual(2, c.calls[1][2]["payload"]["expected_revision"])

    def test_correction_is_explicit(self):
        c = Probe()
        c.set_ballot_review_exclusion("a-example", excluded=False, reason="correction",
                                     detail="I selected the wrong proposal version.", expected_revision=3)
        self.assertFalse(c.calls[0][2]["payload"]["excluded"])

    def test_invalid_values_do_not_contact_server(self):
        c = Probe()
        base = dict(proposal="a-example", excluded=True, reason="verified_evidence",
                    detail="A concrete explanation of the conflict.", expected_revision=0)
        for change in ({"proposal": ""}, {"excluded": 1}, {"excluded": "true"},
                       {"reason": "correction"}, {"reason": "unknown"}, {"detail": "short"},
                       {"detail": "x" * 1001}, {"expected_revision": True},
                       {"expected_revision": -1}, {"expected_revision": 0.0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                c.set_ballot_review_exclusion(**(base | change))
        self.assertEqual([], c.calls)
