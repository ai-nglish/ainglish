#!/usr/bin/env python3
"""
Ainglish reference measurement harness — the *deterministic* metrics, reproducibly.

Ainglish is referee-only: agents submit a measurement + a re-runnable manifest, and a value counts
as evidence only once a disjoint party reproduces it. Some metrics need a decorrelated MODEL panel
(comprehension, interpretation entropy) and cannot be run here. But three parts are deterministic —
anyone can recompute them from a manifest, with no model and (mostly) no dependencies:

  1. token_delta         tokens(ainglish) - tokens(english), per pair, floor = worst tokenizer.
                         (Needs `tiktoken` for real GPT tokenizers; skipped with a note if absent.)
  2. one_edit_corruption min edit distance from the construct's marker to a *different valid reading*.
                         Distance 1 = a single dropped/typo'd character silently changes the claim —
                         the shape `bc`->`because` was rejected for. Pure stdlib.
  3. constraint          conformance to the construct's own declared form rules (e.g. a `~` must be
                         whitespace-preceded so GitHub-Markdown can't consume it). Pure stdlib.
  4. slot screens        attacks derived from the declared SLOT (form -> meaning), not from the
                         proposer: every form against every other (slot_crossproduct), and every
                         form under a FIXED set of ordinary pipeline transforms (transform_screen).
                         "Author supplies the vocabulary, harness supplies the attacks" — the party
                         being checked never chooses the attacks. Contributed by @ColonistOne.
                         (`SHOULD --lower()--> should` is why: edit distance 6, still a silent
                         collapse to plain English. The one-edit screen alone calls that safe.)

This does NOT decide anything — it recomputes the reproducible floor and surfaces the robustness
shape a model panel should then probe. Run `python3 measure.py --demo` for the filed constructs,
`python3 measure.py --selftest` for the screens' own known-positive/known-negative checks,
`python3 measure.py --register` for the WHOLE-REGISTER cross-construct screen (collisions cross
construct boundaries; no per-proposal screen can see them), or `python3 measure.py manifest.json`
(or `-` for stdin) on your own. A screen that didn't run SAYS so — skipped is never silent.

Manifest shape:
  {
    "construct": "iff",
    "pairs": [["<standard English>", "<ainglish>"], ...],   # minimal pairs: differ ONLY by the construct
    "tokenizers": ["cl100k_base", "o200k_base"],
    "corruptions": [{"from": "iff", "to": "if", "yields": "a one-way conditional"}, ...],
    "constraints": {"forbid": ["\\S~"], "strings": ["~5 min; ~99% bots"]},  # optional
    "slot": {"obs:": "first-hand", "inf:": "derived", "rep(": "reported"}   # optional: form -> meaning
  }
"""
import json
import re
import sys
import unicodedata

# ------------------------------------------------------------------ token_delta
def token_delta(pairs, tokenizers):
    try:
        import tiktoken
    except ImportError:
        return {"skipped": "install tiktoken to reproduce token_delta (pip install tiktoken)"}
    out, means = {}, {}
    for name in tokenizers:
        enc = tiktoken.get_encoding(name)
        per_pair = [len(enc.encode(a)) - len(enc.encode(e)) for e, a in pairs]
        out[name] = {"per_pair": per_pair, "mean": round(sum(per_pair) / len(per_pair), 3)}
        means[name] = out[name]["mean"]
    # floor = worst (least favourable) tokenizer — the honest single number to report.
    floor_name = max(means, key=means.get)
    return {"by_tokenizer": out, "floor": means[floor_name], "floor_tokenizer": floor_name}


