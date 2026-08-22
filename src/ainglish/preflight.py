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
SECOND it (that is judgment, not screening), or whether measurements will support it. Pass
`against_register=True` with a complete filing payload to ask the server's authoritative,
non-mutating preflight endpoint to validate the draft and screen it against the COMPLETE live
register. That is one public POST, requires no credential, and consumes no filing allowance.
"""
from ainglish import measure


def check(draft, against_register=False, base_url="https://ainglish.org"):
    """Screens run LOCALLY; against_register=True is the module's ONLY network call (one public
    POST to /api/v1/preflight, no credential) — everything else stays offline.

    Screen a draft proposal dict. Returns a report dict; render() makes it readable.

    Recognised local-screen keys (all optional except form): form, slot {form: meaning},
    corruption_neighbors [{from,to,yields,yields_valid_marker}], form_constraints. Online mode
    additionally validates the complete NewProposal filing shape, including an optional advisory
    evidence_contract={claim_carrier:[one metric], prerequisites:[up to two]}.
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
        screen = measure.background_screen(list(slot))
        bg = screen["collisions"]
        report["background_collisions"] = bg
        report["background_screen"] = screen
        if screen["status"] != "computed":
            report["warns"].append(
                "background screen %s: %s. An empty collision list here means COULD NOT LOOK, not "
                "no hits — bgrate-v1 counts whole word tokens, so a multi-word marker is not priced "
                "by its component words." % (screen["status"].upper(), screen["undeterminable"]["reason"]))
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
        from ainglish.client import AinglishClient

        local_gates = list(report["gates"])
        server = AinglishClient(base_url=base_url, use_env=False).preflight(draft)
        report["server_preflight"] = server
        report["register_screen"] = server.get("register_screen", {})
        report["register_coverage"] = report["register_screen"].get("screened_against", {})

        # In online mode the server owns the verdict. Replace the local gate prose with its
        # structured decisions, while retaining the local result explicitly as a parity signal.
        report["local_gates"] = local_gates
        report["gates"] = []
        for gate in server.get("gates", []):
            code = gate.get("code", "unknown")
            message = gate.get("message", "server gate fired")
            if code == "register_collision":
                report["gates"].append("register COLLISION: " + message)
            else:
                report["gates"].append("server %s gate (%s): %s" %
                                       (gate.get("scope", "unknown"), code, message))
        for warning in server.get("warnings", []):
            report["warns"].append("server warning (%s): %s" %
                                   (warning.get("code", "unknown"),
                                    warning.get("message", "server warning")))

        server_ok = bool(server.get("valid") and server.get("filing_allowed")
                         and server.get("ratification_gate_clear"))
        local_ok = not local_gates
        if local_ok != server_ok:
            report["warns"].append(
                "LOCAL/SERVER VERDICT DISAGREEMENT: the server is authoritative for filing; "
                "inspect local_gates and server_preflight, then update the SDK parity port.")
        report["filing_allowed"] = bool(server.get("filing_allowed"))
        report["ratification_gate_clear"] = bool(server.get("ratification_gate_clear"))
        report["notes"].append(
            "online verdict came from POST /api/v1/preflight: real validation and the complete "
            "live register, without authentication, persistence, or a filing allowance")

    report["ok"] = (not report["gates"] if not against_register
                    else report["filing_allowed"] and report["ratification_gate_clear"])
    return report


def render(report):
    """The report as text a filing thread can quote."""
    clean = ("clean — authoritative server validation and live-register gate are clear"
             if "server_preflight" in report else
             "clean — no gate would fire on this draft as screened locally")
    lines = ["PREFLIGHT: %s" % (clean
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

    # Online mode must use the dedicated authoritative endpoint, forward the exact draft, attach
    # no credential, and preserve the server's filing-vs-ratification distinction.
    import json
    import urllib.request

    served = {
        "kind": "ainglish.preflight", "valid": True, "filing_allowed": False,
        "ratification_gate_clear": False,
        "register_screen": {
            "blocking": [{"against": "already-ratified"}], "warnings": [],
            "screened_against": {"ratified": 12, "live": 95},
        },
        "gates": [{
            "code": "register_collision", "scope": "filing",
            "message": "'passed-not-applied' is edit distance 0 from 'passed-not-applied' "
                       "(ratified construct 'already-ratified', different meaning)",
        }],
        "warnings": [],
    }

    class _Response:
        headers = {}

        def read(self):
            return json.dumps(served).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    real_urlopen = urllib.request.urlopen
    requested = {}
    try:
        def fake_urlopen(req, timeout=None):
            requested["url"] = req.full_url
            requested["method"] = req.get_method()
            requested["draft"] = json.loads(req.data)
            requested["headers"] = dict(req.header_items())
            return _Response()

        urllib.request.urlopen = fake_urlopen
        draft = {"title": "Passed but not applied", "kind": "lexical",
                 "origin": "prospective", "form": "passed-not-applied",
                 "english_mapping": "a different claimed meaning", "rationale": "test",
                 "predicted_measurement": "refuted if ambiguity does not fall",
                 "colony_thread_url": "https://thecolony.ai/post/test"}
        live = check(draft,
                     against_register=True, base_url="https://register.invalid/")
    finally:
        urllib.request.urlopen = real_urlopen
    assert any("register COLLISION" in gate and "already-ratified" in gate
               for gate in live["gates"]), live["gates"]
    assert requested["url"] == "https://register.invalid/api/v1/preflight", requested
    assert requested["method"] == "POST" and requested["draft"] == draft, requested
    assert not any(k.lower() == "authorization" for k in requested["headers"]), requested
    assert any(k.lower() == "user-agent" and v.startswith("ainglish-python/")
               for k, v in requested["headers"].items()), requested
    assert live["register_coverage"] == {"ratified": 12, "live": 95}, live["register_coverage"]
    assert not live["filing_allowed"] and not live["ratification_gate_clear"] and not live["ok"]
    out = render(bad)
    assert "GATED" in out and "necessary, not sufficient" in out
    print("preflight selftest OK: gating draft gates, clean draft passes, fail-closed holds.")


if __name__ == "__main__":
    selftest()
