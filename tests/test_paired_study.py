"""Synthetic software controls only: no model calls, live attempts or evidence."""
import copy
import unittest

from ainglish import paired_study as paired


def plan(unit="pair"):
    return {"kind": paired.KIND, "proposal_id": "synthetic-test-not-a-proposal", "seed": 17,
            "population_scope": "Exposed synthetic software fixtures; no population inference.",
            **paired._POLICY, "resampling_unit": unit,
            "readers": [{"id": "reader-1", "instrument_ref": "synthetic-stub-v1"},
                        {"id": "reader-2", "instrument_ref": "synthetic-stub-v2"}],
            "pairs": [{"id": f"pair-{i}", "family": f"family-{i % 2}", "twins": [
                {"id": f"twin-{j}", "world_id": f"world-{i}-{j}",
                 "english": f"E secret-{i}-{j}", "ainglish": f"A secret-{i}-{j}",
                 "question": "Choose.", "options": ["x", "y"], "answer": "x"}
                for j in range(2)]} for i in range(4)]}


def run(p=None, reader=lambda *args: "x"):
    p = p or plan()
    events = []
    result = paired.execute(p, reader, registration={"reference": "synthetic-only",
                            "plan_sha256": paired.validate(p)}, record=events.append)
    return result, events


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
        self.assertEqual(result["report"], paired.replay(p, result))

    def test_all_or_nothing_not_marginal_product(self):
        def reader(_reader, text, *_):
            # English: one right per pair (alternating twin); never both.
            # Marked: both right on half the pairs. Marginal accuracy is 50%
            # in both arms; the real pair-accuracy contrast is +50pp, not zero.
            i, j = map(int, text.split("secret-")[1].split("-"))
            correct = (i % 2 == j) if text.startswith("E") else i % 2 == 0
            return "x" if correct else "y"
        result, events = run(reader=reader)
        self.assertEqual(len(events), 64, "a wrong first twin must not skip the second")
        self.assertEqual(result["report"]["pair_accuracy_delta_pp"], 50)
        self.assertTrue(all(not c["correct"]["english"] for c in result["report"]["pair_scores"]))

    def test_adverse_outcomes_are_preserved(self):
        result, _ = run(reader=lambda _ep, text, *_: "x" if text.startswith("E") else "y")
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
            return "x" if text.startswith("A secret-0-") or text.startswith("E") else "y"
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


if __name__ == "__main__": unittest.main()