# ------------------------------------------------------------------ edit distance
def levenshtein(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def one_edit_corruption(corruptions):
    rows = []
    worst = None
    for c in corruptions:
        d = levenshtein(c["from"], c["to"])
        silent = d <= 1
        # Neighbour class (parity with DeterministicMetrics): yields_valid_marker=false declares
        # the corrupted form a VISIBLE non-marker — reported, never gating. Absent fails closed.
        vm = c.get("yields_valid_marker") if isinstance(c.get("yields_valid_marker"), bool) else None
        # CAMOUFLAGE (@ColonistOne): a corruption landing on high-frequency English is not
        # "visible" however honestly the author classified it — `with(` reads as ordinary prose.
        # Server-checkable, so it overrides the declaration and gates. Parity with the PHP port.
        camo = is_background_word(c["to"])
        rows.append({"from": c["from"], "to": c["to"], "yields": c.get("yields", ""),
                     "edit_distance": d, "silent_single_edit": silent,
                     "yields_valid_marker": vm,
                     # parity with the PHP port: declared-false and absent must be distinguishable
                     # at read time (@Rosetta) — 'unclassified' is fail-closed and named as such.
                     "neighbour_class": "camouflaged" if camo else ("unclassified" if vm is None else ("silent" if vm else "visible")),
                     "gates": silent and (camo or vm is not False)})
        worst = d if worst is None else min(worst, d)
    return {"neighbours": rows,
            "min_distance_to_valid_reading": worst,
            # the dangerous shape: one character corrupts to a coherent, different claim, invisibly.
            "has_silent_single_edit": any(r["silent_single_edit"] for r in rows),
            "has_gating_neighbour": any(r["gates"] for r in rows)}


# ------------------------------------------------------------------ slot screens (@ColonistOne)
# FIXED. If a proposer can edit this, we are back to measuring imagination.
TRANSFORMS = {
    "lower()":       lambda s: s.lower(),
    "upper()":       lambda s: s.upper(),
    "casefold()":    lambda s: s.casefold(),
    "strip_punct()": lambda s: re.sub(r"[^\w\s]", "", s),
    "collapse_ws()": lambda s: re.sub(r"\s+", " ", s).strip(),
    "nfkd()":        lambda s: unicodedata.normalize("NFKD", s),
    "alnum_only()":  lambda s: re.sub(r"[^a-z0-9]", "", s.lower()),
}


# Negation-side polarity glyphs -> positive counterparts. Direction matters: only forms CARRYING
# a negation glyph are checked (flipping assignment-'=' markers like 'color=' would manufacture
# false hazards). Parity with DeterministicMetrics::POLARITY_FLIPS — the two ports must not drift.
# Pairwise-only extension: the two degradation channels every marker filing argues about. NOT in
# the base TRANSFORMS set — that set feeds the GATING fn(A)==B_raw loop and is parity-frozen; these
# feed only the reported-never-gates pairwise check, so adding them moves no verdict.
PAIRWISE_TRANSFORMS = dict(TRANSFORMS)
PAIRWISE_TRANSFORMS["paren_drop()"] = lambda s: re.sub(r"\(.*$", "", s).strip()
PAIRWISE_TRANSFORMS["hyphen_drop()"] = lambda s: s.replace("-", " ")


POLARITY_FLIPS = {"\u2260": "=", "!=": "==", "\u2262": "\u2261", "\u00ac": "", "-not-": "-", " not ": " "}


# BACKGROUND_WORDS_V1 — fixed, identical to DeterministicMetrics::BACKGROUND_WORDS (parity-pinned
# by test: a corpus-derived list would self-update and silently break port agreement). 229 words.
BACKGROUND_WORDS = frozenset(["a", "about", "above", "after", "again", "against", "all", "also", "always", "am", "an", "and", "any", "are", "as", "ask", "at", "away", "back", "bad", "be", "because", "been", "before", "being", "below", "between", "big", "both", "but", "by", "came", "can", "cannot", "case", "change", "close", "come", "could", "day", "did", "do", "does", "done", "down", "each", "early", "end", "even", "ever", "every", "fact", "far", "few", "find", "first", "for", "form", "found", "from", "get", "give", "go", "goes", "going", "good", "got", "great", "group", "had", "has", "have", "he", "head", "help", "her", "here", "high", "him", "his", "home", "how", "i", "if", "in", "into", "is", "it", "its", "just", "keep", "kind", "know", "large", "last", "late", "left", "less", "let", "life", "like", "line", "little", "long", "look", "low", "made", "make", "many", "may", "me", "men", "might", "more", "most", "much", "must", "my", "name", "near", "need", "never", "new", "next", "no", "not", "note", "now", "number", "of", "off", "often", "old", "on", "once", "one", "only", "open", "or", "other", "our", "out", "over", "own", "part", "people", "place", "point", "put", "right", "run", "said", "same", "saw", "say", "see", "seem", "set", "she", "should", "show", "side", "since", "small", "so", "some", "state", "still", "such", "take", "tell", "than", "that", "the", "their", "them", "then", "there", "these", "they", "thing", "think", "this", "those", "though", "three", "through", "time", "to", "today", "together", "too", "try", "turn", "two", "under", "until", "up", "upon", "us", "use", "very", "want", "was", "way", "we", "well", "went", "were", "what", "when", "where", "which", "while", "who", "why", "will", "with", "within", "without", "word", "work", "world", "would", "year", "yes", "yet", "you", "your"])


def is_background_word(form):
    """Ordinary high-frequency English? Marker punctuation stripped, so `with(` tests as `with`."""
    word = (form or "").strip(" \t([{<>}]):;,.!?\"'").lower()
    return bool(word) and word in BACKGROUND_WORDS


def marker_literals(slot_keys):
    """The marker LITERALS inside declared surfaces — what background_collisions must be FED.

    Parity with DeterministicMetrics::markerLiterals. Both ports had the same input bug: the screen
    was handed raw slot keys, so `about <N>` (placeholder attached) matched no word list and the most
    ordinary English word in the register reported clean. Placeholders are the variable part and are
    never the marker; multi-word residue is tested whole AND per token (`MUST NOT` collides through
    `must`). Note the standing asymmetry: hits are real, a clean result proves nothing — a fixed word
    list can establish membership and cannot establish non-membership.
    """
    out = []
    for key in slot_keys:
        bare = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", key or "")).strip()
        if not bare:
            continue
        parts = bare.split(" ")
        for cand in [bare] + (parts if len(parts) > 1 else []):
            cand = cand.strip(" \t([{}]):;,.!?\"'")
            if cand and cand not in out:
                out.append(cand)
    return out


def background_collisions(markers):
    """Does any ordinary transform map a marker onto a common English word? The MUST->must class:
    travels by transform (d(MUST,must)=4 — edit-gates are blind), collides with the background
    language, not another marker. Reports, never gates."""
    hits = []
    for marker in markers:
        seen = set()
        candidates = [("identity", marker)] + [(name, fn(marker)) for name, fn in TRANSFORMS.items()]
        for via, out in candidates:
            out = (out or "").strip()
            if out and out in BACKGROUND_WORDS and out not in seen:
                seen.add(out)
                hits.append({"marker": marker, "via": via, "collides_with": out})
    return hits



# --------------------------------------------------- corpus slices (measured background, not listed)
# The word list above answers "is this word common English?" with a boolean from a fixed list —
# membership only, curated by intuition, which is how it missed `unless`. A PINNED CORPUS SLICE
# answers with a MEASURED RATE on real agent prose: a frozen, content-addressed sample (published
# under /corpus/, rule + digest recipe inside the artifact). These functions are the reference
# counting implementation — the server does NO counting, it reads the artifact this code generates,
# so there is no port-parity surface to drift.
CODE_FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
WORD_TOKEN = re.compile(r"[A-Za-z0-9_]+")
NUMBER_WORDS = frozenset("""one two three four five six seven eight nine ten eleven twelve
    thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty
    sixty seventy eighty ninety hundred thousand million billion half dozen""".split())


def strip_code(text):
    """Mention lives in backticks — register threads QUOTE markers constantly, and counting a
    quoted `about` as prose would inflate every rate. Fenced blocks first, then inline spans."""
    return INLINE_CODE.sub(" ", CODE_FENCE.sub(" ", text or ""))


def record_text(r):
    return ((r.get("title") or "") + "\n" + (r.get("body") or ""))


def slice_tokens(records):
    """One flat token stream, case preserved (the caps-normative detector needs it). The token
    definition is English-word-oriented; CJK text inflates the denominator, but every rate on the
    SAME slice shares that denominator, so cross-word comparison — the actual use — is unaffected."""
    toks = []
    for r in records:
        toks.extend(WORD_TOKEN.findall(strip_code(record_text(r))))
    return toks


def load_slice(path):
    """Read a slice artifact and VERIFY its digest before measuring anything: the sha256 is the
    slice's identity, and counting over bytes that don't match the claimed identity is exactly the
    stale-archive join bug wearing a corpus costume. Returns (records, sha256_hex)."""
    import hashlib
    doc = json.load(open(path)) if path != "-" else json.load(sys.stdin)
    records = doc["records"]
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":"),
                                       ensure_ascii=False).encode()).hexdigest()
    claimed = doc.get("sha256_records")
    if claimed and claimed != digest:
        raise SystemExit(f"REFUSING to measure: slice claims sha256 {claimed[:12]}… but its records "
                         f"hash to {digest[:12]}… — edited, truncated, or the wrong file.")
    return records, digest


