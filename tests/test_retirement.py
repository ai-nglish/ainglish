import unittest
from unittest.mock import Mock
from ainglish.client import AinglishClient


class RetirementTest(unittest.TestCase):
    def client(self):
        client = object.__new__(AinglishClient)
        client.post = Mock(return_value={"stage": "withdrawn", "withdrawal": {"reason": "author_retired"}})
        return client

    def test_exact_route_and_explanation_without_claiming_local_eligibility(self):
        client = self.client()
        explanation = "  I no longer pursue this version; its evidence stays public.  "
        self.assertEqual("withdrawn", client.retire("name/with spaces", explanation)["stage"])
        client.post.assert_called_once_with("/api/v1/proposals/name%2Fwith%20spaces/retire", {"explanation": explanation})

    def test_bad_inputs_never_make_a_request_and_unicode_boundary_matches_server(self):
        client = self.client()
        for slug in [None, False, [], "", " "]:
            with self.assertRaises(ValueError): client.retire(slug, "Reason")
        for reason in [None, False, [], "", " \n", "界" * 2001]:
            with self.assertRaises(ValueError): client.retire("proposal", reason)
        client.post.assert_not_called()
        client.retire("proposal", "界" * 2000)
        client.post.assert_called_once()

    def test_withdrawal_contract_is_not_expanded(self):
        client = self.client()
        with self.assertRaises(ValueError): client.withdraw("proposal", "author_retired")
        client.post.assert_not_called()


if __name__ == "__main__": unittest.main()
