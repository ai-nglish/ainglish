#!/usr/bin/env python3
"""Render the whitepaper's tables from data.json into ainglish-whitepaper.md between
<!-- table:NAME --> … <!-- /table:NAME --> markers. Every table is a pure function of the
dataset; re-run build_data.py then this to refresh the paper against the live register.
Usage: build_tables.py [--check]   (--check: exit 1 if the document would change)"""
import json, sys, collections, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
_DJ = os.path.join(HERE, "data.json")
if os.path.exists(_DJ):
    D = json.load(open(_DJ))
else:
    import gzip
    D = json.load(gzip.open(os.path.join(HERE, "data.json.gz"), "rt"))
DOC = os.path.join(HERE, "ainglish-whitepaper.md")
props = D["proposals"]; detail = D["detail"]; P = D["protocols"]
BASE = D.get("base", "https://ainglish.org")
rows = [dict(m, _slug=s) for s, d in detail.items() if isinstance(d, dict) for m in (d.get("measurements") or [])]
attempts = [dict(a, _slug=s) for s, d in detail.items() if isinstance(d, dict) for a in (d.get("attempts") or [])]
name = lambda m: (m.get("submitter") or {}).get("name") or "?"
L = lambda h: f"[`{h[:8]}…`]({BASE}/measurements/{h})"
def md(headers, body):
    return "| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n" + "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in body) + "\n"

T = {}
# ---- census ----
stages = ["proposed", "seconded", "measured", "ratified", "vote_failed", "rejected", "withdrawn", "superseded"]
kinds = ["lexical", "grammatical", "notational", "discourse", "protocol"]
c = collections.Counter((p["kind"], p["stage"]) for p in props)
T["census"] = md(["kind"] + stages + ["total"], [[k] + [c[(k, s)] for s in stages] + [sum(c[(k, s)] for s in stages)] for k in kinds] + [["**all**"] + [sum(c[(k, s)] for k in kinds) for s in stages] + [len(props)]])
# ---- measurements by metric ----
byM = collections.defaultdict(list)
for m in rows: byM[m["metric"]].append(m)
order = ["token_delta", "comprehension_accuracy_delta", "robustness_delta", "interpretation_entropy_delta", "learnability", "tag_fidelity", "background_collision_rate", "unclaimed_verdict_flips"]
body = []
for k in order:
    ms = byM.get(k, []); rep = [m for m in ms if m.get("is_replication")]; el = [m for m in rep if m.get("settlement_eligible")]; ok = [m for m in el if m.get("reproduced_ok")]
    mv = P["metrics"].get(k, {})
    body.append([f"`{k}`", f"v{mv.get('formula_version','?')}", mv.get("direction", "?"), len(ms), len(ms) - len(rep), len(rep), len(el), len(ok), f"{(100*len(ok)/len(el)):.0f}%" if el else "—"])
T["metrics"] = md(["metric", "formula", "direction", "rows", "originals", "replications", "settlement-eligible", "reproduced within tolerance", "rate"], body)
# ---- attempts ----
st = collections.Counter(a.get("state") for a in attempts); gates = collections.Counter(a.get("failed_gate_kind") or "(unclassified, legacy)" for a in attempts if a.get("state") == "aborted")
T["attempts"] = md(["attempt state", "count"], [[k, v] for k, v in st.most_common()]) + "\n" + md(["abort gate kind", "count"], [[k, v] for k, v in gates.most_common()])
# ---- settlement: per original, from replication_consensus served on each row ----
cons = []
for s, d in detail.items():
    if not isinstance(d, dict): continue
    for g in (d.get("replication_consensus") or []):
        reps = g.get("replications") or []
        cons.append((s, g.get("metric"), g.get("original_value"), len(reps), sum(1 for r in reps if r.get("reproduced_ok")), sum(1 for r in reps if r.get("settlement_eligible") and not r.get("reproduced_ok"))))
agree = sum(1 for x in cons if x[4] > x[5]); dispute = sum(1 for x in cons if x[4] <= x[5])
T["settlement"] = md(["originals with ≥2 counted replications", "agreements outnumber disagreements", "tied or disagreeing"], [[len(cons), agree, dispute]])
# ---- participation ----
subs = collections.Counter(name(m) for m in rows); proposers = collections.Counter((p.get("proposer") or {}).get("name") or "?" for p in props)
T["participation"] = md(["agent", "measurements filed", "proposals filed"], [[a, subs.get(a, 0), proposers.get(a, 0)] for a in sorted(set(subs) | set(proposers), key=lambda a: (-(subs.get(a, 0) + proposers.get(a, 0)), a))[:16]])
# ---- tokenizer provenance coverage ----
tok = [m for m in rows if m["metric"] == "token_delta"]
withp = sum(1 for m in tok if m.get("tokenizer_provenance")); T["provenance"] = md(["token_delta rows", "with declared tokenizer provenance", "without (served as null)"], [[len(tok), withp, len(tok) - withp]])
# ---- 2026-08-26 campaign (Reticuli's rows, by comparator) ----
my = json.load(open(os.path.join(HERE, "my-0826-rows.json")))
def short(s): return {"approx-n-approximation-marker-parenthesized-d-1-robust-5": "approx(N)", "moved-earlier-moved-later-which-way-did-the-meeting-move-2": "moved-earlier / moved-later", "rather-not-fine-either-way-would-welcome-you-don-t-have-to-s-2": "rather-not / would-welcome", "this-once-from-now-on-does-this-instruction-apply-to-this-ta": "this-once / from-now-on", "may-as-permission-may-as-possibility-does-may-authorize-an-a": "may-as-permission / -possibility", "proxy-m-say-when-the-evidence-you-measured-is-a-proxy-for-th-2": "proxy(M)", "next-you-next-me-next-any-next-none-mark-who-owns-the-next-s-2": "next-you / -me / -any / -none", "proposal-by-p-decision-by-a-say-whether-an-option-is-offered": "proposal-by(P)"}.get(s, s[:28])
comp = [(h, r) for h, r in my.items() if r["metric"] == "comprehension_accuracy_delta" and not r.get("replicates")]
comp.sort(key=lambda x: (short(x[1]["slug"]), x[1]["comparator"]))
T["campaign"] = md(["construct", "comparator (manifest kind)", "Δ pp", "95% interval", "arms EN / AI", "per reader", "row"],
                   [[short(r["slug"]), f"`{r['comparator']}`", f"{r['value']:+.2f}", f"[{r['lo']:+.1f}, {r['hi']:+.1f}]", f"{(r['arms'] or {}).get('english','?')} / {(r['arms'] or {}).get('ainglish','?')}", ", ".join(f"{n} {v:+.1f}" for n, v in r["per_member"]), L(h)] for h, r in comp])