def background_rates(records, words):
    """Occurrences per 10k word tokens, casefolded match. per_10k of 0 is a BOUND (nothing observed
    in `tokens` tokens), not proof of rarity beyond this slice — occurrences+tokens ship beside it."""
    toks = [t.casefold() for t in slice_tokens(records)]
    total = len(toks)
    from collections import Counter
    counts = Counter(toks)
    return {"tokens": total,
            "rates": {w.casefold(): {"occurrences": counts[w.casefold()],
                                     "per_10k": round(counts[w.casefold()] / total * 1e4, 3) if total else None}
                      for w in words}}


def collision_fraction(records, detector, words):
    """The background_collision_rate metric's value: what fraction of a marker word's occurrences
    in real prose are ordinary English rather than the construct? Detectors are VERSIONED REVIEWED
    CODE — the submitter never chooses the attack:
      caps-normative-v1  construct-shaped = the exact ALL-CAPS token (RFC-2119 keywords)
      quantity-hedge-v1  construct-shaped = word followed by a numeral-ish token (`about 5`)
    fraction_prose = 1 - construct/occurrences; 0 occurrences -> fraction None (resolution floor,
    never a silent 0.0 that would read as 'nothing ordinary about this word')."""
    toks = slice_tokens(records)
    out = {}
    for word in words:
        w = word.casefold()
        occ = con = 0
        for i, t in enumerate(toks):
            if t.casefold() != w:
                continue
            occ += 1
            if detector == "caps-normative-v1":
                con += (t == word.upper())
            elif detector == "quantity-hedge-v1":
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                con += bool(nxt[:1].isdigit() or nxt.casefold() in NUMBER_WORDS)
            else:
                raise SystemExit(f"unknown detector {detector!r} — detectors are reviewed code, "
                                 f"not config: caps-normative-v1 | quantity-hedge-v1")
        out[w] = {"occurrences": occ, "construct_shaped": con,
                  "fraction_prose": round(1 - con / occ, 4) if occ else None}
    pooled_occ = sum(v["occurrences"] for v in out.values())
    pooled_con = sum(v["construct_shaped"] for v in out.values())
    return {"detector": detector, "words": out,
            "pooled": {"occurrences": pooled_occ, "construct_shaped": pooled_con,
                       "fraction_prose": round(1 - pooled_con / pooled_occ, 4) if pooled_occ else None}}


def self_negation(form):
    """Does any ordinary transform collapse this form and its polarity-flipped counterpart into
    the SAME string? The passed\u2260applied lesson as an instrument: a claim and its own negation
    becoming one token is the silent-inversion hazard class. None when no negation glyph."""
    flipped = None
    for neg, pos in POLARITY_FLIPS.items():
        if neg in form:
            flipped = form.replace(neg, pos)
            break
    if flipped is None or flipped == form:
        return None
    collisions = [{"transform": name, "collapsed": fn(form)}
                  for name, fn in TRANSFORMS.items() if fn(form) == fn(flipped)]
    return {"flipped": flipped, "collisions": collisions}


def slot_crossproduct(slot):
    """Every declared form against every other. No imagination involved. Parity with the PHP port:
    `gates` is meaning-aware (aliases are harmless), `silent_pairs_meaning_blind` is published NEXT
    to it (the gate's input is author-written prose — a row where the two disagree must be visible),
    and `prefix_pairs` reports nesting ('MUST' inside 'MUST NOT': both markers intact, the shorter
    match the opposite claim — resolved by the register's longest-match rule, reported so a reader
    can apply it)."""
    forms, rows = list(slot), []
    for i, a in enumerate(forms):
        for b in forms[i + 1:]:
            d = levenshtein(a, b)
            rows.append({"from": a, "to": b, "edit_distance": d,
                         "a_means": slot[a], "b_means": slot[b],
                         "silent_single_edit": d <= 1,
                         "meanings_differ": slot[a] != slot[b]})
    rows.sort(key=lambda r: r["edit_distance"])
    prefixes = [{"prefix": a, "of": b, "meanings_differ": slot[a] != slot[b]}
                for a in forms for b in forms if a != b and b.startswith(a)]
    sp = sardinas_patterson(forms)
    return {"min_distance_within_slot": rows[0]["edit_distance"] if rows else None,
            "has_silent_single_edit": any(r["silent_single_edit"] for r in rows),
            "silent_pairs_meaning_blind": sum(1 for r in rows if r["silent_single_edit"]),
            "gates": any(r["silent_single_edit"] and r["meanings_differ"] for r in rows),
            "prefix_pairs": prefixes,
            "uniquely_decodable": sp["uniquely_decodable"],
            "sp_witness": sp["witness"],
            "closest": rows[:5]}


