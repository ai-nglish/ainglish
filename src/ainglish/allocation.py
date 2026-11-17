"""balanced-world-hash-v1: a balanced, deterministic arm allocation for the preservation reading.

Pure functions. No network, no file, no clock, no randomness. Nothing here is wired into a panel
run, a manifest, a mint or a filing: this module only states the rule and computes it, so that the
register and the SDK can be shown to agree on its bytes before anything depends on it.

Why a new rule. `panel.arm_for` deals each (reader, item) to an arm by one hash bit, so the two arms
of a reader and form come out near half each and not at a fixed size. The preservation inventory
fixes `per_cell` for every reader, form and arm. Building a bank against the old allocator until
the counts fit would make selection depend on assignment. This rule gives fixed denominators with
no search. `panel.arm_for` is not changed and is not used here.

The rule, for one reader and one form with exactly 2 * per_cell distinct worlds:

1. For each world, the key is SHA-256 of the canonical JSON of the five-element array
   ["balanced-world-hash-v1", seed, reader, form, world].
2. Worlds are ranked by that digest, ascending, and by the UTF-8 bytes of the world id where two
   digests are equal.
3. The first per_cell worlds read English. The rest read Ainglish.

There is no endpoint argument. Every separately elicited endpoint for a reader and a world inherits
the world's arm; an endpoint cannot be dealt on its own.

Names. `reader`, `form` and each world id are non-empty strings of at most 255 UTF-8 bytes, in
Unicode normalisation form C, with no control character (below U+0020, U+007F to U+009F), no
double quote, no backslash, and neither U+2028 nor U+2029. Under that rule the canonical JSON of
the array is the array spelled with the names as they are, so two JSON libraries cannot disagree
about an escape. `seed` is an integer from 0 to 2**53 - 1; a boolean is refused.

What this does not establish. A balanced hash says nothing about whether the worlds are
independent, whether the sampling was sound or whether a bank is fit for use. The seed and the
world roster must be fixed before any target inference; searching seeds or rosters after seeing
outcomes is outside the rule, and nothing here can detect it.
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

RULE = "balanced-world-hash-v1"
ARMS = ("english", "ainglish")
PORTABLE_INTEGER_MAX = 2 ** 53 - 1
MAX_NAME_BYTES = 255


class AllocationRefused(ValueError):
    """An input the rule does not admit. `code` names the reason; nothing is allocated."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _name(value, what):
    if not isinstance(value, str) or value == "":
        raise AllocationRefused("name_type", "%s must be a non-empty string." % what)
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError:
        raise AllocationRefused("name_encoding", "%s is not valid Unicode text." % what) from None
    if len(raw) > MAX_NAME_BYTES:
        raise AllocationRefused("name_length", "%s is longer than %d UTF-8 bytes." % (what, MAX_NAME_BYTES))
    if unicodedata.normalize("NFC", value) != value:
        raise AllocationRefused("name_not_nfc", "%s is not in Unicode normalisation form C." % what)
    for ch in value:
        o = ord(ch)
        if o < 0x20 or 0x7F <= o <= 0x9F or ch in '"\\' or o in (0x2028, 0x2029):
            raise AllocationRefused("name_character", "%s holds a character the rule does not admit (U+%04X)." % (what, o))
    return value


def _seed(seed):
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= PORTABLE_INTEGER_MAX:
        raise AllocationRefused("seed", "seed must be an integer from 0 to %d." % PORTABLE_INTEGER_MAX)
    return seed


def _per_cell(per_cell):
    if isinstance(per_cell, bool) or not isinstance(per_cell, int) or not 1 <= per_cell <= PORTABLE_INTEGER_MAX:
        raise AllocationRefused("per_cell", "per_cell must be an integer of at least 1.")
    return per_cell


def _worlds(worlds, per_cell, what="worlds"):
    if not isinstance(worlds, (list, tuple)):
        raise AllocationRefused("worlds_type", "%s must be a list of world ids." % what)
    names = [_name(w, "a world id") for w in worlds]
    if len(set(names)) != len(names):
        raise AllocationRefused("worlds_duplicate", "%s names a world more than once." % what)
    if len(names) != 2 * per_cell:
        raise AllocationRefused("worlds_count", "%s holds %d worlds; the rule needs exactly %d (2 x per_cell)." % (what, len(names), 2 * per_cell))
    return names


def preimage(seed, reader, form, world):
    """The bytes that are hashed for one world: the canonical JSON of the five-element array."""
    _seed(seed); _name(reader, "reader"); _name(form, "form"); _name(world, "a world id")
    return ('["%s",%d,"%s","%s","%s"]' % (RULE, seed, reader, form, world)).encode("utf-8")


def order(keyed):
    """Rank (digest bytes, world id) pairs: by digest, then by the UTF-8 bytes of the world id."""
    return sorted(keyed, key=lambda kw: (kw[0], kw[1].encode("utf-8")))


def allocate(seed, reader, form, worlds, per_cell):
    """One reader and one form: {'english': [...], 'ainglish': [...]}, each of per_cell worlds, in rank order."""
    per_cell = _per_cell(per_cell); names = _worlds(worlds, per_cell)
    ranked = [w for _, w in order([(hashlib.sha256(preimage(seed, reader, form, w)).digest(), w) for w in names])]
    return {"english": ranked[:per_cell], "ainglish": ranked[per_cell:]}


def arm_of(allocation, world):
    """The arm a world was dealt in one reader-and-form allocation. Every endpoint of that world inherits it."""
    hits = [arm for arm in ARMS if world in allocation[arm]]
    if len(hits) != 1:
        raise AllocationRefused("world_unknown", "the world is not in this allocation exactly once.")
    return hits[0]


def _roster(seed, readers, forms, per_cell):
    _seed(seed); per_cell = _per_cell(per_cell)
    if not isinstance(readers, (list, tuple)) or not readers:
        raise AllocationRefused("readers_type", "readers must be a non-empty list of reader names.")
    names = [_name(r, "a reader name") for r in readers]
    if len(set(names)) != len(names):
        raise AllocationRefused("readers_duplicate", "readers names a reader more than once.")
    if not isinstance(forms, dict) or not forms:
        raise AllocationRefused("forms_type", "forms must be a non-empty object of form id to world ids.")
    checked = {_name(f, "a form id"): _worlds(ws, per_cell, "the roster of form %r" % f) for f, ws in forms.items()}
    return names, checked, per_cell


def allocate_roster(seed, readers, forms, per_cell):
    """Every reader and form: {reader: {form: {'english': [...], 'ainglish': [...]}}}. Input order does not matter."""
    names, checked, per_cell = _roster(seed, readers, forms, per_cell)
    return {r: {f: allocate(seed, r, f, ws, per_cell) for f, ws in checked.items()} for r in names}


def commitment(seed, readers, forms, per_cell):
    """SHA-256 over everything the allocation depends on, with every set in byte order.

    A changed seed, reader name, form id, world roster or per_cell changes it. A reordered input
    does not. It commits to the inputs, not to a claim that the inputs were soundly chosen."""
    names, checked, per_cell = _roster(seed, readers, forms, per_cell)
    by = lambda s: s.encode("utf-8")
    body = {"rule": RULE, "seed": seed, "per_cell": per_cell, "readers": sorted(names, key=by),
            "forms": [{"id": f, "worlds": sorted(checked[f], key=by)} for f in sorted(checked, key=by)]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
