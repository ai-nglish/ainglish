"""Synthetic software controls only: no model calls, live attempts or evidence."""
import copy
import hashlib
import itertools
import json
import traceback
import unittest
from unittest import mock

from ainglish import paired_study as paired
from ainglish.client import _canonical_json


def plan(unit="pair"):
    return {"kind": paired.KIND, "proposal_id": "synthetic-test-not-a-proposal", "seed": 17,
            "population_scope": "Exposed synthetic software fixtures; no population inference.",
            **paired._POLICY, "resampling_unit": unit,
            "readers": [{"id": "reader-1", "instrument_ref": "synthetic-stub-v1"},
                        {"id": "reader-2", "instrument_ref": "synthetic-stub-v2"}],
            "pairs": [{"id": f"pair-{i}", "family": f"family-{i % 2}", "twins": [
                {"id": f"twin-{j}", "world_id": f"world-{i}-{j}",
                 "english": f"E secret-{i}-{j}", "ainglish": f"A secret-{i}-{j}",
                 "question": "Choose.", "options": ["x", "y"], "answer": ["x", "y"][j]}
                for j in range(2)]} for i in range(4)]}


def run(p=None, reader=lambda *args: "x"):
    p = p or plan()
    events = []
    result = paired.execute(p, reader, registration={"reference": "synthetic-only",
                            "plan_sha256": paired.validate(p)}, record=events.append)
    return result, events


def label(text, correct=True):
    j = int(text.rsplit("-", 1)[1])
    return ["x", "y"][j if correct else 1-j]