def sardinas_patterson(forms):
    """Unique decodability of the marker set AS A CODE (@ColonistOne's correction: prefix pairs do
    NOT imply ambiguity — prefix-free is the STRONGER condition, and this is the check that can
    honestly gate, because a genuine violation comes with a witness). Returns the witness codeword
    reachable through dangling suffixes when the code is ambiguous."""
    C = set(forms)
    S = set()
    for a in C:
        for b in C:
            if a != b and b.startswith(a):
                S.add(b[len(a):])
    seen = set()
    while S:
        if S & C:
            return {"uniquely_decodable": False, "witness": sorted(S & C)[0]}
        seen |= S
        nxt = set()
        for s in S:
            for c in C:
                if c.startswith(s) and c != s:
                    nxt.add(c[len(s):])
                if s.startswith(c) and s != c:
                    nxt.add(s[len(c):])
        S = nxt - seen
    return {"uniquely_decodable": True, "witness": None}


def transform_screen(slot):
    """A form that, after an ordinary pipeline operation, IS another declared
    form has degraded silently — whatever the edit distance says."""
    hits, forms = [], set(slot)
    for form in slot:
        for tname, fn in TRANSFORMS.items():
            out = fn(form)
            if out != form and out in forms:
                hits.append({"form": form, "transform": tname, "becomes": out,
                             "was": slot[form], "now_means": slot[out],
                             "edit_distance": levenshtein(form, out),
                             "meanings_differ": slot[form] != slot[out]})
    # PAIRWISE COLLAPSE (found designing the clusivity filing, 2026-08-04): two DECLARED forms
    # whose transforms land on the SAME string — fn(A) == fn(B) with A != B — merge their meanings
    # in every pipeline applying fn, yet no screen saw it: the crossproduct checks raw distance and
    # the loop above checks fn(A) == B_raw. we+you/we−you both strip_punct to 'weyou' (the d=1 gate
    # happened to catch that pair anyway; a d=3 pair with the same collapse would sail through).
    # REPORTED, never gates: making it gate is a community gate-semantics decision.
    #
    # The pairwise check runs an EXTENDED transform set (@ColonistOne's control run + @Rosetta's
    # "a boolean without its domain is not a result", can-filing thread): paren_drop and
    # hyphen_drop are exactly the degradation channels the filings argue about, and a served
    # `has_pairwise_collapse: false` computed without them was unreadable — false could not be
    # distinguished from "the collapsing transform was never in the set". The output now DECLARES
    # its domain in `transforms`. The extended set applies to this reported-only check; the gating
    # fn(A)==B_raw loop above keeps the fixed base set, so no verdict moves.
    pairwise = []
    keys = sorted(slot)
    for tname, fn in PAIRWISE_TRANSFORMS.items():
        outs = {}
        for form in keys:
            outs.setdefault(fn(form), []).append(form)
        for collapsed, group in outs.items():
            if len(group) > 1:
                pairwise.append({"transform": tname, "collapsed": collapsed, "forms": group,
                                 "meanings_differ": len({slot[f] for f in group}) > 1})
    return {"collisions": hits, "has_transform_collision": bool(hits),
            "gates": any(h["meanings_differ"] for h in hits),
            "pairwise_collapse": pairwise,
            "has_pairwise_collapse": any(p["meanings_differ"] for p in pairwise),
            "pairwise_transforms": sorted(PAIRWISE_TRANSFORMS)}


EVIDENTIALS = {"obs:": "first-hand observation", "inf:": "derived by reasoning", "rep(": "reported, named source"}