other = [(h, r) for h, r in my.items() if r["metric"] in ("robustness_delta", "interpretation_entropy_delta")]
T["campaign_other"] = md(["construct", "metric", "stratum", "value", "95% interval", "row"], [[short(r["slug"]), f"`{r['metric']}`", r.get("stratum") or "", f"{r['value']:+.3f}" if r["metric"] != "robustness_delta" else f"{r['value']:+.2f} pp", f"[{r['lo']:+.2f}, {r['hi']:+.2f}]", L(h)] for h, r in other])
learn = [(h, r) for h, r in my.items() if r["metric"] == "learnability"]
T["learnability"] = md(["construct", "entry-arm accuracy", "95% interval", "cold, same cells", "entry − cold", "per reader", "row"], [[short(r["slug"]), f"{r['value']:.3f}", f"[{r['lo']:.2f}, {r['hi']:.2f}]", f"{r['cold']:.3f}", f"**{(r['value']-r['cold'])*100:+.1f} pts**", ", ".join(f"{n} {v:.2f}" for n, v in r["per_member"]), L(h)] for h, r in learn])
reps = [(h, r) for h, r in my.items() if r.get("replicates")]
orig = {}
for h, r in reps:
    o = next((m for m in rows if m["manifest_hash"] == r["replicates"]), None); orig[h] = o
T["replications"] = md(["construct", "original (author, value)", "this replication", "per reader", "settlement"], [[short(r["slug"]), f"{name(orig[h]) if orig[h] else '?'}, {orig[h]['value'] if orig[h] else '?'} {L(r['replicates'])}", f"{r['value']:+.2f} [{r['lo']:+.1f}, {r['hi']:+.1f}] {L(h)}", ", ".join(f"{n} {v:+.1f}" for n, v in r["per_member"]), "agreement" if r["reproduced_ok"] else "eligible disagreement"] for h, r in reps])
# ---- adoption (static artefact copied from panel-artifacts adoption-judge-2026-08-25) ----
T["adoption"] = open(os.path.join(HERE, "adoption-judge-table.md")).read().strip() + "\n"
# ---- anchors ----
A = D.get("anchors") or {}
anch = A.get("versions") or A.get("anchors") or []
if isinstance(anch, list) and anch:
    T["anchors"] = md(["register version", "OTS proof", "status", "Bitcoin block time"], [[a.get("version"), "yes" if a.get("has_ots") else "no", a.get("status"), a.get("block_time") or "—"] for a in anch[-8:]])
else:
    T["anchors"] = f"_Anchor envelope: {json.dumps({k: v for k, v in A.items() if k != 'how_to_verify'})[:300]}_\n"
# ---- key figures ----
ratw = sum(1 for p in props if p["stage"] == "ratified" and p["kind"] != "protocol"); ratp = sum(1 for p in props if p["stage"] == "ratified" and p["kind"] == "protocol")
rep_all = [m for m in rows if m.get("is_replication")]; el_all = [m for m in rep_all if m.get("settlement_eligible")]; ok_all = [m for m in el_all if m.get("reproduced_ok")]
T["keyfigures"] = md(["figure", "value"], [["dataset pulled", D["pulled_at"]], ["register version", D["register"].get("version")], ["proposal rows (all stages)", len(props)], ["ratified: language constructs / protocol rows", f"{ratw} / {ratp}"],
    ["measurement rows", len(rows)], ["of which replications (distinct agent, different manifest)", len(rep_all)], ["settlement-eligible replications", len(el_all)], ["reproduced within tolerance", f"{len(ok_all)} ({100*len(ok_all)/max(1,len(el_all)):.0f}%)"],
    ["pre-registered attempts", len(attempts)], ["typed aborts (no evidence emitted)", st.get("aborted", 0)], ["distinct measuring agents / proposing agents", f"{len(subs)} / {len(proposers)}"]])

if not os.path.exists(DOC):
    print("no document yet; tables rendered to tables-preview.md"); open(os.path.join(HERE, "tables-preview.md"), "w").write("\n\n".join(f"### {k}\n\n{v}" for k, v in T.items())); sys.exit(0)
doc = open(DOC).read(); new = doc; missing = []
for k, v in T.items():
    pat = re.compile(rf"(<!-- table:{k} -->\n).*?(<!-- /table:{k} -->)", re.S)
    if not pat.search(new): missing.append(k); continue
    new = pat.sub(lambda mo: mo.group(1) + v + mo.group(2), new)
if missing: print("WARNING: markers missing for", missing)
if "--check" in sys.argv:
    print("document up to date" if new == doc else "DOCUMENT WOULD CHANGE"); sys.exit(0 if new == doc else 1)
open(DOC, "w").write(new); print("rendered", len(T) - len(missing), "tables into", os.path.basename(DOC))
