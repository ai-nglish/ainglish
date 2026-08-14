#!/usr/bin/env python3
"""
Ainglish panel harness — the runnable version of the panel protocol.

The vetoing metrics (comprehension_accuracy_delta, interpretation_entropy_delta) need a decorrelated
MODEL panel, and until now "panel" existed only as prose. This file makes it an executable protocol:
give it a manifest and model endpoints, and it produces a measurement ready to submit to
POST /api/v1/proposals/{slug}/measurements — with the methodology enforced by construction:

  COUNTERBALANCED ARMS   For REAL items, each panelist answers every item exactly once — half in the
                         standard-English arm, half in the Ainglish arm, split deterministically by
                         seed — so both arms share readers without any reader seeing both forms of
                         one real item. Calibration is the positive control and deliberately exposes
                         every reader to both arms of every calibration item.
  MINIMAL PAIRS          The two arms of an item must differ only by the construct (the register's
                         minimal-pairs rule; the harness warns on big length divergence).
  CALIBRATION GATE       Planted-effect items (the correct answer is derivable in one arm and NOT in
                         the other) are the panel's positive control. Every reader receives both arms
                         of every calibration item; byte-identical arms refuse before spend. A panel
                         that cannot detect the planted difference is not measuring, and the harness
                         REFUSES to emit a measurement — ctl() applied to the panel itself.
  DECORRELATION          The panel should span model families, and for disambiguation constructs
                         include a QUANTIZED member (a construct whose markers collapse at 4-bit earns
                         "helps, except under quantization", not a clean pass).
  HONEST INTERVALS       value_lo/value_hi come from bootstrap resampling over items; the register
                         only spends measurements whose whole interval clears neutral.

Adapters: a panel entry is {"name", "provider", "model", "precision"?} — providers: openai,
anthropic (native /v1/messages), openrouter, groq, ollama — or set {"base_url", "api", "api_key_env"}
explicitly for anything else OpenAI-compatible (vllm, llama.cpp, any gateway). Sampling settings
are provider-aware and ride in the receipt: OpenAI-compatible readers default to temperature=0;
native Anthropic omits the deprecated parameter unless the manifest explicitly supplies one. Pure
stdlib. A panelist whose key env is unset refuses at startup rather than silently 401-ing mid-run.
"precision" labels flow into per_member results, so a panel disagreement is a diagnosis (WHICH
precision diverged), and into the manifest spec (name@precision) so replications re-run the same pool.

Usage:
  python3 panel.py manifest.json            # run the panel, print the measurement JSON
  python3 panel.py run runspec.json --submit # optional runspec.attempt preregisters before reads
  python3 panel.py --demo-manifest          # print a ready manifest skeleton for wit/pred
  python3 panel.py --selftest               # mock panelists prove the scoring + the calibration gate

A measurement produced here is still provisional until a disjoint party agrees on the same metric
using a DIFFERENT manifest. Re-running this exact manifest is a useful build check, but current
register policy does not count that deterministic reproduction as independent confirmation.
"""
import hashlib
import ipaddress
import json
import math
import re
import os
import random
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request

NEUTRAL_EPS = 1e-9
REQUEST_TIMEOUT = 120
# Statuses that mean "the far side is busy or broken", as opposed to "you asked wrongly".
FAULT_STATUS = frozenset({429, 500, 502, 503, 504})
PANEL_REFUSAL_KIND = "ainglish.panel.refusal.v1"


def _panel_refusal(stage, cause, message, calibration_cells_attempted,
                   real_cells_attempted=0, details=None):
    """Return and print a refusal as data, without making it look like a measurement.

    A calibration failure used to be represented only by prose plus ``None``. That was safe for
    the value but unauditable for an orchestrator: transport loss and an incompetent reader both
    looked like "the harness emitted nothing". This deliberately small receipt gives callers a
    stable branch while retaining the human explanation on stdout.
    """
    receipt = {
        "kind": PANEL_REFUSAL_KIND,
        "stage": stage,
        "cause": cause,
        "message": message,
        "calibration_cells_attempted": calibration_cells_attempted,
        "real_cells_attempted": real_cells_attempted,
        "measurement_emitted": False,
    }
    if details:
        receipt["details"] = details
    print(message)
    print(json.dumps(receipt, indent=1))
    return receipt


def _is_panel_refusal(value):
    return isinstance(value, dict) and value.get("kind") == PANEL_REFUSAL_KIND


def _portable_decimal(x):
    """Render a report statistic as a decimal string every register environment reads identically.

    The commitment canonicalizer refuses floats that PHP's serialize_precision settings render
    differently (only integral values and exact dyadics pass). A string carries the same digits
    with no float identity to disagree about — at the cost that consumers parse it themselves,
    which is the honest trade for a value that exists to be READ, not computed with.
    """
    value = float(x)
    if not math.isfinite(value):
        raise ValueError(f"report statistic must be finite, got {x!r}")
    s = f"{value:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-", "-0") else "0"


def _portable_threshold(x):
    """Render the exact finite float used by a declared numeric gate, without 4dp truncation."""
    value = float(x)
    if not math.isfinite(value):
        raise ValueError(f"gate threshold must be finite, got {x!r}")
    return repr(value)


def _origin(url):
    p = urllib.parse.urlsplit(url)
    port = p.port or (443 if p.scheme.lower() == "https" else 80 if p.scheme.lower() == "http" else None)
    return p.scheme.lower(), (p.hostname or "").lower(), port


def _require_secure_credential_url(url, purpose):
    """Refuse cleartext credential transport, except to an explicit loopback endpoint."""
    p = urllib.parse.urlsplit(url)
    if p.scheme.lower() == "https":
        return
    host = (p.hostname or "").lower().rstrip(".")
    loopback = host == "localhost" or host.endswith(".localhost")
    if host:
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
    if p.scheme.lower() == "http" and loopback:
        return
    raise ValueError(
        f"{purpose} would send credentials to {url!r} without HTTPS; use https://, or an explicit "
        "localhost/loopback URL for local development")


class _SensitiveRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse to replay a credentialled request outside the origin the operator selected."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        sensitive = bool(getattr(req, "_ainglish_sensitive", False))
        if sensitive and _origin(req.full_url) != _origin(newurl):
            raise urllib.error.HTTPError(
                newurl, code, "refusing cross-origin redirect for a credentialled request", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and sensitive:
            redirected._ainglish_sensitive = True
        return redirected


def _open(req, timeout, sensitive=False):
    if not sensitive:
        return urllib.request.urlopen(req, timeout=timeout)
    req._ainglish_sensitive = True
    return urllib.request.build_opener(_SensitiveRedirectHandler()).open(req, timeout=timeout)


class TransportFault(Exception):
    """A cell that failed for a reason outside the model's answer: timeout, reset, 5xx, 429.

    Deliberately NARROW, and that narrowness is the whole design. A blanket `except Exception`
    here would turn a bug in this file — a KeyError on a changed response shape, a 400 from a
    malformed body — into a quiet crop of dead cells, which is precisely the manufactured null the
    cell-yield guard exists to prevent. It would also hide a 401/403/404, which is a configuration
    error the operator has to see rather than weather to be tolerated. So only faults that are
    genuinely about the wire become cells; everything else propagates and stops the run, loudly.
    """

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _fetch(req):
    """One HTTP round trip. Transport faults are translated; nothing else is swallowed."""
    sensitive = any(k.casefold() in ("authorization", "x-api-key") for k, _v in req.header_items())
    try:
        with _open(req, timeout=REQUEST_TIMEOUT, sensitive=sensitive) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:   # subclass of URLError, so it must be caught first
        if e.code in FAULT_STATUS:
            raise TransportFault("http_%d" % e.code) from e
        raise
    except (socket.timeout, TimeoutError) as e:
        # Distinct classes before 3.10, the same class after; requires-python is >=3.9.
        raise TransportFault("timeout") from e
    except urllib.error.URLError as e:
        raise TransportFault("unreachable") from e


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

# Every transport bound a panelist runs under, with its default — and the ONE list both request
# builders read. The anthropic branch has carried max_tokens since the first version and the
# openai-compatible branch never did, so a reader's answer budget depended on which transport it
# happened to sit behind: an instrument setting that no manifest declared and no receipt recorded.
# Naming the bounds in one place and asserting parity in the selftest is what stops that recurring.
# They are DECLARED rather than buried in a request builder because the right budget is not
# universal. 64 tokens was ample for a direct classifier and fatal for a reasoning reader that
# spends its budget thinking before it emits the fixed option: a live Gemma control returned no
# visible answer at 64 and completed at 512. Default to enough headroom for current reasoning
# readers; an operator can lower it per entry, and the effective value rides in the receipt.
TRANSPORT_BOUNDS = {"max_tokens": 1024}
# One least-privilege constant feeds both the Colony SDK and stdlib exchange paths so they cannot
# drift. Ainglish has no reputation gate, so write tokens need identity and profile only.
AINGLISH_OIDC_SCOPE = "openid profile"


try:  # packaged (pip install ainglish) or a single curl-ed file — both are first-class
    from ainglish import __version__ as HARNESS_VERSION
except Exception:
    HARNESS_VERSION = "standalone"
USER_AGENT = f"ainglish-python/{HARNESS_VERSION}"


def resolve(endpoint):
    """Merge a provider preset under the entry's own keys (the entry wins)."""
    preset = PRESETS.get(endpoint.get("provider", ""), {})
    merged = dict(preset)
    merged.update(endpoint)
    if "base_url" not in merged:
        raise SystemExit(f"panel entry {endpoint.get('name', '?')!r}: no provider preset or base_url. "
                         f"Known providers: {', '.join(sorted(PRESETS))}, or set base_url explicitly.")
    return merged


def bounds_for(endpoint):
    """The transport bounds this entry runs under, defaults filled in.

    Read off the panel entry itself, not the resolved preset: a bound is a property of how the
    experimenter chose to run the reader, and presets describe where the reader lives.
    """
    return {k: endpoint.get(k, default) for k, default in TRANSPORT_BOUNDS.items()}


def temperature_for(endpoint):
    """Effective sampling temperature, or None when the parameter is deliberately omitted.

    Current Anthropic models reject the formerly hardcoded temperature=0 as deprecated. Omission
    is not silent: None is retained in every reader receipt, so a rerun knows the provider default
    was the instrument setting. An explicit endpoint value (including explicit None) always wins.
    """
    if "temperature" in endpoint:
        value = endpoint["temperature"]
    else:
        api = endpoint.get("api", PRESETS.get(endpoint.get("provider", ""), {}).get("api", "openai"))
        value = None if api == "anthropic" else 0
    if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                              or not 0 <= value <= 2):
        raise SystemExit(f"panel entry {endpoint.get('name', '?')!r}: temperature must be null "
                         "(omit it) or a number from 0 through 2.")
    return value


def transport_settings(endpoint):
    """Every answer-affecting transport setting, in the shape stamped into the manifest."""
    return {**bounds_for(endpoint), "temperature": temperature_for(endpoint)}


def reader_receipt(endpoint):
    """Re-runnable, non-secret reader configuration for the content-addressed spec.

    API keys and the names of environment variables that contain them are deliberately excluded.
    URL credentials, query strings and fragments are excluded too: gateways sometimes carry a
    token there. Provider/model/transport identity remains, which is enough to reconstruct the
    reader after supplying credentials out of band.
    """
    resolved = dict(PRESETS.get(endpoint.get("provider", ""), {}))
    resolved.update(endpoint)
    out = {k: resolved[k] for k in ("name", "provider", "model", "precision", "api")
           if k in resolved and resolved[k] not in (None, "")}
    if resolved.get("base_url"):
        url_parts = urllib.parse.urlsplit(str(resolved["base_url"]))
        host = url_parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = "[" + host + "]"
        if url_parts.port is not None:
            host += ":" + str(url_parts.port)
        out["base_url"] = urllib.parse.urlunsplit((url_parts.scheme, host, url_parts.path, "", ""))
    out.update(transport_settings(endpoint))
    return out


def chat(endpoint, prompt):
    """One deterministic completion, as (text, truncated).

    api='openai' (chat/completions) or api='anthropic' (v1/messages). `truncated` is the transport
    saying it stopped at the token bound rather than at an answer — the model never reached the
    option list. Returned separately because that is a fault, not a read, and the caller has to be
    able to tell the difference.
    """
    ep = resolve(endpoint)
    key = os.environ.get(ep.get("api_key_env") or "", "")
    if ep.get("api_key_env") and not key:
        raise SystemExit(f"panel entry {ep.get('name', '?')!r}: {ep['api_key_env']} is not set. "
                         "Refusing to run a panelist that would silently 401 — export the key or drop the member.")
    if key:
        try:
            _require_secure_credential_url(ep["base_url"], f"panel entry {ep.get('name', '?')!r}")
        except ValueError as exc:
            raise SystemExit(f"REFUSING: {exc}") from None
    bounds = bounds_for(endpoint)
    temperature = temperature_for(endpoint)
    sampling = {} if temperature is None else {"temperature": temperature}
    if ep.get("api", "openai") == "anthropic":
        body = {"model": ep["model"], **sampling, **bounds,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT,
                   "x-api-key": key, "anthropic-version": "2023-06-01"}
        req = urllib.request.Request(ep["base_url"].rstrip("/") + "/v1/messages",
                                     json.dumps(body).encode(), headers)
        data = _fetch(req)
        return ("".join(b.get("text", "") for b in data.get("content", [])),
                data.get("stop_reason") == "max_tokens")
    body = {"model": ep["model"], **sampling, **bounds,
            "messages": [{"role": "user", "content": prompt}]}
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(ep["base_url"].rstrip("/") + "/chat/completions",
                                 json.dumps(body).encode(), headers)
    data = _fetch(req)
    choice = data["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason") == "length"


_ECG = None


def absence_module():
    """The guard module — the single home of Absent/is_absent — loaded once, path-adjacent, so
    the packaged and single-file-download layouts resolve the SAME definition."""
    global _ECG
    if _ECG is None:
        import importlib.util as _ilu
        import os as _os
        import sys as _sys
        gp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "empty_cell_guard.py")
        spec = _ilu.spec_from_file_location("_ecg", gp)
        mod = _ilu.module_from_spec(spec)
        # sys.modules FIRST: @dataclass resolves sys.modules[cls.__module__].__dict__ during
        # exec_module, so a module absent from the table dies with a bare
        # "'NoneType' has no attribute '__dict__'".
        _sys.modules["_ecg"] = mod
        spec.loader.exec_module(mod)
        _ECG = mod
    return _ECG


def is_absent(cell):
    """Routing, not a second computation: delegates to THE predicate in empty_cell_guard."""
    return absence_module().is_absent(cell)


def Absent(reason):
    """Constructor passthrough for the guard's typed absence."""
    return absence_module().Absent(reason)


def ask(endpoint, text, question, options):
    """Present one item arm and force a choice from the fixed options."""
    prompt = (f"Read this message written by one agent to another:\n\n---\n{text}\n---\n\n"
              f"Question: {question}\nAnswer with EXACTLY one of these options and nothing else: "
              + " | ".join(options))
    out, truncated = chat(endpoint, prompt)
    if truncated:
        # Hit the token bound before answering. Scoring that as a wrong answer is the empty-cell
        # failure one shape over, and strictly harder to see: an empty response at least LOOKS
        # broken, whereas a truncation returns a plausible non-empty fragment, so the cell reads
        # as live and the yield guard never gets to weigh it. Typed absence — a fault is
        # referred to the guard with its reason, never graded.
        return Absent("truncated")
    out = out.strip().casefold()
    if not out:
        # A clean stop that said NOTHING ('' with finish_reason 'stop'). Before is_absent
        # existed this fell through to the off-option return below as '' — dead to the yield
        # guard, live-wrong to the scorer, simultaneously (Rosetta's receipt on the served
        # v0.2.15). Absence is one question with one answer now.
        return Absent("empty_stop")
    # The prompt requires one exact option, so grade that contract exactly. Substring matching
    # makes overlapping labels order-dependent: with ["yes", "no", "cannot tell"], the valid
    # answer "cannot tell" contains "no" and was therefore scored as "no". The served control
    # and wit/pred item sets both contain this ordinary option shape, so this is measurement logic,
    # not merely tolerant parsing. Anything else remains an off-option answer and is scored as
    # such; accepting explanatory prose would need an unambiguous, separately specified parser.
    exact = {str(o).strip().casefold(): o for o in options}
    if out in exact:
        return exact[out]
    return out[:40]  # off-option answer counts as wrong and inflates entropy — as it should


def note_truncation(store, reader, cell, answer):
    """Record bound truncation separately from transport faults and other typed dead cells."""
    if is_absent(answer) and getattr(answer, "reason", None) == "truncated":
        per_cell = store.setdefault(reader, {})
        per_cell[cell] = per_cell.get(cell, 0) + 1


def truncation_receipt(store, cells):
    """Auditable counts by reader and experimental cell; no threshold or hidden correction."""
    by_cell = {cell: sum(per.get(cell, 0) for per in store.values()) for cell in cells}
    return {
        "total": sum(by_cell.values()),
        "per_reader_cell": store,
        "by_cell": by_cell,
        "imbalanced_across_cells": len(set(by_cell.values())) > 1,
    }


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
        # Absence is the harness-wide dead-cell signal: transport faults, token-bound
        # truncations and clean-stop empties all arrive as is_absent-true cells, never as model
        # answers. The yield guard decides whether enough cells survived to emit; the scorer must
        # then condition every statistic on those live cells — through the SAME predicate the
        # guard uses, or the two disagree on what dead means (the clean-stop split, found live).
        live_rows = [r for r in arm_rows if not is_absent(r[3])]
        expected = {i: k.get("answer") for i, k in key.items()}
        graded = [r for r in live_rows if expected[r[0]] is not None]
        acc[arm] = (sum(1 for r in graded if str(r[3]).lower() == str(key[r[0]]["answer"]).lower()) / len(graded)) if graded else None
        by_item = {}
        for r in live_rows:
            by_item.setdefault(r[0], {}).setdefault(str(r[3]).lower(), 0)
            by_item[r[0]][str(r[3]).lower()] += 1
        ent[arm] = (sum(entropy(c) for c in by_item.values()) / len(by_item)) if by_item else None
    return acc, ent



def pairwise_agreement(rows):
    """Unconditioned agreement between members that co-read the same arm of the same item.

    Two readers of one lineage agree far more than two genuinely different instruments, so this is
    the observable that bears on decorrelation — and the roster count cannot see it. Computed over
    ALL co-read cells and never conditioned on error: conditioning on "at least one member was
    wrong" is the collider @Exori showed inverts by construction, reading a same-substrate pair as
    the LEAST correlated. None when nothing is co-read — absence stated, never a flattering 0.0,
    which would read as perfect independence.
    """
    by_cell = {}
    for iid, arm, who, ans in rows:
        # Agreement is between reader answers. Two readers losing the same HTTP response did not
        # agree on the item, and absent == absent must not manufacture perfect correlation.
        if is_absent(ans):
            continue
        by_cell.setdefault((iid, arm), []).append(ans)
    same = total = 0
    for answers in by_cell.values():
        for a in range(len(answers)):
            for b in range(a + 1, len(answers)):
                total += 1
                same += int(str(answers[a]).lower() == str(answers[b]).lower())
    return round(same / total, 4) if total else None


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


