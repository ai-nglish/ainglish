"""Public observed changes, never a fabricated impact receipt or another write."""
import copy
import unittest
from unittest.mock import patch

from ainglish.client import AinglishClient
from ainglish.work import participation_outcome

ID = "a-0000000000000000"


def snapshot():
    return {"public_id": ID, "stage": "measured", "seconds_count": 3, "second_weight": 3,
            "advance_blocked": None, "verdict_class": "screened", "form": "fixture",
            "english_mapping": "Fixture meaning", "evidence_contract": None,
            "evidence_readiness": {"declared": True, "evidence_ready": False,
                "satisfied": ["token_delta"], "missing_evidence": [],
                "unresolved_evidence": ["comprehension_accuracy_delta"], "opposing_evidence": [],
                "work_items": [{"metric": "comprehension_accuracy_delta", "role": "claim_carrier",
                    "state": "replicate_original", "evidence_progress": {"originals": 1,
                        "confirmed_originals": 0, "confirmed_supporting": 0, "confirmed_opposing": 0,
                        "confirmed_inconclusive": 0, "requirement_satisfied": False}}]},
            "ratification": {"readiness": {"ready": True}, "tally": {"yes": 0, "no": 3, "total": 3}},
            "progression_path": {"current_work_section": "needs_evidence_completion",
                                 "current_action": {"what": "Inspect the source", "metric": "comprehension_accuracy_delta"}}}


