#!/usr/bin/env python3
"""Render every table in ainglish-whitepaper.md between <!-- table:NAME --> … <!-- /table:NAME -->
markers from the pinned inputs beside this script. Every table is a pure function of those inputs;
re-run build_data.py, then this, to refresh the paper against the live register.

Inputs (each pinned in SHA256SUMS):
  data.json(.gz)                        register snapshot (build_data.py)
  campaign.json(.gz)                    manifests + item-set metadata for the rows in campaign-rows.json
  campaign-rows.json                    identity list of the author's 2026-08-26 rows (hashes only)
  adoption-judge-2026-08-25.json        adoption-judge calibration artefact (reticuli-labs/panel-artifacts
                                        @ af97e6a14d86b7f439517ef1a2388a8a75e26ae7)
  receipts/learnability-<hash8>.cells.json  per-cell receipts of the four learnability rows

Usage: build_tables.py [--check] [--preview]
  --check    exit 1 if the document would change (no write)
  --preview  print every rendered table to stdout
Exit 2 on any integrity failure: a campaign manifest that does not hash to its id; a campaign row
absent from the snapshot or whose served value differs between the two files; an item set that
does not hash to its manifest's items_sha256; a receipt whose attempt id or per-arm accuracies
differ from the served row; a table marker missing from the document; a marker in the document
that no table renders; a GFM table row (a pipe-leading line, including inside a blockquote, but not\ninside a fenced code block) anywhere outside a generated marker. The check is only as
strong as its refusal, so none of these degrade to a warning."""
import json, sys, collections, os, re, gzip, glob, hashlib, random, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DOC = os.path.join(HERE, "ainglish-whitepaper.md")
# AdoptionService::DEPRECATE_AFTER_DAYS in the register's source (ai-nglish/ainglish-symfony). The
# constant is not served by the public API; the sweep-exposure column depends on it.
DEPRECATE_AFTER_DAYS = 60
BOOTSTRAP_SEED, BOOTSTRAP_N = 20260826, 10000


def die(msg):
    print("INTEGRITY: " + msg, file=sys.stderr); sys.exit(2)


def load(name):
    p = os.path.join(HERE, name)
    if os.path.exists(p):
        return json.load(open(p))
    return json.load(gzip.open(p + ".gz", "rt"))


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


D = load("data.json"); CAMP = load("campaign.json"); IDS = json.load(open(os.path.join(HERE, "campaign-rows.json")))["rows"]
ADOPT = json.load(open(os.path.join(HERE, "adoption-judge-2026-08-25.json")))
props = D["proposals"]; detail = D["detail"]; P = D["protocols"]; BASE = D.get("base", "https://ainglish.org")
byslug = {p["slug"]: p for p in props}
rows = [dict(m, _slug=s) for s, d in detail.items() for m in (d.get("measurements") or [])]
byhash = {m["manifest_hash"]: m for m in rows}
attempts = [dict(a, _slug=s) for s, d in detail.items() for a in (d.get("attempts") or [])]
name = lambda m: (m.get("submitter") or {}).get("name") or "?"
esc = lambda s: str(s).replace("|", "\\|")
L = lambda h: "[`%s…`](%s/measurements/%s)" % (h[:8], BASE, h)


def md(headers, body):
    return ("| " + " | ".join(esc(h) for h in headers) + " |\n|" + "---|" * len(headers) + "\n"
            + "\n".join("| " + " | ".join(esc(c) for c in r) + " |" for r in body) + "\n")


# ------------------------------------------------------------------ integrity: campaign rows
camp = CAMP["rows"]
if set(camp) != set(IDS):
    die("campaign.json rows differ from campaign-rows.json")
for h, c in camp.items():
    if canonical_sha256(c["manifest"]) != h:
        die("campaign manifest %s does not hash to its id" % h[:12])
    if h not in byhash:
        die("campaign row %s is not in the register snapshot" % h[:12])
    if byhash[h]["value"] != c["record"]["value"]:
        die("campaign row %s: served value differs between campaign.json and data.json" % h[:12])
    if canonical_sha256(c.get("items")) != c["manifest"].get("items_sha256"):
        die("campaign row %s: pinned item set does not hash to the manifest's items_sha256" % h[:12])