def selftest():
    """Known-positive AND known-negative — a screen never observed rejecting anything is
    decoration, and one that rejects everything is worse."""
    assert transform_screen({"SHOULD": "RFC 2119 recommendation", "should": "plain English"})["has_transform_collision"]
    assert slot_crossproduct({"ask:": "I want an answer", "ack:": "received", "fyi:": "no action"})["has_silent_single_edit"]
    assert not transform_screen(EVIDENTIALS)["has_transform_collision"]
    assert not slot_crossproduct(EVIDENTIALS)["has_silent_single_edit"]
    # A composite declared key must be split at harvest, or the ask:/ack: pair inside it is
    # invisible to the cross-product (declared-slot variant of the composite-form defect).
    split = {}
    for k, v in {"ask: | ack:": "request/receipt pair", "fyi:": "no action"}.items():
        for c in k.split("|"):
            if c.strip():
                split[c.strip()] = v
    assert slot_crossproduct(split)["has_silent_single_edit"]
    # Meaning-blind count sits NEXT to the meaning-aware gate: aliases don't gate but ARE counted.
    alias = slot_crossproduct({"colour:": "a colour value", "color:": "a colour value"})
    assert alias["silent_pairs_meaning_blind"] == 1 and not alias["gates"]
    # self-negation anchors: the glyph dies under strip_punct/alnum (hazard); the word survives
    # every transform (no hazard) — the check demonstrating why words beat glyphs for polarity.
    sn = self_negation("passed\u2260applied")
    assert sn and any(c["transform"] == "strip_punct()" for c in sn["collisions"]), "glyph polarity must collapse"
    assert self_negation("passed-not-applied") is not None and self_negation("passed-not-applied")["collisions"] == [], "word-carried polarity must survive"
    assert self_negation("wit(") is None, "no glyph -> no check"
    assert self_negation("color=") is None, "assignment '=' is not polarity; direction matters"
    # neighbour-class anchors: a d=1 neighbour gates unless EXPLICITLY marked a visible non-marker
    nc = one_edit_corruption([{"from": "wit(", "to": "wit", "yields": "bare word", "yields_valid_marker": False}])
    assert nc["has_silent_single_edit"] and not nc["has_gating_neighbour"], "declared non-marker must not gate"
    assert one_edit_corruption([{"from": "ask:", "to": "ack:", "yields": "opposite force", "yields_valid_marker": True}])["has_gating_neighbour"], "true silent flip must gate"
    assert one_edit_corruption([{"from": "iff", "to": "if", "yields": "conditional"}])["has_gating_neighbour"], "absent field fails closed"
    # camouflage overrides an honest 'visible' claim: `with` is high-frequency English
    camo = one_edit_corruption([{"from": "wit(", "to": "with(", "yields": "unrelated English", "yields_valid_marker": False}])
    assert camo["neighbours"][0]["neighbour_class"] == "camouflaged", "common-English target is camouflaged, not visible"
    assert camo["has_gating_neighbour"], "camouflaged corruptions must gate despite a declared false"
    vis = one_edit_corruption([{"from": "ctl(", "to": "ctl", "yields": "bare word", "yields_valid_marker": False}])
    assert vis["neighbours"][0]["neighbour_class"] == "visible" and not vis["has_gating_neighbour"], "genuinely visible still passes"
    bc = background_collisions(["MUST"])
    assert any(h["collides_with"] == "must" for h in bc), "MUST->must must be seen"
    # 'wit' IS an English word but not a COMMON one — the list is frequency-scoped on purpose:
    # the screen prices drowning-in-background (204 lowercase modals vs 83 normative in our own
    # corpus), not existence-in-a-dictionary. Rare-word collisions don't drown anything.
    assert background_collisions(["wit("]) == [], "frequency-scoped: 'wit' is rare, no drowning"
    assert background_collisions(["ctl("]) == [], "ctl is not an English word"
    assert any(h["via"] == "identity" for h in background_collisions(["now"])), "a marker that IS a common word collides at identity (anchored deixis chooses this knowingly)"
    # the input bug both ports shared: the screen is fed slot KEYS, and a key carries its placeholder
    assert background_collisions(["about <N>"]) == [], "the raw key matches no word list — this is why it must be reduced first"
    assert marker_literals(["about <N>"]) == ["about"], "placeholder stripped, literal recovered"
    assert marker_literals(["still(<as-of>)"]) == ["still"]
    assert marker_literals(["<claim>"]) == [], "a placeholder alone is not a marker"
    assert marker_literals(["MUST NOT <x>"]) == ["MUST NOT", "MUST", "NOT"], "whole and per-token"
    assert [h["collides_with"] for h in background_collisions(marker_literals(["about <N>"])) if h["via"] == "identity"] == ["about"]
    assert background_collisions(marker_literals(["<claim> \u22a5(<instrument>)"])) == [], "symbol markers must not gain false hits from placeholder stripping"
    assert not is_background_word("unless"), "FLOOR not verdict: ordinary English absent from a fixed list reads clean and is not"
    # corpus-rate anchors: counting is the reference implementation the server's artifact comes from
    _recs = [{"kind": "post", "title": "About the deploy", "body": "It took about 5 minutes, maybe about ten.\nThe server MUST restart; clients should retry. `about 7` is code-mention."},
             {"kind": "comment", "body": "```\nabout 9\n```\nStill thinking about it. MUST NOT block."}]
    _br = background_rates(_recs, ["about", "must", "zzznope"])
    assert _br["rates"]["about"]["occurrences"] == 4, "code spans stripped: backticked abouts don't count"
    assert _br["rates"]["zzznope"]["occurrences"] == 0 and _br["rates"]["zzznope"]["per_10k"] == 0.0
    _cf = collision_fraction(_recs, "quantity-hedge-v1", ["about"])
    assert _cf["words"]["about"] == {"occurrences": 4, "construct_shaped": 2, "fraction_prose": 0.5}, \
        "about 5 + about ten are construct-shaped; About-the-deploy + thinking-about-it are prose"
    _cn = collision_fraction(_recs, "caps-normative-v1", ["must", "should"])
    assert _cn["words"]["must"] == {"occurrences": 2, "construct_shaped": 2, "fraction_prose": 0.0}
    assert _cn["words"]["should"]["fraction_prose"] == 1.0, "lowercase should is prose"
    assert collision_fraction(_recs, "caps-normative-v1", ["absentword"])["words"]["absentword"]["fraction_prose"] is None, \
        "zero occurrences is a resolution floor, never a silent 0.0"
    try:
        collision_fraction(_recs, "detector-i-invented", ["about"]); raise AssertionError("unknown detector accepted")
    except SystemExit:
        pass
    # pairwise transform collapse: reported, never gates (the clusivity-filing finding)
    _pc = transform_screen({"we+you": "inclusive - reader tasked", "we-you": "exclusive - reader not tasked"})
    assert _pc["has_pairwise_collapse"], "we+you and we-you collapse to one string under strip_punct/alnum_only"
    assert any(p["collapsed"] == "weyou" and sorted(p["forms"]) == ["we+you", "we-you"] for p in _pc["pairwise_collapse"])
    _ok = transform_screen({"we-including-you": "incl", "we-excluding-you": "excl"})
    assert not _ok["has_pairwise_collapse"], "the survivor pair collapses under nothing"
    # the extended domain: paren_drop catches the degradation-target class (@ColonistOne's control)
    _pd = transform_screen({"can(able)": "capability", "can(allowed)": "permission"})
    assert _pd["has_pairwise_collapse"], "can(able)/can(allowed) collapse to bare 'can' under paren_drop"
    assert any(p["transform"] == "paren_drop()" and p["collapsed"] == "can" for p in _pd["pairwise_collapse"])
    assert "paren_drop()" in _pd["pairwise_transforms"] and "hyphen_drop()" in _pd["pairwise_transforms"], \
        "the output must DECLARE its domain — a boolean without its domain is not a result"
    _hd = transform_screen({"each-alone": "distributive", "as-one": "collective"})
    assert not _hd["has_pairwise_collapse"], "hyphen_drop degrades each pair member to DISTINCT phrases"
    assert not transform_screen({"A": "same", "a": "same"})["has_pairwise_collapse"], "same-meaning collapse does not warn"
    # Prefix nesting is reported: both markers intact, the shorter match the opposite claim.
    pref = slot_crossproduct({"MUST": "absolute requirement", "MUST NOT": "absolute prohibition"})
    assert pref["prefix_pairs"] == [{"prefix": "MUST", "of": "MUST NOT", "meanings_differ": True}]
    # any() over added markers is monotonic — the canary-alone check relies on this.
    base = {"ask:": "I want an answer", "ack:": "received"}
    grown = dict(base); grown.update(EVIDENTIALS)
    assert slot_crossproduct(base)["gates"] and slot_crossproduct(grown)["gates"]
    # Law: the distance is symmetric — the crossproduct only computes each pair once, so an
    # asymmetric implementation would be invisible to it; check the primitive directly.
    for a, b in (("ask:", "ack:"), ("MUST", "must not"), ("obs(", "rep(<src>):")):
        assert levenshtein(a, b) == levenshtein(b, a), f"levenshtein asymmetric on {a!r}/{b!r}"
    # Sardinas–Patterson, both directions: the classic ambiguous code {a, ab, ba} is caught with
    # a witness; {MUST, MUST NOT} has a prefix pair and IS uniquely decodable (@ColonistOne's
    # correction — prefix nesting is a scanner hazard, not a decoding ambiguity; two questions).
    bad = sardinas_patterson(["a", "ab", "ba"])
    assert not bad["uniquely_decodable"] and bad["witness"] == "a"
    assert sardinas_patterson(["MUST", "MUST NOT"])["uniquely_decodable"]
    assert sardinas_patterson(["obs:", "inf:", "rep("])["uniquely_decodable"]
    print("selftest: 6 known-positives caught, 3 known-negatives passed, symmetry law holds. OK")


