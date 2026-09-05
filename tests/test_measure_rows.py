"""token_delta must count the pair STRINGS, never a row container's keys.

Regression for the register's 2026-09 batch of token rows filed at exactly +2: handing
``token_delta`` dict-shaped rows made ``for e, a in pairs`` unpack the two KEYS
("english", "ainglish"), whose cl100k counts are 1 and 3, so every pair scored +2
regardless of its text. Offline: the encoder is a stub, no tiktoken needed.
"""
import unittest

from ainglish import measure


class _Enc:
    def encode(self, text):
        return text.split()


def _factory(name):
    return _Enc()


ROWS = [("the check ran and failed", "verdict-fail"), ("the check never ran", "no-verdict")]


class TokenDeltaRowShapeTests(unittest.TestCase):
    def test_tuple_rows_and_dict_rows_give_the_same_string_counts(self):
        expect = measure.token_delta(ROWS, ["stub"], encoder_factory=_factory)
        self.assertEqual(expect["by_tokenizer"]["stub"]["per_pair"], [-4, -3])
        as_dicts = [{"english": e, "ainglish": a} for e, a in ROWS]
        got = measure.token_delta(as_dicts, ["stub"], encoder_factory=_factory)
        self.assertEqual(got, expect)

    def test_dict_key_order_does_not_change_token_counts(self):
        """Suggested by Nico on The Colony (comment b0f46593, 2026-09-05).

        The old loop unpacked a dict row's KEYS in iteration order, so ``{"english", "ainglish"}``
        and ``{"ainglish", "english"}`` scored opposite signs (+2 / -2 on cl100k) for the same text.
        With the whitespace stub both key orders happen to score 0, so comparing the two dict orders
        to each other would pass on the old code; each must equal the TUPLE result instead.
        """
        expected = measure.token_delta(ROWS, ["stub"], encoder_factory=_factory)
        self.assertEqual(expected["by_tokenizer"]["stub"]["per_pair"], [-4, -3])

        forward = [{"english": e, "ainglish": a} for e, a in ROWS]
        reverse = [{"ainglish": a, "english": e} for e, a in ROWS]
        for rows in (forward, reverse):
            with self.subTest(key_order=tuple(rows[0])):
                actual = measure.token_delta(rows, ["stub"], encoder_factory=_factory)
                self.assertEqual(actual, expected)

    def test_rows_that_are_not_two_strings_are_refused(self):
        for bad in ([("only-one",)], [("a", "b", "c")], ["a string row"], [{"english": "x"}],
                    [{"english": "x", "ainglish": 3}], [None]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                measure.token_delta(bad, ["stub"], encoder_factory=_factory)


if __name__ == "__main__":
    unittest.main()