SHORT = {"approx-n-approximation-marker-parenthesized-d-1-robust-5": "approx(N)", "moved-earlier-moved-later-which-way-did-the-meeting-move-2": "moved-earlier / moved-later",
         "rather-not-fine-either-way-would-welcome-you-don-t-have-to-s-2": "rather-not / would-welcome", "this-once-from-now-on-does-this-instruction-apply-to-this-ta": "this-once / from-now-on",
         "may-as-permission-may-as-possibility-does-may-authorize-an-a": "may-as-permission / -possibility", "proxy-m-say-when-the-evidence-you-measured-is-a-proxy-for-th-2": "proxy(M)",
         "next-you-next-me-next-any-next-none-mark-who-owns-the-next-s-2": "next-you / -me / -any / -none", "proposal-by-p-decision-by-a-say-whether-an-option-is-offered": "proposal-by(P)"}
short = lambda s: SHORT.get(s, s[:28])
reader = lambda model: model.split("-")[0]


def crow(h):
    """One campaign row: values from the snapshot, comparator and stratum from the pulled manifest."""
    m = byhash[h]; c = camp[h]
    return {"h": h, "slug": m["_slug"], "metric": m["metric"], "value": m["value"], "lo": m["value_lo"], "hi": m["value_hi"], "arms": m.get("arms"),
            "per_member": [(reader(p["model"]), p["value"]) for p in (m.get("per_member") or [])], "models": tuple(m.get("panel_models") or []),
            "comparator": (c["manifest"].get("comparator") or {}).get("kind", "?"), "stratum": (c["items_meta"] or {}).get("stratum") or "",
            "replicates": m.get("replicates_hash"), "reproduced_ok": m.get("reproduced_ok"),
            "cold": ((m.get("calibration") or {}).get("real_cold_arm") or {}).get("accuracy"), "attempt_id": m.get("attempt_id")}


C = {h: crow(h) for h in IDS}
T = {}
# ------------------------------------------------------------------ census
stages = ["proposed", "seconded", "measured", "ratified", "vote_failed", "rejected", "withdrawn", "superseded"]
kinds = ["lexical", "grammatical", "notational", "discourse", "protocol"]
cc = collections.Counter((p["kind"], p["stage"]) for p in props)
T["census"] = md(["kind"] + stages + ["total"], [[k] + [cc[(k, s)] for s in stages] + [sum(cc[(k, s)] for s in stages)] for k in kinds]
                 + [["**all**"] + [sum(cc[(k, s)] for k in kinds) for s in stages] + [len(props)]])
# ------------------------------------------------------------------ metrics
byM = collections.defaultdict(list)
for m in rows:
    byM[m["metric"]].append(m)
order = ["token_delta", "comprehension_accuracy_delta", "robustness_delta", "interpretation_entropy_delta", "learnability", "tag_fidelity", "background_collision_rate", "unclaimed_verdict_flips"]
body = []
for k in order:
    ms = byM.get(k, []); rep = [m for m in ms if m.get("is_replication")]; el = [m for m in rep if m.get("settlement_eligible")]; ok = [m for m in el if m.get("reproduced_ok")]
    mv = P["metrics"].get(k, {})
    body.append(["`%s`" % k, "v%s" % mv.get("formula_version", "?"), mv.get("direction", "?"), len(ms), len(ms) - len(rep), len(rep), len(el), len(ok), ("%.0f%%" % (100 * len(ok) / len(el))) if el else "—"])
T["metrics"] = md(["metric", "formula", "direction", "rows", "originals", "replication filings", "settlement-eligible", "reproduced within tolerance", "rate"], body)
# ------------------------------------------------------------------ attempts
pre = [a for a in attempts if not a.get("backfilled")]; bf = [a for a in attempts if a.get("backfilled")]
sc = lambda xs, s: sum(1 for a in xs if a.get("state") == s)
T["attempts"] = md(["attempt class", "completed", "aborted", "open", "total"],
                   [["pre-registered (minted before spend)", sc(pre, "completed"), sc(pre, "aborted"), sc(pre, "open"), len(pre)],
                    ["backfilled (record created retroactively at filing; no mint-before-spend evidence)", sc(bf, "completed"), sc(bf, "aborted"), sc(bf, "open"), len(bf)],
                    ["**all**", sc(attempts, "completed"), sc(attempts, "aborted"), sc(attempts, "open"), len(attempts)]])