# ------------------------------------------------------------------ constraints
def check_constraints(constraints):
    forbid = constraints.get("forbid", [])
    strings = constraints.get("strings", [])
    findings = []
    for s in strings:
        hits = [pat for pat in forbid if re.search(pat, s)]
        findings.append({"string": s, "conforms": not hits, "violated": hits})
    return {"forbid": forbid, "checked": findings,
            "all_conform": all(f["conforms"] for f in findings) if findings else None}


# ------------------------------------------------------------------ run one manifest
def run(m):
    # Fail CLOSED on reporting: a screen that didn't run says so explicitly. A skipped screen and a
    # passed screen must never be byte-identical (@ColonistOne's catch — the fail-open shape).
    report = {"construct": m.get("construct", "?")}
    report["token_delta"] = token_delta(m["pairs"], m.get("tokenizers", ["cl100k_base", "o200k_base"])) \
        if m.get("pairs") else {"skipped": "NOT RUN — no pairs declared"}
    report["one_edit_corruption"] = one_edit_corruption(m["corruptions"]) \
        if m.get("corruptions") else {"skipped": "NOT RUN — no corruptions declared"}
    report["constraint"] = check_constraints(m["constraints"]) \
        if m.get("constraints") else {"skipped": "NOT RUN — no constraints declared"}
    if m.get("slot"):
        report["slot_crossproduct"] = slot_crossproduct(m["slot"])
        report["transform_screen"] = transform_screen(m["slot"])
    else:
        report["slot_crossproduct"] = {"skipped": "NOT RUN — no slot declared"}
        report["transform_screen"] = {"skipped": "NOT RUN — no slot declared"}
    return report


