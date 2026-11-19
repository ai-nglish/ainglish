"""Write tests/fixtures/strict-json-v1.json: the vectors shared by `ainglish.strict_json` and the register's StrictJson.

Written apart from the module and never importing it. Every expectation below is stated by hand: a refusal's
offset is the length of the text placed before the fault (`pre`), so it follows from how the case was built and
not from what any implementation returns. Accepted cases state their top-level kind, deepest level, object count
and member count by hand.

Raw inputs travel as base64, because several are not valid UTF-8 and a JSON file cannot hold them otherwise.
`acceptance` names the row of docs/preservation-integration-acceptance.md a case answers (RAW-1 to RAW-4).
`python_json_accepts` marks refused texts that Python's own json.loads takes without complaint; the test file
checks that mark in both directions.

    python tools/gen_strict_json_vectors.py            # write the file
    python tools/gen_strict_json_vectors.py --check    # exit 1 unless the committed file is what this would write
"""
import base64
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "strict-json-v1.json"
RULE = "strict-json-v1"
B, D = 4096, 16          # the bounds a case uses unless it says otherwise
cases = []


def raw_of(x):
    return x if isinstance(x, bytes) else x.encode("utf-8")


def ok(name, raw, top, depth, objects, members, max_bytes=B, max_depth=D, acceptance=None):
    cases.append({"name": name, "raw_b64": base64.b64encode(raw_of(raw)).decode("ascii"), "max_bytes": max_bytes, "max_depth": max_depth,
                  "expect": {"ok": True, "top": top, "depth": depth, "objects": objects, "members": members}, "acceptance": acceptance})


def no(name, pre, rest, code, max_bytes=B, max_depth=D, acceptance=None, python_json_accepts=False, offset=None):
    """`pre` is everything before the fault, `rest` the fault and what follows: the offset is len(pre) unless given."""
    pre, rest = raw_of(pre), raw_of(rest)
    cases.append({"name": name, "raw_b64": base64.b64encode(pre + rest).decode("ascii"), "max_bytes": max_bytes, "max_depth": max_depth,
                  "expect": {"ok": False, "code": code, "offset": len(pre) if offset is None else offset}, "acceptance": acceptance, "python_json_accepts": python_json_accepts})


# ---------------- accepted
ok("empty object", "{}", "object", 1, 1, 0)
ok("empty array", "[]", "array", 1, 0, 0)
ok("bare string", '"x"', "string", 0, 0, 0)
ok("bare zero", "0", "number", 0, 0, 0)
ok("bare number with fraction and exponent", "-0.5e+10", "number", 0, 0, 0)
ok("bare true", "true", "literal", 0, 0, 0)
ok("bare false", "false", "literal", 0, 0, 0)
ok("bare null", "null", "literal", 0, 0, 0)
ok("the four whitespace bytes around every token", ' \t\n\r{ "a" : [ 1 , 2 ] , "b" : { } } \n', "object", 2, 2, 2)
ok("the same name at three nesting levels", '{"a":{"a":{"a":1}}}', "object", 3, 3, 3)
ok("the same name in two sibling objects", '[{"a":1},{"a":2}]', "array", 2, 2, 2)
ok("the same string twice in an array", '["a","a"]', "array", 1, 0, 0)
ok("names that differ only by case", '{"a":1,"A":2}', "object", 1, 1, 2)
ok("names that look like numbers", '{"1":1,"01":2,"1.0":3,"10":4,"-1":5,"1e0":6}', "object", 1, 1, 6)
ok("precomposed and decomposed e-acute are two names", '{"é":1,"é":2}', "object", 1, 1, 2)
ok("the empty name once", '{"":1,"a":2}', "object", 1, 1, 2)
ok("a name that is one NUL escape", '{"\\u0000":1,"\\u0001":2}', "object", 1, 1, 2)
ok("every simple escape in a value", '["\\"\\\\\\/\\b\\f\\n\\r\\t"]', "array", 1, 0, 0)
ok("a surrogate pair escape in a value", '["\\ud83d\\ude00"]', "array", 1, 0, 0)
ok("a name outside the basic plane beside another", '{"\U0001F600":1,"\U0001F601":2}', "object", 1, 1, 2)
ok("number forms", "[0,-0,1.5,1e5,1E-5,0.0e0,123456789012345678901234567890]", "array", 1, 0, 0)
ok("neighbouring code points at each encoded length are different names", '{"\u07ff":1,"\\u0800":2,"\uffff":3,"\\ud800\\udc00":4,"\\u20ac":5,"\u20ad":6}', "object", 1, 1, 6)
ok("a name that differs from another only inside an escape", '{"a\\u0062":1,"a\\u0063":2}', "object", 1, 1, 2)
ok("nesting exactly at the bound", "[[[1]]]", "array", 3, 0, 0, max_depth=3, acceptance="RAW-4")
ok("a bare scalar with a depth bound of zero", "7", "number", 0, 0, 0, max_depth=0)
ok("length exactly at the bound", '{"a":1}', "object", 1, 1, 1, max_bytes=7, acceptance="RAW-4")
ok("a duplicate-free text of the kind a plan record is", '{"kind":"ainglish.comparison-plan.v1","forms":[{"id":"form-a","weight":1},{"id":"form-b","weight":1}],"meaning":"m"}', "object", 3, 3, 7)
ok("the object a last-key-wins decoder makes of the two duplicate cases below", '{"a":2}', "object", 1, 1, 1, acceptance="RAW-2")