class PairedStudyTest(unittest.TestCase):
    def test_complete_roster_individual_prompts_and_fixed_order(self):
        p = plan()
        calls = paired.planned_calls(p)
        self.assertEqual(len(calls), 32)
        self.assertEqual(calls, paired.planned_calls(p))
        observed = []
        def reader(identity, text, question, options):
            self.assertEqual(set(identity), {"id", "instrument_ref"})
            self.assertEqual(text.count("secret-"), 1)
            self.assertNotIn("answer", question)
            observed.append((identity["id"], text, question, options))
            return "x"
        result, events = run(p, reader)
        self.assertEqual(len(observed), 32)
        self.assertEqual(len(events), 64)
        self.assertEqual([e["event"] for e in events], ["started", "completed"] * 32)
        self.assertEqual(result["report"]["separate_calls"], 32)
        self.assertEqual(result["report"]["reader_pair_scores"], 8)
        self.assertEqual(result["report"]["pair_accuracy_delta_pp"], 0)
        self.assertFalse(result["registration_verified"])
        self.assertEqual(result["report"]["governance_effect"], "report_only")
        self.assertTrue(all(not any(c["correct"].values()) for c in result["report"]["pair_scores"]))
        self.assertEqual(result["report"]["constant_response_compatible_pairs"], [])
        self.assertEqual(result["report"], paired.replay(p, result))

    def test_all_or_nothing_not_marginal_product(self):
        def reader(_reader, text, *_):
            # English: one right per pair (alternating twin); never both.
            # Marked: both right on half the pairs. Marginal accuracy is 50%
            # in both arms; the real pair-accuracy contrast is +50pp, not zero.
            i, j = map(int, text.split("secret-")[1].split("-"))
            correct = (i % 2 == j) if text.startswith("E") else i % 2 == 0
            return label(text, correct)
        result, events = run(reader=reader)
        self.assertEqual(len(events), 64, "a wrong first twin must not skip the second")
        self.assertEqual(result["report"]["pair_accuracy_delta_pp"], 50)
        self.assertTrue(all(not c["correct"]["english"] for c in result["report"]["pair_scores"]))

    def test_adverse_outcomes_are_preserved(self):
        result, _ = run(reader=lambda _ep, text, *_: label(text, text.startswith("E")))
        self.assertEqual(result["report"]["pair_accuracy_delta_pp"], -100)
        self.assertEqual(result["report"]["bootstrap_95_percentile_interval_pp"], [-100, -100])
        self.assertTrue(any("zero width" in x for x in result["report"]["limitations"]))

    def test_missing_duplicate_extra_and_faulted_rosters_fail_replay(self):
        p = plan()
        original, _ = run(p)
        for mutation in ("missing", "duplicate", "unknown-reader", "wrong-arm", "wrong-twin", "fault", "off-option"):
            with self.subTest(mutation=mutation):
                result = copy.deepcopy(original)
                rows = result["rows"]
                if mutation == "missing": rows.pop()
                elif mutation == "duplicate": rows.append(copy.deepcopy(rows[0]))
                elif mutation == "unknown-reader": rows[0]["key"][0] = "outsider"
                elif mutation == "wrong-arm": rows[0]["key"][2] = "joint"
                elif mutation == "wrong-twin": rows[0]["key"][3] = "third"
                elif mutation == "fault": rows[0]["status"] = "fault"
                else: rows[0]["answer"] = "explanation instead"
                result["rows_sha256"] = paired._digest(rows)
                with self.assertRaises(ValueError): paired.replay(p, result)

    def test_plan_and_journal_tampering_refuse(self):
        p = plan()
        result, _ = run(p)
        result["rows"][0]["answer"] = "y"
        with self.assertRaisesRegex(ValueError, "journal digest"): paired.replay(p, result)
        result, _ = run(p)
        p["pairs"][0]["twins"][0]["answer"] = "y"
        with self.assertRaisesRegex(ValueError, "plan digest"): paired.replay(p, result)

    def test_registration_mismatch_prevents_spend(self):
        events = []
        with self.assertRaisesRegex(ValueError, "registration does not commit"):
            paired.execute(plan(), lambda *_: self.fail("spent before matching registration"),
                           registration={"reference": "external", "plan_sha256": "0" * 64}, record=events.append)
        self.assertEqual(events, [])

    def test_absence_off_option_transport_fault_stop_and_keep_started_cells(self):
        for bad in (None, "not an option", RuntimeError("private secret must not enter exception journal")):
            events, calls = [], []
            def reader(*args):
                calls.append(args)
                if isinstance(bad, Exception): raise bad
                return bad
            p = plan()
            with self.assertRaises(paired.StudyAborted) as abort:
                paired.execute(p, reader, registration={"reference": "synthetic", "plan_sha256": paired.validate(p)}, record=events.append)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(events), 2)
            self.assertEqual(abort.exception.receipt["state"], "aborted")
            self.assertNotIn("private secret", str(abort.exception.receipt))
            with self.assertRaises(ValueError): paired.replay(p, abort.exception.receipt)

    def test_failed_journal_write_stops_before_call(self):
        p = plan()
        def fail(_): raise OSError("disk full")
        with self.assertRaises(OSError):
            paired.execute(p, lambda *_: self.fail("call despite failed durable start"),
                           registration={"reference": "synthetic", "plan_sha256": paired.validate(p)}, record=fail)

    def test_unknown_knobs_invalid_schema_and_joint_exposure_refuse(self):
        mutations = [lambda p: p.update(seed=True), lambda p: p.update(extra="silently ignored"),
                     lambda p: p.update(exposure="joint"), lambda p: p.update(point_weighting="equal-family"),
                     lambda p: p.update(reuse_policy="cache-first-response"),
                     lambda p: p["pairs"][0]["twins"].pop(),
                     lambda p: p["readers"].append(copy.deepcopy(p["readers"][0])),
                     lambda p: p["pairs"][0]["twins"][0].update(answer="z"),
                     lambda p: p["pairs"][0]["twins"][0].update(options=["x", "X"])]
        for mutate in mutations:
            p = plan(); mutate(p)
            with self.assertRaises(ValueError): paired.planned_calls(p)

    def test_repeated_worlds_need_declared_family_and_fresh_calls(self):
        p = plan()
        p["pairs"][2]["twins"][0] = copy.deepcopy(p["pairs"][0]["twins"][0])
        with self.assertRaisesRegex(ValueError, "shared resampling family"): paired.validate(p)
        p["resampling_unit"] = "family"
        result, _ = run(p)
        self.assertEqual(result["report"]["separate_calls"], 32)
        self.assertEqual(result["report"]["resampling_units"], 2)
        p["pairs"][2]["family"] = "other-family"
        p["pairs"][2]["twins"][0]["world_id"] = "renamed-world"
        with self.assertRaisesRegex(ValueError, "shared resampling family"): paired.validate(p)

    def test_point_weighting_is_not_changed_by_bootstrap_unit(self):
        p = plan("family")
        for pair in p["pairs"][1:]: pair["family"] = "large-family"
        def reader(_, text, *__):
            return label(text, text.startswith("A secret-0-") or text.startswith("E"))
        result, _ = run(p, reader)
        self.assertEqual(result["report"]["pair_accuracy_delta_pp"], -75, "equal pairs, not mean of two family means (-50)")
        self.assertEqual(result["report"]["resampling_units"], 2)
        self.assertEqual(result["report"]["bootstrap_95_percentile_interval_pp"], [-100, 0])
        p["resampling_unit"] = "pair"
        unclustered, _ = run(p, reader)
        self.assertEqual(unclustered["report"]["pair_accuracy_delta_pp"], -75)
        self.assertEqual(unclustered["report"]["resampling_units"], 4)

    def test_caller_mutation_cannot_change_frozen_plan_during_execution(self):
        p = plan()
        original = copy.deepcopy(p)
        def mutate(*args):
            p["pairs"][0]["twins"][0]["answer"] = "y"
            return "x"
        result, _ = run(p, mutate)
        self.assertEqual(result["plan_sha256"], paired.validate(original))
        self.assertEqual(result["report"], paired.replay(original, result))

    def test_shared_canonicalizer_including_line_and_paragraph_separators(self):
        p = plan()
        p["pairs"][0]["twins"][0]["english"] += "\u2028\u2029/\U0001f642"
        canonical = _canonical_json(p).encode()
        self.assertIn(b"\\u2028\\u2029", canonical)
        self.assertEqual(paired.validate(p), hashlib.sha256(canonical).hexdigest())

    def test_constant_answer_compatible_pairs_are_explicit_not_distinction_evidence(self):
        p = plan()
        for pair in p["pairs"]:
            pair["twins"][1]["answer"] = "x"
        result, _ = run(p)
        self.assertEqual(result["report"]["constant_response_compatible_pairs"], [p["id"] for p in p["pairs"]])
        self.assertTrue(all(all(c["correct"].values()) for c in result["report"]["pair_scores"]))
        self.assertTrue(any("without distinguishing" in note for note in result["report"]["limitations"]))

    def test_each_actual_arm_fingerprint_excludes_other_arm_ids_and_gold(self):
        for arm in paired.ARMS:
            p = plan("family")
            source, target = p["pairs"][0]["twins"][0], p["pairs"][1]["twins"][0]
            target[arm] = source[arm]  # other arm and world ID remain different
            with self.assertRaisesRegex(ValueError, "shared resampling family"):
                paired.validate(p)
            target["answer"] = "y"
            with self.assertRaisesRegex(ValueError, "conflicting gold"):
                paired.validate(p)
            target["answer"] = "x"
            p["pairs"][1]["family"] = p["pairs"][0]["family"]
            result, _ = run(p)
            self.assertEqual(result["report"]["separate_calls"], 32)
        p = plan("family")
        p["pairs"][1]["twins"][0]["ainglish"] = p["pairs"][0]["twins"][0]["english"]
        with self.assertRaisesRegex(ValueError, "shared resampling family"):
            paired.validate(p)

    def test_shared_world_with_different_text_still_needs_clustering(self):
        p = plan()
        p["pairs"][1]["twins"][0]["world_id"] = p["pairs"][0]["twins"][0]["world_id"]
        with self.assertRaisesRegex(ValueError, "shared resampling family"):
            paired.validate(p)

    def test_size_and_cluster_minima(self):
        p = plan(); p["pairs"] = p["pairs"][:1]
        with self.assertRaisesRegex(ValueError, "2..4096"): paired.validate(p)
        p = plan("family")
        for pair in p["pairs"]: pair["family"] = "one"
        with self.assertRaisesRegex(ValueError, "at least two"): paired.validate(p)
        p = plan()
        p["readers"] = [{"id": str(i), "instrument_ref": "synthetic"} for i in range(32)]
        p["pairs"] = p["pairs"] * 33
        with self.assertRaisesRegex(ValueError, "16384"): paired.validate(p)

    def test_hash_order_is_not_generation_order_and_ignores_gold(self):
        p = plan()
        keys = [[r["id"], pair["id"], arm, t["id"]] for r in p["readers"] for pair in p["pairs"]
                for arm in paired.ARMS for t in pair["twins"]]
        expected = sorted(keys, key=lambda k: (hashlib.sha256(json.dumps([17,k], separators=(",", ":")).encode()).hexdigest(), k))
        actual = [c["key"] for c in paired.planned_calls(p)]
        self.assertNotEqual(actual, keys)
        self.assertEqual(actual, expected)
        for pair in p["pairs"]:
            for twin in pair["twins"]: twin["answer"] = "x" if twin["answer"] == "y" else "y"
        self.assertEqual([c["key"] for c in paired.planned_calls(p)], actual)

    def test_rank_draw_count_and_global_counter_stream(self):
        self.assertEqual(paired._interval(list(range(2000))), (49, 1949))
        for seed in (17, 29):
            expected = [int(hashlib.sha256(json.dumps([paired.KIND,"bootstrap",seed,i], separators=(",", ":")).encode()).hexdigest(),16)%13 for i in range(30)]
            self.assertEqual(list(itertools.islice(paired._bootstrap_indices(seed,13),30)), expected)
        with mock.patch.object(paired, "_bootstrap_indices", wraps=paired._bootstrap_indices) as stream:
            result, _ = run()
            stream.assert_called_once_with(17,4)
            self.assertEqual(result["report"]["bootstrap_draws"],2000)

    def test_malformed_replay_envelopes_are_value_errors(self):
        p = plan(); original, _ = run(p)
        for malformed in (None, [], "text", 1):
            with self.assertRaises(ValueError): paired.replay(p, malformed)
            r = copy.deepcopy(original); r["registration"] = malformed
            with self.assertRaises(ValueError): paired.replay(p, r)

    def test_interrupt_is_not_swallowed_and_journal_failure_hides_transport_context(self):
        p = plan(); registration = {"reference":"synthetic", "plan_sha256":paired.validate(p)}
        def interrupt(*_): raise KeyboardInterrupt()
        events = []
        with self.assertRaises(KeyboardInterrupt) as caught:
            paired.execute(p, interrupt, registration=registration, record=events.append)
        self.assertEqual(caught.exception.paired_study_receipt["state"], "aborted")
        self.assertEqual(len(events),2)
        def fault(*_): raise RuntimeError("SECRET-TRANSPORT-TEXT")
        def journal(row):
            if row["event"] == "completed": raise OSError("disk unavailable")
        try:
            paired.execute(p, fault, registration=registration, record=journal)
        except OSError:
            self.assertNotIn("SECRET-TRANSPORT-TEXT",traceback.format_exc())
        else: self.fail("journal error swallowed")


if __name__ == "__main__": unittest.main()
