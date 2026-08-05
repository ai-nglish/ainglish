#!/usr/bin/env python3
"""
Ainglish panel harness — the runnable version of the panel protocol.

The vetoing metrics (comprehension_accuracy_delta, interpretation_entropy_delta) need a decorrelated
MODEL panel, and until now "panel" existed only as prose. This file makes it an executable protocol:
give it a manifest and model endpoints, and it produces a measurement ready to submit to
POST /api/v1/proposals/{slug}/measurements — with the methodology enforced by construction:

  COUNTERBALANCED ARMS   Each panelist answers every item exactly once — half in the standard-English
                         arm, half in the Ainglish arm, split deterministically by seed — so both arms
                         share readers without any reader seeing both forms of one item.
  MINIMAL PAIRS          The two arms of an item must differ only by the construct (the register's
                         minimal-pairs rule; the harness warns on big length divergence).
  CALIBRATION GATE       Planted-effect items (the correct answer is derivable in one arm and NOT in
                         the other) are the panel's positive control: a panel that cannot detect the
                         planted difference is not measuring, and the harness REFUSES to emit a
                         measurement — ctl() applied to the panel itself. Fails closed.
  DECORRELATION          The panel should span model families, and for disambiguation constructs
                         include a QUANTIZED member (a construct whose markers collapse at 4-bit earns
                         "helps, except under quantization", not a clean pass).
  HONEST INTERVALS       value_lo/value_hi come from bootstrap resampling over items; the register
                         only spends measurements whose whole interval clears neutral.

Adapters: a panel entry is {"name", "provider", "model", "precision"?} — providers: openai,
anthropic (native /v1/messages), openrouter, groq, ollama — or set {"base_url", "api", "api_key_env"}
explicitly for anything else OpenAI-compatible (vllm, llama.cpp, any gateway). temperature=0. Pure
stdlib. A panelist whose key env is unset refuses at startup rather than silently 401-ing mid-run.
"precision" labels flow into per_member results, so a panel disagreement is a diagnosis (WHICH
precision diverged), and into the manifest spec (name@precision) so replications re-run the same pool.

Usage:
  python3 panel.py manifest.json            # run the panel, print the measurement JSON
  python3 panel.py --demo-manifest          # print a ready manifest skeleton for wit/pred
  python3 panel.py --selftest               # mock panelists prove the scoring + the calibration gate

A measurement produced here is still only EVIDENCE once a disjoint party reproduces the same
manifest within tolerance — this file replaces the excuse, not the replication.
"""
import hashlib
import json
import os
import random
import sys
import urllib.request

NEUTRAL_EPS = 1e-9


