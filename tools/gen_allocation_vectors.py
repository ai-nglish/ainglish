"""Write tests/fixtures/balanced-world-hash-v1.json: the vectors both implementations read.

Nothing here is produced by `ainglish.allocation`, and that module is not imported. The expected
values come from a second, differently shaped computation, so the file can catch an error in the
module instead of echoing it:

* the hashed bytes are built by `json.dumps` of the five-element array, where the module spells
  them by hand;
* the ranking is a selection sort over (hex digest, world bytes), where the module calls `sorted`
  on (raw digest, world bytes);
* the commitment is assembled as a string, where the module serialises an object.

Run from the repository root:  python3 tools/gen_allocation_vectors.py
The register carries a byte copy of the output (tests/Fixtures/balanced-world-hash-v1.json).
"""
from __future__ import annotations

import hashlib
import json
import pathlib

RULE = "balanced-world-hash-v1"
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "balanced-world-hash-v1.json"


def digest_hex(seed, reader, form, world):
    return hashlib.sha256(json.dumps([RULE, seed, reader, form, world], separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def ranked(seed, reader, form, worlds):
    rest = [(digest_hex(seed, reader, form, w), w.encode("utf-8"), w) for w in worlds]; out = []
    while rest:
        best = 0
        for i in range(1, len(rest)):
            if (rest[i][0], rest[i][1]) < (rest[best][0], rest[best][1]):
                best = i
        out.append(rest.pop(best))
    return [(h, w) for h, _, w in out]


def case(name, seed, reader, form, worlds, per_cell):
    r = ranked(seed, reader, form, worlds)
    return {"name": name, "seed": seed, "reader": reader, "form": form, "per_cell": per_cell, "worlds": worlds,
            "first_preimage_utf8_hex": json.dumps([RULE, seed, reader, form, worlds[0]], separators=(",", ":"), ensure_ascii=False).encode("utf-8").hex(),
            "rank": [{"world": w, "sha256": h} for h, w in r], "english": [w for _, w in r[:per_cell]], "ainglish": [w for _, w in r[per_cell:]]}


def commit(seed, readers, forms, per_cell):
    q = lambda s: json.dumps(s, ensure_ascii=False)
    b = lambda s: s.encode("utf-8")
    fs = ",".join('{"id":%s,"worlds":[%s]}' % (q(f), ",".join(q(w) for w in sorted(forms[f], key=b))) for f in sorted(forms, key=b))
    text = '{"forms":[%s],"per_cell":%d,"readers":[%s],"rule":%s,"seed":%d}' % (fs, per_cell, ",".join(q(r) for r in sorted(readers, key=b)), q(RULE), seed)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def roster(name, seed, readers, forms, per_cell):
    alloc = {}
    for r in readers:
        alloc[r] = {}
        for f, ws in forms.items():
            rk = ranked(seed, r, f, ws); alloc[r][f] = {"english": [w for _, w in rk[:per_cell]], "ainglish": [w for _, w in rk[per_cell:]]}
    return {"name": name, "seed": seed, "readers": readers, "forms": forms, "per_cell": per_cell, "commitment": commit(seed, readers, forms, per_cell), "allocation": alloc}


W8 = ["w-%02d" % i for i in range(8)]
W32 = ["world-%03d" % i for i in range(32)]
INTL = ["wörld-ä", "wörld-ö", "мир-1", "世界-2", "w/3", "w 4"]          # NFC, non-ASCII, a slash and a space
cases = [case("four worlds", 17, "reader-a", "form-a", ["w-00", "w-01", "w-02", "w-03"], 2),
         case("eight worlds", 17, "reader-a", "form-a", W8, 4),
         case("eight worlds, reversed input", 17, "reader-a", "form-a", list(reversed(W8)), 4),
         case("eight worlds, other reader", 17, "reader-b", "form-a", W8, 4),
         case("eight worlds, other form", 17, "reader-a", "form-b", W8, 4),
         case("reader name with punctuation", 17, "gemma3-12b@q4_k_m", "form-a", W8, 4),
         case("names outside ASCII", 29, "lecteur-é", "forme-ß", INTL, 3),
         case("names that look like numbers", 29, "1", "01", ["1", "01", "1.0", "10", "2", "02"], 3),     # a reader that compares or keys them as numbers merges or reorders them
         case("names that look like numbers, reader and form exchanged", 29, "01", "1", ["1", "01", "1.0", "10", "2", "02"], 3)]
for s in (0, 1, 2, 3, 17, 2 ** 31, 2 ** 53 - 1):
    cases.append(case("thirty-two worlds, seed %d" % s, s, "reader-a", "form-a", W32, 16))
R1 = roster("two readers, two forms", 17, ["reader-b", "reader-a"], {"form-b": W8, "form-a": list(reversed(W8))}, 4)
R2 = roster("the same roster, every input reordered", 17, ["reader-a", "reader-b"], {"form-a": W8, "form-b": list(reversed(W8))}, 4)
R3 = roster("forms and readers that look like numbers", 29, ["1", "01"], {"1": ["1", "01"], "01": ["10", "1.0"], "1.0": ["2", "02"]}, 1)
assert R1["commitment"] == R2["commitment"] and R1["allocation"]["reader-a"]["form-a"] == R2["allocation"]["reader-a"]["form-a"]
changed = {"reader renamed": roster("reader renamed", 17, ["reader-a", "reader-c"], {"form-a": W8, "form-b": W8}, 4),
           "seed changed": roster("seed changed", 18, ["reader-a", "reader-b"], {"form-a": W8, "form-b": W8}, 4),
           "one world replaced": roster("one world replaced", 17, ["reader-a", "reader-b"], {"form-a": W8[:-1] + ["w-99"], "form-b": W8}, 4),
           "form renamed": roster("form renamed", 17, ["reader-a", "reader-b"], {"form-a": W8, "form-c": W8}, 4)}
assert len({R1["commitment"]} | {v["commitment"] for v in changed.values()}) == 5
refusals = [  # hand-written: an input and the code the refusal must carry. \u escapes are decoded by the JSON reader on both sides.
    {"name": "odd total", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "b", "c"], "per_cell": 2, "code": "worlds_count"},
    {"name": "too few worlds", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "b"], "per_cell": 2, "code": "worlds_count"},
    {"name": "duplicate world", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "b", "c", "a"], "per_cell": 2, "code": "worlds_duplicate"},
    {"name": "per_cell zero", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": [], "per_cell": 0, "code": "per_cell"},
    {"name": "negative seed", "call": "allocate", "seed": -1, "reader": "r", "form": "f", "worlds": ["a", "b"], "per_cell": 1, "code": "seed"},
    {"name": "seed above the portable ceiling", "call": "allocate", "seed": 2 ** 53, "reader": "r", "form": "f", "worlds": ["a", "b"], "per_cell": 1, "code": "seed"},
    {"name": "empty reader", "call": "allocate", "seed": 17, "reader": "", "form": "f", "worlds": ["a", "b"], "per_cell": 1, "code": "name_type"},
    {"name": "empty world id", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", ""], "per_cell": 1, "code": "name_type"},
    {"name": "world id not in form C", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "é"], "per_cell": 1, "code": "name_not_nfc"},
    {"name": "double quote in a name", "call": "allocate", "seed": 17, "reader": "r\"x", "form": "f", "worlds": ["a", "b"], "per_cell": 1, "code": "name_character"},
    {"name": "backslash in a name", "call": "allocate", "seed": 17, "reader": "r", "form": "f\\x", "worlds": ["a", "b"], "per_cell": 1, "code": "name_character"},
    {"name": "control character in a name", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "b\u0007"], "per_cell": 1, "code": "name_character"},
    {"name": "line separator in a name", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "b "], "per_cell": 1, "code": "name_character"},
    {"name": "C1 control in a name", "call": "allocate", "seed": 17, "reader": "r", "form": "f", "worlds": ["a", "b\u0085"], "per_cell": 1, "code": "name_character"},
    {"name": "name of 256 bytes", "call": "allocate", "seed": 17, "reader": "r" * 256, "form": "f", "worlds": ["a", "b"], "per_cell": 1, "code": "name_length"},
    {"name": "duplicate reader", "call": "roster", "seed": 17, "readers": ["r", "r"], "forms": {"f": ["a", "b"]}, "per_cell": 1, "code": "readers_duplicate"},
    {"name": "no readers", "call": "roster", "seed": 17, "readers": [], "forms": {"f": ["a", "b"]}, "per_cell": 1, "code": "readers_type"},
    {"name": "no forms", "call": "roster", "seed": 17, "readers": ["r"], "forms": {}, "per_cell": 1, "code": "forms_type"},
    {"name": "one form short of worlds", "call": "roster", "seed": 17, "readers": ["r"], "forms": {"f": ["a", "b"], "g": ["a"]}, "per_cell": 1, "code": "worlds_count"},
]
doc = {"kind": "ainglish.balanced-world-hash-vectors.v1", "rule": RULE,
       "generated_by": {"tool": "tools/gen_allocation_vectors.py", "tool_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
                        "method": "preimage by json.dumps of the five-element array; selection sort over (hex digest, world bytes); commitment assembled as a string; independent of ainglish.allocation", "python": "3.12"},
       "note": "Synthetic software vectors. Not language evidence, not a measurement, not a sampled bank, not an activation of any reading.",
       "arms": {"first_per_cell": "english", "rest": "ainglish"}, "max_name_bytes": 255, "portable_integer_max": 2 ** 53 - 1,
       "cases": cases, "rosters": [R1, R2, R3], "commitment_changes": {"base": R2["commitment"], "changed": {k: v["commitment"] for k, v in changed.items()}}, "refusals": refusals}
OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("wrote", OUT.relative_to(ROOT), "| cases", len(cases), "| rosters 3 | refusals", len(refusals), "| sha256", hashlib.sha256(OUT.read_bytes()).hexdigest())
