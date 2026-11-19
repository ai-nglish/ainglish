"""strict-json-v1 against the shared vector file, and against Python's own decoder as a second opinion.

Every expectation in the file is written by hand in tools/gen_strict_json_vectors.py, which never
imports the module. The register reads a byte copy of the same file.
"""
import base64
import hashlib
import importlib.util
import json
import random
import unittest
from pathlib import Path

from ainglish import strict_json as S

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "strict-json-v1.json"
VECTORS = json.loads(FIXTURE.read_text(encoding="ascii"))
CASES = VECTORS["cases"]


def raw_of(case):
    return base64.b64decode(case["raw_b64"])


def run(case):
    return S.scan(raw_of(case), max_bytes=case["max_bytes"], max_depth=case["max_depth"])


def pairs_oracle(raw):
    """Python's decoder, told to hand over every member: (has a duplicate name, objects, members, deepest level)."""
    seen = {"dup": False, "objects": 0, "members": 0}

    def hook(pairs):
        names = [k for k, _ in pairs]
        seen["dup"] = seen["dup"] or len(set(names)) != len(names)
        seen["objects"] += 1
        seen["members"] += len(names)
        return dict(pairs)

    value = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)

    def depth(v):
        if isinstance(v, dict):
            return 1 + max([depth(x) for x in v.values()], default=0)
        if isinstance(v, list):
            return 1 + max([depth(x) for x in v], default=0)
        return 0

    return seen["dup"], seen["objects"], seen["members"], depth(value)