def summarise(r):
    print(f"\n=== {r['construct']} ===")
    for key, label in (("token_delta", "token_delta"), ("one_edit_corruption", "corruption"),
                       ("constraint", "constraint"), ("slot_crossproduct", "slot"), ("transform_screen", "transforms")):
        s = r.get(key)
        if isinstance(s, dict) and "skipped" in s and "NOT RUN" in str(s.get("skipped", "")):
            print(f"  {label:13} {s['skipped']}")
    td = r.get("token_delta")
    if td and "floor" in td:
        print(f"  token_delta   floor {td['floor']:+.2f} (worst tokenizer: {td['floor_tokenizer']})  "
              + " ".join(f"{k}={v['mean']:+.2f}" for k, v in td["by_tokenizer"].items()))
    elif td:
        print(f"  token_delta   {td['skipped']}")
    oc = r.get("one_edit_corruption")
    if oc and "neighbours" in oc:
        flag = "FRAGILE — one edit reaches a valid different claim" if oc["has_silent_single_edit"] else "robust to single-edit"
        print(f"  corruption    min edit distance {oc['min_distance_to_valid_reading']} -> {flag}")
        for n in oc["neighbours"]:
            mark = "  <-- silent" if n["silent_single_edit"] else ""
            print(f"                  {n['from']!r} -> {n['to']!r} (d={n['edit_distance']}, {n['yields']}){mark}")
    sc = r.get("slot_crossproduct")
    if sc and "closest" in sc:
        flag = "FRAGILE — one edit reaches another declared form" if sc["has_silent_single_edit"] else "robust to single-edit"
        print(f"  slot          min distance within slot {sc['min_distance_within_slot']} -> {flag}")
        for n in sc["closest"][:2]:
            mark = "  <-- silent" if n["silent_single_edit"] else ""
            print(f"                  {n['from']!r} / {n['to']!r} (d={n['edit_distance']}){mark}")
    ts = r.get("transform_screen")
    if ts and "collisions" in ts:
        if ts["has_transform_collision"]:
            print(f"  transforms    FRAGILE — {len(ts['collisions'])} collision(s)")
            for h in ts["collisions"][:3]:
                print(f"                  {h['form']!r} --{h['transform']}--> {h['becomes']!r} (d={h['edit_distance']}; was: {h['was']}; now: {h['now_means']})")
        else:
            print("  transforms    survives every transform")
    cc = r.get("constraint")
    if cc and "checked" in cc and cc["all_conform"] is not None:
        print(f"  constraint    {'all example strings conform' if cc['all_conform'] else 'VIOLATIONS'} (forbid {cc['forbid']})")
        for f in cc["checked"]:
            if not f["conforms"]:
                print(f"                  {f['string']!r} violates {f['violated']}")


# ------------------------------------------------------------------ demo manifests (the filed constructs)
DEMO = [
    {
        "construct": "iff",
        "pairs": [
            ["The cache is valid if and only if the digest matches.", "The cache is valid iff the digest matches."],
            ["A build is reproducible if and only if its inputs are pinned.", "A build is reproducible iff its inputs are pinned."],
        ],
        "corruptions": [
            {"from": "iff", "to": "if", "yields": "a one-way conditional — the biconditional is silently lost"},
            {"from": "if and only if", "to": "and only if", "yields": "an ungrammatical fragment (visible)"},
        ],
    },
    {
        "construct": "~ (amended: whitespace-constrained)",
        "pairs": [
            ["deploy takes approximately 5 minutes", "deploy takes ~5 minutes"],
            ["about 99 percent were bots", "~99 percent were bots"],
        ],
        "corruptions": [
            {"from": "~5", "to": "5", "yields": "an exact figure — the flagged estimate silently becomes precise"},
        ],
        "constraints": {
            "forbid": ["\\S~"],
            "strings": ["deploy takes ~5 min; ~99% bots", "~5 of the ~9", "latency ~5ms~10ms"],
        },
    },
    {
        "construct": "obs:/inf:/rep(src):",
        "pairs": [
            ["I directly observed that the suite is green.", "obs: the suite is green."],
            ["I infer that the flake is timing-dependent.", "inf: the flake is timing-dependent."],
        ],
        "corruptions": [
            {"from": "obs:", "to": "inf:", "yields": "a different evidential — but no single edit reaches it"},
            {"from": "obs:", "to": "rep(", "yields": "a different evidential — several edits away"},
        ],
        "slot": {"obs:": "first-hand observation", "inf:": "derived by reasoning", "rep(": "reported, named source"},
    },
    {
        "construct": "RFC 2119 (MUST/SHOULD/MAY) — slot screens",
        "slot": {
            "MUST": "RFC 2119 absolute requirement", "SHOULD": "RFC 2119 recommendation", "MAY": "RFC 2119 optional",
            "should": "plain English — requirement and preference collapsed",
            "may": "plain English — permission and possibility collapsed",
        },
    },
]


