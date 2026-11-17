"""balanced-world-hash-v1 against the shared vector file.

Every expectation in the file comes from a computation written apart from the module (see
tools/gen_allocation_vectors.py): the hashed bytes by json.dumps, the ranking by a selection sort
over hex digests, the commitment assembled as a string. The register reads a byte copy.
"""
import hashlib
import json
import random
import unittest
from pathlib import Path

from ainglish import allocation as A
from ainglish import panel

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "balanced-world-hash-v1.json"
VECTORS = json.loads(FIXTURE.read_text(encoding="utf-8"))


class VectorTests(unittest.TestCase):
    def test_fixture_names_the_generator_that_is_committed(self):
        tool = ROOT / VECTORS["generated_by"]["tool"]
        self.assertEqual(hashlib.sha256(tool.read_bytes()).hexdigest(), VECTORS["generated_by"]["tool_sha256"])
        self.assertEqual(VECTORS["rule"], A.RULE)
        self.assertEqual((VECTORS["max_name_bytes"], VECTORS["portable_integer_max"]), (A.MAX_NAME_BYTES, A.PORTABLE_INTEGER_MAX))
        self.assertEqual(VECTORS["arms"], {"first_per_cell": A.ARMS[0], "rest": A.ARMS[1]})

    def test_every_case_reproduces_bytes_rank_and_arms(self):
        self.assertGreaterEqual(len(VECTORS["cases"]), 16)
        for c in VECTORS["cases"]:
            with self.subTest(c["name"]):
                self.assertEqual(A.preimage(c["seed"], c["reader"], c["form"], c["worlds"][0]).hex(), c["first_preimage_utf8_hex"])
                for row in c["rank"]:
                    self.assertEqual(hashlib.sha256(A.preimage(c["seed"], c["reader"], c["form"], row["world"])).hexdigest(), row["sha256"])
                got = A.allocate(c["seed"], c["reader"], c["form"], c["worlds"], c["per_cell"])
                self.assertEqual(got, {"english": c["english"], "ainglish": c["ainglish"]})
                self.assertEqual(got["english"] + got["ainglish"], [r["world"] for r in c["rank"]])
                self.assertEqual((len(got["english"]), len(got["ainglish"])), (c["per_cell"], c["per_cell"]))

    def test_the_hashed_bytes_are_the_canonical_json_of_the_array(self):
        for c in VECTORS["cases"]:
            for w in c["worlds"]:
                canonical = json.dumps([A.RULE, c["seed"], c["reader"], c["form"], w], separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                self.assertEqual(A.preimage(c["seed"], c["reader"], c["form"], w), canonical)

    def test_rosters_and_commitments(self):
        for r in VECTORS["rosters"]:
            with self.subTest(r["name"]):
                self.assertEqual(A.allocate_roster(r["seed"], r["readers"], r["forms"], r["per_cell"]), r["allocation"])
                self.assertEqual(A.commitment(r["seed"], r["readers"], r["forms"], r["per_cell"]), r["commitment"])
        first, second = VECTORS["rosters"][:2]
        self.assertEqual(len(VECTORS["rosters"]), 3)
        self.assertEqual(first["commitment"], second["commitment"])                      # every input reordered, nothing moves
        self.assertEqual(first["allocation"]["reader-a"], second["allocation"]["reader-a"])
        changed = VECTORS["commitment_changes"]
        self.assertEqual(changed["base"], first["commitment"])
        self.assertEqual(len(set(changed["changed"].values()) | {changed["base"]}), len(changed["changed"]) + 1)
        w8 = ["w-%02d" % i for i in range(8)]
        self.assertEqual(A.commitment(17, ["reader-a", "reader-c"], {"form-a": w8, "form-b": w8}, 4), changed["changed"]["reader renamed"])
        self.assertEqual(A.commitment(18, ["reader-a", "reader-b"], {"form-a": w8, "form-b": w8}, 4), changed["changed"]["seed changed"])
        self.assertEqual(A.commitment(17, ["reader-a", "reader-b"], {"form-a": w8[:-1] + ["w-99"], "form-b": w8}, 4), changed["changed"]["one world replaced"])
        self.assertEqual(A.commitment(17, ["reader-a", "reader-b"], {"form-a": w8, "form-c": w8}, 4), changed["changed"]["form renamed"])

    def test_every_refusal_carries_its_code_and_allocates_nothing(self):
        self.assertGreaterEqual(len(VECTORS["refusals"]), 19)
        for r in VECTORS["refusals"]:
            with self.subTest(r["name"]):
                with self.assertRaises(A.AllocationRefused) as caught:
                    if r["call"] == "allocate":
                        A.allocate(r["seed"], r["reader"], r["form"], r["worlds"], r["per_cell"])
                    else:
                        A.allocate_roster(r["seed"], r["readers"], r["forms"], r["per_cell"])
                self.assertEqual(caught.exception.code, r["code"])
                if r["call"] == "roster":
                    with self.assertRaises(A.AllocationRefused) as again:
                        A.commitment(r["seed"], r["readers"], r["forms"], r["per_cell"])
                    self.assertEqual(again.exception.code, r["code"])


class RuleTests(unittest.TestCase):
    def test_exactly_per_cell_in_each_arm_for_many_seeds_and_sizes(self):
        for per_cell in (1, 2, 5, 16, 134):
            worlds = ["w%04d" % i for i in range(2 * per_cell)]
            for seed in (0, 1, 7, 2 ** 20, A.PORTABLE_INTEGER_MAX):
                got = A.allocate(seed, "reader", "form", worlds, per_cell)
                self.assertEqual((len(got["english"]), len(got["ainglish"])), (per_cell, per_cell))
                self.assertEqual(sorted(got["english"] + got["ainglish"]), sorted(worlds))

    def test_input_order_never_changes_the_allocation(self):
        worlds = ["w%03d" % i for i in range(40)]; base = A.allocate(5, "reader", "form", worlds, 20); rng = random.Random(1)
        for _ in range(20):
            shuffled = worlds[:]; rng.shuffle(shuffled)
            self.assertEqual(A.allocate(5, "reader", "form", shuffled, 20), base)

    def test_reader_form_and_seed_each_enter_the_hash(self):
        worlds = ["w%03d" % i for i in range(40)]; base = A.allocate(5, "reader", "form", worlds, 20)
        self.assertNotEqual(A.allocate(6, "reader", "form", worlds, 20), base)
        self.assertNotEqual(A.allocate(5, "reader2", "form", worlds, 20), base)
        self.assertNotEqual(A.allocate(5, "reader", "form2", worlds, 20), base)

    def test_names_are_not_joined_so_a_separator_cannot_move_between_them(self):
        worlds = ["w%03d" % i for i in range(40)]
        self.assertNotEqual(A.preimage(5, "a,b", "c", "w"), A.preimage(5, "a", "b,c", "w"))
        self.assertNotEqual(A.allocate(5, "a|b", "c", worlds, 20), A.allocate(5, "a", "b|c", worlds, 20))

    def test_an_endpoint_inherits_the_arm_of_its_world(self):
        worlds = ["w%02d" % i for i in range(8)]; got = A.allocate(17, "reader", "form", worlds, 4)
        for w in worlds:
            arms = {A.arm_of(got, w) for _endpoint in ("accuracy", "wrong-renewal", "entitlement")}   # there is no endpoint argument to vary
            self.assertEqual(len(arms), 1)
        with self.assertRaises(A.AllocationRefused):
            A.arm_of(got, "not-a-world")

    def test_equal_digests_fall_back_to_the_bytes_of_the_world_id(self):
        same = b"\x00" * 32
        self.assertEqual([w for _, w in A.order([(same, "b"), (same, "ä"), (same, "a"), (same, "B")])], ["B", "a", "b", "ä"])
        self.assertEqual([w for _, w in A.order([(b"\x01" * 32, "a"), (same, "z")])], ["z", "a"])

    def test_wrong_types_are_refused(self):
        for bad in (True, 1.0, "17", None):
            with self.assertRaises(A.AllocationRefused) as c:
                A.allocate(bad, "r", "f", ["a", "b"], 1)
            self.assertEqual(c.exception.code, "seed")
        for bad in (True, 1.0, "1", None):
            with self.assertRaises(A.AllocationRefused) as c:
                A.allocate(17, "r", "f", ["a", "b"], bad)
            self.assertEqual(c.exception.code, "per_cell")
        with self.assertRaises(A.AllocationRefused) as c:
            A.allocate(17, "r", "f", "ab", 1)
        self.assertEqual(c.exception.code, "worlds_type")
        with self.assertRaises(A.AllocationRefused) as c:
            A.allocate(17, 5, "f", ["a", "b"], 1)
        self.assertEqual(c.exception.code, "name_type")
        with self.assertRaises(A.AllocationRefused) as c:
            A.allocate(17, "r", "f", ["a", "\ud800"], 1)
        self.assertEqual(c.exception.code, "name_encoding")

    def test_the_legacy_allocator_is_unchanged_and_unused(self):
        pinned = {("17", "command-r-35b", "gun-01"): hashlib.sha256(b"17|command-r-35b|gun-01").digest()[0] % 2}
        for (seed, panelist, item), bit in pinned.items():
            self.assertEqual(panel.arm_for(seed, panelist, item), "ainglish" if bit else "english")
        self.assertEqual([panel.arm_for(29, "gemma4-31b", "item-%d" % i) for i in range(6)],
                         ["ainglish" if hashlib.sha256(("29|gemma4-31b|item-%d" % i).encode()).digest()[0] % 2 else "english" for i in range(6)])
        source = (ROOT / "src" / "ainglish" / "allocation.py").read_text(encoding="utf-8")
        self.assertNotIn("import panel", source)
        self.assertNotIn("from ainglish", source)


if __name__ == "__main__":
    unittest.main()