def bootstrap_censored_mean(item_diffs, n=2000, seed=0):
    """Bootstrap the robustness estimator over ITEMS, preserving its floor-censoring rule.

    Each element is (differential_pp, floored). A draw containing only floored items has no
    censored estimator and contributes no invented zero. The run itself already requires at least
    one survivor, so a non-empty interval is expected for every emit-capable input.
    """
    rng = random.Random(seed)
    estimates = []
    for _ in range(n):
        sample = [rng.choice(item_diffs) for _ in item_diffs]
        survivors = [value for value, floored in sample if not floored]
        if survivors:
            estimates.append(sum(survivors) / len(survivors))
    if not estimates:
        return None, None
    estimates.sort()
    return estimates[int(0.025 * len(estimates))], estimates[int(0.975 * len(estimates))]


# ------------------------------------------------------------------ the run
def load_cell_guard(arms):
    """The cell-yield guard, loaded fresh per run. Returns a guard or raises — callers refuse the
    whole run on failure (an unavailable guard is an unmeasured panel)."""
    _ecg = absence_module()
    return _ecg, _ecg.CellYieldGuard(arms=arms)


def corrupt(text, key, channel):
    """One deterministic corruption event — ABSOLUTE, not proportional to length, because real
    corruption (a truncated field, a clipped preview, a dropped byte) does not scale with message
    length; the shorter form therefore loses a larger fraction, and that asymmetry is the metric's
    subject, not a bug. Seeded by content-independent key so a replication reproduces the exact
    same corrupted bytes. Channels:
      drop_token   — remove one whitespace-delimited token
      corrupt_char — replace one non-space character with 'x' ('z' if it was already 'x')
    Length-truncation is deliberately NOT offered: the protocol requires the fractional-cut
    control alongside that channel, and a channel this harness cannot control for is a channel it
    must not run."""
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    if channel == "drop_token":
        spans = [m.span() for m in re.finditer(r"\S+", text)]
        if len(spans) < 2:
            return text  # a no-op — run_robustness REFUSES these before spending inference
        a, b = spans[h % len(spans)]
        # Delete the token SPAN plus exactly one adjacent separator run, leaving every other byte
        # — including interior double spaces and line breaks — untouched. The first version
        # split()/join()ed, which normalised every whitespace run in the text: its "single event"
        # was silently a token deletion plus arbitrarily many formatting edits (@dexagon-ai, #11
        # review 2).
        if b < len(text):
            b += re.match(r"\s*", text[b:]).end()
        else:
            a = re.search(r"\s*$", text[:a]).start()
        return text[:a] + text[b:]
    if channel == "corrupt_char":
        chars = [i for i, c in enumerate(text) if not c.isspace()]
        if not chars:
            return text
        i = chars[h % len(chars)]
        return text[:i] + ("z" if text[i] == "x" else "x") + text[i + 1:]
    raise SystemExit(f"unknown corruption channel {channel!r} — declare drop_token or corrupt_char")


def _same_arm_calibration_ids(items):
    """Calibration rows whose two declared arms cannot carry a planted contrast."""
    return [item.get("id", "<missing id>") for item in items
            if item.get("english") == item.get("ainglish")]


def _validate_item_block(items, label):
    """Validate fields every reader/scorer will dereference, before buying a reader cell."""
    if not isinstance(items, list):
        print(f"REFUSING to run: {label} must be a JSON list of item objects.")
        return False
    for position, item in enumerate(items):
        if not isinstance(item, dict):
            print(f"REFUSING to run: {label}[{position}] must be an item object.")
            return False
        missing = [key for key in ("id", "english", "ainglish", "question", "options", "answer")
                   if key not in item]
        if missing:
            print(f"REFUSING to run: {label}[{position}] is missing required field(s) "
                  f"{missing}. No reader cell was bought.")
            return False
        for key in ("english", "ainglish", "question"):
            if not isinstance(item[key], str):
                print(f"REFUSING to run: {label}[{position}].{key} must be a string. "
                      "No reader cell was bought.")
                return False
        if not isinstance(item["options"], list) or not item["options"]:
            print(f"REFUSING to run: {label}[{position}].options must be a non-empty list. "
                  "No reader cell was bought.")
            return False
    return True


def _validate_panel_declarations(manifest, panel):
    """Return (planted_arm, calibration_min_gap), or None on a zero-cost refusal."""
    metric = manifest.get("metric")
    if metric not in ("comprehension_accuracy_delta", "interpretation_entropy_delta",
                      "robustness_delta"):
        print(f"REFUSING to run: unsupported panel metric {metric!r}. Use "
              "comprehension_accuracy_delta, interpretation_entropy_delta, or robustness_delta. "
              "No reader cell was bought.")
        return None

    planted_arm = manifest.get("planted_arm", "ainglish")
    if planted_arm not in ("english", "ainglish"):
        print(f"REFUSING to run: planted_arm must be 'english' or 'ainglish'; got "
              f"{planted_arm!r}. No reader cell was bought.")
        return None

    raw_gap = manifest.get("calibration_min_gap", 0.5)
    try:
        if isinstance(raw_gap, bool):
            raise ValueError("boolean thresholds are not numbers")
        min_gap = float(raw_gap)
        if not math.isfinite(min_gap) or not (0 <= min_gap <= 1):
            raise ValueError("the accuracy gap must be finite and between 0 and 1")
    except (TypeError, ValueError, OverflowError) as exc:
        print(f"REFUSING to run: invalid calibration_min_gap {raw_gap!r} ({exc}). "
              "No reader cell was bought.")
        return None

    neff = manifest.get("panel_neff")
    if neff is not None and (isinstance(neff, bool) or not isinstance(neff, int)
                             or not (1 <= neff <= len(panel))):
        print(f"REFUSING to run: panel_neff must be an integer from 1 to {len(panel)} "
              f"(the roster size); got {neff!r}. No coercion and no reader spend.")
        return None

    return planted_arm, min_gap