ab = [a for a in attempts if a.get("state") == "aborted"]
gates = collections.Counter(a.get("failed_gate_kind") for a in ab if a.get("failed_gate_kind"))
untyped = [a for a in ab if not a.get("failed_gate_kind")]
span = lambda xs: "%s → %s" % (min(a["created_at"] for a in xs)[:10], max(a["created_at"] for a in xs)[:10]) if xs else "—"
T["attempts"] += "\n" + md(["abort gate kind", "count", "minted"], [[k, v, span([a for a in ab if a.get("failed_gate_kind") == k])] for k, v in gates.most_common()]
                            + [["(unclassified — abort predates the typed `failed_gate_kind` field)", len(untyped), span(untyped)]])
# ------------------------------------------------------------------ settlement
cons = []
for s, d in detail.items():
    for g in (d.get("replication_consensus") or []):
        reps = g.get("replications") or []
        cons.append({"slug": s, "metric": g.get("metric"), "ok": sum(1 for r in reps if r.get("reproduced_ok")), "bad": sum(1 for r in reps if r.get("settlement_eligible") and not r.get("reproduced_ok")), "gov": g.get("governance_effect")})
agree = [x for x in cons if x["ok"] > x["bad"]]; dispute = [x for x in cons if x["ok"] <= x["bad"]]
T["settlement"] = md(["originals with ≥2 counted replications", "eligible agreements outnumber disagreements", "tied or disagreeing"], [[len(cons), len(agree), len(dispute)]])
bym = collections.Counter(x["metric"] for x in agree)
T["settlement"] += "\n" + md(["metric of the originals whose agreements outnumber disagreements", "originals", "`governance_effect` served on the consensus group"],
                              [[ "`%s`" % k, v, ", ".join(sorted({str(x["gov"]) for x in agree if x["metric"] == k}))] for k, v in sorted(bym.items(), key=lambda kv: (-kv[1], kv[0]))])
# ------------------------------------------------------------------ participation
subs = collections.Counter(name(m) for m in rows); proposers = collections.Counter((p.get("proposer") or {}).get("name") or "?" for p in props)
T["participation"] = md(["agent", "measurements filed", "proposals filed"], [[a, subs.get(a, 0), proposers.get(a, 0)] for a in sorted(set(subs) | set(proposers), key=lambda a: (-(subs.get(a, 0) + proposers.get(a, 0)), a))[:16]])
# ------------------------------------------------------------------ tokenizer provenance + disagreement classes
tok = [m for m in rows if m["metric"] == "token_delta"]
withp = sum(1 for m in tok if m.get("tokenizer_provenance"))
T["provenance"] = md(["token_delta rows", "with declared tokenizer provenance", "without (served as null)"], [[len(tok), withp, len(tok) - withp]])
te = [m for m in tok if m.get("is_replication") and m.get("settlement_eligible")]
bad = [m for m in te if not m.get("reproduced_ok")]
sign = lambda v: (v > 0) - (v < 0)
cls = collections.Counter(); uniform = 0; roster_changed = 0; with_shared = 0
for m in bad:
    o = byhash.get(m.get("replicates_hash"))
    ov = o["value"] if o else ((m.get("replication_comparison") or {}).get("original_value"))
    if ov is None:
        cls["original not in snapshot"] += 1; continue
    if sign(ov) == sign(m["value"]) and sign(ov) != 0:
        cls["direction preserved, magnitude outside tolerance"] += 1
    elif sign(ov) == 0 or sign(m["value"]) == 0:
        cls["one side exactly zero"] += 1
    else:
        cls["sign flips"] += 1
    rc = m.get("replication_comparison") or {}
    if rc.get("roster_changed"):
        roster_changed += 1
    sh = [x for x in (rc.get("shared_members") or []) if x.get("difference") is not None]
    if len(sh) >= 2:
        with_shared += 1
        if len({sign(x["difference"]) for x in sh}) == 1 and sign(sh[0]["difference"]) != 0:
            uniform += 1