class ParticipationOutcomeTests(unittest.TestCase):
    def test_no_io_mutation_or_private_fields_and_no_change_is_not_failure(self):
        before = snapshot()
        before["ratification"]["independent_review"] = {"private": "do-not-copy"}
        before["observation"] = {"receipt_id": "private"}
        saved = copy.deepcopy(before)
        c = AinglishClient(use_env=False)
        with patch.object(c, "get", side_effect=AssertionError("no read")), \
                patch.object(c, "post", side_effect=AssertionError("no write")):
            report = c.participation_outcome(before, before)
        self.assertEqual("no_tracked_change", report["comparison"])
        self.assertEqual([], report["changes"])
        self.assertFalse(report["causal_attribution"])
        self.assertNotIn("do-not-copy", str(report))
        report["next_action"]["value"]["what"] = "changed locally"
        self.assertEqual(saved, before)

    def test_more_evidence_without_stage_change_and_an_adverse_ballot_are_visible(self):
        a = snapshot()
        b = copy.deepcopy(a)
        b["ratification"]["tally"].update(no=5, total=5)
        b["evidence_readiness"]["work_items"][0]["evidence_progress"].update(confirmed_originals=1, confirmed_opposing=1)
        report = participation_outcome(a, b, receipt_url="https://ainglish.org/proposals/" + ID)
        changes = {x["field"]: x for x in report["changes"]}
        self.assertEqual(5, changes["ratification.tally.no"]["after"])
        self.assertEqual(1, changes["evidence_work[comprehension_accuracy_delta/claim_carrier].evidence_progress.confirmed_opposing"]["after"])
        self.assertNotIn("stage", changes)
        self.assertFalse(report["receipt"]["verified"])
        self.assertFalse(report["causal_attribution"])

    def test_missing_null_and_zero_are_distinct(self):
        a = {"public_id": ID, "stage": "seconded"}
        b = {"public_id": ID, "stage": "seconded", "seconds_count": 0, "advance_blocked": None}
        report = participation_outcome(a, b)
        self.assertEqual("incomplete", report["comparison"])
        self.assertEqual([], report["changes"])
        unknown = {x["field"]: x for x in report["unknown_fields"]}
        self.assertEqual({"known": True, "value": 0}, unknown["seconds_count"]["after"])
        self.assertEqual({"known": True, "value": None}, unknown["advance_blocked"]["after"])
        self.assertFalse(report["next_action"]["known"])

    def test_terminal_transition_is_not_assumed_ratification(self):
        a, b = snapshot(), snapshot()
        b["stage"] = "vote_failed"
        b["progression_path"]["current_action"] = None
        report = participation_outcome(a, b)
        self.assertEqual({"field": "stage", "before": "measured", "after": "vote_failed"}, report["changes"][0])
        self.assertEqual({"known": True, "value": None}, report["next_action"])

    def test_other_version_invalid_links_and_duplicate_inventory_refuse(self):
        for bad in (None, {}, {"public_id": "slug"}, {"public_id": "a-1111111111111111"}):
            with self.assertRaises(ValueError):
                participation_outcome(snapshot(), bad)
        for url in (False, "http://example.org/receipt", "https://secret@example.org/receipt", "https://example.org/a b"):
            with self.assertRaises(ValueError):
                participation_outcome(snapshot(), snapshot(), receipt_url=url)
        b = snapshot()
        b["evidence_readiness"]["work_items"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate"):
            participation_outcome(snapshot(), b)

    def test_inventory_removal_is_unknown_not_zero_and_content_change_is_flagged(self):
        a, b = snapshot(), snapshot()
        b["evidence_readiness"]["work_items"] = []
        b["form"] = "different claim"
        report = participation_outcome(a, b)
        self.assertEqual(["form"], report["content_changed"])
        self.assertTrue(any(x["field"].startswith("evidence_work[") for x in report["unknown_fields"]))
        self.assertFalse(any(x["field"].startswith("evidence_work[") for x in report["changes"]))

    def test_one_sided_content_presence_is_unknown_not_unchanged_or_a_claimed_edit(self):
        for field in ("form", "english_mapping", "evidence_contract"):
            for value in (None, {"fixture": ["value"]}):
                for reverse in (False, True):
                    with self.subTest(field=field, value=value, reverse=reverse):
                        a, b = snapshot(), snapshot()
                        del a[field]
                        b[field] = copy.deepcopy(value)
                        if reverse:
                            a, b = b, a
                        saved = copy.deepcopy((a, b))
                        report = participation_outcome(a, b)
                        unknown = {x["field"]: x for x in report["unknown_fields"]}
                        self.assertEqual("incomplete", report["comparison"])
                        self.assertEqual([], report["content_changed"])
                        self.assertEqual([], report["changes"])
                        self.assertNotIn(field, report["unchanged_fields"])
                        self.assertEqual({"known": reverse, "value": value if reverse else None}, unknown[field]["before"])
                        self.assertEqual({"known": not reverse, "value": None if reverse else value}, unknown[field]["after"])
                        self.assertEqual(saved, (a, b))

    def test_content_missing_on_both_sides_is_unknown(self):
        a, b = snapshot(), snapshot()
        del a["evidence_contract"], b["evidence_contract"]
        report = participation_outcome(a, b)
        self.assertEqual("incomplete", report["comparison"])
        unknown = {x["field"]: x for x in report["unknown_fields"]}
        self.assertFalse(unknown["evidence_contract"]["before"]["known"])
        self.assertFalse(unknown["evidence_contract"]["after"]["known"])

    def test_explicit_null_to_contract_is_a_known_content_change(self):
        for reverse in (False, True):
            a, b = snapshot(), snapshot()
            b["evidence_contract"] = {"claim_carrier": ["comprehension_accuracy_delta"]}
            if reverse:
                a, b = b, a
            report = participation_outcome(a, b)
            self.assertEqual("observed_changes", report["comparison"])
            self.assertEqual(["evidence_contract"], report["content_changed"])
            self.assertEqual([{"field": "evidence_contract", "before": a["evidence_contract"],
                               "after": b["evidence_contract"]}], report["changes"])
            report["changes"][0]["before" if reverse else "after"]["claim_carrier"].append("local")
            self.assertNotIn("local", str((a, b)))


if __name__ == "__main__":
    unittest.main()
