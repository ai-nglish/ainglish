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


_REGISTER_LIMIT_MAX = 200  # openapi.json's maximum; at the cap completeness is unknowable
_TERMINAL_STAGES = frozenset({"rejected", "lapsed", "superseded", "vote_failed"})


def _markers_of(proposal):
    """The register filing door's effective marker surface, ported from RegisterScreen::markersOf.

    A declared slot is authoritative; otherwise a short ``form1 | form2`` enumeration can be
    derived when ``english_mapping`` carries the same number of ``meaning1 · meaning2`` entries;
    otherwise a bare, whitespace-free form is one marker. Protocol filings deliberately have no
    token surface. Keeping this small port beside the live check is preferable to silently treating
    ``slot is None`` as ``proposal declares nothing`` — the false-clean direction this guards.
    """
    if proposal.get("kind") == "protocol":
        return []
    slot = proposal.get("slot")
    if isinstance(slot, dict) and slot:
        return [str(form) for form in slot]

    form = str(proposal.get("form") or "").strip()
    mapping = str(proposal.get("english_mapping") or "")
    if "|" in form:
        forms = [part.strip() for part in form.split("|") if part.strip()]
        meanings = [part.strip() for part in mapping.split("·") if part.strip()]
        if (2 <= len(forms) <= 16 and len(forms) == len(meanings)
                and all(len(marker) <= 120 and len(marker.split()) <= 2 for marker in forms)):
            return forms
    if form and "|" not in form and not any(ch.isspace() for ch in form):
        return [form]
    return []


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
            urllib.request.Request(base_url.rstrip("/") +
                                   f"/api/v1/proposals?limit={_REGISTER_LIMIT_MAX}",
                                   headers={"User-Agent": "ainglish-preflight"}), timeout=30).read())["proposals"]
        if len(rows) >= _REGISTER_LIMIT_MAX:
            report["gates"].append(
                "register screen INCOMPLETE: the proposals endpoint returned its 200-row maximum, "
                "so later live markers may be absent. Narrowing a collision screen silently is "
                "not a clean result; the API needs pagination or a dedicated marker endpoint.")
        union = []
        eligible = contributing = 0
        for p in rows:
            if p.get("stage") in _TERMINAL_STAGES or p.get("kind") == "protocol":
                continue
            eligible += 1
            markers = _markers_of(p)
            contributing += bool(markers)
            union.extend((marker, p["slug"]) for marker in markers)
        mine = _markers_of(draft)
        report["register_coverage"] = {
            "fetched": len(rows), "eligible_word_proposals": eligible,
            "contributing_proposals": contributing, "markers": len(union),
            "capped": len(rows) >= _REGISTER_LIMIT_MAX,
        }
        if not mine:
            report["warns"].append(
                "register collision screen NOT RUN for this draft: no markers were declared or "
                "derivable from its slot/form/mapping")
        near = []
        for m in mine:
            for other, owner in union:
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

    # against_register must see the server-derived surface, not only explicit slot keys. The live
    # register contains this exact shape: a single-token form with slot=null. Before this guard an
    # exact re-filing returned `ok: True`, despite the function's own d=0 claim check below.
    import json
    import urllib.request

    served = {"proposals": [
        {"slug": "already-live", "kind": "lexical", "stage": "seconded",
         "form": "passed-not-applied", "english_mapping": "a pass that was not applied", "slot": None},
        {"slug": "closed", "kind": "lexical", "stage": "vote_failed",
         "form": "retired-marker", "english_mapping": "closed ballot", "slot": None},
        {"slug": "machinery", "kind": "protocol", "stage": "seconded",
         "form": "passed-not-applied", "english_mapping": "not a word surface", "slot": None},
    ]}

    class _Response:
        def read(self):
            return json.dumps(served).encode()

    real_urlopen = urllib.request.urlopen
    requested = {}
    try:
        def fake_urlopen(req, timeout=None):
            requested["url"] = req.full_url
            return _Response()

        urllib.request.urlopen = fake_urlopen
        live = check({"kind": "lexical", "form": "passed-not-applied",
                      "english_mapping": "a different claimed meaning"},
                     against_register=True, base_url="https://register.invalid/")
    finally:
        urllib.request.urlopen = real_urlopen
    assert any("register CLAIM" in gate and "already-live" in gate for gate in live["gates"]), \
        "a bare live form with no explicit slot must still own its marker"
    assert "limit=200" in requested["url"], "the live screen must request the documented maximum"
    assert live["register_coverage"] == {
        "fetched": 3, "eligible_word_proposals": 1,
        "contributing_proposals": 1, "markers": 1, "capped": False,
    }, live["register_coverage"]
    out = render(bad)
    assert "GATED" in out and "necessary, not sufficient" in out
    print("preflight selftest OK: gating draft gates, clean draft passes, fail-closed holds.")


if __name__ == "__main__":
    selftest()