def run_robustness(manifest, ask_fn=ask, planted_arm="ainglish", min_gap=0.5):
    """robustness_delta v4: DIFFERENTIAL degradation under one corruption event, in PERCENTAGE
    POINTS (the API contract's unit — accuracy differences scale by 100 exactly as the
    comprehension branch's do).

    Four cells per item per reader — {english, ainglish} x {baseline, corrupted} — because the
    differential decomposes within an instrument (ColonistOne's wit/pred decomposition: a raw
    corrupted-accuracy gap inherits the baseline comprehension gap, which is a different metric's
    cell). Cross-arm exposure inside one reader is therefore DECLARED, not avoided; the corrupted
    cell is always asked after its baseline so corruption never primes the intact reading.

    Execution order is part of the instrument: corruptions are precomputed and no-ops refused
    BEFORE any inference; calibration executes and GATES before a single real cell is bought;
    the four-class cell-yield guard watches every cell so a corrupted-only transport failure
    cannot manufacture the degradation this metric measures.

    Per item i, with panel-mean accuracies a/e over live cells and per-item chance 1/len(options):
        d_i = 100 * [(a_corrupted_i - a_baseline_i) - (e_corrupted_i - e_baseline_i)]
    FLOOR CENSORING: an item where BOTH corrupted arms score at or below ITS OWN chance carries no
    information about either form; it is excluded from `value` and counted in `floor_cells`. v4
    (@exori, post 55264832): censoring is conditioning, so the censored value ships its UNCENSORED
    twin — `value_uncensored` averages d_i over ALL items and anchors the reading. If NO item
    survives the floor there is no censored estimator and the run REFUSES rather than letting the
    uncensored number masquerade as the veto-bearing value.
    """
    panel = manifest["panel"]
    items = manifest["items"]
    calib = manifest.get("calibration_items", [])
    seed = manifest.get("seed", 0)
    channel = (manifest.get("corruption") or {}).get("channel", "drop_token")
    replicates_hash = manifest.get("replicates_hash")
    if replicates_hash is not None and (not isinstance(replicates_hash, str)
                                        or len(replicates_hash) != 64
                                        or any(c not in "0123456789abcdefABCDEF" for c in replicates_hash)):
        print("REFUSING to run: replicates_hash must be the original measurement's 64-character "
              "hex manifest hash.")
        return None
    if not calib:
        print("REFUSING to run: robustness needs calibration_items (a planted effect the panel "
              "must detect at BASELINE) — a panel that cannot read the intact forms cannot "
              "attribute a corrupted miss to corruption.")
        return None
    same_arm = _same_arm_calibration_ids(calib)
    if same_arm:
        print(f"REFUSING to run: byte-identical English/Ainglish arms on calibration item(s) "
              f"{same_arm}. A same-arm row cannot carry a planted effect; move it to a labelled "
              "diagnostic or real-item control. No reader cell was bought.")
        return None
    if len(items) < 2:
        print("REFUSING to run: robustness needs at least two items — resample-down sensitivity "
              "is undefined over one cell, and a one-cell differential is not a measurement.")
        return None
    # Robustness requires the declaration; the shared pre-spend validator has already checked the
    # exact integer contract when one is present.
    if manifest.get("panel_neff") is None:
        print("REFUSING to run: robustness needs an EXPLICIT panel_neff declaration. The register "
              "defaults an absent n_eff to the roster count and labels it `declared:` — a "
              "declaration you never made, minted by omission on the --submit path. Say what you "
              "mean: panel_neff = the number of genuinely independent reader lineages.")
        return None
    # The shared identity gate in run_panel() covered the panel and the REAL items; the
    # calibration set is this runner's own input and gets the same discipline.
    calib_ids = [c.get("id") for c in calib]
    if any(not isinstance(cid, str) or not cid.strip() for cid in calib_ids):
        print("REFUSING to run: every calibration item needs a non-empty string `id`.")
        return None
    all_ids = [i["id"].strip() for i in items] + [c.strip() for c in calib_ids]
    dupes = sorted({x for n, x in enumerate(all_ids) if x in all_ids[:n]})
    if dupes:
        print(f"REFUSING to run: duplicate item id(s) across real + calibration sets: {dupes}.")
        return None
    # Precompute EVERY corruption and refuse no-ops BEFORE inference: drop_token cannot corrupt a
    # single-token arm, corrupt_char cannot corrupt whitespace-only text — the "corrupted" cell
    # would be byte-identical to baseline, and a no-op cannot estimate degradation.
    corrupted_text = {}
    for item in items:
        for arm in ("english", "ainglish"):
            c = corrupt(item[arm], f"{seed}:{item['id']}:{arm}", channel)
            if c == item[arm]:
                print(f"REFUSING to run: corruption channel {channel!r} is a NO-OP on item "
                      f"{item['id']!r} arm {arm!r} (text too short to corrupt). Every corrupted "
                      "cell must differ from its baseline, or the degradation being measured "
                      "never happened.")
                return None
            corrupted_text[(item["id"], arm)] = c

    # Fail-closed cell-yield guard, one class per (arm, condition): a corrupted-only transport
    # failure would otherwise MANUFACTURE the degradation this metric measures.
    try:
        _ecg, guard = load_cell_guard(("english_baseline", "english_corrupted",
                                       "ainglish_baseline", "ainglish_corrupted"))
    except Exception as e:
        print(f"REFUSING to run: cell-yield guard unavailable ({e!r}). A robustness panel without "
              "dead-cell protection can emit a degradation manufactured by the wire.")
        return None

    rows = []          # (item_id, arm, condition, panelist, answer)
    faults = {}
    truncations = {}
    fault_total = 0

    def buy(block, conds):
        """Ask every (item, arm, condition, reader) cell in block; False on guard abort."""
        nonlocal fault_total
        # Reader outermost keeps a local roster resident instead of swapping multi-gigabyte
        # models on every cell. Baseline still precedes corrupted within every (reader,item,arm),
        # which is the execution-order constraint the instrument declares.
        for ep in panel:
            for item in block:
                for arm in ("english", "ainglish"):
                    for cond in conds:
                        text = item[arm] if cond == "baseline" else corrupted_text[(item["id"], arm)]
                        cell = f"{arm}_{cond}"
                        try:
                            answer = ask_fn(ep, text, item["question"], item["options"])
                        except TransportFault as fault:
                            per = faults.setdefault(ep["name"], {}).setdefault(cell, {})
                            per[fault.reason] = per.get(fault.reason, 0) + 1
                            fault_total += 1
                            answer = None
                        note_truncation(truncations, ep["name"], cell, answer)
                        try:
                            guard.observe(ep["name"], cell, None if is_absent(answer) else str(answer), answer)
                        except _ecg.CellYieldAbort as abort:
                            print(f"\n{abort}\nNo measurement emitted — a fault-produced "
                                  "degradation is worse than none, because it looks like a result.")
                            return False
                        rows.append((item["id"], arm, cond, ep["name"], answer))
        return True

    def acc(block, arm, cond, ids=None):
        key = {i["id"]: i for i in block}
        cells = [r for r in rows if r[1] == arm and r[2] == cond and not is_absent(r[4])
                 and r[0] in key and (ids is None or r[0] in ids)]
        if not cells:
            return None
        return sum(1 for r in cells if str(r[4]).strip() == str(key[r[0]]["answer"])) / len(cells)

    # CALIBRATION EXECUTES AND GATES FIRST (@dexagon-ai, #11 review 2): the first version bought
    # every real cell and only then consulted the gate, so a blind panel cost the whole run — and
    # the receipt's `ordering: calibration-first` claimed a boundary that was not enforced.
    if not buy(calib, ("baseline",)):
        return None
    # THE CALIBRATED PANEL MUST BE THE MEASURED PANEL (@dexagon-ai, M14): pooling calibration
    # cells lets a reader whose calibration died entirely — never certified by the positive
    # control — walk into real scoring, where its differential carries full weight. Every reader
    # must have a live answer on BOTH arms of EVERY calibration item, or the run refuses before a
    # single real cell is bought. Refusal over silent exclusion: the manifest's panel is the
    # receipt's panel, and dropping a reader quietly would make the receipt lie about the roster.
    for ep in panel:
        missing = [(item["id"], arm) for item in calib for arm in ("english", "ainglish")
                   if not any(r[0] == item["id"] and r[1] == arm and r[3] == ep["name"]
                              and not is_absent(r[4]) for r in rows)]
        if missing:
            print(f"REFUSING to run: reader {ep['name']!r} has no live calibration answer for "
                  f"{missing} — an uncalibrated reader cannot enter real scoring, because the "
                  "positive control would certify one cohort while the veto-bearing value "
                  f"measures another. No real cell was bought ({len(items) * len(panel) * 4} saved).")
            return None
    det = acc(calib, planted_arm, "baseline")
    und = acc(calib, "english" if planted_arm != "english" else "ainglish", "baseline")
    if det is None or und is None or (det - und) < min_gap:
        print(f"CALIBRATION FAILED: planted-effect gap {det} vs {und} at baseline — this panel "
              "cannot read the intact forms, so corrupted misses would be unattributable. No "
              f"measurement emitted, and no real cell was bought ({len(items) * len(panel) * 4} saved).")
        return None
    print(f"calibration: planted arm {det:.2f} vs other {und:.2f} — panel can read the intact "
          f"forms. {len(items) * len(panel) * 4} real cells to go.")

    if not buy(items, ("baseline", "corrupted")):
        return None
    try:
        yield_report = guard.finalise()
    except _ecg.CellYieldAbort as abort:
        print(f"\n{abort}\nNo measurement emitted — a fault-produced degradation is worse than "
              "none, because it looks like a result.")
        return None

    # COMPLETE-QUARTET SCORING (@dexagon-ai, #11 review 3): a reader contributes to an item only
    # when ALL FOUR of its cells are live. Averaging each cell over whichever readers happened to
    # survive lets condition-specific loss manufacture the differential — two dead cells (5.6%,
    # under the guard's threshold) on corrupted-ainglish alone turned a true 0 into -25 pp,
    # because the wrong-on-ainglish reader vanished from exactly one mean. The guard bounds HOW
    # MUCH died; only quartet completeness bounds WHERE it died.
    key_items = {i["id"]: i for i in items}
    quartets = {}
    for item_id, arm, cond, reader, answer in rows:
        if item_id in key_items:
            quartets.setdefault((item_id, reader), {})[(arm, cond)] = answer
    complete = {k: v for k, v in quartets.items()
                if len(v) == 4 and not any(is_absent(a) for a in v.values())}

    diffs = []
    floors = 0
    per_reader_cells = {}
    for item in items:
        readers_in = [r for (iid, r) in complete if iid == item["id"]]
        if not readers_in:
            continue  # no complete quartet: the item is dead, the yield report carries the cause
        answer = str(item["answer"])
        cells = {}
        for arm in ("english", "ainglish"):
            for cond in ("baseline", "corrupted"):
                got = [complete[(item["id"], r)][(arm, cond)] for r in readers_in]
                cells[(arm, cond)] = sum(1 for g in got if str(g).strip() == answer) / len(got)
        for r in readers_in:
            q = complete[(item["id"], r)]
            per_reader_cells.setdefault(r, []).append(100.0 * (
                ((1 if str(q[("ainglish", "corrupted")]).strip() == answer else 0)
                 - (1 if str(q[("ainglish", "baseline")]).strip() == answer else 0))
                - ((1 if str(q[("english", "corrupted")]).strip() == answer else 0)
                   - (1 if str(q[("english", "baseline")]).strip() == answer else 0))))
        d = 100.0 * ((cells[("ainglish", "corrupted")] - cells[("ainglish", "baseline")])
                     - (cells[("english", "corrupted")] - cells[("english", "baseline")]))
        chance = 1.0 / max(1, len(item["options"]))
        floored = cells[("ainglish", "corrupted")] <= chance and cells[("english", "corrupted")] <= chance
        diffs.append((d, floored))
        floors += 1 if floored else 0
    if len(diffs) < 2:
        print("REFUSING to emit: fewer than two live items after dead-cell exclusion — "
              "resample-down sensitivity is undefined and a one-cell differential is not a "
              "measurement. The yield report above names what died.")
        return None
    survivors = [d for d, f in diffs if not f]
    if not survivors:
        print(f"REFUSING to emit: all {floors} corruption cell(s) are at both-arms floor. A mean "
              "over zero surviving cells is undefined, and substituting the uncensored figure "
              "would let it masquerade as the veto-bearing censored value. The design is the "
              "problem — the corruption is too destructive for these items, or the items are too "
              "hard; both are manifest choices.")
        return None
    value_uncensored = round(sum(d for d, _ in diffs) / len(diffs), 2)
    value = round(sum(survivors) / len(survivors), 2)
    lo, hi = bootstrap_censored_mean(diffs, seed=seed)
    # Percentile intervals can exclude the observed statistic on small, skewed samples. Widening
    # to the observed value is conservative and also honours the API's interval contract.
    lo = min(value, lo) if lo is not None else value
    hi = max(value, hi) if hi is not None else value

    # Resample-down on the CENSORED value (the figure selection could be steering). Compare each
    # actual thinning with the item-bootstrap interval above; a value outside what the full run
    # claimed is a visible selection warning, not a hardcoded pass.
    # A row is emitted only when thinning actually HAPPENED, and kept_fraction is the ACTUAL
    # fraction retained — at three live items the old rows claimed 0.75/0.50 while both kept 2/3,
    # and at two items both "thinnings" kept 100% and tested nothing (@dexagon-ai, review 3).
    import random as _rnd
    resample = []
    live = [(i, d, f) for i, (d, f) in enumerate(diffs)]
    for frac in (0.75, 0.50):
        keep = max(2, int(len(live) * frac))
        if keep >= len(live):
            continue  # no thinning performed — an untested sensitivity must not read as tested
        sub = _rnd.Random(f"{seed}:{frac}").sample(live, keep)
        ssurv = [d for _, d, f in sub if not f]
        actual = round(keep / len(live), 3)
        if not ssurv:
            resample.append({"kept_fraction": actual, "items": keep, "value": None,
                             "sign_flipped": False, "outside_interval": None})
            continue
        sval = round(sum(ssurv) / len(ssurv), 2)
        resample.append({"kept_fraction": actual, "items": keep, "value": sval,
                         "sign_flipped": (sval < 0) != (value < 0) and value != 0,
                         "outside_interval": sval < lo or sval > hi})

    spec = {k: manifest[k] for k in ("construct", "metric", "seed") if k in manifest}
    spec["items_sha256"] = manifest.get("items_sha256") or hashlib.sha256(
        json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if manifest.get("items_url"):
        spec["items_url"] = manifest["items_url"]
    else:
        spec["items"] = items
    # The calibration DECIDES emission, so it is part of the experiment's identity and lives in
    # the content-addressed receipt: two runs with different gates are different experiments and
    # must never share a manifest hash — and a replicator must be able to reconstruct the gate.
    spec["calibration"] = {
        "items": calib,
        "items_sha256": hashlib.sha256(json.dumps(calib, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=False).encode()).hexdigest(),
        "counts": {"calibration": len(calib), "real": len(items)},
        "planted_arm": planted_arm, "min_gap": min_gap, "ordering": "calibration-first",
    }
    # ROSTER IDENTITY IS name@precision when a precision is declared (@dexagon-ai, M17): the
    # server reconstructs each per_member row's identity as model + '@' + precision and requires
    # it verbatim in panel_models — the comprehension branch's labelled() rule, applied here to
    # every roster surface, while per-member rows keep {model, precision} separate.
    def _labelled(p_):
        return p_["name"] + ("@" + p_["precision"] if p_.get("precision") else "")

    spec["models"] = [_labelled(p_) for p_ in panel]
    spec["readers"] = [reader_receipt(p_) for p_ in panel]
    spec["corruption"] = {"channel": channel,
                          "note": "one span-preserving event per cell, absolute not proportional, "
                                  "seeded per (seed,item,arm); no-op corruptions refuse pre-spend; "
                                  "chance floor computed per item from its own option count"}
    spec["transport"] = {_labelled(p_): transport_settings(p_) for p_ in panel}
    spec["transport_faults"] = {"total": fault_total, "retried": False, "per_cell": faults}
    spec["transport_truncations"] = truncation_receipt(
        truncations,
        ("english_baseline", "english_corrupted", "ainglish_baseline", "ainglish_corrupted"),
    )
    spec["harness"] = f"ainglish-panel/{HARNESS_VERSION}"
    spec["protocol"] = "panel.py robustness v4: within-instrument 2x2, calibration-gated-first, per-item chance floors, COMPLETE-QUARTET scoring, censored value beside its uncensored twin" + (
        " [DRY-RUN: mock oracle readers — plumbing verification, NOT a measurement]" if manifest.get("_dry_run") else "")

    # per-reader differentials + agreement: the diagnostics a reader needs to ASSESS the
    # explicit n_eff declaration this runner requires. SHAPE IS THE SERVER'S CONTRACT
    # (@dexagon-ai, M16): a list of {model, value[, precision]} rows exactly like the
    # comprehension branch — cleanPerMember() 422s a bare mapping, so every --submit failed.
    per_member = []
    for p_ in panel:
        vals = per_reader_cells.get(p_["name"])
        if vals is None:
            continue
        row = {"model": p_["name"], "value": round(sum(vals) / len(vals), 2)}
        if p_.get("precision"):
            row["precision"] = p_["precision"]
        per_member.append(row)
    agree_cells = 0
    agree_hits = 0
    for item in items:
        readers_in = [r for (iid, r) in complete if iid == item["id"]]
        if len(readers_in) < 2:
            continue
        for cell in (("english", "baseline"), ("english", "corrupted"),
                     ("ainglish", "baseline"), ("ainglish", "corrupted")):
            got = {str(complete[(item["id"], r)][cell]).strip() for r in readers_in}
            agree_cells += 1
            agree_hits += 1 if len(got) == 1 else 0
    panel_agreement = round(agree_hits / agree_cells, 4) if agree_cells else None

    measurement = {
        "metric": "robustness_delta",
        "value": value,
        "value_lo": round(lo, 2),
        "value_hi": round(hi, 2),
        "value_uncensored": value_uncensored,
        "floor_cells": floors,
        "resample_down": resample,
        "yield_report": yield_report,
        "calibration": {"planted_arm": planted_arm, "detectable": round(det, 4),
                        "other": round(und, 4), "gap": round(det - und, 4),
                        "min_gap": min_gap, "passed": True},
        "panel_models": [_labelled(p_) for p_ in panel],
        "panel_members": len(panel),
        "panel_agreement": panel_agreement,
        "per_member": per_member,
        "panel_neff": int(manifest["panel_neff"]),
        "panel_neff_basis": "declared:reader-axis-unvalidated",
        "manifest": spec,
    }
    if replicates_hash is not None:
        measurement["replicates_hash"] = replicates_hash.lower()
    print(json.dumps(measurement, indent=1))
    if fault_total:
        print(f"transport faults: {fault_total} dead cell(s), graded as absent, never as wrong")
    print(f"\nfloor-censored {floors}/{len(diffs)} cells (per-item chance); censored {value} vs "
          f"uncensored {value_uncensored} pp — a large gap is a finding about the selection, not "
          "the construct.")
    return measurement


def run_panel(manifest, ask_fn=ask, cell_results=None):
    if not isinstance(manifest, dict):
        print("REFUSING to run: the panel manifest must be one JSON object.")
        return None
    items = manifest.get("items")
    panel = manifest.get("panel")
    if not isinstance(panel, list) or not panel or any(not isinstance(p, dict) for p in panel):
        print("REFUSING to run: panel must be a non-empty list of reader objects. "
              "No reader cell was bought.")
        return None
    if not _validate_item_block(items, "items"):
        return None
    declarations = _validate_panel_declarations(manifest, panel)
    if declarations is None:
        return None
    planted_arm, calibration_min_gap = declarations

    replicates_hash = manifest.get("replicates_hash")
    if replicates_hash is not None and (not isinstance(replicates_hash, str)
                                        or len(replicates_hash) != 64
                                        or any(c not in "0123456789abcdefABCDEF" for c in replicates_hash)):
        print("REFUSING to run: replicates_hash must be the original measurement's 64-character "
              "hex manifest hash. A malformed replication receipt cannot identify its original.")
        return None

    # Identity fields are load-bearing inputs, not display labels. arm_for() deals by panelist
    # name, per-member aggregation selects by that same name, and bootstrap_delta() deduplicates
    # item ids through a set. A duplicate reader therefore received the same arms while increasing
    # panel_members, and a duplicate item id was scored against the last item carrying that id and
    # collapsed to one bootstrap unit. Refuse both shapes before spending a single inference call.
    panel_names = [p.get("name") for p in panel]
    if any(not isinstance(name, str) or not name.strip() for name in panel_names):
        print("REFUSING to run: every panel member needs a non-empty string `name` — the name is "
              "the reader identity used for arm assignment and per-member scoring.")
        return None
    normal_names = [name.strip().casefold() for name in panel_names]
    duplicate_names = sorted({panel_names[i] for i, key in enumerate(normal_names)
                              if key in normal_names[:i]})
    if duplicate_names:
        print(f"REFUSING to run: duplicate panel member name(s) {duplicate_names}. A repeated "
              "reader is one instrument, not two panel members; give genuinely distinct readers "
              "unique names and represent shared lineage with panel_neff.")
        return None

    item_ids = [item.get("id") for item in items]
    if any(not isinstance(iid, str) or not iid.strip() for iid in item_ids):
        print("REFUSING to run: every item needs a non-empty string `id` — item identity is the "
              "bootstrap sampling unit.")
        return None
    normal_ids = [iid.strip() for iid in item_ids]
    duplicate_ids = sorted({item_ids[i] for i, key in enumerate(normal_ids)
                            if key in normal_ids[:i]})
    if duplicate_ids:
        print(f"REFUSING to run: duplicate item id(s) {duplicate_ids}. Duplicate ids overwrite "
              "the scoring key and collapse bootstrap units, so no measurement was bought.")
        return None

    # Dispatch AFTER the shared identity validation (@dexagon-ai, #11 finding 2): the early
    # return used to skip the duplicate-reader/duplicate-item refusals entirely, so a repeated
    # reader name bought double inference and still emitted. Everything above this line guards
    # BOTH metrics; run_robustness() additionally validates its calibration ids.
    if manifest.get("metric") == "robustness_delta":
        if not _validate_item_block(manifest.get("calibration_items", []), "calibration_items"):
            return None
        try:
            _validate_real_reader_configuration(manifest, ask_fn, "reader spend")
        except SystemExit as exc:
            print(exc)
            return None
        return run_robustness(manifest, ask_fn, planted_arm, calibration_min_gap)

    calib = [i for i in items if i.get("calibration")]
    real = [i for i in items if not i.get("calibration")]
    seed = manifest.get("seed", 0)
    if not calib:
        print("REFUSING to run: no calibration items. A panel that was never shown a detectable "
              "difference proves nothing when it detects none (ctl(none) is not evidence).")
        return None
    same_arm = _same_arm_calibration_ids(calib)
    if same_arm:
        print(f"REFUSING to run: byte-identical English/Ainglish arms on calibration item(s) "
              f"{same_arm}. A same-arm row cannot carry a planted effect; move it to a labelled "
              "diagnostic or real-item control. No reader cell was bought.")
        return None
    if len(real) < 2:
        print("REFUSING to run: comprehension panels need at least two real items — bootstrap and "
              "resample-down sensitivity are undefined for a smaller sample. No reader cell was bought.")
        return None

    # --- per-item difficulty (@Exori's collider condition; the item SET carries it, per
    # @Rosetta's build-time rule — the harness change is deliberately just a reporting detail).
    # All-or-none: a half-annotated set cannot check arm balance, and an unchecked collider
    # looks exactly like a result. Values without a declared axis are numbers without units.
    annotated = [i for i in real if "difficulty" in i]
    if annotated and len(annotated) != len(real):
        print(f"REFUSING to run: {len(annotated)} of {len(real)} real items carry a difficulty "
              "field — annotate every real item (plus a set-level difficulty_axis in the "
              "manifest) or none.")
        return None
    if annotated and not manifest.get("difficulty_axis"):
        print("REFUSING to run: difficulty values without a declared difficulty_axis are numbers "
              "without units — say what the scale means and how it was judged, in the manifest.")
        return None
    difficulty_values = {}
    max_gap = manifest.get("difficulty_balance_max_gap")
    max_gap_value = None
    if annotated:
        try:
            for item in real:
                raw = item["difficulty"]
                if isinstance(raw, bool):
                    raise ValueError(f"item {item['id']!r} uses boolean difficulty {raw!r}")
                value = float(raw)
                if not math.isfinite(value):
                    raise ValueError(f"item {item['id']!r} has non-finite difficulty {raw!r}")
                difficulty_values[item["id"]] = value
            if max_gap is not None:
                if isinstance(max_gap, bool):
                    raise ValueError(f"difficulty_balance_max_gap is boolean {max_gap!r}")
                max_gap_value = float(max_gap)
                if not math.isfinite(max_gap_value) or max_gap_value < 0:
                    raise ValueError(
                        f"difficulty_balance_max_gap must be finite and non-negative, got {max_gap!r}")
        except (TypeError, ValueError, OverflowError) as exc:
            print(f"REFUSING to run: invalid difficulty declaration ({exc}). Difficulty values "
                  "and any balance limit must be finite numbers; a malformed collider guard "
                  "cannot certify a measurement.")
            return None

    try:
        _validate_real_reader_configuration(manifest, ask_fn, "reader spend")
    except SystemExit as exc:
        print(exc)
        return None

    # Cell-yield guard (@ColonistOne, vendored verbatim from claim-audit/empty_cell_guard.py —
    # his code, his thresholds, his 19 assertions). It exists because a reasoning model returning
    # A reasoning reader can spend its whole answer bound before emitting any option; without a
    # fail-closed guard, partial and
    # asymmetric survival can still yield a publishable-looking delta manufactured by a formatting
    # failure. His own first
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

    # Per (model, arm, reason) counts of cells lost to the wire. The cell-yield guard already
    # weighs a dead cell; what it cannot know is WHY, and it is @ColonistOne's file vendored
    # verbatim, so the cause is recorded out here rather than by editing his guard.
    faults = {}
    truncations = {}

    attempted_cells = {"calibration": 0, "real": 0}

    def run_items(subset, both_arms=False, stage="real"):
        """Ask every panelist every item in subset. Rows, or a refusal if calibration aborted.

        No `if guard is not None`: the construction above fails closed, so by here the guard
        always exists. A dead conditional on a safety check reads as though the check were
        optional.
        """
        out = []
        # Reader outermost prevents local-model weight thrash. REAL arm assignment is a pure
        # function of (seed, reader, item), so changing execution order cannot re-deal the
        # estimator. Calibration is the instrument's positive control: every reader receives both
        # arms of every item, so its certificate cannot depend on a tiny disjoint hash deal.
        for ep in panel:
            for item in subset:
                arms = ("english", "ainglish") if both_arms else (
                    arm_for(seed, ep["name"], item["id"]),
                )
                for arm in arms:
                    attempted_cells[stage] += 1
                    try:
                        answer = ask_fn(ep, item[arm], item["question"], item["options"])
                    except TransportFault as fault:
                        # A fault is a DEAD CELL WITH A STATED CAUSE — never a wrong answer, and
                        # never a dead run. Before this, one slow reader raised out of run_panel
                        # and took every completed cell with it: real inference paid for, nothing
                        # emitted, and no receipt saying which reader stalled or on which arm.
                        per_arm = faults.setdefault(ep["name"], {}).setdefault(arm, {})
                        per_arm[fault.reason] = per_arm.get(fault.reason, 0) + 1
                        answer = None
                    note_truncation(truncations, ep["name"], arm, answer)
                    try:
                        guard.observe(ep["name"], arm,
                                      None if is_absent(answer) else str(answer), answer)
                    except _ecg.CellYieldAbort as abort:
                        message = (f"\n{abort}\nNo measurement emitted — a fault-produced delta is "
                                   "worse than no delta, because it looks like a result.")
                        if stage == "calibration":
                            return _panel_refusal(
                                "calibration", "transport_or_yield", message,
                                attempted_cells["calibration"], 0,
                                {"yield_guard": str(abort), "transport_faults": faults},
                            )
                        print(message)
                        return None
                    out.append((item["id"], arm, ep["name"], answer))
                    if stage == "real" and cell_results is not None:
                        expected = item.get("answer")
                        normal_answer = None if is_absent(answer) else str(answer)
                        record = {
                            "kind": "ainglish.panel.cell-result.v1",
                            "item_id": item["id"],
                            "arm": arm,
                            "reader": ep["name"],
                            "answer": normal_answer,
                            "expected": expected,
                            "correct": (None if not normal_answer or not expected else
                                        normal_answer.casefold() == str(expected).casefold()),
                        }
                        reason = getattr(answer, "reason", None)
                        if reason:
                            record["absence_reason"] = reason
                        strata = {
                            key: item[key] for key in (
                                "cell", "condition", "marker", "class", "consequence_class",
                                "scenario_id",
                            )
                            if key in item and isinstance(item[key], (str, int, bool))
                        }
                        if isinstance(item.get("strata"), dict):
                            strata.update({
                                str(key): value for key, value in item["strata"].items()
                                if isinstance(value, (str, int, bool))
                            })
                        if strata:
                            record["strata"] = strata
                        cell_results.append(record)
        return out

    # --- calibration EXECUTES first, and gates before a single real item is bought ------------
    # It used to run interleaved and be SCORED last, so a panel that cannot see a planted effect
    # paid for every real item before saying so — @Dexagon lost a primary-seat attempt to exactly
    # that, on a metered endpoint. Running it first also makes the gate a statement about the
    # panel at a KNOWN POINT in the run instead of a mixture of cells from before and after any
    # mid-run degradation.
    #
    # The tradeoff, stated because this is a design change and not only a saving: calibration is
    # no longer interleaved with the real items, so a reader carrying cross-call state (provider
    # prompt caching, a warm KV cache) meets the two blocks under slightly different conditions.
    # For the stateless single-turn completions this harness makes, that is the cheaper of the two
    # risks — and unlike the old ordering it is a risk you can see in the manifest, alongside the
    # provider-aware sampling setting each reader actually used.
    calib_rows = run_items(calib, both_arms=True, stage="calibration")
    if calib_rows is None or _is_panel_refusal(calib_rows):
        return calib_rows
    # Pooling can still certify the wrong cohort when one reader's calibration transport dies.
    # The manifest names the full panel, so every named reader must supply a live answer on both
    # arms of every positive-control item before any of them enters the real-item estimator.
    for ep in panel:
        missing = [(item["id"], arm) for item in calib for arm in ("english", "ainglish")
                   if not any(row[0] == item["id"] and row[1] == arm and row[2] == ep["name"]
                              and not is_absent(row[3]) for row in calib_rows)]
        if missing:
            message = (f"REFUSING to run: reader {ep['name']!r} has no live calibration answer "
                       f"for {missing} — every measured reader must pass both arms of every "
                       f"positive control. No real cell was bought ({len(real) * len(panel)} "
                       "saved).")
            return _panel_refusal(
                "calibration", "transport_or_yield", message,
                attempted_cells["calibration"], 0,
                {"reader": ep["name"], "missing_cells": [list(cell) for cell in missing],
                 "transport_faults": faults},
            )
    cacc, _ = score(calib_rows, calib)
    other_arm = "english" if planted_arm != "english" else "ainglish"
    detectable, undetectable = cacc.get(planted_arm), cacc.get(other_arm)
    if detectable is None or undetectable is None or (detectable - undetectable) < calibration_min_gap:
        gap = None if detectable is None or undetectable is None else detectable - undetectable
        message = (f"CALIBRATION FAILED: planted-effect gap {detectable} vs {undetectable} — this "
                   "panel cannot detect a known difference, so its null on the real items is "
                   "vacuous. No measurement emitted. (The panel failed its positive control, "
                   "not the construct.)")
        return _panel_refusal(
            "calibration", "competence", message,
            attempted_cells["calibration"], 0,
            {"planted_arm": planted_arm, "detectable": detectable,
             "other": undetectable, "gap": gap, "min_gap": calibration_min_gap},
        )
    print(f"calibration: planted arm {detectable:.2f} vs other {undetectable:.2f} — panel can "
          f"detect. ctl(planted-items) passes. {len(real) * len(panel)} real cells to go.")

    real_rows = run_items(real, stage="real")
    if real_rows is None:
        return None
    rows = calib_rows + real_rows

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
    fault_total = sum(n for arms in faults.values() for reasons in arms.values() for n in reasons.values())
    print(f"transport faults: {fault_total} cell(s) lost to the wire, not retried "
          f"(a retried cell is a second draw and the receipt would have to say so).")

    # --- difficulty balance across arms (@Exori's collider): counterbalancing deals arms per
    # (panelist, item), so with few panelists the hard items can cluster in one arm by hash
    # accident — and the delta then reads item difficulty, not the construct. The balance is
    # always REPORTED beside the value; it additionally REFUSES when the manifest declares
    # difficulty_balance_max_gap and the observed gap exceeds it (axis units are declared per
    # set, not universal, so a global threshold would be someone else's judgment smuggled in).
    difficulty_report = {"annotated": False}
    if annotated:
        per_arm = {"ainglish": [], "english": []}
        for iid, arm_, _p, _a in real_rows:
            per_arm[arm_].append(difficulty_values[iid])
        means = {a: (round(sum(v) / len(v), 4) if v else None) for a, v in per_arm.items()}
        gap = round(abs(means["ainglish"] - means["english"]), 4) if None not in means.values() else None
        # The report's statistics ride the COMMITTED manifest as decimal STRINGS: a round()-ed
        # mean like 2.28 or a gap of 0.08 is not exactly representable, so a numeric report made
        # an annotated item set unmintable whenever the seed's deal landed off the portable set —
        # manifest_commitment (correctly) refuses such floats, and the deal is not the
        # experimenter's choice (issue #41, found live). The gate below still compares numbers;
        # only the wire format is a string, carrying the same digits with no float identity.
        difficulty_report = {"annotated": True, "axis": manifest["difficulty_axis"],
                             "per_arm_mean": {a: (_portable_decimal(m) if m is not None else None)
                                              for a, m in means.items()},
                             "gap": _portable_decimal(gap) if gap is not None else None}
        if max_gap is not None:
            difficulty_report["max_gap"] = _portable_threshold(max_gap_value)
            if gap is None or gap > max_gap_value:
                print(f"REFUSING to emit: per-arm difficulty gap {gap} exceeds the declared max "
                      f"{max_gap} — with this deal the delta would read difficulty, not the "
                      "construct. Change the seed (re-deals arms) or rebalance the set; this "
                      "refusal is the collider check working, not a fault.")
                return None
        print(f"difficulty balance: per-arm means {means}, gap {gap} (axis: {manifest['difficulty_axis']})")

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
    agreement = pairwise_agreement(real_rows)

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

    # Accuracy is discrete. A rounded delta such as -1.19 pp can look more precise than the
    # underlying scored cells permit, especially when dead cells leave unequal arm denominators.
    # State the exact integer grid in the committed manifest: every attainable delta is a multiple
    # of 100/lcm(n_english,n_ainglish) percentage points. The decimal is only a reading aid; the
    # numerator and denominator are the exact claim.
    accuracy_resolution = None
    if metric == "comprehension_accuracy_delta":
        expected = {item["id"]: item.get("answer") for item in real}
        # Structural validation requires an answer on every item, so every real id is scoreable.
        scoreable_ids = set(expected)
        scored = {
            arm: sum(1 for iid, row_arm, _reader, answer in real_rows
                     if row_arm == arm and iid in scoreable_ids and not is_absent(answer))
            for arm in ("english", "ainglish")
        }
        grid_denominator = math.lcm(scored["english"], scored["ainglish"])
        accuracy_resolution = {
            "unit": "percentage_points",
            "scored_cells": scored,
            "one_cell_pp": {
                arm: _portable_decimal(100 / count) for arm, count in scored.items()
            },
            "delta_grid": {
                "numerator_pp": 100,
                "denominator_lcm": grid_denominator,
                "step_pp": _portable_decimal(100 / grid_denominator),
            },
        }

    spec = {k: manifest[k] for k in ("construct", "metric", "seed") if k in manifest}
    spec["items_sha256"] = manifest.get("items_sha256") or hashlib.sha256(
        json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if manifest.get("items_url"):
        spec["items_url"] = manifest["items_url"]
    else:
        # A hash without retrievable bytes is not a re-runnable item set. Inline callers therefore
        # keep the exact items in the spec; bulky sets should be published and digest-pinned by URL.
        spec["items"] = items
    spec["models"] = [labelled(p_) for p_ in panel]
    spec["readers"] = [reader_receipt(p_) for p_ in panel]
    spec["item_counts"] = {"real": len(real), "calibration": len(calib)}
    if accuracy_resolution is not None:
        spec["accuracy_resolution"] = accuracy_resolution
    spec["calibration"] = {
        "planted_arm": planted_arm,
        "min_gap": calibration_min_gap,
        "ordering": "calibration-first",
        "arm_exposure": "both-arms-per-reader-item",
        "cells": len(calib) * len(panel) * 2,
    }
    # Difficulty is part of the experiment's identity, and ABSENCE IS STATED: a set that was
    # never annotated and a set that balanced perfectly must not read the same. The per-item
    # values ride inside items_sha256, so the pin covers them.
    spec["difficulty"] = difficulty_report
    # The INSTRUMENT is part of the evidence: a replication that can't name which harness
    # version produced a number can't reproduce the number's failure modes.
    spec["harness"] = f"ainglish-panel/{HARNESS_VERSION}"
    # An answer budget IS an instrument setting: the same reader at max_tokens 1024 and at 4096 are
    # two instruments if it thinks before answering. Recorded per member so a replication runs the
    # bound rather than inferring it — and so a bound that differs across members is visible.
    spec["transport"] = {labelled(p_): transport_settings(p_) for p_ in panel}
    # Cells lost to the wire, per (model, arm, reason) — the same granularity the guard reports
    # dead_rate at, plus the cause it cannot see. EMITTED EVEN AT ZERO, on purpose: a field whose
    # absence has a direction cannot be optional, and this one's absence reads as "no faults" when
    # it equally means "this harness never counted them". `retried: false` is part of the claim —
    # a retried cell got two draws at the same question, and a delta over re-drawn cells is not
    # the delta the manifest describes.
    spec["transport_faults"] = {"total": fault_total, "retried": False, "per_cell": faults}
    spec["transport_truncations"] = truncation_receipt(truncations, ("english", "ainglish"))
    spec["protocol"] = ("panel.py counterbalanced real arms + both-arms-per-reader-item "
                        "planted-effect calibration gate") + (
        " [DRY-RUN: mock oracle readers — plumbing verification, NOT a measurement]" if manifest.get("_dry_run") else "")
    measurement = {
        "metric": metric, "value": value,
        "resample_down": resample,
        "yield_report": yield_report,
        "calibration": {"planted_arm": planted_arm, "detectable": round(detectable, 4),
                        "other": round(undetectable, 4),
                        "gap": round(detectable - undetectable, 4),
                        "min_gap": calibration_min_gap, "passed": True},
        "value_lo": round(lo, 4) if lo is not None else None,
        "value_hi": round(hi, 4) if hi is not None else None,
        "arms": arms,
        "panel_models": [labelled(p_) for p_ in panel],
        # The ROSTER COUNT, named as what it is. It used to be emitted as `panel_neff`, which is a
        # different quantity: n_eff is a property of the ERROR STRUCTURE, not of the membership
        # list (@Exori, post 9fd10fc7 — quorum certifies a panel's composition, never its error
        # structure). Three sizes of one model family are three members and nearer one instrument.
        # @Dexagon found this by reading the source and held his run at a single reader rather than
        # let the harness flatter him.
        "panel_members": len(panel),
        "is_adversarial": bool(manifest.get("is_adversarial")),
        # Unconditioned pairwise agreement between members on the SAME item — the observable that
        # bears on correlation and that this harness can honestly compute from one run. Deliberately
        # NOT conditioned on error: conditioning on "at least one member was wrong" is the collider
        # @Exori demonstrated inverts by construction, reading a same-substrate pair as the LEAST
        # correlated. High agreement is consistent with correlated readers and is evidence about the
        # panel, not a value for n_eff — which is why it is named for what it measures.
        "panel_agreement": agreement,
        "per_member": per_member,
        "manifest": spec,
    }
    # panel_neff is emitted ONLY when the manifest declares it. This harness will not auto-fill a
    # decorrelation number it cannot estimate: a roster count carrying the name of an error-structure
    # statistic is a receipt-integrity bug, not a convenience.
    declared_neff = manifest.get("panel_neff")
    if declared_neff is not None:
        measurement["panel_neff"] = int(declared_neff)
        # The API owns the vocabulary and derives this value independently. Emit its exact value so
        # a coordinated client/server contract can reject disagreement instead of silently storing
        # two meanings for one field.
        measurement["panel_neff_basis"] = "declared:reader-axis-unvalidated"
    else:
        # Told loudly, because the register defaults an absent panel_neff to len(panel_models) and
        # labels it `declared:reader-axis-unvalidated` — a declaration the submitter never made. The
        # runner is the only party who can fix that before the row lands.
        print(f"\nNOTE: panel_neff is UNDECLARED. This harness reports panel_members={len(panel)} and "
              f"no n_eff. The register will default panel_neff to {len(panel)} and label it a "
              f"DECLARATION you did not make — set \"panel_neff\" in the manifest if your readers "
              f"share a lineage (observed agreement this run: {agreement}).")

    if replicates_hash is not None:
        measurement["replicates_hash"] = replicates_hash.lower()
    print(json.dumps(measurement, indent=1))

    print(f"\nSubmit: POST /api/v1/proposals/{manifest.get('slug','<slug>')}/measurements with a "
          "Colony Bearer (see /developers). Confirmation needs a DISJOINT party to agree on the "
          "same metric using a DIFFERENT manifest; this exact manifest is only a build check.")
    return measurement


# ------------------------------------------------------------------ selftest (mock panelists)
def selftest():
    """A perfect reader and a coin-flipper prove the scoring and the gate, no models needed."""
    import contextlib
    import io

    assert _parse_cli(["panel.py", "run", "spec.json", "--dry-run"]) == {
        "command": "run", "path": "spec.json", "dry_run": True, "submit": False,
    }
    assert _parse_cli(["panel.py", "run", "-", "--submit"]) == {
        "command": "run", "path": "-", "dry_run": False, "submit": True,
    }
    for bad_argv, expected in (
            (["panel.py", "run", "spec.json", "--dryrun"], "unknown"),
            (["panel.py", "run", "spec.json", "--dry-run", "--submit"], "mutually exclusive"),
            (["panel.py", "run", "spec.json", "--submit", "--submit"], "duplicate"),
            (["panel.py", "manifest.json", "--dry-run"], "exactly one"),
            (["panel.py", "--selftest", "ignored"], "no additional"),
    ):
        try:
            _parse_cli(bad_argv)
            raise AssertionError("bad CLI tokens were silently accepted: %r" % (bad_argv,))
        except SystemExit as exc:
            assert expected in str(exc), (bad_argv, exc)

    global _open
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

    def assert_pre_spend_refusal(candidate, label):
        calls = []

        def probe(*args):
            calls.append(args)
            return "yes"

        assert run_panel(candidate, ask_fn=probe) is None, label
        assert calls == [], f"{label} must refuse before buying a reader cell"

    for real_count in (0, 1):
        assert_pre_spend_refusal(
            dict(good, items=items[:4] + items[4:4 + real_count]),
            f"a {real_count}-real-item comprehension sample is not bootstrap-able",
        )
    assert_pre_spend_refusal(dict(good, panel_neff="2"),
                             "panel_neff must be an exact integer, not a coercible string")
    for bad_gap in ("bogus", float("nan"), -0.1, 1.1, True):
        assert_pre_spend_refusal(dict(good, calibration_min_gap=bad_gap),
                                 f"invalid calibration_min_gap {bad_gap!r}")
    assert_pre_spend_refusal(dict(good, planted_arm="baseline"),
                             "planted_arm must name one of the two measured arms")
    assert_pre_spend_refusal(dict(good, metric="not_a_panel_metric"),
                             "an unsupported metric must not buy a comprehension panel")
    malformed_items = [dict(item) for item in items]
    del malformed_items[0]["question"]
    assert_pre_spend_refusal(dict(good, items=malformed_items),
                             "missing reader/scorer fields must fail during structural validation")

    # Duplicate identities must refuse before inference: otherwise repeated reader names receive
    # the same arm, are aggregated into the same per-member bucket, yet still increase the roster;
    # repeated item ids overwrite the answer key and collapse bootstrap sampling units.
    identity_calls = []

    def identity_probe(*args):
        identity_calls.append(args)
        return "yes"

    assert run_panel(dict(good, panel=[{"name": "reader-a"}, {"name": "READER-A"}]),
                     ask_fn=identity_probe) is None
    assert not identity_calls, "duplicate readers must refuse before buying calibration cells"
    duplicate_items = [dict(item) for item in items]
    duplicate_items[-1]["id"] = duplicate_items[0]["id"]
    assert run_panel(dict(good, items=duplicate_items), ask_fn=identity_probe) is None
    assert not identity_calls, "duplicate items must refuse before buying calibration cells"

    # Calibration is the reader instrument's positive control, not part of the randomized
    # construct estimator. Every reader must therefore receive BOTH arms of EVERY calibration
    # item; dealing one arm per item certifies only a tiny, seed-dependent subset. Give each
    # calibration row unique text so this assertion proves the full Cartesian coverage rather
    # than merely counting calls whose item identity cannot be recovered.
    dual_items = [dict(item) for item in items]
    for n, item in enumerate(dual_items[:4]):
        item["english"] = f"The check {n} passed."
        item["ainglish"] = f"The check {n} passed wit(counterparty-settled)."
    calibration_texts = {
        (item["id"], arm): item[arm]
        for item in dual_items if item.get("calibration") for arm in ("english", "ainglish")
    }
    calibration_calls = []

    def calibration_probe(ep, text, q, options):
        calibration_calls.append((ep["name"], text))
        return tag_reliant(ep, text, q, options)

    dual_cells = []
    dual = run_panel(dict(good, items=dual_items), ask_fn=calibration_probe,
                     cell_results=dual_cells)
    assert dual is not None
    assert len(dual_cells) == len([item for item in dual_items if not item.get("calibration")]) * 2
    assert all(row["kind"] == "ainglish.panel.cell-result.v1" and
               row["answer"] is not None and isinstance(row["correct"], bool)
               for row in dual_cells), \
        "the sidecar source must retain every normalized real-cell verdict and no calibration row"
    expected_calibration_calls = sorted(
        (reader["name"], text)
        for reader in good["panel"] for text in calibration_texts.values()
    )
    got_calibration_calls = sorted(
        call for call in calibration_calls if call[1] in set(calibration_texts.values())
    )
    assert got_calibration_calls == expected_calibration_calls, \
        "every reader must receive both arms of every calibration item exactly once"
    for reader in good["panel"]:
        for item in (item for item in dual_items if not item.get("calibration")):
            exposed = sum((reader["name"], item[arm]) in calibration_calls
                          for arm in ("english", "ainglish"))
            assert exposed == 1, \
                "real items must remain one counterbalanced arm per reader after calibration doubles"

    # Asking every cell is insufficient if a named reader never returns one of them. A pooled
    # gate could still pass on the other readers and then measure a cohort the control did not
    # certify, so calibration completeness is per reader/item/arm and gates before real spend.
    incomplete_calls = []
    missing_text = dual_items[0]["ainglish"]
    real_texts = {item[arm] for item in dual_items if not item.get("calibration")
                  for arm in ("english", "ainglish")}

    def incomplete_calibration_probe(ep, text, q, options):
        incomplete_calls.append(text)
        if ep["name"] == "reader-b" and text == missing_text:
            raise TransportFault("timeout")
        return tag_reliant(ep, text, q, options)

    incomplete = run_panel(dict(good, items=dual_items),
                           ask_fn=incomplete_calibration_probe)
    assert _is_panel_refusal(incomplete)
    assert incomplete["stage"] == "calibration" and incomplete["cause"] == "transport_or_yield"
    assert incomplete["real_cells_attempted"] == 0
    assert not (set(incomplete_calls) & real_texts), \
        "a reader missing one calibration arm must be refused before all real spend"

    # A byte-identical pair cannot carry a planted contrast. It must refuse before reader spend,
    # not merely dilute the gate until a particular seed happens to fail.
    same_arm_items = [dict(item) for item in dual_items]
    same_arm_items[0]["ainglish"] = same_arm_items[0]["english"]
    same_arm_calls = []
    assert run_panel(dict(good, items=same_arm_items),
                     ask_fn=lambda *args: same_arm_calls.append(args) or "yes") is None
    assert same_arm_calls == [], "same-arm calibration must refuse before a single reader call"

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

    # urllib's default handler forwards Authorization/x-api-key across origins. The request must
    # be stopped before a redirect can replay a provider key (or a credential in a 307 body).
    assert _origin("https://api.openai.com/v1") == _origin("https://API.OPENAI.COM:443/v2")
    for safe in ("https://example.test/api", "http://localhost:11434/v1",
                 "http://127.0.0.1:8920/api", "http://[::1]:11434/v1"):
        _require_secure_credential_url(safe, "selftest")
    for unsafe in ("http://api.example.test/v1", "ftp://localhost/key", "relative/path"):
        try:
            _require_secure_credential_url(unsafe, "selftest")
            raise AssertionError(f"credential URL must refuse: {unsafe}")
        except ValueError:
            pass
    redirect_probe = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        b"{}", {"Authorization": "Bearer sentinel"})
    redirect_probe._ainglish_sensitive = True
    try:
        _SensitiveRedirectHandler().redirect_request(
            redirect_probe, None, 307, "Temporary Redirect", {}, "https://example.invalid/capture")
        raise AssertionError("a credentialled cross-origin redirect must refuse before replay")
    except urllib.error.HTTPError as err:
        assert err.code == 307 and "refusing cross-origin" in str(err)

    # --- transport parity, and truncation as a dead cell -------------------------------------
    # The defect this pins: max_tokens rode in the anthropic body and NOT the openai-compatible
    # one, so a reader's answer budget was decided by which transport it happened to sit behind.
    # A missing bound is invisible in every direction — no error, no warning, and the receipt named
    # neither value — so only a test that reads the wire can hold the two builders together.
    sent = {}

    class _Resp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode()

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _capture(payload):
        def fake(req, timeout=None, sensitive=False):
            sent["body"] = json.loads(req.data)
            sent["sensitive"] = sensitive
            return _Resp(payload)
        return fake

    _ok_openai = {"choices": [{"message": {"content": "yes"}, "finish_reason": "stop"}]}
    _ok_anthropic = {"content": [{"text": "yes"}], "stop_reason": "end_turn"}
    real_open, had_key = _open, "ANTHROPIC_API_KEY" in os.environ
    os.environ.setdefault("ANTHROPIC_API_KEY", "selftest")
    try:
        bodies, sensitivities = {}, {}
        for label, entry, payload in (
            ("openai-compatible", {"name": "o", "provider": "ollama", "model": "m"}, _ok_openai),
            ("anthropic", {"name": "a", "provider": "anthropic", "model": "m"}, _ok_anthropic),
        ):
            _open = _capture(payload)
            assert chat(entry, "hi") == ("yes", False), f"{label}: clean completion"
            bodies[label] = sent["body"]
            sensitivities[label] = sent["sensitive"]
        assert sensitivities["anthropic"] is True, "x-api-key requests must use the guarded opener"
        for bound, default in TRANSPORT_BOUNDS.items():
            for label, body in bodies.items():
                assert body.get(bound) == default, \
                    f"{label} request body dropped the declared bound {bound!r}"
        assert bodies["openai-compatible"]["temperature"] == 0, \
            "OpenAI-compatible direct classifiers retain deterministic sampling by default"
        assert "temperature" not in bodies["anthropic"], \
            "native Anthropic must omit the parameter current models reject as deprecated"
        assert reader_receipt({"name": "a", "provider": "anthropic", "model": "m"})["temperature"] is None, \
            "omission is still explicit in the re-runnable reader receipt"

        _open = _capture(_ok_anthropic)
        chat({"name": "a", "provider": "anthropic", "model": "m", "temperature": 0.4}, "hi")
        assert sent["body"]["temperature"] == 0.4, "an explicit Anthropic sampling setting must win"
        try:
            temperature_for({"name": "bad", "provider": "ollama", "temperature": True})
            raise AssertionError("boolean temperature was accepted as numeric")
        except SystemExit:
            pass

        # "Declared" is decoration unless the declared value reaches the wire.
        _open = _capture(_ok_openai)
        chat({"name": "o", "provider": "ollama", "model": "m", "max_tokens": 4096}, "hi")
        assert sent["body"]["max_tokens"] == 4096, "a declared bound must override the default"

        # Truncation must never be graded — on either transport. The fragment here CONTAINS a valid
        # option, so before the check it graded as a CORRECT answer: a transport fault could raise
        # an arm's accuracy. That is why this is a dead cell and not merely a wrong one.
        for label, entry, payload in (
            ("openai-compatible", {"name": "o", "provider": "ollama", "model": "m"},
             {"choices": [{"message": {"content": "process-ran, and the reason is"},
                           "finish_reason": "length"}]}),
            ("anthropic", {"name": "a", "provider": "anthropic", "model": "m"},
             {"content": [{"text": "process-ran, and the reason is"}], "stop_reason": "max_tokens"}),
        ):
            _open = _capture(payload)
            _cut = ask(entry, "text", "q?", ["process-ran", "cannot tell"])
            assert is_absent(_cut) and getattr(_cut, "reason", None) == "truncated", \
                f"{label}: a bound-truncated read must be a TYPED dead cell, not a scored answer (got {_cut!r})"

        # Option labels can overlap. "cannot tell" contains the shorter valid option "no", and
        # the old substring parser returned whichever option appeared first in the manifest.
        # Exercise the real adapter/parser path because a direct equality assertion would miss a
        # future reintroduction in ask().
        _open = _capture(
            {"choices": [{"message": {"content": "cannot tell"}, "finish_reason": "stop"}]})
        assert ask({"name": "o", "provider": "ollama", "model": "m"}, "text", "q?",
                   ["yes", "no", "cannot tell"]) == "cannot tell", \
            "an exact longer option must not be captured by an earlier substring option"
    finally:
        _open = real_open
        if not had_key:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    # …and a dead cell must reach the yield guard, which is what makes it safe not to grade it:
    # an all-truncated run emits nothing rather than a delta over an empty denominator.
    truncated = run_panel(good, ask_fn=lambda *a: None)
    assert _is_panel_refusal(truncated), \
        "a panel whose every read is bound-truncated must emit a refusal, not a measurement"
    assert truncated["stage"] == "calibration" and truncated["cause"] == "transport_or_yield"
    assert truncated["real_cells_attempted"] == 0

    # --- transport faults: a cell, with a cause, not a dead run --------------------------------
    # _fetch's taxonomy. The NARROWNESS is the load-bearing half: a 400 or a 401 is the operator's
    # problem and must keep travelling, or a config error arrives disguised as a thin panel.
    class _Raiser:
        def __init__(self, exc):
            self.exc = exc

        def __call__(self, req, timeout=None, sensitive=False):
            raise self.exc

    real_open = _open
    try:
        for exc, reason in (
            (socket.timeout("timed out"), "timeout"),
            (TimeoutError("timed out"), "timeout"),
            (urllib.error.HTTPError("u", 503, "busy", {}, None), "http_503"),
            (urllib.error.HTTPError("u", 429, "slow down", {}, None), "http_429"),
            (urllib.error.URLError("connection refused"), "unreachable"),
        ):
            _open = _Raiser(exc)
            try:
                _fetch(urllib.request.Request("http://x", b"{}"))
                raise AssertionError(f"{exc!r} must become a TransportFault")
            except TransportFault as f:
                assert f.reason == reason, f"{exc!r} → {f.reason!r}, expected {reason!r}"
        # …and these must NOT be converted: they are bugs or misconfiguration, not weather.
        for exc in (urllib.error.HTTPError("u", 400, "bad request", {}, None),
                    urllib.error.HTTPError("u", 401, "unauthorized", {}, None),
                    urllib.error.HTTPError("u", 404, "no such model", {}, None),
                    ValueError("response shape changed")):
            _open = _Raiser(exc)
            try:
                _fetch(urllib.request.Request("http://x", b"{}"))
                raise AssertionError(f"{exc!r} should have propagated")
            except TransportFault:
                raise AssertionError(
                    f"{exc!r} was swallowed as a transport fault — a bug or a misconfiguration "
                    f"must stop the run, not become a quiet dead cell")
            except (urllib.error.HTTPError, ValueError):
                pass
    finally:
        _open = real_open

    # Integration: one reader stalls on one real cell. Before this the exception left run_panel and
    # took every completed cell with it; now the run finishes and the receipt names reader and arm.
    seen = {"n": 0}

    def stalls_once(ep, text, q, options):
        seen["n"] += 1
        if seen["n"] == 17:         # 4 calibration items x 2 arms x 2 readers = cells 1-16
            raise TransportFault("timeout")
        return tag_reliant(ep, text, q, options)

    m_fault = run_panel(good, ask_fn=stalls_once)
    assert m_fault is not None, "one stalled cell must not kill the run"
    tf = m_fault["manifest"]["transport_faults"]
    assert tf["total"] == 1 and tf["retried"] is False, tf
    assert sum(n for arms in tf["per_cell"].values() for r in arms.values() for n in r.values()) == 1
    assert any("timeout" in r for arms in tf["per_cell"].values() for r in arms.values()), tf

    m = run_panel(good, ask_fn=tag_reliant)
    assert m is not None and m["value"] > 0, "calibrated tag-reliant panel must find the recovery effect"
    # --- panel_neff is a claim, not a headcount ------------------------------------------------
    # It used to be emitted as len(panel): a roster count wearing an error-structure statistic's
    # name. The harness now refuses to auto-fill it and reports the roster count under its own name.
    assert m["panel_members"] == 2, "the roster count, named as what it is"
    assert "panel_neff" not in m, \
        "an UNDECLARED n_eff must be absent, never defaulted to the membership count"
    assert "panel_neff_basis" not in m
    m_dec = run_panel(dict(good, panel_neff=1, panel_neff_axis="reader"), ask_fn=tag_reliant)
    assert m_dec["panel_neff"] == 1 and m_dec["panel_neff_basis"] == "declared:reader-axis-unvalidated", \
        "a declared n_eff rides with its provenance"
    assert m_dec["panel_members"] == 2, "and does not overwrite the roster count it disagrees with"
    assert m["yield_report"]["cells"] == (8 + 4 * 2) * 2, \
        "real rows buy one arm/read; calibration rows buy both arms/read"
    assert m["calibration"] == {"planted_arm": "ainglish", "detectable": 1.0, "other": 0.0,
                                "gap": 1.0, "min_gap": 0.5, "passed": True}
    assert m["manifest"]["calibration"] == {
        "planted_arm": "ainglish", "min_gap": 0.5, "ordering": "calibration-first",
        "arm_exposure": "both-arms-per-reader-item", "cells": 16,
    }, "the committed manifest must disclose the full positive-control exposure"
    assert m["manifest"]["items"] == items and "items_url" not in m["manifest"], \
        "inline bytes must survive beside their digest so another party can rerun them"
    assert all("api_key_env" not in r for r in m["manifest"]["readers"]), \
        "reproducible reader configuration must never carry credential locations"
    assert m["manifest"]["transport_truncations"] == {
        "total": 0, "per_reader_cell": {},
        "by_cell": {"english": 0, "ainglish": 0},
        "imbalanced_across_cells": False,
    }, "a clean run must state zero bound truncations"
    order = []

    def ordered_reader(ep, text, q, options):
        order.append(ep["name"])
        return tag_reliant(ep, text, q, options)

    assert run_panel(good, ask_fn=ordered_reader) is not None
    assert order == (["reader-a"] * 8 + ["reader-b"] * 8
                     + ["reader-a"] * 8 + ["reader-b"] * 8), \
        "calibration and real blocks must each group calls by reader, never swap local models per item"
    original_hash = "a" * 64
    replication_output = io.StringIO()
    with contextlib.redirect_stdout(replication_output):
        m_rep = run_panel(dict(good, replicates_hash=original_hash), ask_fn=tag_reliant)
    assert m_rep["replicates_hash"] == original_hash, \
        "--submit must be able to file a replication without manual payload surgery"
    assert f'"replicates_hash": "{original_hash}"' in replication_output.getvalue(), \
        "the printed copy-and-submit JSON must identify the original it replicates"

    # --- robustness_delta v4: through run_panel(), the boundary the dispatch lives behind -------
    # The oracle answers by EXACT LOOKUP over texts precomputed with the same deterministic
    # corrupt() the runner uses — no prefix heuristics for the corruption to break.
    def r_item(i, options=("yes", "no")):
        return {"id": f"r{i}", "english": f"the build finished and every check passed run {i}",
                "ainglish": f"build pass(clean) run {i}",
                "question": "did it pass", "options": list(options), "answer": "yes"}

    r_items = [r_item(1), r_item(2), r_item(3), r_item(4)]
    r_floor_id = "r4"
    r_calib = [{"id": "rc1", "english": "the weather is unrelated to any build",
                "ainglish": "build pass(clean) calibration", "question": "did it pass",
                "options": ["yes", "no"], "answer": "yes"}]
    r_seed = 11
    r_answers = {}
    for item in r_items + r_calib:
        for arm in ("english", "ainglish"):
            intact = item[arm]
            corrupted = corrupt(intact, f"{r_seed}:{item['id']}:{arm}", "drop_token")
            unreadable_calib = item["id"].startswith("rc") and arm == "english"
            r_answers[intact] = "no" if unreadable_calib else "yes"
            if item["id"] == r_floor_id:
                r_answers[corrupted] = "no"                       # both arms floor
            else:
                r_answers[corrupted] = "yes" if arm == "english" else "no"

    def r_oracle(ep, text, question, options):
        return r_answers[text]

    r_good = {"construct": "rob-demo", "slug": "demo", "metric": "robustness_delta", "seed": r_seed,
              "items": r_items, "calibration_items": r_calib, "planted_arm": "ainglish",
              "panel": [{"name": "reader-a"}, {"name": "reader-b", "precision": "q4_k_m"}],
              "panel_neff": 2, "corruption": {"channel": "drop_token"}}
    rm = run_panel(dict(r_good), ask_fn=r_oracle)
    assert rm is not None, "a readable panel with live items must emit"
    assert rm["metric"] == "robustness_delta" and "value_uncensored" in rm and "floor_cells" in rm, \
        "v4 requires the censored value to ship its uncensored twin and the floor count"
    assert rm["floor_cells"] == 1, "the both-arms-at-chance item is censored and counted"
    assert rm["value"] == -100.0, \
        "PERCENTAGE POINTS on the wire: full-scale ainglish break vs english survival is -100 pp, not -1"
    assert rm["value"] != rm["value_uncensored"], \
        "the floored item is excluded from value but present in value_uncensored — censoring is visible"
    assert rm["value_lo"] <= rm["value"] <= rm["value_hi"], \
        "robustness must ship an honest item-bootstrap interval accepted by the API contract"
    assert "yield_report" in rm, "the four-cell yield guard's report rides the payload"
    assert rm["manifest"]["calibration"]["items_sha256"], \
        "the gate is part of the experiment's identity — it must be inside the hashed receipt"
    assert all(isinstance(r["outside_interval"], bool) for r in rm["resample_down"] if r["value"] is not None), \
        "actual robustness thinnings must be compared with the emitted interval"
    assert corrupt("alpha beta gamma", "k1", "drop_token") == corrupt("alpha beta gamma", "k1", "drop_token")
    assert corrupt("ab", "k", "corrupt_char") in ("xb", "ax")
    assert bootstrap_censored_mean([(-100.0, False), (100.0, False)], seed=3)[0] <= 0.0 \
        <= bootstrap_censored_mean([(-100.0, False), (100.0, False)], seed=3)[1]

    r_order = []

    def ordered_robustness_reader(ep, text, question, options):
        r_order.append(ep["name"])
        return r_answers[text]

    assert run_panel(dict(r_good), ask_fn=ordered_robustness_reader) is not None
    assert r_order == (["reader-a"] * 2 + ["reader-b"] * 2
                       + ["reader-a"] * 16 + ["reader-b"] * 16), \
        "robustness must keep each reader resident while preserving baseline-before-corrupted"

    truncated_text = corrupt(
        r_items[0]["ainglish"], f"{r_seed}:{r_items[0]['id']}:ainglish", "drop_token")

    def one_bound_truncation(ep, text, question, options):
        if ep["name"] == "reader-a" and text == truncated_text:
            return Absent("truncated")
        return r_answers[text]

    r_truncated = run_panel(dict(r_good), ask_fn=one_bound_truncation)
    assert r_truncated is not None, "one typed truncation below the guard threshold may emit"
    tr = r_truncated["manifest"]["transport_truncations"]
    assert tr["total"] == 1 and tr["by_cell"]["ainglish_corrupted"] == 1, tr
    assert tr["imbalanced_across_cells"] is True, \
        "condition-correlated truncation must be visible in the receipt, never only a dead-cell total"

    # a changed calibration set is a DIFFERENT EXPERIMENT: the receipts must differ
    other_calib = [dict(r_calib[0], id="rc9", english="the moon is unrelated to any build")]
    r_answers[other_calib[0]["english"]] = "no"
    rm2 = run_panel(dict(r_good, calibration_items=other_calib), ask_fn=r_oracle)
    assert json.dumps(rm["manifest"], sort_keys=True) != json.dumps(rm2["manifest"], sort_keys=True), \
        "two runs with different gates must never share a manifest hash"

    # per-item chance: a 4-option item whose corrupted panel-accuracy is 0.5 sits BETWEEN the two
    # chance levels (0.25 for its own options, 0.5 for a binary item's) — so taking chance from
    # items[0] floors it in one ordering and not the other. Reordering must change nothing.
    r4opt = dict(r_item(5, options=("a", "b", "c", "d")), answer="a")
    r_split = {}
    for arm in ("english", "ainglish"):
        r_answers[r4opt[arm]] = "a"
        r_split[corrupt(r4opt[arm], f"{r_seed}:r5:{arm}", "drop_token")] = True  # per-reader split

    def r_oracle_split(ep, text, question, options):
        if text in r_split:
            return "a" if ep["name"] == "reader-a" else "b"   # panel-mean 0.5 on both arms
        return r_answers[text]

    fwd = run_panel(dict(r_good, items=r_items + [r4opt]), ask_fn=r_oracle_split)
    rev = run_panel(dict(r_good, items=[r4opt] + r_items), ask_fn=r_oracle_split)
    assert fwd["floor_cells"] == rev["floor_cells"] == 1 and fwd["value"] == rev["value"], \
        "chance is a property of each item's own option count — item order must change nothing"

    # zero survivors REFUSE: the uncensored anchor must never masquerade as the censored value
    all_floor = [dict(r_item(20 + n), id=f"rf{n}") for n in range(2)]
    for item in all_floor:
        for arm in ("english", "ainglish"):
            r_answers[item[arm]] = "yes"
            r_answers[corrupt(item[arm], f"{r_seed}:{item['id']}:{arm}", "drop_token")] = "no"
    assert run_panel(dict(r_good, items=all_floor), ask_fn=r_oracle) is None, \
        "a mean over zero surviving cells is undefined — refuse, never substitute"

    # the shared identity gate covers robustness (the dispatch sits BEHIND it now)
    assert run_panel(dict(r_good, panel=[{"name": "reader-a"}, {"name": "Reader-A"}]),
                     ask_fn=r_oracle) is None, "case-insensitive duplicate readers must refuse pre-inference"
    assert run_panel(dict(r_good, calibration_items=[dict(r_calib[0], id="r1")]),
                     ask_fn=r_oracle) is None, "a calibration id colliding with a real id must refuse"
    same_arm_robustness_calls = []
    same_arm_robustness = [dict(r_calib[0], ainglish=r_calib[0]["english"])]
    assert run_panel(dict(r_good, calibration_items=same_arm_robustness),
                     ask_fn=lambda *args: same_arm_robustness_calls.append(args) or "yes") is None
    assert same_arm_robustness_calls == [], \
        "same-arm calibration must refuse before spend on every panel metric"

    # a reader faulting on every call is HALF the cells dead: the guard must kill the run
    def r_half_dead(ep, text, question, options):
        if ep["name"] == "reader-b":
            raise TransportFault("timeout")
        return r_answers[text]
    assert run_panel(dict(r_good), ask_fn=r_half_dead) is None, \
        "a 50%-dead panel must refuse — a corrupted-only failure could manufacture the degradation"

    # review-2 findings, pinned at the same public boundary --------------------------------
    # (1) the gate REFUSES BEFORE a single real cell is bought: a blind panel pays for
    # calibration only (1 calib item x 2 arms x baseline x 2 readers = 4 calls, nothing real)
    r_calls = []

    def r_counting_oracle(ep, text, question, options):
        r_calls.append(text)
        return r_answers[text]

    assert run_panel(dict(r_good, planted_arm="english"), ask_fn=r_counting_oracle) is None
    assert len(r_calls) == 4, \
        f"a failed gate must cost calibration only — {len(r_calls)} calls made, 4 allowed"

    # (2) a no-op corruption refuses BEFORE any inference: single-token arms cannot be corrupted
    r_calls.clear()
    tiny = [{"id": "t1", "english": "passed", "ainglish": "pass!", "question": "did it pass",
             "options": ["yes", "no"], "answer": "yes"},
            {"id": "t2", "english": "failed", "ainglish": "fail!", "question": "did it pass",
             "options": ["yes", "no"], "answer": "no"}]
    assert run_panel(dict(r_good, items=tiny), ask_fn=r_counting_oracle) is None, \
        "byte-identical corrupted cells cannot estimate degradation"
    assert r_calls == [], "the no-op refusal must fire before a single inference call"

    # ...and drop_token deletes ONE span, preserving every other byte — the split()/join() version
    # rewrote all whitespace, so its single event was silently many formatting edits
    _t = "alpha  beta\ngamma"
    _out = corrupt(_t, "kw", "drop_token")
    assert _out in {"beta\ngamma", "alpha  gamma", "alpha  beta"}, _out
    assert ("  " in _out) or ("\n" in _out), "untouched whitespace runs must survive the deletion"

    # (3) one item refuses UP FRONT — resample-down is undefined over one cell (was a
    # ValueError). The pin is the cost boundary, not just the None: the late fewer-than-two-live
    # net would also refuse, but only after buying every cell.
    r_calls.clear()
    assert run_panel(dict(r_good, items=[r_item(1)]), ask_fn=r_counting_oracle) is None, \
        "a one-item manifest must refuse, not crash in resample-down"
    assert r_calls == [], \
        f"the one-item refusal must fire before a single inference call ({len(r_calls)} made)"

    # (4) omission must not become a server-side declaration ANYWHERE, including --submit: the
    # runner refuses outright without an explicit n_eff (the server defaults absence to the
    # roster count and stamps `declared:` — an assertion the submitter never made), and the
    # refusal costs zero inference calls.
    r_calls.clear()
    no_neff = {k: v for k, v in r_good.items() if k != "panel_neff"}
    assert run_panel(no_neff, ask_fn=r_counting_oracle) is None, \
        "robustness without an explicit panel_neff must refuse — omission is not a declaration"
    assert r_calls == [], "and the refusal must cost nothing"
    assert rm["panel_members"] == 2
    assert rm["panel_neff"] == 2 and rm["panel_neff_basis"] == "declared:reader-axis-unvalidated"
    # -75.0: the per-reader mean runs over ALL complete-quartet items INCLUDING the floored one
    # (censoring applies to the headline value, not to the diagnostic that explains the readers).
    assert [(r["model"], r["value"], r.get("precision")) for r in rm["per_member"]] == \
        [("reader-a", -75.0, None), ("reader-b", -75.0, "q4_k_m")], \
        "per_member is the SERVER's list-of-rows contract, precision separate when declared"
    # ...and the SERVER's identity rule holds end to end (M17): every per_member row's
    # model[@precision] identity appears verbatim in BOTH submitted roster arrays.
    assert rm["panel_models"] == ["reader-a", "reader-b@q4_k_m"]
    assert rm["manifest"]["models"] == rm["panel_models"]
    for row in rm["per_member"]:
        ident = row["model"] + ("@" + row["precision"] if row.get("precision") else "")
        assert ident in rm["panel_models"], \
            f"{ident} missing from panel_models — cleanPerMember() would 422 this payload"
    assert rm["panel_agreement"] is not None
    rn = run_panel(dict(r_good, panel_neff=1), ask_fn=r_oracle)
    assert rn["panel_neff"] == 1 and rn["panel_neff_basis"] == "declared:reader-axis-unvalidated"

    # (5) COMPLETE QUARTETS: condition-specific cell loss below the guard threshold must not
    # manufacture the veto. Two readers, NO true degradation anywhere (every per-reader quartet
    # is flat); reader-a faults on exactly two corrupted-ainglish cells. Cell-wise means would
    # read -25 pp from those two dead cells alone; quartet scoring reads the truth: 0.
    q_ainglish = set()
    q_calib_texts = set()
    for item in r_items:
        q_ainglish.add(item["ainglish"])
        q_ainglish.add(corrupt(item["ainglish"], f"{r_seed}:{item['id']}:ainglish", "drop_token"))
    for item in r_calib:
        q_calib_texts.update({item["ainglish"], item["english"]})
    q_faults = {corrupt(r_items[i]["ainglish"], f"{r_seed}:{r_items[i]['id']}:ainglish", "drop_token")
                for i in (0, 1)}

    def q_oracle(ep, text, question, options):
        if text in q_calib_texts:
            return "yes" if text == r_calib[0]["ainglish"] else "no"   # gate: planted arm readable
        if ep["name"] == "reader-a" and text in q_faults:
            raise TransportFault("timeout")
        if text in q_ainglish:
            return "yes" if ep["name"] == "reader-a" else "no"         # flat per reader, both conds
        return "yes"                                                    # english: everyone, both conds

    qm = run_panel(dict(r_good), ask_fn=q_oracle)
    assert qm is not None, "5.6% dead cells is under the guard threshold — the run may emit"
    assert qm["value"] == 0.0 and qm["value_uncensored"] == 0.0, \
        f"asymmetric cell loss must never manufacture degradation (got {qm['value']})"

    # (6) resample rows exist only when thinning HAPPENED, and say the actual fraction: with two
    # live items both requested thinnings clamp to keeping everything — an untested sensitivity
    # must not read as tested.
    two = run_panel(dict(r_good, items=r_items[:2]), ask_fn=r_oracle)
    assert two is not None and two["resample_down"] == [], \
        "no thinning performed at two live items -> no sensitivity rows, never 100%-kept rows dressed as 50%"
    assert all(r["kept_fraction"] == round(r["items"] / 4, 3) for r in rm["resample_down"]), \
        "kept_fraction is the ACTUAL retained fraction of the four live items"

    # (7) M14: the calibrated panel IS the measured panel. reader-b faults on both calibration
    # arms (never certified) but would be live on every real cell with differential -100 while
    # reader-a reads flat 0 — pooled calibration passed and emitted -50. Must refuse before any
    # real cell is bought.
    r_calls.clear()
    real_texts = {t for item in r_items for t in (item["english"], item["ainglish"])}

    def m14_oracle(ep, text, question, options):
        r_calls.append(text)
        if ep["name"] == "reader-b" and text in q_calib_texts:
            raise TransportFault("timeout")
        return q_oracle(ep, text, question, options)

    assert run_panel(dict(r_good), ask_fn=m14_oracle) is None, \
        "an uncalibrated reader must not enter real scoring"
    assert not (set(r_calls) & real_texts), \
        "the uncalibrated-reader refusal must fire before a single real cell is bought"

    # (8) M15: panel_neff is contract-checked BEFORE spend — exact integer, 1..roster, no coercion
    for bad in (0, -1, 3, True, 1.5, "bogus"):
        r_calls.clear()
        assert run_panel(dict(r_good, panel_neff=bad), ask_fn=r_counting_oracle) is None, \
            f"panel_neff={bad!r} must refuse — the server contract is an integer in 1..len(panel)"
        assert r_calls == [], f"panel_neff={bad!r} refusal must cost zero calls"

    rm_rep = run_panel(dict(r_good, replicates_hash="b" * 64), ask_fn=r_oracle)
    assert rm_rep["replicates_hash"] == "b" * 64
    blind = run_panel(dict(r_good, planted_arm="english"), ask_fn=r_oracle)
    assert blind is None, "a robustness panel that cannot read intact forms must refuse at calibration"

    # the documented dry-run path completes AND stamps itself non-evidence
    dry = run_panel(dict(r_good, _dry_run=True), ask_fn=dry_reader(r_items, dict(r_good, _dry_run=True)))
    assert dry is not None, "the robustness dry run must survive its own calibration gate"
    assert "DRY-RUN" in dry["manifest"]["protocol"], "a dry payload must carry the non-evidence stamp"

    # A dead cell is censored, never graded as the answer string "none". This is the acceptance
    # test the transport-fault integration lacked: it asserted that a run survived and recorded
    # the fault, but never asserted that the fault stayed out of the value it emitted.
    one = [{"id": "one", "answer": "yes"}]
    dead_mixed = [("one", "english", "live", "yes"),
                  ("one", "english", "dead", None)]
    dead_acc, dead_ent = score(dead_mixed, one)
    assert dead_acc["english"] == 1.0, "a transport fault must not lower arm accuracy"
    assert dead_ent["english"] == 0.0, "a transport fault must not become an entropy category"

    # panel_agreement is the observable that bears on correlation, computed UNCONDITIONED — two
    # readers that always answer alike are the correlated case the roster count cannot see.
    def twin(ep, text, q, options):
        return tag_reliant(ep, text, q, dict.fromkeys(options))  # identical behaviour per item
    m_twin = run_panel(dict(good, seed=7), ask_fn=lambda ep, t, q, o: tag_reliant({"name": "same"}, t, q, o))
    assert m_twin["panel_agreement"] == 1.0, \
        "two readers with identical behaviour must show agreement 1.0 — the roster still says 2"
    assert m_twin["panel_members"] == 2
    assert 0.0 <= m["panel_agreement"] < 1.0, "distinct-behaviour readers must agree less than always"
    # Nothing co-read -> None, not 0.0. A single member reads each item's one dealt arm alone, so
    # there is no pair to compare, and 0.0 would read as perfect independence rather than as silence.
    assert pairwise_agreement([("i1", "english", "solo", "yes")]) is None, \
        "no co-read cell: absence STATED, never a flattering 0.0"
    assert pairwise_agreement([("i1", "english", "a", "yes"), ("i1", "english", "b", "yes")]) == 1.0
    assert pairwise_agreement([("i1", "english", "a", "yes"), ("i1", "english", "b", "no")]) == 0.0
    assert pairwise_agreement([("i1", "english", "a", None), ("i1", "english", "b", None)]) is None, \
        "two dead transports are absence, not perfect reader agreement"
    assert pairwise_agreement([("i1", "english", "a", "yes"), ("i1", "english", "b", None)]) is None, \
        "one surviving reader supplies no pairwise comparison"
    # And the collider guard, stated as a test of what this does NOT do: a disagreeing pair is
    # counted, not dropped. Conditioning the denominator on error is the inversion @Exori found.
    assert pairwise_agreement([("i1", "english", "a", "wrong1"), ("i1", "english", "b", "wrong2")]) == 0.0
    # Absence has a direction, so the fault count is emitted even when nothing went wrong: an
    # omitted count reads as "no faults" and equally means "this harness never counted them".
    assert m["manifest"]["transport_faults"] == {"total": 0, "retried": False, "per_cell": {}}, \
        "a clean run must still STATE zero faults"

    bad = dict(good, panel=[{"name": "flip-a"}, {"name": "flip-b"}])
    refused_cells = []
    incompetent = run_panel(bad, ask_fn=coinflip, cell_results=refused_cells)
    assert _is_panel_refusal(incompetent) and incompetent["cause"] == "competence", \
        "a coin-flipping panel must FAIL the calibration gate with a competence receipt"
    assert refused_cells == [], "a calibration refusal must prove zero real rows in the sidecar"

    # …and it must fail BEFORE buying a single real item. The gate used to be scored last, so a
    # blind panel paid for the whole run before saying it was blind. Asserting "returns None" does
    # not test that at all — only counting what was ASKED does, which is why this counts.
    asked = []

    def counting(ep, text, q, options):
        asked.append(text)
        return coinflip(ep, text, q, options)

    counted_refusal = run_panel(bad, ask_fn=counting)
    assert _is_panel_refusal(counted_refusal) and counted_refusal["real_cells_attempted"] == 0
    real_texts = {i[arm] for i in items if not i.get("calibration") for arm in ("english", "ainglish")}
    assert not (set(asked) & real_texts), \
        f"calibration failed but {len(set(asked) & real_texts)} real items were still bought"
    assert len(asked) == len([i for i in items if i.get("calibration")]) * len(bad["panel"]) * 2, \
        "exactly the calibration cells should have been spent"

    # Reordering must not move a number: arms are dealt per (seed, panelist, item), so execution
    # order is not part of the estimator. A refactor that silently re-deals arms would look like
    # a passing selftest and a changed result.
    assert run_panel(good, ask_fn=tag_reliant)["value"] == m["value"], \
        "calibration-first must not change the measured value"

    # --- difficulty (@Exori's collider condition), all four behaviours -----------------------
    assert m["manifest"]["difficulty"] == {"annotated": False}, "absence must be STATED, never implied"
    half_items = [dict(i, difficulty=2) if i["id"] in ("r0", "r1", "r2") else i for i in items]
    assert run_panel(dict(good, items=half_items), ask_fn=tag_reliant) is None, \
        "a half-annotated set must refuse — it cannot check arm balance"
    ann_items = [dict(i, difficulty=2) if not i.get("calibration") else i for i in items]
    assert run_panel(dict(good, items=ann_items), ask_fn=tag_reliant) is None, \
        "difficulty without a declared axis is numbers without units — refuse"
    m_ann = run_panel(dict(good, items=ann_items, difficulty_axis="test axis, ordinal 1-3"), ask_fn=tag_reliant)
    assert m_ann is not None and m_ann["manifest"]["difficulty"]["annotated"] is True
    # The report's statistics are decimal STRINGS, never floats: round()-ed means like 2.28 or a
    # gap of 0.08 are not exactly-representable, so a numeric report can make an annotated set
    # UNMINTABLE — manifest_commitment (correctly) refuses non-portable floats, and the dealt
    # means are the seed's choice, not the experimenter's (issue #41, found live on a real mint).
    d_report = m_ann["manifest"]["difficulty"]
    assert d_report["gap"] == "0", "uniform difficulty must report a zero gap, as a portable string"
    assert all(isinstance(v, str) for v in d_report["per_arm_mean"].values()), \
        "per-arm difficulty means must be portable decimal strings, not floats"
    m_gap = run_panel(dict(good, items=ann_items, difficulty_axis="test axis, ordinal 1-3",
                           difficulty_balance_max_gap=0.6), ask_fn=tag_reliant)
    assert m_gap is not None and m_gap["manifest"]["difficulty"]["max_gap"] == "0.6", \
        "a declared max_gap like 0.6 is itself non-portable and must be stringified in the report"
    m_precise_gap = run_panel(dict(good, items=ann_items, difficulty_axis="test axis, ordinal 1-3",
                                   difficulty_balance_max_gap=0.00011), ask_fn=tag_reliant)
    assert m_precise_gap is not None and \
        m_precise_gap["manifest"]["difficulty"]["max_gap"] == "0.00011", \
        "the receipt must preserve the exact threshold compared, not round it to four decimals"
    try:
        from ainglish.client import manifest_commitment as _difficulty_commitment
    except ImportError:
        print("selftest note: difficulty-report commitment round-trip SKIPPED — standalone file, "
              "no ainglish.client; the string-type asserts above still pin the portable format.")
    else:
        assert _difficulty_commitment(m_gap["manifest"]), \
            "an annotated set's manifest must be commitable — the report may not reintroduce floats"
    # Lopsided deal: one reader, difficulty 9 on exactly the items that reader sees in the
    # ainglish arm — the gap is maximal by construction and a declared max_gap must refuse.
    lop = [dict(i, difficulty=(9 if arm_for(7, "reader-a", i["id"]) == "ainglish" else 1))
           if not i.get("calibration") else i for i in items]
    solo = dict(good, panel=[{"name": "reader-a"}], items=lop,
                difficulty_axis="test axis", difficulty_balance_max_gap=0.5)
    assert run_panel(solo, ask_fn=tag_reliant) is None, \
        "a deal whose difficulty gap exceeds the declared max must refuse to emit"
    # Strings make the report portable only after the numeric declaration is valid. Converting
    # NaN/Inf to ordinary strings would bypass manifest_commitment's float guard and let an
    # undefined collider report become a valid JSON manifest. Refuse every such declaration
    # before calibration — the zero calls are the cost boundary, not merely a late None.
    invalid_difficulty_calls = []

    def invalid_difficulty_reader(*args):
        invalid_difficulty_calls.append(args)
        return tag_reliant(*args)

    for bad_value in (float("nan"), float("inf"), float("-inf")):
        bad_items = [dict(item, difficulty=bad_value) if not item.get("calibration") else item
                     for item in items]
        assert run_panel(dict(good, items=bad_items, difficulty_axis="test axis"),
                         ask_fn=invalid_difficulty_reader) is None
    for bad_limit in (float("nan"), float("inf"), float("-inf"), -0.5, True):
        assert run_panel(dict(good, items=ann_items, difficulty_axis="test axis",
                              difficulty_balance_max_gap=bad_limit),
                         ask_fn=invalid_difficulty_reader) is None
    assert invalid_difficulty_calls == [], \
        "invalid difficulty values and limits must refuse before a single reader call"
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
    resolution = m["manifest"]["accuracy_resolution"]
    en_cells = resolution["scored_cells"]["english"]
    ai_cells = resolution["scored_cells"]["ainglish"]
    assert resolution["delta_grid"]["denominator_lcm"] == math.lcm(en_cells, ai_cells)
    assert resolution["delta_grid"]["numerator_pp"] == 100
    assert resolution["delta_grid"]["step_pp"] == _portable_decimal(
        100 / math.lcm(en_cells, ai_cells)
    ), "the committed resolution must come from exact scored-cell counts, not rounded accuracy"
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

    assert AINGLISH_OIDC_SCOPE == "openid profile", \
        "one shared least-privilege exchange scope; no reputation claim is required"

    # ---- absence: ONE predicate, both consumers, no second computation (Rosetta's receipt) ----
    _ecg_m = absence_module()

    # (1) The pinned regression: '' with finish_reason 'stop' — the exact input the served
    # v0.2.15 graded dead-by-guard and live-by-scorer SIMULTANEOUSLY. ask() must type it.
    _open = _capture({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})
    clean_stop = ask({"name": "o", "provider": "ollama", "model": "m"}, "text", "q?", ["yes", "no"])
    assert isinstance(clean_stop, _ecg_m.Absent) and clean_stop.reason == "empty_stop", \
        f"a clean-stop empty must be TYPED absence, got {clean_stop!r}"
    _open = _capture({"choices": [{"message": {"content": "truncat"}, "finish_reason": "length"}]})
    cut = ask({"name": "o", "provider": "ollama", "model": "m"}, "text", "q?", ["yes", "no"])
    assert isinstance(cut, _ecg_m.Absent) and cut.reason == "truncated", \
        "truncation and clean-stop must be DISTINGUISHABLE absences, not one bare None"
    # Both consumers, one verdict: the guard counts it dead AND the scorer's live filter drops it.
    _g = _ecg_m.CellYieldGuard(arms=("a",), min_cells=0) if "min_cells" in _ecg_m.CellYieldGuard.__dataclass_fields__ else _ecg_m.CellYieldGuard(arms=("a",))
    _g.observe("m", "a", None if is_absent(clean_stop) else str(clean_stop), clean_stop)
    assert _g._all.empty == 1, "the guard must count a clean-stop empty as dead"
    _fixture_rows = [("i1", "english", "baseline", "m", clean_stop), ("i1", "english", "baseline", "m", "yes")]
    _live = [r for r in _fixture_rows if not is_absent(r[4])]
    assert len(_live) == 1, "the scorer-side filter must exclude the same cell the guard counted dead"

    # (2) The mutation pair: flip is_absent and BOTH consumers must move — proving each routes
    # through the single predicate instead of holding a private definition that happens to agree.
    _real_is_absent = _ecg_m.is_absent
    _ecg_m.is_absent = lambda cell: False  # the mutant: nothing is ever absent
    try:
        _gm = _ecg_m.CellYieldGuard(arms=("a",))
        _gm.observe("m", "a", None, None)
        _guard_moved = _gm._all.empty == 0
        _scorer_moved = len([r for r in _fixture_rows if not is_absent(r[4])]) == 2
    finally:
        _ecg_m.is_absent = _real_is_absent
    assert _guard_moved, "MUTATION NOT DETECTED: the guard does not route through is_absent"
    assert _scorer_moved, "MUTATION NOT DETECTED: the scorer filter does not route through is_absent"

    # (3) The decision-surface sweep (@sram's allowlist inversion): any code line that keys a
    # CELL CARRIER against an absence shape, outside the single allowed computation, is a second
    # absence definition growing back — the fifth patch wearing a shared name. The shape
    # inventory lives NEXT TO is_absent in the guard (same-commit rule). finish_reason is
    # standalone: keying on the transport reason ANYWHERE outside chat() is a violation whether
    # or not a carrier shares the line, because chat() is the one classifier allowed to read it.
    import re as _re
    _carrier_re = _re.compile(r"\b(?:raw|cell|answer|ans|parsed)\b|r\[[34]\]")
    _sweep_hits = []
    for _fname in ("panel.py", "empty_cell_guard.py"):
        _fn = ""
        _in_tests = False
        for _ln, _line in enumerate(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), _fname)).read().splitlines(), 1):
            if _re.match(r"(?:def |class )", _line):
                _fn = _line.strip()
                # test scaffolding builds fixture cells on purpose; the sweep guards PRODUCTION
                # verdict paths (everything before each file's selftest section).
                _in_tests = _in_tests or _fn.startswith(("def selftest", "def _ok", "def _run", "def _stream"))
            _code = _line.split("#", 1)[0]
            if _in_tests or not _code.strip():
                continue
            if "finish_reason" in _code and not _fn.startswith(("def chat", "def is_absent")):
                _sweep_hits.append(f"{_fname}:{_ln} in {_fn!r}: transport-reason keying outside chat(): {_code.strip()!r}")
                continue
            if _carrier_re.search(_code) and not _fn.startswith(("def is_absent",)):
                for _shape in _ecg_m.ABSENCE_SHAPES:
                    if _shape == "finish_reason":
                        continue
                    if _re.search(_shape, _code):
                        _sweep_hits.append(f"{_fname}:{_ln} in {_fn!r}: {_code.strip()!r} matches {_shape!r}")
    assert not _sweep_hits, "decision-surface violations (a second absence computation):\n  " + "\n  ".join(_sweep_hits)

    # ---- attempt lifecycle: the mint must precede the FIRST real reader cell -----------------
    # This file is also SERVED standalone by the register, where ainglish.client does not exist.
    # The attempt path itself already refuses cleanly without the package (see main); the
    # selftest mirrors that split: settings validation runs everywhere, the client-dependent
    # lifecycle section runs only where the package is importable (the SDK checkout and CI).
    try:
        from ainglish.client import AinglishError as _SelftestAinglishError  # noqa: F401
        from ainglish.client import manifest_commitment as _selftest_commitment  # noqa: F401
        _attempt_client_available = True
    except ImportError:
        _attempt_client_available = False

    class _AttemptProbe:
        def __init__(self, events):
            self.events = events
            self.aborts = []

        def mint_attempt(self, slug, manifest, **pin):
            self.events.append(("mint", manifest, pin))
            return {"attempt": {"attempt_id": "selftest-attempt"}}

        def measure(self, slug, payload):
            self.events.append(("measure", payload))
            return {"measurement": {"manifest_hash": "filed"}}

        def abort_attempt(self, attempt_id, **receipt):
            self.events.append(("abort", receipt))
            self.aborts.append(receipt)
            return {"attempt": {"attempt_id": attempt_id, "state": "aborted"}}

    attempt_spec = {"slug": "selftest", "attempt": {
        "estimand": "difference in comprehension accuracy",
        "admissibility_gates": ["planted calibration gap >= 0.5"],
        "planned_sample": {"items": len(items), "readers": len(good["panel"]), "arms": 2},
    }}

    if _attempt_client_available:
        events = []

        def tracked_reader(ep, text, q, options):
            events.append(("reader", ep["name"]))
            return tag_reliant(ep, text, q, options)

        attempted = _run_preregistered_panel(good, attempt_spec, tracked_reader,
                                             _AttemptProbe(events))
        assert attempted is not None and attempted["attempt_id"] == "selftest-attempt"
        assert events[0][0] == "mint" and events[1][0] == "reader", \
            "the attempt must exist before the first real reader call"
        assert events[-1][0] == "measure" and not any(e[0] == "abort" for e in events), \
            "a clean matching manifest must complete through measurement, not abort"
        assert events[0][1] == attempted["manifest"], \
            "the exact preregistered manifest, not a lookalike, must ride in the measurement"
        assert _HARNESS_ATTEMPT_GATES[1] in events[0][2]["admissibility_gates"], \
            "the clean-transport assumption must be an explicit gate"

        receipt_events = []
        with tempfile.TemporaryDirectory() as receipt_dir:
            receipted = _run_preregistered_panel(
                good, attempt_spec, tag_reliant, _AttemptProbe(receipt_events),
                receipt_dir=receipt_dir, receipt_stem="panel runspec.json")
            request_paths = [name for name in os.listdir(receipt_dir)
                             if name.endswith(".measurement.json")]
            assert request_paths == [
                "panel-runspec.json.attempt-selftest-attempt.measurement.json"
            ], "a successful attempt must save one deterministic pre-submission request"
            request_path = os.path.join(receipt_dir, request_paths[0])
            with open(request_path, encoding="utf-8") as handle:
                saved_request = json.load(handle)
            filed_request = next(event[1] for event in receipt_events if event[0] == "measure")
            assert saved_request == filed_request == receipted, \
                "the saved request, submitted object and returned measurement must be identical"
            warning = io.StringIO()
            with contextlib.redirect_stdout(warning):
                unsaved = _write_measurement_request(
                    "unwritable", receipted, os.path.join(receipt_dir, "missing"), "runspec")
            assert unsaved is None and "Submission will continue" in warning.getvalue(), \
                "local receipt failure must warn without becoming a new filing gate"

        failed_events = []
        failed_probe = _AttemptProbe(failed_events)
        assert _run_preregistered_panel(bad, attempt_spec, coinflip, failed_probe) is None
        assert failed_events[0][0] == "mint" and failed_events[-1][0] == "abort", \
            "a gated run must close its visible obligation as aborted"
        assert not any(e[0] == "measure" for e in failed_events), \
            "an aborted attempt must never file a measurement"
        assert failed_probe.aborts[-1]["failed_gate"] == "panel harness refused at calibration"

        divergent_events = []
        divergent_probe = _AttemptProbe(divergent_events)
        divergent_calls = {"n": 0}

        def prereg_fault_once(ep, text, q, options):
            divergent_calls["n"] += 1
            if divergent_calls["n"] == 17:  # 16 calibration cells, then first real cell
                raise TransportFault("timeout")
            return tag_reliant(ep, text, q, options)

        assert _run_preregistered_panel(good, attempt_spec, prereg_fault_once,
                                        divergent_probe) is None
        assert divergent_events[-1][0] == "abort"
        assert "diverged" in divergent_events[-1][1]["failed_gate"], \
            "an observed transport receipt must abort, not alter the preregistered manifest"
        assert not any(e[0] == "measure" for e in divergent_events)

        exit_events = []
        exit_probe = _AttemptProbe(exit_events)

        def harness_exit(*_args):
            raise SystemExit("reader configuration changed after mint")

        try:
            _run_preregistered_panel(good, attempt_spec, harness_exit, exit_probe)
            raise AssertionError("SystemExit escaped without closing its attempt")
        except SystemExit as exc:
            assert "configuration changed" in str(exc)
        assert exit_events[0][0] == "mint" and exit_events[-1][0] == "abort", \
            "a normal harness SystemExit after mint must terminalise its obligation"

        class _LostResponseProbe(_AttemptProbe):
            def measure(self, slug, payload):
                self.events.append(("measure-lost", payload))
                raise _SelftestAinglishError(0, {"error": "transport_error",
                                                  "message": "response connection closed"})

            def attempt(self, attempt_id):
                self.events.append(("reconcile", attempt_id))
                return {"attempt_id": attempt_id, "state": "completed",
                        "measurement_ref": "filed-after-lost-response"}

        lost_events = []
        recovered = _run_preregistered_panel(good, attempt_spec, tag_reliant,
                                             _LostResponseProbe(lost_events))
        assert recovered is not None
        assert [e[0] for e in lost_events].count("measure-lost") == 1
        assert lost_events[-1][0] == "reconcile", \
            "a lost write response must reconcile against the immutable attempt before retrying"

        class _OpenThenSuccessProbe(_AttemptProbe):
            def __init__(self, events):
                super().__init__(events)
                self.submissions = 0

            def measure(self, slug, payload):
                self.submissions += 1
                self.events.append(("measure", payload))
                if self.submissions == 1:
                    raise _SelftestAinglishError(0, {"error": "transport_error",
                                                      "message": "nothing reached the server"})
                return {"measurement": {"manifest_hash": "filed-on-exact-retry"}}

            def attempt(self, attempt_id):
                self.events.append(("reconcile-open", attempt_id))
                return {"attempt_id": attempt_id, "state": "open"}

        retry_events = []
        retried = _run_preregistered_panel(good, attempt_spec, tag_reliant,
                                           _OpenThenSuccessProbe(retry_events))
        assert retried is not None and [e[0] for e in retry_events].count("measure") == 2, \
            "an observed-open attempt may retry the same payload once"
        attempt_summary = "attempts mint before reader spend and close on success/refusal"
    else:
        print("selftest note: attempt-lifecycle section SKIPPED — standalone file, no "
              "ainglish.client available; the attempt path itself refuses cleanly without the "
              "package, and the lifecycle assertions run in the packaged checkout and CI.")
        attempt_summary = ("attempt settings still validate standalone (lifecycle assertions "
                           "ran in the packaged checkout)")
    try:
        _attempt_settings({**attempt_spec["attempt"], "mystery": True})
        raise AssertionError("an unknown attempt setting was silently ignored")
    except SystemExit as exc:
        assert "unknown runspec.attempt" in str(exc)

    saved_openai_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        try:
            _validate_real_reader_configuration(
                {"panel": [{"name": "unfunded", "provider": "openai", "model": "gpt-test"}]}, ask)
            raise AssertionError("a missing built-in provider key reached attempt minting")
        except SystemExit as exc:
            assert "before attempt mint" in str(exc) and "OPENAI_API_KEY" in str(exc)
        assert run_panel(dict(good, panel=[{"name": "unfunded", "provider": "openai",
                                           "model": "gpt-test"}]), ask_fn=ask) is None, \
            "ordinary non-attempt runs must validate every built-in reader before inference"
        os.environ["OPENAI_API_KEY"] = "sentinel"
        try:
            _validate_real_reader_configuration(
                {"panel": [{"name": "cleartext", "provider": "openai", "model": "gpt-test",
                            "base_url": "http://api.example.test/v1"}]}, ask)
            raise AssertionError("a cleartext provider key destination reached attempt minting")
        except SystemExit as exc:
            assert "before attempt mint" in str(exc) and "without HTTPS" in str(exc)
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
        try:
            _validate_real_reader_configuration(
                {"panel": [{"name": "bad-bound", "provider": "ollama", "model": "m",
                            "max_tokens": False}]}, ask)
            raise AssertionError("an invalid transport bound reached attempt minting")
        except SystemExit as exc:
            assert "before attempt mint" in str(exc) and "positive integer" in str(exc)
    finally:
        if saved_openai_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_openai_key

    print("\nselftest OK: real effect measured by a calibrated panel; uncalibrated panel refused; "
          "arms ship with the payload; unpinned/tampered/swapped item sets refuse; robustness v4 "
          "censors floors beside their uncensored twin; " + attempt_summary + "; absence is ONE "
          "predicate (typed, mutation-verified, decision-surface swept).")


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
     "question": "Could a stranger have observed this evidence?", "options": ["yes", "no", "cannot tell"], "answer": "yes"},
    {"id": "r2",
     "english": "The receipt matched, and the evidence generator is of class public-path.",
     "ainglish": "The receipt matched wit(public-path).",
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
        doc = json.loads(_open(
            urllib.request.Request(url_or_path, headers={"User-Agent": USER_AGENT}),
            timeout=45).read())
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


def dry_reader(items, manifest=None):
    """Factory for the --dry-run mock: an ORACLE that answers the ainglish arm perfectly and
    guesses the english arm. It cheats, openly — a dry run verifies PLUMBING (fetch, digest pin,
    guards, calibration gate, scoring, bootstrap, resample, payload shape), not language, and a
    mock that had to genuinely comprehend would just be a worse panel. Zero API calls; the emitted
    payload is stamped DRY-RUN and refuses submission, so the cheat cannot leak into evidence."""
    by_key = {}
    if manifest is not None and manifest.get("metric") == "robustness_delta":
        # Robustness asks texts the real-item map never contains: the calibration set and every
        # corrupted variant (@dexagon-ai #11 finding 5 — the plain oracle answered both
        # calibration arms with its unknown-text fallback and the gate refused the dry run).
        # Deterministic behaviour mirroring the selftest oracle: intact anything reads correctly,
        # EXCEPT the calibration english arm (that unreadability IS the planted effect); english
        # survives its corruption, ainglish misreads under it.
        seed = manifest.get("seed", 0)
        channel = (manifest.get("corruption") or {}).get("channel", "drop_token")
        table = {}  # text -> (correct_answer, reads_correctly)
        for it in items:
            for arm in ("english", "ainglish"):
                table[it[arm]] = (str(it["answer"]), True)
                corrupted = corrupt(it[arm], f"{seed}:{it['id']}:{arm}", channel)
                table[corrupted] = (str(it["answer"]), arm == "english")
        for it in manifest.get("calibration_items", []):
            table[it["ainglish"]] = (str(it["answer"]), True)
            table[it["english"]] = (str(it["answer"]), False)  # the planted effect: unreadable arm

        def robustness_oracle(ep, text, q, options):
            opts = [str(o) for o in options]
            correct, reads = table.get(text, (opts[-1], False))
            if reads and correct in opts:
                return correct
            return next((o for o in opts if o != correct), opts[-1])  # deterministic miss

        return robustness_oracle
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


_DRY_PROTOCOL_SUFFIX = " [DRY-RUN: mock oracle readers — plumbing verification, NOT a measurement]"
_ATTEMPT_KEYS = frozenset({"estimand", "admissibility_gates", "planned_sample", "proposal_revision"})
_HARNESS_ATTEMPT_GATES = (
    "panel harness emits a measurement (calibration, yield, and protocol gates pass)",
    "filed manifest matches the preregistered clean-run manifest (no transport faults or bound truncations)",
)


def _attempt_settings(raw):
    """Validate the optional runspec attempt block before minting or buying a reader cell."""
    if not isinstance(raw, dict):
        raise SystemExit("REFUSING: runspec.attempt must be an object, or be omitted entirely.")
    unknown = sorted(set(raw) - _ATTEMPT_KEYS)
    if unknown:
        raise SystemExit("REFUSING: unknown runspec.attempt key(s): %s. Accepted: %s."
                         % (", ".join(unknown), ", ".join(sorted(_ATTEMPT_KEYS))))
    estimand = raw.get("estimand")
    gates = raw.get("admissibility_gates")
    sample = raw.get("planned_sample")
    if not isinstance(estimand, str) or not estimand.strip():
        raise SystemExit("REFUSING: runspec.attempt.estimand must be a non-empty string.")
    if not isinstance(gates, list) or not gates:
        raise SystemExit("REFUSING: runspec.attempt.admissibility_gates must be a non-empty array.")
    if not isinstance(sample, dict) or not sample:
        raise SystemExit("REFUSING: runspec.attempt.planned_sample must be a non-empty object.")
    revision = raw.get("proposal_revision")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise SystemExit("REFUSING: runspec.attempt.proposal_revision must be a non-empty string when present.")

    # The clean preview below commits to zero transport faults/truncations. That assumption is an
    # admissibility gate whether or not a runspec author remembered to spell it out, so freeze it
    # explicitly rather than abort later under an undeclared condition.
    frozen_gates = list(gates)
    for gate in _HARNESS_ATTEMPT_GATES:
        if gate not in frozen_gates:
            frozen_gates.append(gate)
    return {"estimand": estimand.strip(), "admissibility_gates": frozen_gates,
            "planned_sample": sample, "proposal_revision": revision.strip() if revision else None}


def _planned_panel_manifest(manifest):
    """Derive the exact clean-run manifest without calling a real reader.

    The panel receipt records observed transport faults and bound truncations inside the filed
    manifest. A clean run is therefore the only final manifest knowable before spend. The dry
    oracle builds that manifest from frozen inputs; only its loud non-evidence protocol suffix is
    removed. If the real run later records a fault, the commitment differs and the attempt aborts
    instead of filing a changed design under the preregistration.
    """
    import contextlib
    import io

    preview = dict(manifest)
    preview["_dry_run"] = True
    with contextlib.redirect_stdout(io.StringIO()):
        measurement = run_panel(preview, ask_fn=dry_reader(preview["items"], preview))
    if measurement is None or _is_panel_refusal(measurement):
        raise SystemExit("REFUSING before attempt mint: the zero-cost dry preview could not emit "
                         "the manifest this run would preregister. Run --dry-run for the refusal.")
    planned = json.loads(json.dumps(measurement["manifest"]))
    protocol = planned.get("protocol", "")
    if not protocol.endswith(_DRY_PROTOCOL_SUFFIX):
        raise SystemExit("REFUSING before attempt mint: dry preview lost its non-evidence stamp; "
                         "the harness cannot safely derive a real-run commitment.")
    planned["protocol"] = protocol[:-len(_DRY_PROTOCOL_SUFFIX)]
    return planned


def _validate_real_reader_configuration(manifest, ask_fn, context="attempt mint"):
    """Refuse deterministic reader configuration faults before inference or attempt minting.

    The free manifest preview deliberately uses mock readers, so it cannot discover a missing
    provider key or an incomplete transport entry. Those are not experimental outcomes and must
    not create an open preregistration obligation. Custom/injected readers own their own transport
    contract; this check applies only to the built-in ``ask`` path used by the CLI.
    """
    if ask_fn is not ask:
        return
    for endpoint in manifest.get("panel", []):
        try:
            resolved = resolve(endpoint)
            bounds = bounds_for(endpoint)
            temperature_for(endpoint)
        except SystemExit as exc:
            raise SystemExit(f"REFUSING before {context}: {exc}") from None
        name = resolved.get("name", "?")
        if not resolved.get("model"):
            raise SystemExit(f"REFUSING before {context}: panel entry {name!r} needs a non-empty model.")
        # Validate every setting consumed by chat(), without making a network call.
        for bound, value in bounds.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise SystemExit(f"REFUSING before {context}: panel entry {name!r} needs {bound} "
                                 "to be a positive integer.")
        key_env = resolved.get("api_key_env") or ""
        key = os.environ.get(key_env, "") if key_env else ""
        if key_env and not key:
            raise SystemExit(f"REFUSING before {context}: panel entry {name!r} needs {key_env}, "
                             "but it is not set. Export the key or drop the member.")
        if key:
            try:
                _require_secure_credential_url(resolved["base_url"], f"panel entry {name!r}")
            except ValueError as exc:
                raise SystemExit(f"REFUSING before {context}: {exc}") from None


class _Transcript:
    """Mirror panel output to the terminal while retaining an abort-receipt digest."""
    def __init__(self, target):
        import io
        self._target = target
        self._buffer = io.StringIO()

    def write(self, value):
        self._target.write(value)
        return self._buffer.write(value)

    def flush(self):
        self._target.flush()

    def text(self):
        return self._buffer.getvalue()


def _abort_panel_attempt(client, attempt_id, slug, failed_gate, details, receipt_dir=None,
                         receipt_stem="runspec"):
    receipt = {
        "kind": "ainglish.panel.abort-receipt.v1",
        "attempt_id": attempt_id,
        "proposal": slug,
        "failed_gate": failed_gate,
        "details": details,
    }
    encoded = json.dumps(receipt, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    if receipt_dir:
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", receipt_stem).strip("-") or "runspec"
        path = os.path.join(receipt_dir, f"{safe_stem}.attempt-{attempt_id}.abort.json")
        with open(path, "wb") as handle:
            handle.write(encoded + b"\n")
        print(f"ABORT RECEIPT: {path} (sha256 {digest})")
    client.abort_attempt(attempt_id, failed_gate=failed_gate, preflight_receipt_hash=digest)
    print(f"ATTEMPT ABORTED: {attempt_id} — {failed_gate}")


def _write_cell_results(attempt_id, slug, rows, receipt_dir, receipt_stem):
    """Persist normalized comprehension-cell answers beside an attempt, never in its API payload.

    The aggregate is sufficient for the register's scalar, but not for a preregistered stratum
    claim. A local sidecar keeps that audit surface without expanding the server schema or putting
    observed answers inside the preregistered manifest commitment. Calibration rows are omitted;
    a calibration refusal therefore produces an explicit zero-row receipt.
    """
    if not receipt_dir:
        return None
    document = {
        "kind": "ainglish.panel.cell-results.v1",
        "attempt_id": attempt_id,
        "proposal": slug,
        "real_cells_recorded": len(rows),
        "rows": rows,
    }
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", receipt_stem).strip("-") or "runspec"
    path = os.path.join(receipt_dir, f"{safe_stem}.attempt-{attempt_id}.cells.json")
    with open(path, "wb") as handle:
        handle.write(encoded + b"\n")
    print(f"CELL RESULTS: {path} ({len(rows)} real cell(s), sha256 {digest})")
    return {"path": path, "sha256": digest, "real_cells_recorded": len(rows)}


def _write_measurement_request(attempt_id, measurement, receipt_dir, receipt_stem):
    """Persist the exact JSON request object before its first submission attempt.

    The register is authoritative after a successful filing, but a response-bearing rejection or
    an unreconciled lost response can otherwise leave an expensive panel result only in terminal
    scrollback. Saving is deliberately advisory: an unwritable directory warns but never turns a
    valid experimental result into a new process gate.
    """
    if not receipt_dir:
        return None
    import tempfile

    encoded = json.dumps(measurement, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", receipt_stem).strip("-") or "runspec"
    path = os.path.join(receipt_dir, f"{safe_stem}.attempt-{attempt_id}.measurement.json")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{safe_stem}.measurement-", dir=receipt_dir)
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as exc:
        print(f"MEASUREMENT REQUEST WARNING: could not save the pre-submission request in "
              f"{receipt_dir}: {exc}. Submission will continue; preserve the printed payload.")
        return None
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    print(f"MEASUREMENT REQUEST: {path} (sha256 {digest})")
    return {"path": path, "sha256": digest}


def _run_preregistered_panel(manifest, spec, ask_fn, client, receipt_dir=None,
                             receipt_stem="runspec"):
    """Mint -> spend -> complete/abort, with no real reader call before the mint."""
    import contextlib
    from ainglish.client import AinglishError, manifest_commitment

    settings = _attempt_settings(spec["attempt"])
    _validate_real_reader_configuration(manifest, ask_fn)
    planned = _planned_panel_manifest(manifest)
    opened = client.mint_attempt(
        spec["slug"], planned,
        estimand=settings["estimand"],
        admissibility_gates=settings["admissibility_gates"],
        planned_sample=settings["planned_sample"],
        proposal_revision=settings["proposal_revision"],
    )
    attempt_id = opened["attempt"]["attempt_id"]
    expected = manifest_commitment(planned)
    print(f"ATTEMPT MINTED BEFORE READER SPEND: {attempt_id} (manifest {expected})")

    transcript = _Transcript(sys.stdout)
    # The comprehension path has one answer per scored cell. Robustness has four condition cells
    # and a complete-quartet estimator, so a flat answer sidecar would misstate its sampling unit;
    # leave that path unchanged until it has a quartet-shaped receipt of its own.
    cell_results = [] if manifest.get("metric") != "robustness_delta" else None
    try:
        with contextlib.redirect_stdout(transcript):
            measurement = run_panel(manifest, ask_fn=ask_fn, cell_results=cell_results)
    except (Exception, SystemExit) as exc:
        _abort_panel_attempt(client, attempt_id, spec["slug"],
                             "panel harness raised before measurement emission",
                             {"exception": type(exc).__name__, "message": str(exc),
                              "transcript": transcript.text()},
                             receipt_dir, receipt_stem)
        raise
    cell_receipt = (_write_cell_results(
        attempt_id, spec["slug"], cell_results, receipt_dir, receipt_stem
    ) if cell_results is not None else None)
    if _is_panel_refusal(measurement):
        _abort_panel_attempt(client, attempt_id, spec["slug"],
                             "panel harness refused at calibration",
                             {"refusal": measurement, "cell_results": cell_receipt,
                              "transcript": transcript.text()},
                             receipt_dir, receipt_stem)
        return None
    if measurement is None:
        _abort_panel_attempt(client, attempt_id, spec["slug"],
                             "panel harness emitted no measurement",
                             {"cell_results": cell_receipt, "transcript": transcript.text()},
                             receipt_dir, receipt_stem)
        return None

    actual = manifest_commitment(measurement["manifest"])
    if actual != expected:
        _abort_panel_attempt(client, attempt_id, spec["slug"],
                             "filed manifest diverged from preregistered clean-run manifest",
                             {"expected_manifest_commitment": expected,
                              "actual_manifest_commitment": actual,
                              "transcript": transcript.text()},
                             receipt_dir, receipt_stem)
        return None

    measurement["attempt_id"] = attempt_id
    request_receipt = _write_measurement_request(
        attempt_id, measurement, receipt_dir, receipt_stem)
    response = None
    for submission in range(2):
        try:
            response = client.measure(spec["slug"], measurement)
            break
        except AinglishError as exc:
            # A response-bearing 4xx/5xx is unambiguous: the server answered, so preserve its
            # refusal. Only transport loss or an unreadable successful response can conceal a
            # committed measurement. Reconcile those against the public attempt record before a
            # single exact-payload retry; attempt completion is atomic with measurement filing.
            if exc.error == "invalid_response" and exc.status not in (0, 502):
                raise
            if exc.error not in ("transport_error", "invalid_response"):
                raise
            try:
                state = client.attempt(attempt_id)
            except Exception:
                print(f"SUBMISSION STATUS UNKNOWN: {attempt_id}. The response was lost and the "
                      "attempt record could not be read; do not abort or change the manifest. "
                      "Inspect client.attempt(attempt_id) before retrying the same payload."
                      + (f" Exact request: {request_receipt['path']}." if request_receipt else ""))
                raise exc
            if state.get("state") == "completed":
                response = {"attempt": state, "recovered_after_lost_response": True}
                print(f"SUBMISSION CONFIRMED FROM ATTEMPT RECORD: {attempt_id} completed as "
                      f"{state.get('measurement_ref') or 'a filed measurement'}.")
                break
            if state.get("state") != "open" or submission == 1:
                print(f"SUBMISSION NOT CONFIRMED: {attempt_id} is {state.get('state', 'unknown')}. "
                      "The exact payload remains safe to inspect/retry only while it is open."
                      + (f" Exact request: {request_receipt['path']}." if request_receipt else ""))
                raise exc
            print(f"SUBMISSION RESPONSE LOST: {attempt_id} is still open; retrying the exact "
                  "manifest and attempt id once.")
    print("SUBMITTED:", json.dumps(response, ensure_ascii=False)[:400])
    return measurement


def mint_colony_access_token(colony, key, totp=None):
    """Mint a Colony access token, resolving a callable TOTP at the moment of the request.

    This is shared by tools that need Colony's own API and by the stdlib OIDC exchange fallback;
    keeping the credentialled POST in one guarded implementation prevents 2FA and redirect safety
    from drifting between command-line harnesses.
    """
    _require_secure_credential_url(colony, "Colony token exchange")
    code = totp() if callable(totp) else totp
    body = {"api_key": key}
    if code:
        body["totp_code"] = str(code)
    req = urllib.request.Request(
        f"{colony.rstrip('/')}/api/v1/auth/token",
        data=json.dumps(body).encode(),
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        method="POST",
    )
    with _open(req, timeout=45, sensitive=True) as resp:
        token = json.loads(resp.read()).get("access_token") or ""
    if not token:
        raise RuntimeError("Colony token endpoint returned no access_token — refusing an unauthenticated continuation.")
    return token


def mint_id_token(colony, client_id, key, totp=None):
    """Exchange a Colony agent key for an ainglish-audienced id_token (RFC 8693, ~5 min lifetime).

    colony-sdk first when installed — the platform maintains its own exchange, and it is authored
    by the same party the key is already being sent to, so the trust boundary does not move.
    Pure-stdlib fallback keeps the curl-ed single file and zero-dep installs first-class. ONLY
    ImportError falls back: an installed SDK that fails is a real error, and silently switching
    paths would bury it under a second failure envelope. This library helper never writes to
    stdout: callers producing machine-readable output must not gain an authentication preamble.

    totp: for 2FA-enabled Colony accounts (@Rosetta, 0.2.1 feedback: the key path 401'd with
    AUTH_2FA_REQUIRED and nothing on this side could supply the code). A string, or a zero-arg
    callable returning one (mirrors colony-sdk's own parameter); resolved at mint time because
    codes are short-lived and a re-mint needs a FRESH one. CLI paths read AINGLISH_TOTP.
    """
    _require_secure_credential_url(colony, "Colony OIDC exchange")
    try:
        import colony_sdk
    except ImportError:
        pass
    else:
        r = colony_sdk.ColonyClient(api_key=key, base_url=f"{colony}/api/v1", totp=totp).exchange_token(
            audience=client_id, scope=AINGLISH_OIDC_SCOPE)
        tok = r.get("id_token") or ""
        if not tok:
            raise RuntimeError("colony-sdk exchange_token returned no id_token — SDK contract drift; "
                               "report it (or uninstall colony-sdk to use the stdlib exchange).")
        return tok
    import urllib.parse
    import urllib.request

    def post(url, data, headers):
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **headers},
                                     method="POST")
        # Both calls carry credentials (first the raw Colony key, then the subject token in the
        # form body). A 307/308 can replay a POST body, so protecting headers alone is insufficient.
        with _open(req, timeout=45, sensitive=True) as resp:
            return json.loads(resp.read())

    jwt = mint_colony_access_token(colony, key, totp=totp)
    form = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": jwt, "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "audience": client_id, "scope": AINGLISH_OIDC_SCOPE}).encode()
    exchanged = post(f"{colony}/oauth/token", form, {"Content-Type": "application/x-www-form-urlencoded"})
    tok = exchanged.get("id_token") if isinstance(exchanged, dict) else ""
    if not tok:
        raise RuntimeError("Colony OIDC exchange returned no id_token — refusing an unauthenticated continuation.")
    return tok


