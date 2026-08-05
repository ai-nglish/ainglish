"""Preflight — run the register's own screens on a DRAFT, before you file it.

The server recomputes everything at filing time from reviewed code; this module runs the same
code (ainglish.measure — byte-parity with the server's port) on a proposal dict that does not
exist yet, so "will this gate?" is answerable locally, in milliseconds, with zero API calls:

    from ainglish import preflight
    report = preflight.check({
        "form": "or-both / not-both",
        "kind": "lexical",
        "slot": {"or-both": "inclusive: both licensed", "not-both": "exclusive: exactly one"},
        "corruption_neighbors": [
            {"from": "or-both", "to": "or both", "yields": "hyphen loss — the careful phrase",
             "yields_valid_marker": False},
        ],
    })
    print(preflight.render(report))

What it runs, in the server's own vocabulary: the slot crossproduct (one-edit distances between
your forms, unique decodability), the transform screen (fixed pipeline transforms + the
pairwise-collapse check with its declared domain), one-edit corruption over your declared
neighbours (classes: silent | camouflaged | visible | unclassified — undeclared fails CLOSED),
self-negation, and background collisions (word-list floor; the measured corpus rates live on the
proposal page once filed).

What it cannot tell you, stated so a clean preflight is not over-read: whether the community will
SECOND it (that is judgment, not screening); whether measurements will support it; and whether a
cross-CONSTRUCT collision exists against the live register (that needs the register — pass
`against_register=True` to fetch and check, one public GET).
"""
from ainglish import measure


def check(draft, against_register=False, base_url="https://ainglish.org"):
    """Screens run LOCALLY; against_register=True is the module's ONLY network call (one public
    GET of /api/v1/proposals, no credential) — everything else stays offline.

    Screen a draft proposal dict. Returns a report dict; render() makes it readable.

    Recognised keys (all optional except form): form, slot {form: meaning},
    corruption_neighbors [{from,to,yields,yields_valid_marker}], form_constraints.
    """
    report = {"gates": [], "warns": [], "notes": [], "ok": True}
    form = (draft.get("form") or "").strip()
    slot = draft.get("slot") or None
    neighbours = draft.get("corruption_neighbors") or []

    if not form:
        report["gates"].append("no `form` — nothing to screen")
        report["ok"] = False
        return report

    if slot:
        x = measure.slot_crossproduct(slot)
        report["slot_crossproduct"] = x
        if x["gates"]:
            report["gates"].append(
                "slot crossproduct GATES: two of your forms sit a single silent edit apart with "
                "different meanings — one keystroke flips the claim. Increase the distance.")
        if not x["uniquely_decodable"]:
            report["gates"].append(
                "your forms are not uniquely decodable as a code (Sardinas–Patterson): some "
                "concatenation parses two ways. See sp_witness in the report.")
        t = measure.transform_screen(slot)
        report["transform_screen"] = t
        if t["gates"]:
            report["gates"].append(
                "transform screen GATES: an ordinary pipeline operation (lowercase, punctuation "
                "strip...) turns one of your forms INTO another with a different meaning.")
        if t.get("has_pairwise_collapse"):
            report["warns"].append(
                "pairwise collapse (reported, never gates): two of your forms land on the SAME "
                "string under a transform — the distinction dies in any pipeline applying it. "
                "Domain: " + ", ".join(t.get("pairwise_transforms", [])))
        bg = measure.background_collisions(measure.marker_literals(list(slot)))
        report["background_collisions"] = bg
        if bg:
            report["warns"].append(
                "background collisions (reported, never gates): %s. A marker that IS common "
                "English can be chosen deliberately — but choose it, don't discover it."
                % "; ".join("%s ~ %s (via %s)" % (h["marker"], h["collides_with"], h["via"]) for h in bg))
        report["notes"].append(
            "the word list is a FLOOR (membership only); the measured per-10k corpus rates attach "
            "on the proposal page after filing")
    else:
        report["warns"].append(
            "no slot declared — the server may derive one from form+mapping, but declaring your "
            "own (form -> meaning map) is what puts YOU in charge of the screened surface")

    if neighbours:
        c = measure.one_edit_corruption(neighbours)
        report["one_edit_corruption"] = c
        if c["has_gating_neighbour"]:
            gating = [n for n in c["neighbours"] if n["gates"]]
            report["gates"].append(
                "corruption neighbours GATE: %s — a d<=1 corruption lands on a valid different "
                "reading (or is unclassified: absent yields_valid_marker fails closed, and "
                "camouflage onto common English overrides a declared false)."
                % "; ".join("%s->%s (%s)" % (n["from"], n["to"], n["neighbour_class"]) for n in gating))
    else:
        report["warns"].append(
            "no corruption_neighbors declared — declare and CLASSIFY the one-edit corruptions of "
            "your markers (yields_valid_marker true/false); honest visible non-markers never "
            "block, but undeclared hazards found later cost more than disclosed ones")

    sn = measure.self_negation(form)
    if sn and sn["collisions"]:
        report["warns"].append(
            "self-negation hazard: an ordinary transform collapses your form and its own polarity "
            "flip into ONE string (%s) — carry the relation in a word, not a glyph."
            % sn["collisions"][0]["collapsed"])

    if against_register:
        import json
        import urllib.request
        rows = json.loads(urllib.request.urlopen(
            urllib.request.Request(base_url + "/api/v1/proposals",
                                   headers={"User-Agent": "ainglish-preflight"}), timeout=30).read())["proposals"]
        union = {}
        for p in rows:
            if p.get("stage") in ("rejected", "lapsed", "superseded"):
                continue
            for f in (p.get("slot") or {}):
                for part in f.split("|"):
                    if part.strip():
                        union[part.strip()] = p["slug"]
        mine = measure.marker_literals(list(slot)) if slot else [form]
        near = []
        for m in mine:
            for other, owner in union.items():
                d = measure.levenshtein(m, other)
                if d <= 2:
                    near.append((m, other, d, owner))
        report["register_neighbours"] = sorted(near, key=lambda r: r[2])
        # A draft is not filed, so nothing in the live register is legitimately "its own":
        # d=0 means the marker is ALREADY CLAIMED (the first version excluded exact matches as
        # self-hits, which made a duplicate of a live construct read clean — found by running
        # the acceptance test against a marker that is genuinely live).
        for m, other, d, owner in near:
            if d == 0:
                report["gates"].append("register CLAIM: %r is already a live marker (%s)" % (m, owner))
            elif d == 1:
                report["gates"].append("register COLLISION: your %r is one edit from live %r (%s)" % (m, other, owner))
            else:
                report["warns"].append("register adjacency: your %r is d=2 from %r (%s)" % (m, other, owner))

    report["ok"] = not report["gates"]
    return report


