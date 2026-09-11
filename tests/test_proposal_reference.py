"""Copied identities work before the detail server learns IDs; no arbitrary URL fetches."""
import copy
import unittest

from ainglish.client import AinglishClient, AinglishError

ID = "a-3fmyebhemzm02fds"


class Probe(AinglishClient):
    def __init__(self, **kwargs):
        super().__init__(use_env=False, **kwargs)
        self.calls = []
        self.namespace = {"proposal_public_id": ID, "current_slug": "renamed-canonical"}
        self.detail = {"public_id": ID, "slug": "renamed-canonical", "stage": "superseded",
                       "superseded_by": "different-version"}

    def get(self, path, auth=False):
        self.calls.append((path, auth))
        if path.endswith("/slug-history"):
            if isinstance(self.namespace, Exception):
                raise self.namespace
            return copy.deepcopy(self.namespace)
        if path == "/api/v1/proposals/" + ID:
            raise AssertionError("older detail server is slug-only")
        return copy.deepcopy(self.detail)


class ProposalReferenceTests(unittest.TestCase):
    def test_public_id_and_copied_urls_resolve_exact_version(self):
        for value in (ID, ID.upper(), "https://ainglish.org/proposals/" + ID,
                      "https://ainglish.org/proposals/" + ID + "/#ratification",
                      "https://ainglish.org/register/" + ID):
            with self.subTest(value=value):
                c = Probe()
                self.assertEqual(c.proposal(value, authenticated=True), c.detail)
                self.assertEqual(c.calls, [("/api/v1/proposals/" + ID + "/slug-history", False),
                                           ("/api/v1/proposals/renamed-canonical", True)])

    def test_slug_keyword_and_alias_keep_single_read_and_public_default(self):
        for value in ("old-alias", "https://ainglish.org/proposals/old-alias"):
            c = Probe()
            self.assertEqual(c.proposal(slug=value), c.detail)
            self.assertEqual(c.calls, [("/api/v1/proposals/old-alias", False)])

    def test_bad_and_foreign_references_make_no_request(self):
        values = [None, 4, "", "x/y", "a\nslug", "//evil.test/proposals/x", "https://evil.test/proposals/" + ID,
                  "https://ainglish.org.evil.test/proposals/" + ID,
                  "https://user@ainglish.org/proposals/" + ID,
                  "http://ainglish.org/proposals/" + ID, "https://ainglish.org:444/proposals/" + ID,
                  "https://ainglish.org/proposals/" + ID + "?token=x",
                  "https://ainglish.org/api/v1/proposals/" + ID,
                  "https://ainglish.org/proposals/foo%2fbar", "https://ainglish.org/register/slug",
                  "https://ainglish.org/proposals/../register", "https://ainglish.org/proposals/" + ID + "/history"]
        for value in values:
            with self.subTest(value=value):
                c = Probe()
                with self.assertRaises(ValueError):
                    c.proposal(value, authenticated=True)
                self.assertEqual(c.calls, [])

    def test_configured_origin_is_respected(self):
        c = Probe(base_url="http://localhost:8123")
        self.assertEqual(c.proposal("http://localhost:8123/proposals/" + ID), c.detail)
        with self.assertRaises(ValueError):
            c.proposal("https://ainglish.org/proposals/" + ID)
        self.assertEqual(len(c.calls), 2)

    def test_legacy_literal_slug_encoding_is_preserved(self):
        c = Probe()
        c.proposal("some slug", authenticated=True)
        self.assertEqual(c.calls, [("/api/v1/proposals/some%20slug", True)])

    def test_namespace_mismatch_or_unsafe_slug_fails_before_detail(self):
        for namespace in ({}, [], {"proposal_public_id": "a-0000000000000000", "current_slug": "renamed-canonical"},
                          {"proposal_public_id": ID, "current_slug": "../elsewhere"},
                          {"proposal_public_id": ID, "current_slug": ID}):
            c = Probe()
            c.namespace = namespace
            with self.assertRaisesRegex(ValueError, "namespace"):
                c.proposal(ID)
            self.assertEqual(len(c.calls), 1)

    def test_detail_mismatch_does_not_retry_or_follow_successor(self):
        for field, value in (("public_id", "a-0000000000000000"), ("slug", "changed-again")):
            c = Probe()
            c.detail[field] = value
            with self.assertRaisesRegex(ValueError, "identity changed"):
                c.proposal(ID)
            self.assertEqual(len(c.calls), 2)

    def test_hidden_namespace_refusal_preserved_without_fallback(self):
        c = Probe()
        c.namespace = AinglishError(404, {"error": "not_found"})
        with self.assertRaises(AinglishError) as caught:
            c.proposal(ID, authenticated=True)
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(len(c.calls), 1)


if __name__ == "__main__":
    unittest.main()