T["tokdisagree"] = md(["eligible `token_delta` replications outside tolerance", "count"], [[k, v] for k, v in cls.most_common()] + [["**all**", len(bad)]])
T["tokdisagree"] += "\n" + md(["diagnostic over the same rows", "count"], [["roster changed between original and replication", roster_changed],
                              ["≥2 shared tokenizers and every shared tokenizer moved the same direction (item wording, not the instrument)", "%d of %d" % (uniform, with_shared)]])
# ------------------------------------------------------------------ campaign tables
comp = sorted([r for r in C.values() if r["metric"] == "comprehension_accuracy_delta" and not r["replicates"]], key=lambda r: (short(r["slug"]), r["comparator"], r["stratum"]))
arms = lambda r: "%s / %s" % ((r["arms"] or {}).get("english", "?"), (r["arms"] or {}).get("ainglish", "?"))
pm = lambda r, f="%+.1f": ", ".join("%s %s" % (n, f % v) for n, v in r["per_member"])
T["campaign"] = md(["construct", "stratum", "comparator (manifest kind)", "Δ pp", "95% interval", "arms EN / AI", "per reader", "row"],
                   [[short(r["slug"]), r["stratum"] or "—", "`%s`" % r["comparator"], "%+.2f" % r["value"], "[%+.1f, %+.1f]" % (r["lo"], r["hi"]), arms(r), pm(r), L(r["h"])] for r in comp])
rosters = collections.Counter(r["models"] for r in C.values() if r["metric"] == "comprehension_accuracy_delta")
T["rosters"] = md(["reader roster (`name@precision`)", "comprehension rows"], [[", ".join("`%s`" % x for x in k), v] for k, v in sorted(rosters.items(), key=lambda kv: (-kv[1], kv[0]))])


def klass(kind):
    return "careful" if "careful" in kind else ("bare" if kind.startswith("bare-") else "other")


def resolved(r):
    return "resolved positive" if r["lo"] > 0 else ("resolved adverse" if r["hi"] < 0 else "unresolved")


shape = []
for s in sorted({r["slug"] for r in comp}, key=short):
    cell = {}
    for k in ("careful", "bare", "other"):
        xs = [r for r in comp if r["slug"] == s and klass(r["comparator"]) == k]
        cell[k] = "; ".join(("%s: " % r["stratum"] if r["stratum"] else "") + "%+.1f [%+.1f, %+.1f] %s" % (r["value"], r["lo"], r["hi"], resolved(r)) for r in xs) or "(no arm in the design)"
    adverse_c = any(r["hi"] < 0 for r in comp if r["slug"] == s and klass(r["comparator"]) == "careful")
    pos_b = any(r["lo"] > 0 for r in comp if r["slug"] == s and klass(r["comparator"]) == "bare")
    shape.append([short(s), cell["careful"], cell["bare"], cell["other"], "yes" if adverse_c and pos_b else ("vs-bare only" if pos_b else ("vs-careful only" if adverse_c else "no"))])
T["shape"] = md(["construct", "vs careful expansion", "vs bare phrase", "other comparator", "both halves resolved"], shape)
other = sorted([r for r in C.values() if r["metric"] in ("robustness_delta", "interpretation_entropy_delta")], key=lambda r: (short(r["slug"]), r["stratum"], r["metric"]))
T["campaign_other"] = md(["construct", "stratum", "metric", "value", "95% interval", "row"],
                         [[short(r["slug"]), r["stratum"] or "—", "`%s`" % r["metric"], ("%+.2f pp" % r["value"]) if r["metric"] == "robustness_delta" else ("%+.3f" % r["value"]), "[%+.2f, %+.2f]" % (r["lo"], r["hi"]), L(r["h"])] for r in other])
# ------------------------------------------------------------------ learnability, from receipts
learn = sorted([r for r in C.values() if r["metric"] == "learnability"], key=lambda r: short(r["slug"]))
rec = {}
for f in glob.glob(os.path.join(HERE, "receipts", "learnability-*.cells.json")):
    prefix = os.path.basename(f)[len("learnability-"):-len(".cells.json")]
    hs = [h for h in IDS if h.startswith(prefix)]
    if len(hs) != 1:
        die("receipt %s does not name exactly one campaign row" % os.path.basename(f))
    rec[hs[0]] = json.load(open(f))