def submit_measurement(measurement, slug):
    """Submission, least-privilege first. Two credentials work, and the register only ever sees
    the NARROW one either way:

      AINGLISH_ID_TOKEN   (preferred) an id_token you already exchanged, audienced to
                          ainglish.org's client_id — mint it with your own SSO tooling and hand
                          this process nothing else. Audience-scoping makes it useless anywhere
                          but ainglish.org, and it expires in ~5 minutes. Least privilege.
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
        _require_secure_credential_url(url, "Ainglish measurement submission")
        req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT, **(headers or {})},
                                     method="POST")
        with _open(req, timeout=45, sensitive=True) as r:
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


def _usage():
    return (__doc__.strip().split("\n\n")[0]
            + "\n\nusage: panel.py manifest.json            (items inline)"
              "\n       panel.py run runspec.json [--dry-run | --submit]   "
              "(items fetched by URL, digest-pinned)"
              "\n       panel.py --demo-manifest | --selftest"
              "\n       panel.py --help")


def _parse_cli(argv):
    """Parse the deliberately small CLI, refusing every ignored or contradictory token.

    This is kept local rather than delegated to an application framework because panel.py is a
    served standalone instrument. A typo in ``--dry-run`` must never fall through to a paid real
    run, and a stray flag must never be silently absent from the experiment an operator thought
    they requested.
    """
    if len(argv) == 1 or (len(argv) == 2 and argv[1] in ("-h", "--help")):
        return {"command": "help"}
    if argv[1] in ("--selftest", "--demo-manifest"):
        if len(argv) != 2:
            raise SystemExit("REFUSING: %s takes no additional arguments." % argv[1])
        return {"command": argv[1][2:].replace("-", "_")}
    if argv[1] == "run":
        if len(argv) < 3:
            raise SystemExit("ainglish-panel run needs a runspec path (or - for stdin).")
        path, flags = argv[2], argv[3:]
        allowed = {"--dry-run", "--submit"}
        unknown = [value for value in flags if value not in allowed]
        if unknown:
            raise SystemExit(
                "REFUSING: unknown panel run argument(s): %s. Accepted: --dry-run or --submit."
                % ", ".join(unknown))
        duplicates = sorted({value for value in flags if flags.count(value) > 1})
        if duplicates:
            raise SystemExit("REFUSING: duplicate panel run argument(s): %s."
                             % ", ".join(duplicates))
        if "--dry-run" in flags and "--submit" in flags:
            raise SystemExit(
                "REFUSING: --dry-run and --submit are mutually exclusive; choose the free "
                "preview or the real filing run.")
        return {"command": "run", "path": path,
                "dry_run": "--dry-run" in flags, "submit": "--submit" in flags}
    if argv[1].startswith("-") and argv[1] != "-":
        raise SystemExit("REFUSING: unknown panel command or option %r. Use --help." % argv[1])
    if len(argv) != 2:
        raise SystemExit(
            "REFUSING: inline-manifest mode accepts exactly one path (or - for stdin); "
            "unexpected argument(s): %s" % ", ".join(argv[2:]))
    return {"command": "manifest", "path": argv[1]}


def main(argv):
    parsed = _parse_cli(argv)
    if parsed["command"] == "selftest":
        selftest(); return 0
    if parsed["command"] == "demo_manifest":
        print(DEMO_NOTE); return 0
    if parsed["command"] == "help":
        print(_usage())
        return 0
    if parsed["command"] == "run":
        path = parsed["path"]
        spec = json.loads(sys.stdin.read() if path == "-" else open(path).read())
        items, digest = fetch_items(spec["items_url"], spec.get("items_sha256"))
        manifest = dict(spec, items=items, items_sha256=digest)
        dry = parsed["dry_run"]
        if "attempt" in spec:
            _attempt_settings(spec["attempt"])
        if dry:
            manifest["_dry_run"] = True
        if "attempt" in spec and not dry:
            if not parsed["submit"]:
                raise SystemExit("REFUSING before reader spend: this runspec declares an attempt, "
                                 "so a real run needs --submit to close it atomically with its "
                                 "measurement. Use --dry-run for the zero-cost preview.")
            try:
                from ainglish.client import AinglishClient
            except ImportError:
                raise SystemExit("runspec.attempt needs the installed ainglish package so the "
                                 "panel and attempt client share one canonicalizer: pip install ainglish")
            client = AinglishClient(
                base_url=os.environ.get("AINGLISH_BASE", "https://ainglish.org"),
                colony_base=os.environ.get("COLONY_BASE", "https://thecolony.ai"),
            )
            receipt_dir = os.getcwd() if path == "-" else os.path.dirname(os.path.abspath(path))
            receipt_stem = "stdin-runspec" if path == "-" else os.path.basename(path)
            return 0 if _run_preregistered_panel(
                manifest, spec, ask, client, receipt_dir, receipt_stem) is not None else 1
        m = run_panel(manifest, ask_fn=dry_reader(items, manifest) if dry else ask)
        if m is None or _is_panel_refusal(m):
            return 1
        if dry:
            print("\nDRY RUN complete: pipeline + payload verified, zero API calls. The payload above "
                  "is stamped DRY-RUN inside its own manifest — not submittable as evidence.")
            return 0
        if parsed["submit"]:
            submit_measurement(m, spec["slug"])
        return 0
    path = parsed["path"]
    manifest = json.loads(sys.stdin.read() if path == "-" else open(path).read())
    result = run_panel(manifest)
    return 1 if result is None or _is_panel_refusal(result) else 0


def cli():
    raise SystemExit(main(sys.argv))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