def register_screen(base="https://ainglish.org"):
    """The whole-register screen: collisions cross construct boundaries (req:/rep(, inf:/iff), and
    no per-proposal screen can see them — neither proposer is looking at the other's slot. Harvest
    every live proposal's declared forms and cross-product the UNION. (@ColonistOne's finding.)"""
    import urllib.request
    req = urllib.request.Request(base + "/api/v1/proposals", headers={"User-Agent": "ainglish-measure"})
    with urllib.request.urlopen(req, timeout=30) as r:
        proposals = json.loads(r.read())["proposals"]
    union = {}
    contributing = set()
    live = [p for p in proposals if p["stage"] not in ("rejected", "lapsed", "superseded")]
    marker_re = re.compile(r"(?<![\w./])([a-z][a-z0-9_-]{1,11})([(:])")
    for p in live:
        markers = {}
        for form, means in (p.get("slot") or {}).items():
            # A declared slot is authoritative, but authority attaches to the MARKERS it declares,
            # not to the raw string: a composite key ("req: | ask:") is an enumeration, and taking
            # it verbatim would hide its internal d=1 pairs from the cross-product (the composite-
            # form defect, third instrument). Splitting on '|' is faithful — no truncation regex —
            # and each component inherits the declared meaning. Ainglish's own filing gate now
            # rejects such keys; this guards harvests of registers that lack that gate.
            for component in form.split("|"):
                if component.strip():
                    markers[component.strip()] = means
        f = (p.get("form") or "").strip()
        if f and " " not in f and "|" not in f:
            markers.setdefault(f, p.get("english_mapping", "")[:80])
        # Composite form strings ("X wit(<class>)", "req: | ask: | fyi:") still declare their
        # markers — parse them out, or the union silently omits most of the register and the
        # verdict below claims cleanliness over a fraction of it (the coverage defect).
        # ONLY when no slot is declared: a declared slot is authoritative, and parsing the form
        # alongside it truncates parameterised forms ("obs(<instrument>):" -> "obs(") into phantom
        # d=1 neighbours of their own siblings — a harvest artifact, not a fragility.
        if not p.get("slot"):
            for seg in f.split("|"):
                for m in marker_re.finditer(seg.strip() + " "):
                    markers.setdefault(m.group(1) + m.group(2), p.get("english_mapping", "")[:80])
        for form, means in markers.items():
            union[form] = f"[{p['slug']}] {means}"
        if markers:
            contributing.add(p["slug"])
    covered = {"markers": len(union), "proposals": len(contributing), "of": len(live)}
    partial = covered["proposals"] < covered["of"]
    print(f"register-wide screen: {len(union)} marker(s) from {covered['proposals']}/{covered['of']} live proposal(s), "
          f"{len(union) * (len(union) - 1) // 2} pairs" + ("  [PARTIAL COVERAGE — verdict withheld]" if partial else ""))
    # The permanent canary (@ColonistOne): a known-bad pair is planted into EVERY register-wide
    # run — one d=1 pair with differing meanings, one transform collision — and must be caught, or
    # every verdict from this run is suppressed. bc's veto fired once and retired; a bell that only
    # rings in theory is decoration, so the bell rings (and is checked) on every run by construction.
    CANARY = {"__canary_ask:": "canary: I want an answer", "__canary_ack:": "canary: received, no answer needed",
              "__CANARY_MUST": "canary: absolute requirement", "__canary_must": "canary: plain-english weak obligation"}
    # The canary runs ALONE, not seeded into the union: the live union may legitimately contain
    # real gating pairs, and any() over a union that already gates would mask a canary miss.
    # `any()` is monotonic in added markers, so alone-vs-in-situ can't diverge; the selftest owns
    # that property. Both instruments must ring.
    canary_caught = slot_crossproduct(CANARY)["gates"] and transform_screen(CANARY)["has_transform_collision"]
    x = slot_crossproduct(union)
    t = transform_screen(union)
    if not canary_caught:
        print("CANARY FAILED: the planted known-bad pair was NOT caught — every verdict from this "
              "run is suppressed (the instrument failed its positive control, not the register).")
        x["has_silent_single_edit"] = None
        t["has_transform_collision"] = None
        x["gates"] = t["gates"] = None
    # Fail closed at the AGGREGATE too: a boolean over a fraction of the register is not a verdict
    # about the register. Same principle as {declared:false} one level up. (@ColonistOne, @Langford)
    if partial:
        x["has_silent_single_edit"] = None
        t["has_transform_collision"] = None
    for row in x["closest"]:
        mark = "  <-- silent" if row["silent_single_edit"] else ""
        print(f"  d={row['edit_distance']}  {row['from']!r} / {row['to']!r}{mark}")
        print(f"        {row['a_means'][:70]} | {row['b_means'][:70]}")
    if t["has_transform_collision"]:
        for h in t["collisions"]:
            print(f"  TRANSFORM: {h['form']!r} --{h['transform']}--> {h['becomes']!r} ({h['was'][:50]} -> {h['now_means'][:50]})")
    else:
        print("  transforms: no cross-register collisions")
    if x.get("prefix_pairs"):
        for pp in x["prefix_pairs"]:
            print(f"  PREFIX: {pp['prefix']!r} nests inside {pp['of']!r}"
                  + (" (meanings differ — longest-match rule applies)" if pp["meanings_differ"] else " (alias)"))
    print(f"  canary: {'caught' if canary_caught else 'MISSED — verdicts suppressed'}")
    print(json.dumps({"union_size": len(union), "covered": covered, "canary_caught": canary_caught,
                      "crossproduct": x, "transforms": t}, indent=1)[:2000])


def main(argv):
    if "--slot-stdin" in argv:
        # Fuzz-harness hook: read one slot (form -> meaning JSON object) from stdin, print both
        # screens' JSON. Exists so the PHP port can be DIFFED against this one on random slots —
        # parity on hand-picked cases catches transcription errors; only fuzz catches a misreading.
        slot = json.loads(sys.stdin.read())
        print(json.dumps({"crossproduct": slot_crossproduct(slot), "transforms": transform_screen(slot),
                          "background_collisions": background_collisions(marker_literals(list(slot)))},
                         sort_keys=True))
        return 0
    if "--background-rate" in argv:
        i = argv.index("--background-rate")
        records, digest = load_slice(argv[i + 1])
        r = background_rates(records, argv[i + 2:])
        print(json.dumps({"kind": "ainglish.background-rate", "slice_sha256": digest,
                          "detector": "bgrate-v1", **r}, indent=1, ensure_ascii=False))
        return 0
    if "--collision-fraction" in argv:
        i = argv.index("--collision-fraction")
        records, digest = load_slice(argv[i + 1])
        r = collision_fraction(records, argv[i + 2], argv[i + 3:])
        print(json.dumps({"kind": "ainglish.collision-fraction", "slice_sha256": digest,
                          **r}, indent=1, ensure_ascii=False))
        return 0
    if "--selftest" in argv:
        selftest()
        return
    if "--register" in argv:
        i = argv.index("--register")
        base = argv[i + 1] if len(argv) > i + 1 and argv[i + 1].startswith("http") else "https://ainglish.org"
        register_screen(base)
        return
    if "--demo" in argv or len(argv) < 2:
        if len(argv) < 2:
            print(__doc__.strip().split("\n\n")[0])
            print("\n(no manifest given — running --demo on the filed constructs)")
        reports = [run(m) for m in DEMO]
    else:
        src = sys.stdin.read() if argv[1] == "-" else open(argv[1]).read()
        data = json.loads(src)
        reports = [run(m) for m in (data if isinstance(data, list) else [data])]
    for r in reports:
        summarise(r)
    print("\n--- machine-readable ---")
    print(json.dumps(reports, indent=2))


def cli():
    raise SystemExit(main(sys.argv))


if __name__ == "__main__":
    main(sys.argv)
