"""Copied identities work before the detail server learns IDs; no arbitrary URL fetches."""
import copy
import unittest

from ainglish.client import AinglishClient, AinglishError, manifest_commitment

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

    def post(self, path, body, **kwargs):
        self.calls.append(("POST", path, copy.deepcopy(body)))
        if path.endswith("/preflight"):
            return {"kind": "ainglish.attempt-preflight.v1", "accepted": True,
                    "manifest_commitment": body["manifest_commitment"],
                    "replication_preparation": {"kind": "ainglish.replication-preparation.v1",
                        "replicates_hash": body["manifest"].get("replicates_hash"),
                        "status": "no_known_obstruction", "known_obstructions": []}}
        return {"accepted": True}


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


class MeasurementReferenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {"metric": "learnability", "models": ["exact-reader"], "items": []}
        self.design = {"estimand": "fixed scope", "admissibility_gates": ["before exposure"],
                       "planned_sample": {"items": 8}}
        self.payload = {"metric": "learnability", "value": 0.7, "manifest": self.manifest}

    def invoke(self, client, method, reference, **extra):
        if method == "measure":
            return client.measure(reference, self.payload)
        if method == "attempts":
            return client.attempts(reference)
        return getattr(client, method)(reference, self.manifest, **self.design, **extra)

    def test_three_writes_resolve_copied_identity_without_changing_frozen_inputs(self):
        for method, suffix in (("preflight_attempt", "attempts/preflight"),
                               ("mint_attempt", "attempts"), ("measure", "measurements")):
            for reference in (ID, ID.upper(), "https://ainglish.org/proposals/" + ID + "#measurements",
                              "https://ainglish.org/register/" + ID):
                with self.subTest(method=method, reference=reference):
                    c = Probe()
                    original = copy.deepcopy(self.payload)
                    self.invoke(c, method, reference)
                    self.assertEqual(c.calls[:2], [("/api/v1/proposals/" + ID + "/slug-history", False),
                                                  ("/api/v1/proposals/renamed-canonical", True)])
                    self.assertEqual(c.calls[-1][1], "/api/v1/proposals/renamed-canonical/" + suffix)
                    body = c.calls[-1][2]
                    self.assertEqual(body["manifest"], self.manifest)
                    self.assertEqual(self.payload, original)
                    if method != "measure":
                        self.assertEqual(body["proposal_revision"], "renamed-canonical")
                        self.assertEqual(body["manifest_commitment"], manifest_commitment(self.manifest))

    def test_canonical_slug_calls_add_no_reads_and_keep_keyword_compatibility(self):
        for method in ("preflight_attempt", "mint_attempt", "measure"):
            c = Probe()
            if method == "measure":
                c.measure(slug="some slug", payload=self.payload)
            else:
                getattr(c, method)(slug="some slug", manifest=self.manifest, **self.design)
            self.assertEqual(len(c.calls), 1)
            self.assertIn("/proposals/some%20slug/", c.calls[0][1])

    def test_explicit_revision_is_not_rewritten(self):
        for method in ("preflight_attempt", "mint_attempt"):
            c = Probe()
            self.invoke(c, method, ID, proposal_revision="old-declared-surface@v2")
            self.assertEqual(c.calls[-1][2]["proposal_revision"], "old-declared-surface@v2")

    def test_hidden_or_mismatched_identity_never_writes(self):
        for method in ("preflight_attempt", "mint_attempt", "measure", "attempts"):
            for failure in ("hidden", "namespace", "detail"):
                with self.subTest(method=method, failure=failure):
                    c = Probe()
                    if failure == "hidden":
                        c.namespace = AinglishError(404, {"error": "not_found"})
                    elif failure == "namespace":
                        c.namespace["proposal_public_id"] = "a-0000000000000000"
                    else:
                        c.detail["public_id"] = "a-0000000000000000"
                    with self.assertRaises((ValueError, AinglishError)):
                        self.invoke(c, method, ID)
                    self.assertFalse(any(row[0] == "POST" for row in c.calls))

    def test_foreign_or_malformed_url_makes_no_request(self):
        for method in ("preflight_attempt", "mint_attempt", "measure", "attempts"):
            for value in ("https://evil.test/proposals/" + ID, "https://ainglish.org/proposals/" + ID + "?secret=x",
                          "https://ainglish.org/proposals/foo%2Fbar", "https://user@ainglish.org/proposals/" + ID):
                c = Probe()
                with self.assertRaises(ValueError):
                    self.invoke(c, method, value)
                self.assertEqual(c.calls, [])

    def test_attempt_list_resolves_id_without_credentialed_reads(self):
        c = Probe()
        c.attempts(ID)
        self.assertEqual(c.calls, [("/api/v1/proposals/" + ID + "/slug-history", False),
                                  ("/api/v1/proposals/renamed-canonical", False),
                                  ("/api/v1/proposals/renamed-canonical/attempts", False)])

    def test_confirmation_preview_and_mint_use_one_checked_identity(self):
        c = Probe()
        self.manifest["replicates_hash"] = "a" * 64
        self.invoke(c, "mint_attempt", ID, for_confirmation=True)
        self.assertEqual(len(c.calls), 4)
        self.assertTrue(c.calls[-2][1].endswith("/renamed-canonical/attempts/preflight"))
        self.assertTrue(c.calls[-1][1].endswith("/renamed-canonical/attempts"))
        self.assertEqual(c.calls[-2][2], c.calls[-1][2])

    def test_identity_is_refreshed_between_operations_and_successor_is_not_followed(self):
        c = Probe()
        self.invoke(c, "preflight_attempt", ID)
        c.namespace["current_slug"] = c.detail["slug"] = "new-canonical"
        self.invoke(c, "mint_attempt", ID)
        self.assertEqual(c.calls[-1][1], "/api/v1/proposals/new-canonical/attempts")
        self.assertEqual(c.detail["stage"], "superseded")
        self.assertFalse(any("different-version" in str(row) for row in c.calls))

    def test_rejected_write_is_not_retried_or_redirected(self):
        class Rejected(Probe):
            def post(self, path, body, **kwargs):
                super().post(path, body, **kwargs)
                raise AinglishError(409, {"error": "proposal_changed"})

        c = Rejected()
        with self.assertRaises(AinglishError):
            self.invoke(c, "measure", ID)
        self.assertEqual(sum(row[0] == "POST" for row in c.calls), 1)


if __name__ == "__main__":
    unittest.main()