class VectorTests(unittest.TestCase):
    def test_fixture_is_what_the_committed_generator_writes(self):
        tool = ROOT / VECTORS["generated_by"]["tool"]
        self.assertEqual(hashlib.sha256(tool.read_bytes()).hexdigest(), VECTORS["generated_by"]["tool_sha256"])
        spec = importlib.util.spec_from_file_location("gen_strict_json_vectors", tool)
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        self.assertEqual(gen.build(), FIXTURE.read_bytes())
        self.assertEqual((VECTORS["rule"], tuple(VECTORS["codes"])), (S.RULE, S.CODES))

    def test_every_code_and_every_acceptance_row_has_cases(self):
        self.assertGreaterEqual(len(CASES), 100)
        refused = [c["expect"]["code"] for c in CASES if not c["expect"]["ok"]]
        self.assertEqual(set(refused), set(S.CODES))
        for code in S.CODES:
            self.assertGreaterEqual(refused.count(code), 3, code)
        self.assertEqual({c["acceptance"] for c in CASES if c["acceptance"]}, {"RAW-1", "RAW-2", "RAW-3", "RAW-4"})
        self.assertEqual(VECTORS["counts"]["accepted"], sum(1 for c in CASES if c["expect"]["ok"]))

    def test_every_case_gives_the_stated_result(self):
        for c in CASES:
            with self.subTest(c["name"]):
                want = c["expect"]
                if want["ok"]:
                    self.assertEqual(run(c), {k: want[k] for k in ("top", "depth", "objects", "members")})
                else:
                    with self.assertRaises(S.StrictJsonRefused) as ctx:
                        run(c)
                    self.assertEqual((ctx.exception.code, ctx.exception.offset), (want["code"], want["offset"]))

    def test_accepted_cases_agree_with_pythons_decoder(self):
        for c in CASES:
            if c["expect"]["ok"]:
                with self.subTest(c["name"]):
                    dup, objects, members, depth = pairs_oracle(raw_of(c))
                    self.assertFalse(dup)
                    self.assertEqual((objects, members, depth), (c["expect"]["objects"], c["expect"]["members"], c["expect"]["depth"]))

    def test_the_mark_for_texts_pythons_decoder_takes_is_right_in_both_directions(self):
        """A refused text marked python_json_accepts must load with json.loads; an unmarked grammar refusal must not."""
        marked = 0
        for c in CASES:
            want = c["expect"]
            if want["ok"] or want["code"] in ("size", "depth"):
                continue
            with self.subTest(c["name"]):
                try:
                    json.loads(raw_of(c))
                    loads = True
                except (ValueError, RecursionError):
                    loads = False
                self.assertEqual(loads, c["python_json_accepts"])
                marked += loads
        self.assertGreaterEqual(marked, 25)

    def test_every_duplicate_case_that_decodes_is_a_duplicate_to_pythons_decoder_too(self):
        n = 0
        for c in CASES:
            if not c["expect"]["ok"] and c["expect"]["code"] == "duplicate_key" and c["python_json_accepts"]:
                with self.subTest(c["name"]):
                    self.assertTrue(pairs_oracle(raw_of(c))[0])
                    n += 1
        self.assertGreaterEqual(n, 15)

    def test_a_digest_of_the_decoded_value_cannot_see_the_duplicate(self):
        """RAW-2: three raw texts, one decoded object. Only the raw text tells them apart."""
        by_name = {c["name"]: c for c in CASES}
        plain = by_name["the object a last-key-wins decoder makes of the two duplicate cases below"]
        twice = by_name["the same name twice with different values"]
        alias = by_name["an escaped alias of a plain name"]
        canon = lambda c: hashlib.sha256(json.dumps(json.loads(raw_of(c)), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(len({canon(plain), canon(twice), canon(alias)}), 1)
        self.assertEqual(len({raw_of(plain), raw_of(twice), raw_of(alias)}), 3)
        self.assertTrue(plain["expect"]["ok"])
        for c in (twice, alias):
            with self.assertRaises(S.StrictJsonRefused) as ctx:
                run(c)
            self.assertEqual(ctx.exception.code, "duplicate_key")


class SeededTests(unittest.TestCase):
    """Random texts, written by a serializer in this file that escapes at random and can plant one duplicate."""

    ALPHABET = ["a", "b", "/", "é", "\u20ac", "\U0001F600", "1", "", "\n", "k k"]

    def spell(self, rng, text):
        out = ['"']
        for ch in text:
            o = ord(ch)
            roll = rng.random()
            if ch == "/" and roll < 0.4:
                out.append("\\/")
            elif ch == "\n" and roll < 0.5:
                out.append("\\n")
            elif o < 0x20 or ch in '"\\' or roll < 0.3:
                if o > 0xFFFF:
                    o -= 0x10000
                    out.append("\\u%04x\\u%04X" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
                else:
                    out.append(("\\u%04x" if rng.random() < 0.5 else "\\u%04X") % o)
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)

    def doc(self, rng, level, plant):
        """Returns (text, planted). `plant` is consumed by the first object that has a member to repeat."""
        kind = rng.choice("oaos") if level < 4 else "s"
        if kind == "s":
            return rng.choice(["1", "-2.5e3", "true", "null", self.spell(rng, rng.choice(self.ALPHABET))]), False
        planted = False
        parts = []
        if kind == "a":
            for _ in range(rng.randint(0, 3)):
                t, p = self.doc(rng, level + 1, plant and not planted)
                planted = planted or p
                parts.append(t)
            return "[" + rng.choice(["", " "]) + ",".join(parts) + "]", planted
        names = rng.sample(self.ALPHABET, rng.randint(0, 4))
        for name in names:
            t, p = self.doc(rng, level + 1, plant and not planted)
            planted = planted or p
            parts.append(self.spell(rng, name) + rng.choice([":", " : "]) + t)
        if plant and not planted and names:
            parts.insert(rng.randint(0, len(parts)), self.spell(rng, rng.choice(names)) + ":0")
            planted = True
        return "{" + ",".join(parts) + "}", planted

    def test_three_hundred_seeded_texts_against_pythons_decoder(self):
        rng = random.Random(20261119)
        with_dup = without = 0
        for n in range(300):
            text, planted = self.doc(rng, 0, n % 2 == 0)
            raw = text.encode("utf-8")
            dup, objects, members, depth = pairs_oracle(raw)
            self.assertEqual(dup, planted, text)
            with self.subTest(n=n, text=text):
                if dup:
                    with self.assertRaises(S.StrictJsonRefused) as ctx:
                        S.scan(raw, max_bytes=len(raw), max_depth=8)
                    self.assertEqual(ctx.exception.code, "duplicate_key")
                    self.assertEqual(raw[ctx.exception.offset:ctx.exception.offset + 1], b'"')
                    with_dup += 1
                else:
                    got = S.scan(raw, max_bytes=len(raw), max_depth=8)
                    self.assertEqual((got["objects"], got["members"], got["depth"]), (objects, members, depth))
                    without += 1
        self.assertGreaterEqual(with_dup, 60)
        self.assertGreaterEqual(without, 100)

    def test_the_utf8_check_agrees_with_pythons_codec_on_two_thousand_byte_strings(self):
        rng = random.Random(20261119)
        pieces = [b"a", b"\xc3\xa9", b"\xe2\x82\xac", b"\xf0\x9f\x98\x80", b"\xed\x9f\xbf", b"\xee\x80\x80", b"\xf4\x8f\xbf\xbf",
                  b"\x80", b"\xc0", b"\xc1\xbf", b"\xe0\x9f\xbf", b"\xed\xa0\x80", b"\xf0\x8f\xbf\xbf", b"\xf4\x90\x80\x80", b"\xf5", b"\xff", b"\xe2\x82", b"\xf0\x9f"]
        faults = 0
        for _ in range(2000):
            raw = b"".join(rng.choice(pieces) if rng.random() < 0.8 else bytes([rng.randrange(256)]) for _ in range(rng.randint(0, 12)))
            try:
                raw.decode("utf-8")
                want = -1
            except UnicodeDecodeError as e:
                want = e.start
            self.assertEqual(S.utf8_fault(raw), want, raw)
            faults += want >= 0
        self.assertGreaterEqual(faults, 500)
        self.assertGreaterEqual(2000 - faults, 100)


class ArgumentTests(unittest.TestCase):
    def test_text_must_be_bytes(self):
        for not_bytes in ("{}", [123, 125], 2, None, memoryview(b"{}")):
            with self.subTest(type(not_bytes).__name__):
                with self.assertRaises(TypeError):
                    S.scan(not_bytes, max_bytes=10, max_depth=1)
        self.assertEqual(S.scan(bytearray(b"{}"), max_bytes=10, max_depth=1)["top"], "object")

    def test_bounds_must_be_integers_inside_the_ceilings(self):
        for kw in ({"max_bytes": 0, "max_depth": 1}, {"max_bytes": True, "max_depth": 1}, {"max_bytes": 10.0, "max_depth": 1}, {"max_bytes": S.BYTES_CEILING + 1, "max_depth": 1},
                   {"max_bytes": 10, "max_depth": -1}, {"max_bytes": 10, "max_depth": False}, {"max_bytes": 10, "max_depth": S.DEPTH_CEILING + 1}, {"max_bytes": 10, "max_depth": "4"}):
            with self.subTest(kw):
                with self.assertRaises(ValueError) as ctx:
                    S.scan(b"{}", **kw)
                self.assertIs(type(ctx.exception), ValueError)          # a bad bound is a caller's error, never a refusal of the text
        self.assertEqual(S.scan(b"1", max_bytes=S.BYTES_CEILING, max_depth=S.DEPTH_CEILING)["top"], "number")

    def test_the_deepest_text_the_ceiling_allows_is_scanned_and_one_more_level_is_refused(self):
        deep = b"[" * S.DEPTH_CEILING + b"]" * S.DEPTH_CEILING
        self.assertEqual(S.scan(deep, max_bytes=len(deep), max_depth=S.DEPTH_CEILING)["depth"], S.DEPTH_CEILING)
        with self.assertRaises(S.StrictJsonRefused) as ctx:
            S.scan(b"[" + deep + b"]", max_bytes=len(deep) + 2, max_depth=S.DEPTH_CEILING)
        self.assertEqual((ctx.exception.code, ctx.exception.offset), ("depth", S.DEPTH_CEILING))

    def test_nothing_in_the_sdk_calls_the_module(self):
        for path in (ROOT / "src" / "ainglish").glob("*.py"):
            if path.name != "strict_json.py":
                self.assertNotIn("strict_json", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