# ---------------- size (nothing else is looked at)
no("one byte over the bound", "", '{"a":10}', "size", max_bytes=7, offset=7, acceptance="RAW-4")
no("over the bound and also a duplicate", "", '{"a":1,"a":2}', "size", max_bytes=8, offset=8, acceptance="RAW-4")
no("over the bound and also ill-formed UTF-8", "", b'"\xff\xff\xff\xff"', "size", max_bytes=3, offset=3, acceptance="RAW-4")

# ---------------- utf8 (the whole input is checked before the grammar)
no("a lone continuation byte", '["', b'\x80"]', "utf8")
no("an overlong two-byte form", '["', b'\xc0\x80"]', "utf8")
no("an overlong three-byte form", '["', b'\xe0\x80\x80"]', "utf8")
no("an overlong four-byte form", '["', b'\xf0\x80\x80\x80"]', "utf8")
no("an encoded surrogate", '["', b'\xed\xa0\x80"]', "utf8", python_json_accepts=True)
no("a value above U+10FFFF", '["', b'\xf4\x90\x80\x80"]', "utf8")
no("a lead byte that never occurs", '["', b'\xf5\x80\x80\x80"]', "utf8")
no("a sequence cut off by the end", '["', b'\xe2\x82', "utf8")
no("a sequence cut off by an ASCII byte", '["', b'\xe2\x28\xa1"]', "utf8")
no("the second continuation byte wrong", '["aé', b'\xe2\x82\x28"]', "utf8")
no("ill-formed UTF-8 after an earlier duplicate name", '{"a":1,"a":"', b'\xff"}', "utf8")
no("ill-formed UTF-8 outside any string", "[1,", b"\xff]", "utf8")