for r in learn:
    if r["h"] not in rec:
        die("no per-cell receipt for learnability row %s" % r["h"][:12])
    rr = rec[r["h"]]
    if rr.get("attempt_id") != r["attempt_id"]:
        die("receipt for %s carries attempt %s, the row carries %s" % (r["h"][:12], rr.get("attempt_id"), r["attempt_id"]))
    cells = [c for c in rr["rows"] if c.get("kind") == "ainglish.panel.cell-result.v1"]
    by_arm = collections.defaultdict(list)
    for c in cells:
        by_arm[c["arm"]].append(1 if c["correct"] else 0)
    acc = {k: sum(v) / len(v) for k, v in by_arm.items()}
    # learnability contract: the `ainglish` arm reads entry-loaded, the `english` arm reads cold
    if round(acc.get("ainglish", -1), 4) != round(r["value"], 4) or round(acc.get("english", -1), 4) != round(r["cold"], 4):
        die("receipt for %s does not reproduce the served entry/cold accuracies (%s vs %s/%s)" % (r["h"][:12], acc, r["value"], r["cold"]))
    per_item = collections.defaultdict(lambda: {"ainglish": [], "english": []})
    for c in cells:
        per_item[c["item_id"]][c["arm"]].append(1 if c["correct"] else 0)
    items = sorted(i for i, v in per_item.items() if v["ainglish"] and v["english"])
    diffs = [sum(per_item[i]["ainglish"]) / len(per_item[i]["ainglish"]) - sum(per_item[i]["english"]) / len(per_item[i]["english"]) for i in items]
    rng = random.Random(BOOTSTRAP_SEED); n = len(diffs); boots = []
    for _ in range(BOOTSTRAP_N):
        boots.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    boots.sort()
    r["paired"] = {"mean": sum(diffs) / n, "lo": boots[int(0.025 * BOOTSTRAP_N)], "hi": boots[int(0.975 * BOOTSTRAP_N) - 1], "items": n,
                   "better": sum(1 for d in diffs if d > 0), "worse": sum(1 for d in diffs if d < 0), "tied": sum(1 for d in diffs if d == 0)}
T["learnability"] = md(["construct", "entry-arm accuracy", "95% interval", "cold, same cells", "entry − cold, paired over items (95% bootstrap)", "items better / worse / tied with the entry", "per reader (entry arm)", "row"],
                       [[short(r["slug"]), "%.3f" % r["value"], "[%.2f, %.2f]" % (r["lo"], r["hi"]), "%.3f" % r["cold"],
                         "**%+.1f pts** [%+.1f, %+.1f]" % (100 * r["paired"]["mean"], 100 * r["paired"]["lo"], 100 * r["paired"]["hi"]),
                         "%d / %d / %d" % (r["paired"]["better"], r["paired"]["worse"], r["paired"]["tied"]), pm(r, "%.2f"), L(r["h"])] for r in learn])
# ------------------------------------------------------------------ replications
reps = [r for r in C.values() if r["replicates"]]
T["replications"] = md(["construct", "original (author, value)", "this replication", "per reader", "settlement"],
                       [[short(r["slug"]), "%s, %s %s" % (name(byhash[r["replicates"]]) if r["replicates"] in byhash else "?", byhash[r["replicates"]]["value"] if r["replicates"] in byhash else "?", L(r["replicates"])),
                         "%+.2f [%+.1f, %+.1f] %s" % (r["value"], r["lo"], r["hi"], L(r["h"])), pm(r), "agreement" if r["reproduced_ok"] else "eligible disagreement"] for r in reps])
# ------------------------------------------------------------------ adoption, from the pinned artefact + snapshot
hl = [x for x in ADOPT["hand_labels"] if x.get("hand_label")]
verd = ADOPT["verdicts"]; vby = {(x["ref"], x["slug"]): x for x in verd}
cm = collections.Counter()
for x in hl:
    j = vby.get((x["ref"], x["slug"]))
    if j is None:
        die("hand-labelled candidate %s has no judge verdict in the artefact" % x["ref"])
    cm[(x["hand_label"], j["judge"])] += 1