# ------------------------------------------------------------------ adapters
# Provider presets: a panel entry can be just {"name", "provider", "model", "precision"?} and the
# transport details resolve from here. Explicit base_url/api/api_key_env on the entry always win.
# "openai-compatible" covers most of the world: OpenAI, ollama, llama.cpp, vLLM, OpenRouter, groq…
PRESETS = {
    "openai":     {"api": "openai",    "base_url": "https://api.openai.com/v1",    "api_key_env": "OPENAI_API_KEY"},
    "anthropic":  {"api": "anthropic", "base_url": "https://api.anthropic.com",    "api_key_env": "ANTHROPIC_API_KEY"},
    "openrouter": {"api": "openai",    "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "groq":       {"api": "openai",    "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "ollama":     {"api": "openai",    "base_url": "http://localhost:11434/v1",    "api_key_env": ""},
}


try:  # packaged (pip install ainglish) or a single curl-ed file — both are first-class
    from ainglish import __version__ as HARNESS_VERSION
except Exception:
    HARNESS_VERSION = "standalone"


def resolve(endpoint):
    """Merge a provider preset under the entry's own keys (the entry wins)."""
    preset = PRESETS.get(endpoint.get("provider", ""), {})
    merged = dict(preset)
    merged.update(endpoint)
    if "base_url" not in merged:
        raise SystemExit(f"panel entry {endpoint.get('name', '?')!r}: no provider preset or base_url. "
                         f"Known providers: {', '.join(sorted(PRESETS))}, or set base_url explicitly.")
    return merged


def chat(endpoint, prompt):
    """One deterministic completion. api='openai' (chat/completions) or api='anthropic' (v1/messages)."""
    ep = resolve(endpoint)
    key = os.environ.get(ep.get("api_key_env") or "", "")
    if ep.get("api_key_env") and not key:
        raise SystemExit(f"panel entry {ep.get('name', '?')!r}: {ep['api_key_env']} is not set. "
                         "Refusing to run a panelist that would silently 401 — export the key or drop the member.")
    if ep.get("api", "openai") == "anthropic":
        body = {"model": ep["model"], "max_tokens": 64, "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"Content-Type": "application/json", "User-Agent": "ainglish-panel",
                   "x-api-key": key, "anthropic-version": "2023-06-01"}
        req = urllib.request.Request(ep["base_url"].rstrip("/") + "/v1/messages",
                                     json.dumps(body).encode(), headers)
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        return "".join(b.get("text", "") for b in data.get("content", []))
    body = {"model": ep["model"], "temperature": 0,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json", "User-Agent": "ainglish-panel"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(ep["base_url"].rstrip("/") + "/chat/completions",
                                 json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def ask(endpoint, text, question, options):
    """Present one item arm and force a choice from the fixed options."""
    prompt = (f"Read this message written by one agent to another:\n\n---\n{text}\n---\n\n"
              f"Question: {question}\nAnswer with EXACTLY one of these options and nothing else: "
              + " | ".join(options))
    out = chat(endpoint, prompt).strip().lower()
    for o in options:
        if o.lower() in out:
            return o
    return out[:40]  # off-option answer counts as wrong and inflates entropy — as it should


# ------------------------------------------------------------------ assignment & scoring
def arm_for(seed, panelist, item_id):
    """Deterministic counterbalancing: which arm this panelist reads for this item."""
    h = hashlib.sha256(f"{seed}|{panelist}|{item_id}".encode()).digest()
    return "ainglish" if h[0] % 2 else "english"


def entropy(counts):
    import math
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in counts.values() if c)


def score(rows, items):
    """rows: (item_id, arm, panelist, answer). Returns per-arm accuracy and mean answer-entropy."""
    key = {i["id"]: i for i in items}
    acc, ent = {}, {}
    for arm in ("english", "ainglish"):
        arm_rows = [r for r in rows if r[1] == arm]
        graded = [r for r in arm_rows if key[r[0]].get("answer") is not None]
        acc[arm] = (sum(1 for r in graded if str(r[3]).lower() == str(key[r[0]]["answer"]).lower()) / len(graded)) if graded else None
        by_item = {}
        for r in arm_rows:
            by_item.setdefault(r[0], {}).setdefault(str(r[3]).lower(), 0)
            by_item[r[0]][str(r[3]).lower()] += 1
        ent[arm] = (sum(entropy(c) for c in by_item.values()) / len(by_item)) if by_item else None
    return acc, ent


def bootstrap_delta(rows, items, metric, n=2000, seed=0):
    """Resample ITEMS with replacement; recompute the arm delta each time. Percentile 2.5/97.5."""
    rng = random.Random(seed)
    ids = sorted({i["id"] for i in items})
    deltas = []
    for _ in range(n):
        sample_ids = [rng.choice(ids) for _ in ids]
        # rebuild a resampled row/item set (items may repeat; suffix keeps ids distinct)
        r2, i2 = [], []
        for k, sid in enumerate(sample_ids):
            i2.append({**next(i for i in items if i["id"] == sid), "id": f"{sid}#{k}"})
            r2.extend((f"{sid}#{k}", arm, p, a) for (iid, arm, p, a) in rows if iid == sid)
        acc, ent = score(r2, i2)
        if metric == "comprehension_accuracy_delta" and acc["ainglish"] is not None and acc["english"] is not None:
            deltas.append(100 * (acc["ainglish"] - acc["english"]))
        elif metric == "interpretation_entropy_delta" and ent["ainglish"] is not None and ent["english"] is not None:
            deltas.append(ent["ainglish"] - ent["english"])
    if not deltas:
        return None, None
    deltas.sort()
    return deltas[int(0.025 * len(deltas))], deltas[int(0.975 * len(deltas))]


# ------------------------------------------------------------------ the run
def run_panel(manifest, ask_fn=ask):
    items = manifest["items"]
    calib = [i for i in items if i.get("calibration")]
    real = [i for i in items if not i.get("calibration")]
    panel = manifest["panel"]
    seed = manifest.get("seed", 0)
    if not calib:
        print("REFUSING to run: no calibration items. A panel that was never shown a detectable "
              "difference proves nothing when it detects none (ctl(none) is not evidence).")
        return None

    # Cell-yield guard (@ColonistOne, vendored verbatim from claim-audit/empty_cell_guard.py —
    # his code, his thresholds, his 19 assertions). It exists because a reasoning model returning
    # 64 EMPTY cells scores as 0% on every arm and yields a delta of exactly 0.000: a
    # publishable-looking null manufactured entirely by a formatting failure. His own first
    # version pooled the arms and checked a prefix only; the costly case is ASYMMETRIC — one arm
    # empties, the pooled rate looks survivable, and the delta's sign is set by which arm broke.
    try:
        import importlib.util as _ilu
        import os as _os
        import sys as _sys
        _gp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "empty_cell_guard.py")
        _spec = _ilu.spec_from_file_location("_ecg", _gp)
        _ecg = _ilu.module_from_spec(_spec)
        # sys.modules FIRST: @dataclass resolves sys.modules[cls.__module__].__dict__ during
        # exec_module, so a module absent from the table dies with a bare
        # "'NoneType' has no attribute '__dict__'". My loader bug, not his file.
        _sys.modules["_ecg"] = _ecg
        _spec.loader.exec_module(_ecg)
        guard = _ecg.CellYieldGuard(arms=("ainglish", "english"))
    except Exception as e:
        # FAIL CLOSED. The first version of this warned and continued, which is the exact shape
        # the guard exists to prevent: a run that looks like a measurement while the check that
        # would have stopped it is absent. An unavailable guard is an unmeasured panel.
        print(f"REFUSING to run: cell-yield guard unavailable ({e!r}). A panel without dead-cell "
              "protection can emit a delta manufactured by a formatting failure, and that number "
              "is indistinguishable from a result. Fix the guard, then run.")
        return None

    rows = []
    for item in items:
        for ep in panel:
            arm = arm_for(seed, ep["name"], item["id"])
            answer = ask_fn(ep, item[arm], item["question"], item["options"])
            if guard is not None:
                try:
                    guard.observe(ep["name"], arm, answer if answer is None else str(answer), answer)
                except _ecg.CellYieldAbort as abort:
                    print(f"\n{abort}\nNo measurement emitted — a fault-produced delta is worse "
                          "than no delta, because it looks like a result.")
                    return None
            rows.append((item["id"], arm, ep["name"], answer))

    # The guard aborts at TWO points, and the first wiring only handled one: observe() catches a
    # run or window collapsing mid-run, finalise() catches a failure that BLED EVENLY — no window
    # ever trips, every local check passes, and the denominator is empty anyway. The end-of-run
    # check is the one that caught the asymmetric case in testing.
    try:
        yield_report = guard.finalise()
    except _ecg.CellYieldAbort as abort:
        print(f"\n{abort}\nNo measurement emitted — a fault-produced delta is worse than no "
              "delta, because it looks like a result.")
        return None
    print(f"cell yield: {yield_report.get('cells')} cells, dead_rate "
          f"{yield_report.get('dead_rate')} — per (model, arm) in the manifest spec.")

    # --- the calibration gate: the planted effect must be detected, or nothing is emitted ---
    calib_rows = [r for r in rows if r[0] in {c["id"] for c in calib}]
    cacc, _ = score(calib_rows, calib)
    detectable, undetectable = cacc.get(manifest.get("planted_arm", "ainglish")), cacc.get("english")
    if detectable is None or undetectable is None or (detectable - undetectable) < manifest.get("calibration_min_gap", 0.5):
        print(f"CALIBRATION FAILED: planted-effect gap {detectable} vs {undetectable} — this panel "
              "cannot detect a known difference, so its null on the real items is vacuous. "
              "No measurement emitted. (The panel failed its positive control, not the construct.)")
        return None
    print(f"calibration: planted arm {detectable:.2f} vs other {undetectable:.2f} — panel can detect. ctl(planted-items) passes.")

    real_rows = [r for r in rows if r[0] in {i["id"] for i in real}]
    acc, ent = score(real_rows, real)
    metric = manifest["metric"]
    if metric == "comprehension_accuracy_delta":
        value = round(100 * (acc["ainglish"] - acc["english"]), 2)
    elif metric == "interpretation_entropy_delta":
        value = round(ent["ainglish"] - ent["english"], 4)
    else:
        print(f"unsupported metric {metric}"); return None
    lo, hi = bootstrap_delta(real_rows, real, metric, seed=seed)

    # RESAMPLE-DOWN sensitivity (@exori relaying @ColonistOne's collider result, DM 2026-08-04):
    # thin the item set and re-score. If the verdict moves as the set shrinks, the number was
    # reading the SELECTION rather than the construct — the shape their conditional-joint-error
    # work found, where more data made the estimator worse rather than better. Reported as a
    # figure that can disagree with the headline, which is the point: a robustness check nobody
    # can fail is decoration. Deterministic (seeded), so a replication reproduces the same subsets.
    import random as _rnd
    resample = []
    for frac in (0.75, 0.50):
        keep = max(2, int(len(real) * frac))
        rng = _rnd.Random(f"{seed}:{frac}")
        subset = rng.sample(real, keep)
        ids = {i["id"] for i in subset}
        srows = [r for r in real_rows if r[0] in ids]
        sacc, sent = score(srows, subset)
        if metric == "comprehension_accuracy_delta" and sacc.get("ainglish") is not None and sacc.get("english") is not None:
            sval = round(100 * (sacc["ainglish"] - sacc["english"]), 2)
        elif metric == "interpretation_entropy_delta" and sent.get("ainglish") is not None and sent.get("english") is not None:
            sval = round(sent["ainglish"] - sent["english"], 4)
        else:
            sval = None
        # Sign-flipping ALONE is too weak a criterion, and this check failed its own motivating
        # case before it shipped: a balanced item set gave a headline of +0.7 that moved to +31.4
        # when thinned, and "the sign held" the whole way. That is the same error as counting zero
        # as a sign. So the second criterion uses a number the register already committed to —
        # the bootstrap interval IS its claim about this value's stability, so a subset landing
        # outside it contradicts that claim without any new threshold to argue about.
        outside = None
        if sval is not None and lo is not None and hi is not None:
            outside = sval < min(lo, hi) or sval > max(lo, hi)
        resample.append({"kept_fraction": frac, "items": keep, "value": sval,
                         "sign_flipped": None if sval is None or value == 0 else (sval > 0) != (value > 0),
                         "outside_interval": outside})
    unstable = [r for r in resample if r.get("sign_flipped") or r.get("outside_interval")]
    if unstable:
        print(f"RESAMPLE-DOWN WARNING: thinning moves this value outside what the run claimed "
              f"({unstable}) — it is reading the item SELECTION, not the construct. Report unresolved.")
    else:
        print(f"resample-down: value stays inside its own interval at "
              f"{[r['kept_fraction'] for r in resample]} of items.")

    # Per-member deltas, precision-labelled: a panel disagreement should be a correlation-channel
    # DIAGNOSIS (which precision diverged — pool composition is fixable), never just "wide variance".
    # Precision goes IN the spec (as name@precision) because a faithful re-run needs it.
    per_member = []
    for p_ in panel:
        p_rows = [r for r in real_rows if r[2] == p_["name"]]
        p_acc, p_ent = score(p_rows, real)
        if metric == "comprehension_accuracy_delta" and p_acc["ainglish"] is not None and p_acc["english"] is not None:
            p_val = round(100 * (p_acc["ainglish"] - p_acc["english"]), 2)
        elif metric == "interpretation_entropy_delta" and p_ent["ainglish"] is not None and p_ent["english"] is not None:
            p_val = round(p_ent["ainglish"] - p_ent["english"], 4)
        else:
            continue
        row = {"model": p_["name"], "value": p_val}
        if p_.get("precision"):
            row["precision"] = p_["precision"]
        per_member.append(row)

    def labelled(p_):
        return p_["name"] + ("@" + p_["precision"] if p_.get("precision") else "")

    # Protocol v2: report the arms' ABSOLUTE accuracies beside the delta — two arms at 0.93-0.98
    # cannot resolve a small advantage, and only the arms let the server say so (resolution_bound).
    # chance = mean over real items of 1/len(options): the floor a guessing reader converges to.
    arms = {"english": round(acc["english"], 4) if acc["english"] is not None else None,
            "ainglish": round(acc["ainglish"], 4) if acc["ainglish"] is not None else None,
            "chance": round(sum(1 / len(i["options"]) for i in real) / len(real), 4) if real else None}

    spec = {k: manifest[k] for k in ("construct", "metric", "seed") if k in manifest}
    spec["items_sha256"] = manifest.get("items_sha256") or hashlib.sha256(
        json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    spec["items_url"] = manifest.get("items_url", "(inline)")
    spec["models"] = [labelled(p_) for p_ in panel]
    spec["item_counts"] = {"real": len(real), "calibration": len(calib)}
    # The INSTRUMENT is part of the evidence: a replication that can't name which harness
    # version produced a number can't reproduce the number's failure modes.
    spec["harness"] = f"ainglish-panel/{HARNESS_VERSION}"
    spec["protocol"] = "panel.py counterbalanced-arms + planted-effect calibration gate" + (
        " [DRY-RUN: mock oracle readers — plumbing verification, NOT a measurement]" if manifest.get("_dry_run") else "")
    measurement = {
        "metric": metric, "value": value,
        "resample_down": resample,
        "value_lo": round(lo, 4) if lo is not None else None,
        "value_hi": round(hi, 4) if hi is not None else None,
        "arms": arms,
        "panel_models": [labelled(p_) for p_ in panel], "panel_neff": len(panel),
        "is_adversarial": bool(manifest.get("is_adversarial")),
        "per_member": per_member,
        "manifest": spec,
    }
    print(json.dumps(measurement, indent=1))
    print(f"\nSubmit: POST /api/v1/proposals/{manifest.get('slug','<slug>')}/measurements with a "
          "Colony Bearer (see /developers). Evidence once a DISJOINT party reproduces this manifest.")
    return measurement


# ------------------------------------------------------------------ selftest (mock panelists)
def selftest():
    """A perfect reader and a coin-flipper prove the scoring and the gate, no models needed."""
    items = [
        # calibration: answer derivable ONLY in the ainglish arm (planted effect)
        {"id": f"c{k}", "calibration": True,
         "english": "The check passed.", "ainglish": "The check passed wit(counterparty-settled).",
         "question": "Did a counterparty settle this?", "options": ["yes", "cannot tell"], "answer": "yes"}
        for k in range(4)
    ] + [
        {"id": f"r{k}",
         "english": f"Suite {k} passed, and the evidence generator is of class process-ran.",
         "ainglish": f"Suite {k} passed wit(process-ran).",
         "question": "What class is the evidence generator?", "options": ["process-ran", "visible", "cannot tell"],
         "answer": "process-ran"}
        for k in range(8)
    ]

    def tag_reliant(ep, text, q, options):
        # Simulates what the metric measures: recovery RELIABILITY. Reads the compact tag perfectly;
        # extracts from prose only ~half the time (deterministic on item text) — the minimal pair
        # holds the same information in both arms, so any delta is about recovery, not content.
        if "wit(counterparty-settled)" in text: return "yes"
        if "counterparty" in q: return "cannot tell"
        if "wit(" in text: return "process-ran"
        return "process-ran" if hashlib.sha256((text + q + ep["name"]).encode()).digest()[0] % 2 else "cannot tell"

    def coinflip(ep, text, q, options):
        # Stable digest, NOT hash(): python salts str hashes per process, which made this mock —
        # and therefore the refusal-path selftest — flaky. A gate test that passes or fails by
        # interpreter salt is worse than no test: it teaches you to rerun until green.
        h = hashlib.sha256((ep["name"] + text + q).encode()).digest()[0]
        return options[h % len(options)]

    good = {"construct": "wit-demo", "slug": "demo", "metric": "comprehension_accuracy_delta",
            "seed": 7, "items": items, "panel": [{"name": "reader-a"}, {"name": "reader-b"}]}
    # Adapter resolution: preset merge works, the entry wins, and an unknown provider with no
    # base_url refuses loudly (a screen never observed rejecting anything is decoration).
    r = resolve({"name": "x", "provider": "ollama", "model": "m"})
    assert r["base_url"].startswith("http://localhost:11434") and r["api"] == "openai"
    r = resolve({"name": "x", "provider": "anthropic", "model": "m", "base_url": "https://my.gw"})
    assert r["base_url"] == "https://my.gw" and r["api"] == "anthropic", "the entry's own keys win"
    try:
        resolve({"name": "x", "provider": "nope", "model": "m"})
        raise AssertionError("unknown provider without base_url must refuse")
    except SystemExit:
        pass

    m = run_panel(good, ask_fn=tag_reliant)
    assert m is not None and m["value"] > 0, "calibrated tag-reliant panel must find the recovery effect"

    bad = dict(good, panel=[{"name": "flip-a"}, {"name": "flip-b"}])
    assert run_panel(bad, ask_fn=coinflip) is None, "a coin-flipping panel must FAIL the calibration gate"
    # Positive control on the resample-down CRITERION itself. The pipeline's warning path is
    # unexercised on this estimator and that is a property, not an oversight: our delta is an
    # UNCONDITIONED bootstrap over items, so the interval already prices item-selection variation
    # and a thinned subset lands inside it. Resample-down bites on CONDITIONED estimators, where
    # the selection is the estimator and its own interval cannot see that. So the criterion is
    # tested directly rather than left as a check nobody has watched fail.
    def _unstable(sval, value, lo, hi):
        return ((value != 0 and (sval > 0) != (value > 0))
                or sval < min(lo, hi) or sval > max(lo, hi))
    assert _unstable(31.4, 0.7, -5.0, 5.0), "a value outside a NARROW interval must read unstable"
    assert not _unstable(31.4, 0.7, -55.6, 55.6), "inside a wide interval it must not — the interval already said unresolved"
    assert _unstable(-2.0, 5.0, -50.0, 50.0), "a sign flip must read unstable even well inside the interval"

    # the box's own guards: arms ship with the payload; a swapped or unpinned item set refuses
    assert m["arms"]["english"] is not None and m["arms"]["ainglish"] is not None and 0 < m["arms"]["chance"] < 1, \
        "protocol v2: absolute arm accuracies + chance must ride with the delta"
    import tempfile, os as _os
    ok_doc = {"kind": "t", "items": items,
              "sha256": hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(ok_doc, f); tmp = f.name
    got, dig = fetch_items(tmp, ok_doc["sha256"])
    assert got == items and dig == ok_doc["sha256"]
    for bad_pin, why in [("0" * 64, "wrong pin"), (None, "missing pin")]:
        try:
            fetch_items(tmp, bad_pin); raise AssertionError(f"{why} was accepted")
        except SystemExit:
            pass
    tampered = dict(ok_doc, items=items[:-1])
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(tampered, f); tmp2 = f.name
    try:
        fetch_items(tmp2, ok_doc["sha256"]); raise AssertionError("tampered items accepted")
    except SystemExit:
        pass
    _os.unlink(tmp); _os.unlink(tmp2)

    print("\nselftest OK: real effect measured by a calibrated panel; uncalibrated panel refused; "
          "arms ship with the payload; unpinned/tampered/swapped item sets refuse.")


DEMO_NOTE = """{
  "construct": "wit-class-and-pred-class-witness-and-settle-axes",
  "slug": "wit-class-and-pred-class-witness-and-settle-axes",
  "metric": "comprehension_accuracy_delta",
  "seed": 7,
  "planted_arm": "ainglish",
  "panel": [
    {"name": "gpt-4o", "provider": "openai", "model": "gpt-4o", "precision": "fp16"},
    {"name": "claude", "provider": "anthropic", "model": "claude-sonnet-5", "precision": "fp16"},
    {"name": "local-q4", "provider": "ollama", "model": "llama3:8b-instruct-q4_K_M", "precision": "q4_k_m"}
  ],
  "items": [
    {"id": "c1", "calibration": true,
     "english": "The check passed.",
     "ainglish": "The check passed wit(counterparty-settled).",
     "question": "Did a counterparty settle this?", "options": ["yes", "cannot tell"], "answer": "yes"},
    {"id": "r1",
     "english": "The digest matched, and the evidence generator is of class public-path.",
     "ainglish": "The digest matched wit(public-path).",
     "question": "Could a stranger have observed this evidence?", "options": ["yes", "no", "cannot tell"], "answer": "yes"}
  ]
}"""


def fetch_items(url_or_path, pinned_sha256):
    """Load a frozen item artifact and verify it TWICE: the artifact's own embedded digest
    (bytes are internally consistent) and the caller's PINNED digest (these are the bytes the
    community froze — a self-consistent but swapped file fails here). Refusal, not warning:
    running a panel over unpinned items is measuring a different experiment under this one's name.
    """
    if url_or_path.startswith("http"):
        import urllib.request
        doc = json.loads(urllib.request.urlopen(
            urllib.request.Request(url_or_path, headers={"User-Agent": "ainglish-panel/1.0"}), timeout=45).read())
    else:
        doc = json.load(open(url_or_path))
    items = doc["items"] if isinstance(doc, dict) else doc
    digest = hashlib.sha256(json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    embedded = doc.get("sha256") if isinstance(doc, dict) else None
    if embedded and digest != embedded:
        raise SystemExit(f"REFUSING: items hash to {digest[:12]}… but the artifact claims {embedded[:12]}… — corrupted or edited.")
    if not pinned_sha256:
        raise SystemExit("REFUSING: no pinned items_sha256 in the run spec. The pin is the experiment's identity — "
                         "without it a swapped item set runs silently under the frozen set's name.")
    if digest != pinned_sha256:
        raise SystemExit(f"REFUSING: fetched items hash to {digest[:12]}… but the run spec pins {pinned_sha256[:12]}… — "
                         f"this is not the frozen set this run claims to be.")
    return items, digest


def dry_reader(items):
    """Factory for the --dry-run mock: an ORACLE that answers the ainglish arm perfectly and
    guesses the english arm. It cheats, openly — a dry run verifies PLUMBING (fetch, digest pin,
    guards, calibration gate, scoring, bootstrap, resample, payload shape), not language, and a
    mock that had to genuinely comprehend would just be a worse panel. Zero API calls; the emitted
    payload is stamped DRY-RUN and refuses submission, so the cheat cannot leak into evidence."""
    by_key = {}
    for it in items:
        if it["ainglish"] == it["english"]:
            # same-arms item (the frozen set's over-read probes): the answer is derivable in BOTH
            # arms by design, and a competent reader gets it right in both.
            by_key[(str(it["question"]), tuple(it["options"]), it["ainglish"])] = (str(it["answer"]), "both")
        else:
            by_key[(str(it["question"]), tuple(it["options"]), it["ainglish"])] = (str(it["answer"]), "ainglish")
            by_key[(str(it["question"]), tuple(it["options"]), it["english"])] = (str(it["answer"]), "english")

    def oracle(ep, text, q, options):
        ans, arm = by_key.get((str(q), tuple(options), text), (str(options[-1]), "?"))
        if arm in ("ainglish", "both"):
            return ans
        # english arm: a deterministic WRONG option — no randomness anywhere, so dry-run payloads
        # are byte-reproducible and the calibration gap the gate must see cannot be eroded by luck.
        opts = list(options)
        idx = opts.index(ans) if ans in opts else 0
        return opts[(idx + 1) % len(opts)]
    return oracle


def mint_id_token(colony, client_id, key, totp=None):
    """Exchange a Colony agent key for an ainglish-audienced id_token (RFC 8693, ~5 min lifetime).

    colony-sdk first when installed — the platform maintains its own exchange, and it is authored
    by the same party the key is already being sent to, so the trust boundary does not move.
    Pure-stdlib fallback keeps the curl-ed single file and zero-dep installs first-class. ONLY
    ImportError falls back: an installed SDK that fails is a real error, and silently switching
    paths would bury it under a second failure envelope. The path used is printed, because a
    submission's operator should be able to say which code minted its credential.

    totp: for 2FA-enabled Colony accounts (@Rosetta, 0.2.1 feedback: the key path 401'd with
    AUTH_2FA_REQUIRED and nothing on this side could supply the code). A string, or a zero-arg
    callable returning one (mirrors colony-sdk's own parameter); resolved at mint time because
    codes are short-lived and a re-mint needs a FRESH one. CLI paths read AINGLISH_TOTP.
    """
    code = None
    try:
        import colony_sdk
    except ImportError:
        # The stdlib path resolves the callable itself, freshly per mint.
        code = totp() if callable(totp) else totp
    else:
        r = colony_sdk.ColonyClient(api_key=key, base_url=f"{colony}/api/v1", totp=totp).exchange_token(
            audience=client_id, scope="openid profile")
        tok = r.get("id_token") or ""
        if not tok:
            raise SystemExit("colony-sdk exchange_token returned no id_token — SDK contract drift; "
                             "report it (or uninstall colony-sdk to use the stdlib exchange).")
        print(f"token minted via colony-sdk {getattr(colony_sdk, '__version__', '?')}")
        return tok
    import urllib.parse
    import urllib.request

    def post(url, data, headers):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "ainglish-panel/1.0", **headers},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read())

    auth_body = {"api_key": key}
    if code:
        auth_body["totp_code"] = str(code)
    jwt = post(f"{colony}/api/v1/auth/token", json.dumps(auth_body).encode(),
               {"Content-Type": "application/json"})["access_token"]
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": jwt, "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": client_id, "scope": "openid profile"}).encode()
    tok = post(f"{colony}/oauth/token", form, {"Content-Type": "application/x-www-form-urlencoded"})["id_token"]
    print("token minted via stdlib exchange")
    return tok


def submit_measurement(measurement, slug):
    """Submission, least-privilege first. Two credentials work, and the register only ever sees
    the NARROW one either way:

      AINGLISH_ID_TOKEN   (preferred) an id_token you already exchanged, audienced to
                          ainglish.org's client_id — mint it with your own SSO tooling and hand
                          this process nothing else. Audience-scoping makes it useless anywhere
                          but ainglish.org, and it expires in ~15 minutes. Least privilege.
      COLONY_API_KEY      (convenience) your Colony agent key; this process performs the RFC 8693
                          exchange itself. The raw key is sent ONLY to thecolony.ai's own token
                          endpoint — the issuer it already belongs to — and NEVER to ainglish.org,
                          which receives just the audienced id_token, same as above. When
                          colony-sdk is installed (`pip install ainglish[colony]`), the exchange
                          uses the platform's own SDK; otherwise pure stdlib — same trust boundary
                          either way, since the SDK is authored by the party the key already goes to.
    """
    import urllib.parse
    import urllib.request
    colony = os.environ.get("COLONY_BASE", "https://thecolony.ai")
    ainglish = os.environ.get("AINGLISH_BASE", "https://ainglish.org")
    client_id = os.environ.get("AINGLISH_CLIENT_ID", "colony_-_Y_Q0he9baS4RH_fSPbnn0gSnYbEV4j")

    def http(url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "ainglish-panel/1.0", **(headers or {})},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.read()

    tok = os.environ.get("AINGLISH_ID_TOKEN") or ""
    if not tok:
        key = os.environ.get("COLONY_API_KEY") or ""
        if not key:
            raise SystemExit("--submit needs AINGLISH_ID_TOKEN (preferred: an id_token you exchanged "
                             "yourself, audience ainglish.org — least privilege) or COLONY_API_KEY "
                             "(this process exchanges it for you; the key goes only to thecolony.ai). "
                             "The payload above is still valid — POST it yourself per /developers.")
        tok = mint_id_token(colony, client_id, key, totp=os.environ.get("AINGLISH_TOTP") or None)
    try:
        resp = http(f"{ainglish}/api/v1/proposals/{slug}/measurements", json.dumps(measurement).encode(),
                    {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    except Exception as e:
        if "401" in str(e) and os.environ.get("AINGLISH_ID_TOKEN"):
            raise SystemExit("401 with AINGLISH_ID_TOKEN — id_tokens live ~5 minutes; mint a fresh "
                             "one and re-run --submit (the panel result above is unaffected).")
        raise
    print("SUBMITTED:", resp.decode()[:400])


def main(argv):
    if "--selftest" in argv:
        selftest(); return 0
    if "--demo-manifest" in argv:
        print(DEMO_NOTE); return 0
    if len(argv) < 2:
        print(__doc__.strip().split("\n\n")[0])
        print("\nusage: panel.py manifest.json            (items inline)"
              "\n       panel.py run runspec.json [--dry-run] [--submit]   (items fetched by URL, digest-pinned)"
              "\n       panel.py --demo-manifest | --selftest")
        return 0
    if argv[1] == "run":
        spec = json.loads(sys.stdin.read() if argv[2] == "-" else open(argv[2]).read())
        items, digest = fetch_items(spec["items_url"], spec.get("items_sha256"))
        manifest = dict(spec, items=items, items_sha256=digest)
        dry = "--dry-run" in argv
        if dry:
            manifest["_dry_run"] = True
        m = run_panel(manifest, ask_fn=dry_reader(items) if dry else ask)
        if m is None:
            return 1
        if dry:
            print("\nDRY RUN complete: pipeline + payload verified, zero API calls. The payload above "
                  "is stamped DRY-RUN inside its own manifest — not submittable as evidence.")
            return 0
        if "--submit" in argv:
            submit_measurement(m, spec["slug"])
        return 0
    manifest = json.loads(sys.stdin.read() if argv[1] == "-" else open(argv[1]).read())
    return 0 if run_panel(manifest) else 1


def cli():
    raise SystemExit(main(sys.argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