# ---------------- syntax
no("empty input", "", "", "syntax")
no("whitespace only", " \n\t ", "", "syntax")
no("a byte-order mark", "", b"\xef\xbb\xbf{}", "syntax", python_json_accepts=True)
no("data after the value", "{} ", "x", "syntax")
no("two values", "1 ", "2", "syntax")
no("a second object", "{}", "{}", "syntax")
no("a trailing comma in an object", '{"a":1,', "}", "syntax")
no("a trailing comma in an array", "[1,", "]", "syntax")
no("an empty slot in an array", "[1,", ",2]", "syntax")
no("a missing colon", '{"a"', " 1}", "syntax", offset=5)
no("a missing value", '{"a":', "}", "syntax")
no("an unquoted name", "{", "a:1}", "syntax")
no("a number as a name", "{", "1:1}", "syntax")
no("a single-quoted string", "[", "'a']", "syntax")
no("a raw newline inside a string", '["a', '\nb"]', "syntax")
no("a raw NUL inside a string", '["a', '\x00b"]', "syntax")
no("an escape JSON does not define", '["a', '\\x41"]', "syntax")
no("a \\u escape with a letter that is not hexadecimal", '["a', '\\u12G4"]', "syntax")
no("a \\u escape cut off by the closing quote", '["a', '\\u12"]', "syntax")
no("a \\u escape cut off by the end", '["a', "\\u12", "syntax")
no("a backslash at the very end", '["a', "\\", "syntax")
no("a string that is never closed", '["abc', "", "syntax")
no("a leading zero", "0", "1", "syntax")
no("a leading zero inside an array", "[0", "1]", "syntax")
no("a plus sign", "", "+1", "syntax")
no("a fraction with no integer part", "", ".5", "syntax")
no("a fraction with no digits", "1.", "", "syntax")
no("an exponent with no digits", "1e", "", "syntax")
no("an exponent sign with no digits", "[1e+", "]", "syntax")
no("a lone minus", "-", "", "syntax")
no("NaN", "", "NaN", "syntax", python_json_accepts=True)
no("Infinity", "", "Infinity", "syntax", python_json_accepts=True)
no("minus Infinity", "-", "Infinity", "syntax", python_json_accepts=True)
no("a truncated literal", "", "tru", "syntax")
no("a literal in capitals", "", "True", "syntax")
no("a comment", "", "/* c */ {}", "syntax")
no("an array that is never closed", "[1", "", "syntax")
no("an object that is never closed", '{"a":1', "", "syntax")
no("the wrong closing bracket", "[1", "}", "syntax")
no("a syntax fault before a later duplicate name", '{"a":1,', ',"a":2}', "syntax")
no("a vertical tab used as whitespace", "[1,", "\x0b2]", "syntax")
no("a no-break space used as whitespace", "[1,", " 2]", "syntax")

# ---------------- surrogate
no("a lone high surrogate escape in a value", '["', '\\ud83d"]', "surrogate", python_json_accepts=True)
no("a lone low surrogate escape in a value", '["', '\\ude00"]', "surrogate", python_json_accepts=True)
no("a high surrogate followed by a letter", '["', '\\ud83dx"]', "surrogate", python_json_accepts=True)
no("a high surrogate followed by an ordinary escape", '["', '\\ud83d\\u0041"]', "surrogate", python_json_accepts=True)
no("two high surrogates", '["', '\\ud83d\\ud83d"]', "surrogate", python_json_accepts=True)
no("a lone high surrogate escape in a name", "{", '"\\ud83d":1}', "surrogate", python_json_accepts=True, offset=2)
no("a high surrogate at the end of a string", '["a', '\\udbff"]', "surrogate", python_json_accepts=True)
no("a high surrogate followed by a malformed escape", '["\\ud83d', '\\uZZZZ"]', "syntax")

# ---------------- depth
no("one level over the bound, arrays", "[[[", "[1]]]]", "depth", max_depth=3, acceptance="RAW-4")
no("one level over the bound, objects", '{"a":{"b":', '{"c":1}}}', "depth", max_depth=2, acceptance="RAW-4")
no("any container with a bound of zero", "", "[]", "depth", max_depth=0, acceptance="RAW-4")
no("too deep before a later duplicate name", '{"a":[', '[1]],"a":2}', "depth", max_depth=2, acceptance="RAW-4")
no("too deep in the second element", "[[1],[", "[2]]]", "depth", max_depth=2, acceptance="RAW-4")