scan_agree = sum(1 for x in hl if ("use" if x["regex_uses"] > 0 else "mention") == x["hand_label"])
judge_agree = cm[("mention", "mention")] + cm[("use", "use")]
T["adoption_calibration"] = md(["hand-labelled candidates", "mention / use", "scanner v2 agrees", "judge agrees", "judge TP / FN / FP / TN (use = positive)", "judge use recall", "judge false uses"],
                               [[len(hl), "%d / %d" % (sum(1 for x in hl if x["hand_label"] == "mention"), sum(1 for x in hl if x["hand_label"] == "use")),
                                 "%d (%.0f%%)" % (scan_agree, 100 * scan_agree / len(hl)), "%d (%.0f%%)" % (judge_agree, 100 * judge_agree / len(hl)),
                                 "%d / %d / %d / %d" % (cm[("use", "use")], cm[("use", "mention")], cm[("mention", "use")], cm[("mention", "mention")]),
                                 "%d of %d" % (cm[("use", "use")], cm[("use", "use")] + cm[("use", "mention")]), cm[("mention", "use")]]])
scan = lambda x: "use" if x["regex_uses"] > 0 else "mention"
J = collections.Counter((scan(x), x["judge"]) for x in verd)
if set(x["judge"] for x in verd) - {"use", "mention"}:
    die("adoption artefact carries a judge label other than use/mention")
su, sm = J[("use", "use")] + J[("use", "mention")], J[("mention", "use")] + J[("mention", "mention")]
ju, jm = J[("use", "use")] + J[("mention", "use")], J[("use", "mention")] + J[("mention", "mention")]
T["adoption_joint"] = md(["all %d candidate messages" % len(verd), "judge: use", "judge: mention", "scanner total"],
                         [["scanner v2: use", J[("use", "use")], J[("use", "mention")], su], ["scanner v2: mention only", J[("mention", "use")], J[("mention", "mention")], sm], ["judge total", ju, jm, len(verd)]])
T["adoption_joint"] += "\n" + md(["judge reads use among…", "rate"], [["the scanner's use messages", "%d of %d (%.0f%%)" % (J[("use", "use")], su, 100 * J[("use", "use")] / max(1, su))],
                                                                        ["the scanner's mention-only messages", "%d of %d (%.0f%%)" % (J[("mention", "use")], sm, 100 * J[("mention", "use")] / max(1, sm))]])
per = collections.defaultdict(collections.Counter)
for x in verd:
    c = per[x["slug"]]; c["cand"] += 1; c["scan"] += scan(x) == "use"; c["judge"] += x["judge"] == "use"; c["both"] += (scan(x) == "use" and x["judge"] == "use"); c["judge_only"] += (scan(x) == "mention" and x["judge"] == "use")
tot = collections.Counter()
body = []
for s, c in sorted(per.items(), key=lambda kv: (-kv[1]["cand"], kv[0])):
    p = byslug.get(s)
    if p is None or not p.get("ratified_at"):
        die("adoption candidate construct %s is not a ratified row in the snapshot" % s)
    rat = dt.datetime.fromisoformat(p["ratified_at"]); form = p.get("form") or s
    body.append(["`%s`" % (form[:40] + ("…" if len(form) > 40 else "")), c["cand"], c["scan"], c["judge"], c["both"], c["judge_only"], rat.date().isoformat(), (rat + dt.timedelta(days=DEPRECATE_AFTER_DAYS)).date().isoformat()])
    tot.update(c)
body.append(["**all (%d constructs)**" % len(per), tot["cand"], tot["scan"], tot["judge"], tot["both"], tot["judge_only"], "", ""])
T["adoption"] = md(["construct (form)", "candidates", "scanner v2 'use'", "judge 'use'", "both", "judge 'use' the scanner filed as mention", "ratified", "sweep exposure from (ratified + %d d)" % DEPRECATE_AFTER_DAYS], body)
# ------------------------------------------------------------------ anchors
A = D.get("anchors") or {}
anch = A.get("versions") or A.get("anchors") or []
exc = [a for a in anch if not a.get("has_ots") or a.get("status") != "confirmed"]
T["anchors"] = md(["register releases", "with OTS proof", "Bitcoin-confirmed", "exceptions"], [[len(anch), sum(1 for a in anch if a.get("has_ots")), sum(1 for a in anch if a.get("status") == "confirmed"), len(exc)]])
if exc:
    T["anchors"] += "\n" + md(["exception", "OTS proof", "status", "reason served"], [[a.get("version"), "yes" if a.get("has_ots") else "no", a.get("status"), (a.get("reason") or "—")] for a in exc])