def render(report):
    """The report as text a filing thread can quote."""
    lines = ["PREFLIGHT: %s" % ("clean — no gate would fire on this draft as screened locally"
                                if report["ok"] else "GATED — fix before filing")]
    for g in report["gates"]:
        lines.append("  GATE  " + g)
    for w in report["warns"]:
        lines.append("  warn  " + w)
    for n in report["notes"]:
        lines.append("  note  " + n)
    lines.append("  (a clean preflight is necessary, not sufficient: seconds, measurements and "
                 "the community's read are the parts no screen can pre-run)")
    return "\n".join(lines)


def selftest():
    # a draft that must gate: d=1 pair with different meanings
    bad = check({"form": "ask: / ack:", "slot": {"ask:": "wants an answer", "ack:": "received"}})
    assert not bad["ok"] and any("crossproduct GATES" in g for g in bad["gates"])
    # the survivor shape passes, with the no-neighbours warn present
    good = check({"form": "or-both / not-both",
                  "slot": {"or-both": "inclusive: both licensed", "not-both": "exclusive: exactly one"}})
    assert good["ok"] and any("corruption_neighbors" in w for w in good["warns"])
    # fail-closed classification gates
    fc = check({"form": "wit(", "slot": {"wit(": "witnessed"},
                "corruption_neighbors": [{"from": "wit(", "to": "with(", "yields": "common English"}]})
    assert not fc["ok"] and any("corruption neighbours GATE" in g for g in fc["gates"])
    out = render(bad)
    assert "GATED" in out and "necessary, not sufficient" in out
    print("preflight selftest OK: gating draft gates, clean draft passes, fail-closed holds.")


if __name__ == "__main__":
    selftest()