# ---------------- duplicate_key
no("the same name twice with equal values", '{"a":1,', '"a":1}', "duplicate_key", acceptance="RAW-1", python_json_accepts=True)
no("the same name twice with different values", '{"a":1,', '"a":2}', "duplicate_key", acceptance="RAW-2", python_json_accepts=True)
no("an escaped alias of a plain name", '{"a":2,', '"\\u0061":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a plain name after its escaped alias", '{"\\u0061":1,', '"a":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("two escapes of one character that differ in letter case", '{"\\u00E9":1,', '"\\u00e9":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("an escape and the character itself, outside ASCII", '{"\\u00e9":1,', '"é":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a surrogate pair escape and the character it spells", '{"\\ud83d\\ude00":1,', '"\U0001F600":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("an escape and the character itself, three bytes long", '{"\\u20ac":1,', '"\u20ac":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("the last two-byte character and its escape", '{"\u07ff":1,', '"\\u07FF":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("the first three-byte character and its escape", '{"\\u0800":1,', '"\u0800":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("the last three-byte character and its escape", '{"\uffff":1,', '"\\uffff":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("the first character outside the basic plane and its pair", '{"\\ud800\\udc00":1,', '"\U00010000":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("the last code point and its pair", '{"\U0010FFFF":1,', '"\\uDBFF\\uDFFF":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("an escaped slash and a slash", '{"\\/":1,', '"/":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a \\u002f escape and an escaped slash", '{"\\u002f":1,', '"\\/":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a \\n escape and its \\u form", '{"a\\nb":1,', '"a\\u000ab":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a name that looks like a number and its escaped alias", '{"1":1,', '"\\u0031":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("the empty name twice", '{"":1,', '"":2}', "duplicate_key", acceptance="RAW-1", python_json_accepts=True)
no("a duplicate that is not adjacent", '{"a":1,"b":2,', '"a":3}', "duplicate_key", acceptance="RAW-2", python_json_accepts=True)
no("a duplicate one level down", '{"a":{"b":1,', '"b":2}}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a duplicate inside an array element", '[{"a":1},{"a":1,', '"a":2}]', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a duplicate four levels down", '{"p":[{"q":{"r":1,', '"\\u0072":2}}]}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)
no("a duplicate before a later syntax fault", '{"a":1,', '"a":', "duplicate_key", acceptance="RAW-2")
no("a duplicate whose value would be too deep", '{"a":1,', '"a":[[[1]]]}', "duplicate_key", max_depth=2, acceptance="RAW-2", python_json_accepts=True)
no("a duplicate with whitespace before the second name", '{"a":1,\n  ', '"a":2}', "duplicate_key", acceptance="RAW-2", python_json_accepts=True)
no("a NUL name and its lower-case escape twin", '{"\\u0000":1,', '"\\u0000":2}', "duplicate_key", acceptance="RAW-3", python_json_accepts=True)


def build():
    names = [c["name"] for c in cases]
    assert len(set(names)) == len(names), "two cases share a name"
    by_code = {}
    for c in cases:
        k = "accepted" if c["expect"]["ok"] else c["expect"]["code"]
        by_code[k] = by_code.get(k, 0) + 1
    doc = {"kind": "ainglish.strict-json-vectors.v1", "rule": RULE,
           "generated_by": {"tool": "tools/gen_strict_json_vectors.py", "tool_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
                            "method": "every expectation is written by hand; a refusal's offset is the length of the text placed before the fault; independent of ainglish.strict_json"},
           "note": "Synthetic software vectors. Not language evidence, not a measurement, not an activation of any reading.",
           "codes": ["size", "utf8", "syntax", "surrogate", "depth", "duplicate_key"], "tops": ["object", "array", "string", "number", "literal"],
           "counts": by_code, "cases": cases}
    return (json.dumps(doc, indent=1, ensure_ascii=True) + "\n").encode("ascii")


if __name__ == "__main__":
    data = build()
    if "--check" in sys.argv:
        same = OUT.exists() and OUT.read_bytes() == data
        print("committed file matches" if same else "committed file DIFFERS from what this tool writes")
        sys.exit(0 if same else 1)
    OUT.write_bytes(data)
    print("wrote", OUT.relative_to(ROOT), "| cases", len(cases), "| sha256", hashlib.sha256(data).hexdigest())