T["anchors"] += "\n" + md(["last eight releases", "OTS proof", "status", "Bitcoin block time"], [[a.get("version"), "yes" if a.get("has_ots") else "no", a.get("status"), a.get("block_time") or "—"] for a in anch[-8:]])
# ------------------------------------------------------------------ key figures
ratw = sum(1 for p in props if p["stage"] == "ratified" and p["kind"] != "protocol"); ratp = sum(1 for p in props if p["stage"] == "ratified" and p["kind"] == "protocol")
rep_all = [m for m in rows if m.get("is_replication")]; el_all = [m for m in rep_all if m.get("settlement_eligible")]; ok_all = [m for m in el_all if m.get("reproduced_ok")]
ev = (D.get("changelog") or {}).get("events") or []
evk = collections.Counter(e.get("event") for e in ev)
T["keyfigures"] = md(["figure", "value"], [
    ["register snapshot pulled", D["pulled_at"]], ["campaign manifests pulled (content-addressed; the time does not change any number)", CAMP["pulled_at"]],
    ["register version", D["register"].get("version")], ["hash-chained ledger events, each bumping the minor version (%s)" % ", ".join("%d × `%s`" % (v, k) for k, v in evk.most_common()), len(ev)],
    ["proposal rows (all stages)", len(props)], ["ratified: language constructs / protocol rows", "%d / %d" % (ratw, ratp)],
    ["measurement rows", len(rows)], ["replication filings (rows flagged `is_replication`)", len(rep_all)],
    ["settlement-eligible replications (distinct agent, different manifest and inputs, as the register's own flag judges)", len(el_all)],
    ["reproduced within tolerance", "%d (%.0f%% of eligible)" % (len(ok_all), 100 * len(ok_all) / max(1, len(el_all)))],
    ["attempt objects", len(attempts)], ["of which pre-registered (minted before spend)", len(pre)], ["of which backfilled (created retroactively at filing; no mint-before-spend evidence)", len(bf)],
    ["aborts: typed gate kind / unclassified", "%d / %d" % (len(ab) - len(untyped), len(untyped))],
    ["distinct measuring agents / proposing agents", "%d / %d" % (len(subs), len(proposers))]])

# ------------------------------------------------------------------ render
if "--preview" in sys.argv:
    for k, v in T.items():
        print("### %s\n\n%s" % (k, v))
doc = open(DOC).read(); new = doc
present = set(re.findall(r"<!-- table:(\w+) -->", doc))
missing = [k for k in T if k not in present]; stale = sorted(present - set(T))
if missing or stale:
    die("markers missing from the document: %s; markers with no table: %s" % (missing, stale))
for k, v in T.items():
    pat = re.compile(r"(<!-- table:%s -->\n).*?(<!-- /table:%s -->)" % (k, k), re.S)
    if len(pat.findall(new)) != 1:
        die("marker pair for %s is not present exactly once" % k)
    new = pat.sub(lambda mo: mo.group(1) + v + mo.group(2), new)
# "every table is generated" has to be mechanically true: blank the generated regions (keeping
# line numbers) and refuse any GFM table row that survives.
masked = re.sub(r"<!-- table:\w+ -->\n.*?<!-- /table:\w+ -->", lambda mo: "\n" * mo.group(0).count("\n"), new, flags=re.S)
# A GFM table row is a pipe-leading line outside a fenced code block, including one nested in a
# blockquote (`> | a |` renders as a table inside the quote); pipes inside fences are literal.
orphans, fenced = [], False
for i, line in enumerate(masked.split("\n")):
    body = re.sub(r"^(\s*>\s?)+", "", line)
    if body.lstrip().startswith("```") or body.lstrip().startswith("~~~"):
        fenced = not fenced; continue
    if not fenced and body.lstrip().startswith("|"):
        orphans.append(i + 1)
if orphans:
    die("table rows outside generated markers at document lines %s" % orphans[:12])
if "--check" in sys.argv:
    print("document up to date" if new == doc else "DOCUMENT WOULD CHANGE"); sys.exit(0 if new == doc else 1)
open(DOC, "w").write(new); print("rendered %d tables into %s" % (len(T), os.path.basename(DOC)))
