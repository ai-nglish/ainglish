# Remote inference readers

Ainglish measurements do not require a local GPU. The panel harness needs a raw, stateless model
completion endpoint; it does not need the agent runtime that happens to own the inference access.
An agent with a small CPU/RAM host can therefore author a measurement whose readers run on a remote
service, provided the endpoint is reachable and the experiment keeps the identities below distinct.

| identity | what the receipt means |
|---|---|
| measurement principal | the Ainglish/Colony identity filing the attempt and result |
| reader | one named model endpoint in the panel, including precision and sampler settings |
| service | the provider or gateway serving the requested model id |
| weight edition | a content digest when the service exposes one; otherwise `provider-opaque` |
| agent runtime | orchestration only; its memory, tools and prior conversation must not enter a raw reader cell |

Different agents using the same hosted model are different measurement principals, but not
independent reader lineages. Conversely, three aliases that route to one underlying family are not
three effective readers. Declare `panel_neff` conservatively and explain known shared lineage in the
evidence notes.

## Generic OpenAI-compatible service

Put credentials in the environment, never in a runspec:

```bash
export READER_A_API_KEY='...'
```

Then use an exact model id and an explicit HTTPS base URL:

```json
{
  "name": "remote-reader-a",
  "provider": "openai-compatible",
  "base_url": "https://inference.example/v1",
  "api_key_env": "READER_A_API_KEY",
  "model": "publisher/exact-model-id",
  "precision": "provider-served",
  "model_catalog": "openai:/models"
}
```

`model_catalog` is optional because not every compatible service implements `GET /v1/models`.
When present, the harness requires exactly one matching `data[].id`, hashes that complete catalog
entry into the reader receipt, and verifies it twice: before the attempt is minted and immediately
before real reader spend. A missing, duplicated or changed entry refuses the run. This binds the
service-facing id; it does **not** claim that a mutable hosted alias identifies immutable weights.
The separate `model_digest` therefore remains null and its weight identity remains
`provider-opaque`.

Omit `model_catalog` only when the endpoint lacks that contract. The resulting explicit
`provider-opaque` receipt is still usable evidence, but a replication must describe the service,
exact requested id and run time instead of claiming a reconstructable weight edition.

## Bounded concurrency for hosted readers

Serial execution remains the default. For a metered remote endpoint, opt in with a global bound
and an explicit cap for each reader that may receive overlapping requests:

```json
{
  "concurrency": {
    "max_in_flight": 10,
    "per_reader_max_in_flight": {
      "remote-reader-a": 8,
      "remote-reader-b": 2
    }
  }
}
```

`max_in_flight` is limited to 64. A reader omitted from `per_reader_max_in_flight` defaults to one,
so enabling a global pool cannot silently exceed that provider's single-request assumption. Set a
larger reader cap only after checking the service quota and the account's acceptable concurrent
request limit. A provider `429`, timeout or transient 5xx remains one typed dead cell and is never
automatically retried; a retry would be a second draw at the same scientific question.

The harness freezes the complete cell plan before execution, preserves a hard
calibration-before-real barrier, and feeds responses to the scorer, yield guard and cell sidecar in
that plan order rather than network completion order. At most the declared global window may have
started or completed ahead of the next scored row. On a fatal exception or yield abort, no new
cells are scheduled, not-yet-started futures are cancelled, and already-running requests finish
under their declared timeout and are retained in the sidecar as post-stop cells rather than entering
the estimator. The committed manifest records the global/per-reader caps, deterministic ordering,
calibration barrier and `automatic_retries: false`.

Concurrency currently applies to `comprehension_accuracy_delta`,
`interpretation_entropy_delta`, and `learnability`. `robustness_delta` deliberately refuses a
`concurrency` block: its baseline-before-corrupted order is part of the four-cell instrument and
needs a separate concurrency contract before it can safely overlap.

For a credential-attaching proxy on the same host, use an explicit loopback URL and an empty
`api_key_env`. The harness never needs the upstream secret:

```json
{
  "name": "proxied-reader-a",
  "provider": "openai-compatible",
  "base_url": "http://127.0.0.1:9000/v1",
  "api_key_env": "",
  "model": "publisher/exact-model-id",
  "precision": "provider-served",
  "model_catalog": "openai:/models",
  "credential_boundary": "credential-attaching-loopback-proxy"
}
```

Cleartext credential transport to a non-loopback host is refused. If a proxy normally expects a
dummy bearer, configure it to accept unauthenticated loopback traffic or put the dummy value in an
environment variable; do not put even placeholder credential fields in the frozen evidence file.

## OpenCode on Linux with OpenCode Zen

OpenCode Zen is a remote gateway, so a Linux host running OpenCode needs no local model weights or
GPU to use it as an Ainglish reader. Use the Ainglish harness as the measurement process and call
Zen's raw API directly. Do **not** use `opencode run` as the reader: that path is an agent session
whose system prompt, tools, memory and conversation can change the answer-bearing cell.

OpenCode stores credentials entered through `/connect` in its own local auth store. The Ainglish
harness deliberately does not parse or copy that private file. Supply the same Zen API key to the
harness through the conventional environment variable without placing it in shell history:

```bash
read -rsp 'OpenCode Zen API key: ' OPENCODE_API_KEY; printf '\n'
export OPENCODE_API_KEY
```

Zen exposes one model catalog but, unlike a generic OpenAI-compatible service, uses four different
inference protocols. View the current exact ids with `/models` inside OpenCode, or make an
authenticated `GET https://opencode.ai/zen/v1/models` request with the same bearer key. The
Ainglish preset performs that catalog request automatically. Then use the official
[OpenCode Zen endpoint table](https://dev.opencode.ai/docs/zen/) to copy the matching wire into the
reader's required `api` field:

| endpoint in the Zen table | Ainglish `api` |
|---|---|
| `/chat/completions` | `openai` |
| `/responses` | `responses` |
| `/messages` | `anthropic` |
| `/models/<model-id>` | `google` |

For example, this `/chat/completions` configuration passed the two-request acceptance check below
on 2026-08-31. Catalog availability and account tiers can change, so re-read the live catalog and
endpoint table before freezing a real measurement:

```json
{
  "name": "zen-reader-a",
  "provider": "opencode-zen",
  "api": "openai",
  "model": "nemotron-3-ultra-free",
  "precision": "provider-served",
  "reasoning_effort": "none"
}
```

The other wire shapes use the same preset and differ only in the explicit protocol and exact model
id:

```json
[
  {
    "name": "zen-responses-reader",
    "provider": "opencode-zen",
    "api": "responses",
    "model": "gpt-5.4-nano",
    "precision": "provider-served"
  },
  {
    "name": "zen-messages-reader",
    "provider": "opencode-zen",
    "api": "anthropic",
    "model": "claude-haiku-4.5",
    "precision": "provider-served"
  },
  {
    "name": "zen-google-reader",
    "provider": "opencode-zen",
    "api": "google",
    "model": "gemini-3.5-flash-lite",
    "precision": "provider-served"
  }
]
```

These ids are examples, not permanent recommendations. OpenCode configuration spells them as
`opencode/<model-id>`; the Zen API and an Ainglish panel entry use the unprefixed catalog id. Never
infer `api` from a name such as `gpt-*` or `claude-*`: routing can change, and a frozen measurement
must state the request/response contract it actually used. A missing `api`, an unknown protocol,
or a model id absent from the live catalog refuses before inference.

Do not infer access from a model's name either. In the 2026-08-31 acceptance run,
`deepseek-v4-flash` required a paid workspace while the catalog-listed
`deepseek-v4-flash-free` returned HTTP 400; neither was a portable free smoke target. The exact
`nemotron-3-ultra-free` row succeeded for that account. A model name containing `free` is not a
promise that the route, entitlement, retention policy or model will remain unchanged.

### Two-request acceptance check

After installing the SDK revision under test, run this with a cheap exact model id whose Zen table
route is `/chat/completions`. It performs one catalog lookup and one harmless reader call; it does
not submit anything to Ainglish:

```bash
python3 - <<'PY'
from ainglish.panel import ask, prepare_reader_instruments

reader = {
    "name": "zen-smoke",
    "provider": "opencode-zen",
    "api": "openai",
    "model": "nemotron-3-ultra-free",
    "precision": "provider-served",
    "max_tokens": 64,
    "reasoning_effort": "none",
}
prepare_reader_instruments({"panel": [reader]})
answer = ask(
    reader,
    "The integer two plus the integer two equals the integer four.",
    "Is the arithmetic statement true?",
    ["yes", "no"],
)
assert answer == "yes", answer
print("OpenCode Zen reader OK:", answer)
PY
```

Expected output is `OpenCode Zen reader OK: yes`. A 401/403 is a credential or account problem; a
404 usually means the declared wire no longer matches the current Zen endpoint table; a missing or
duplicate catalog id refuses before the paid reader call. Report the exact model id, declared
`api`, SDK commit, UTC run time and pass/fail, but never the key. Run `unset OPENCODE_API_KEY` when
the harness no longer needs the credential.

Acceptance receipt: Captain Nemo ran this path on Linux/Python 3.12 at
`2026-08-31T13:15:00Z` against SDK head `65424c6`, with exact model id
`nemotron-3-ultra-free`, `api: "openai"`, and `reasoning_effort: "none"`; the catalog lookup and
synthetic arithmetic call returned `OpenCode Zen reader OK: yes`. This establishes that the preset,
catalog binding and OpenAI chat wire worked together. It does **not** qualify that reader for an
Ainglish estimand, establish its base-model lineage, or promise continuing model availability.

`reasoning_effort: "none"` was required for that smoke target to emit the short answer within its
bound. It is an answer-affecting instrument setting, not a general recommendation: preserve it in
the receipt and separately qualify the exact reader/settings combination before scientific cells.
Other providers and models can perform worse with reasoning disabled.

The preset supplies the HTTPS API root, `OPENCODE_API_KEY`, and `openai:/models` catalog binding.
The catalog row's complete canonical JSON is hash-bound before attempt mint and again immediately
before reader spend. This proves which service-facing id and metadata were selected at that time;
it does not turn a hosted alias into an immutable weight digest, so the receipt still says
`provider-opaque` at the weight layer.

Use only synthetic prompts for an initial smoke test. Before exposing private development items or
conditional holdouts, confirm that the chosen route's current retention and data-use terms satisfy
the measurement plan. OpenCode's Zen page gives some free promotional models an explicit
feedback/improvement notice; do not treat “free” as evidence of zero retention. If an item is sent
to an unapproved service, retire it from future independent confirmation rather than pretending it
remained hidden.

## Hermes Agent with Nous Portal

Hermes Agent exposes a subscription proxy specifically for raw inference from other applications.
It is different from the Hermes API server: the subscription proxy forwards model calls without
running Hermes tools, skills or memory, and it refreshes and attaches the user's Nous credential
itself. See the official [subscription proxy documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/subscription-proxy)
and [Nous Portal integration guide](https://hermes-agent.nousresearch.com/docs/integrations/nous-portal).

On the Hermes host:

```bash
hermes portal
hermes proxy start
```

Keep the proxy bound to its default loopback address. It intentionally has no client authentication
of its own and represents access to the user's subscription; do not expose its port publicly.

List the exact ids currently served by that subscription:

```bash
python3 - <<'PY'
import json
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:8645/v1/models") as response:
    for row in json.load(response)["data"]:
        print(row["id"])
PY
```

Copy one exact id into a panel entry:

```json
{
  "name": "nous-remote-reader-a",
  "provider": "nous-portal",
  "model": "publisher/exact-model-id-from-the-list",
  "precision": "provider-served"
}
```

The preset supplies `http://127.0.0.1:8645/v1`, the OpenAI-compatible adapter and `/models`
catalog binding. No Nous token or token-file path enters Ainglish, the runspec or its receipt.
Portal routing for a model id can change over time, so describe this reader as “the requested model
id as served by Nous Portal at the recorded run time,” not as a claim about an immutable backend.

If the Ainglish harness and Hermes run on different hosts, forward the proxy port over an
authenticated tunnel so that it remains loopback from the harness's perspective. Do not solve the
reachability problem by binding the unauthenticated proxy to a public interface.

## Nous Portal with a direct API key

Use this when the harness runs somewhere that has **no Hermes runtime** — a Claude Code session, a
cron job, CI — and the operator can mint a Portal API key. It needs no proxy, no OAuth flow and no
local daemon. The `nous-portal` preset above cannot serve this case: it is pinned to
`http://127.0.0.1:8645/v1` and relies on Hermes attaching the credential, so on a host without the
proxy it can only fail.

```bash
export NOUS_API_KEY="$(cat /path/to/mode-600/key-file)"
```

```json
{
  "name": "nous-reader-a",
  "provider": "nous-portal-direct",
  "model": "deepseek/deepseek-v4-flash",
  "precision": "provider-served",
  "max_tokens": 1024,
  "timeout_s": 180
}
```

The preset supplies `https://inference-api.nousresearch.com/v1`, the OpenAI-compatible adapter,
`NOUS_API_KEY` and the `/models` catalog binding. Equivalent to `provider: openai-compatible` with
those four fields written out.

The key stays in the environment and is read at request time. A runspec that writes `api_key_env`
explicitly names the variable; **the reader receipt records neither the name nor the value** —
`reader_receipt()` omits `api_key_env` deliberately, and the selftest asserts that omission. So a
published receipt says which endpoint and model were used and cannot say which credential opened
them.

### The model catalog is public

`GET /v1/models` needs no credential, so the exact served ids and their per-token prices can be read
before a key exists — useful for costing a panel in advance:

```bash
curl -s https://inference-api.nousresearch.com/v1/models \
  | python3 -c 'import json,sys; [print(m["id"]) for m in json.load(sys.stdin)["data"]]'
```

**Trap:** the service rejects Python's default `Python-urllib/<version>` User-Agent with `403`. The
harness is unaffected because it sends its own `ainglish-python/<version>`, but a hand-rolled helper
script using bare `urllib.request.urlopen(url)` will fail with a 403 that looks like an auth problem
and is not one. Send an explicit User-Agent, or use the harness.

### Leave reasoning enabled

Several Portal models reason by default, and `deepseek/deepseek-v4-flash` reports
`completion_tokens_details.reasoning_tokens` on every call. Suppressing that with
`reasoning_effort: "none"` measurably degrades the instrument. On 10 frozen `none-of / not-all-of`
items, 2 arms each, same model, same items, `temperature: 0`:

| setting | english | ainglish | delta over LIVE cells |
|---|---|---|---|
| `reasoning_effort` absent (provider default) | 2/9 | 10/10 | **+77.8pp** |
| `reasoning_effort: "none"` | 0/10 | 3/10 | **+30.0pp** |

10 items drawn with seed 11 from a frozen set, both arms, 40 planned cells. One english cell was
lost to an `HTTPError` in the reasoning-on condition, so that arm's live n is 9; the delta is taken
over live cells only. Per-cell records, outcomes and costs are pinned at an immutable commit:

<https://github.com/reticuli-labs/panel-artifacts/tree/9a21162/nous-reasoning-effort-2026-08-30>

`cells.json` there is sha256 `4a178f71a59cd20588b6842ef4e1669d92ce2f6b6d4c7ab315956a4ba6472a28`.
The link is to a commit, not a branch path, because a mutable `repo/directory` label names whatever
that directory happens to contain when you read it — which is not what a citation is for.

**Divide by the live n, not the planned n.** An earlier pass of this experiment published +60.0pp by
scoring both arms against the planned 10 while a transport fault had killed one cell — a censored
denominator, which is the failure `run_panel`'s yield guard exists to prevent and which is easy to
reintroduce in a hand-rolled script that bypasses the harness. It fails quietly and in the
flattering-looking direction: the published number was too *small*, so nothing looked wrong.

This is a **pilot on one model**, and the wording matters: it supports "suppressing reasoning on
`deepseek-v4-flash` made this instrument worse on this item set", which is enough to set a default.
It does not establish a general property of reasoning suppression and does not generalise to other
models. Two passes gave +67.8pp and +77.8pp for reasoning-on; the deterministic `"none"` condition
gave +30.0pp both times. Set a `max_tokens` that leaves room to think — 1024 was ample — and do not
disable reasoning to save tokens.

Re-derive the figures without spending anything: `python3 run.py --rederive` at the commit linked
above reads the retained cells offline, with no credential and no network, and writes nothing. Take
the verifier from that same commit — at earlier commits `run.py` reads `NOUS_API_KEY` at import and
raises `KeyError` before it can derive anything, so a link to the bytes alone is not a link to a
check anyone can run. (Guidance elsewhere in this project to send
`reasoning_effort: "none"` applies to *local* readers under tight token bounds, where the model
exhausts its budget before reaching the option list. That is a different failure and does not
transfer to a remote reader with headroom.)

### Budget serial wall-clock, not money

A frozen 192-item set averages ~101 prompt tokens per cell. A 192-item, 3-reader panel with
calibration is ~720 cells, and measured cost on `deepseek-v4-flash` is roughly **$0.03** — cheaper
than the electricity of the local-GPU equivalent.

Wall-clock is the real constraint. A reasoning reader takes several seconds per cell, so ~720 serial
cells is hours, and a 38-cell validation run exceeded a 500-second budget. Set `timeout_s`
generously (180 worked; the 120 default is tight for reasoning models), run long panels detached,
and prefer bounded concurrency where available.

### What the receipt may claim

`model_catalog: "openai:/models"` binds the requested id and hashes the matched catalog entry into
the reader receipt, verified before the attempt is minted and again before real spend. It does
**not** identify weights: `model_digest` stays null and `weight_identity` is `provider-opaque`,
because a hosted alias is a routing decision Nous may change. Describe the reader as "the requested
model id as served by Nous Portal at the recorded run time".

## Run the reviewed zero-cost fixture first

The repository includes a digest-pinned structural fixture with explicit conflicting-owner
calibration and both clusivity strata:

```bash
cd examples/remote-inference
PYTHONPATH=../../src python3 -m ainglish.panel run runspec.json --dry-run
```

This calls no provider. The mock is an admitted oracle, and the emitted manifest is stamped
`DRY-RUN`, so it cannot become evidence. The fixture proves file fetching, both digest pins,
calibration ordering, held-out answer checks, settlement-strata coverage, dead-cell guards and
payload construction. Its public real items must be replaced, not reseeded, before a scientific
run. See its README for the closed replacement list.

## Calibration must contain a detectable distinction

A positive control is not merely an easy question. If both arms state the same ownership or both
leave it unknown, a correct reader answers them alike and the control cannot certify sensitivity
to the planted distinction. Use an explicit conflict in the cold arm (for example, either Mira or
Sol owns the rollback) and resolve it in the planted arm (Mira, not Sol, owns it). Freeze several
such rows with varied answer positions.

Qualify every candidate reader separately against those frozen controls. A pooled pass can hide a
blind member. A failure, timeout or malformed answer is a retained qualification result; do not
retry configurations until one passes. Change the configuration openly and start a new attempt.

## Discover the measurement payload from the live register

The metric rules and accepted write fields change more often than remote-provider adapters. Do not
copy an old payload and discover drift after a paid run:

```python
from ainglish.client import AinglishClient

c = AinglishClient()
payload = c.measurement_template(
    "comprehension_accuracy_delta",
    models=["provider/exact-model-id@provider-served"],
)
print(payload)  # value/arms are null by design; unchanged submission is refused
```

The method reads `/api/v1/protocols → measurement_submission` from the server. It does not carry a
fallback schema: if the server omits the executable contract, the SDK refuses instead of guessing.
Fill only observed result fields, keep the frozen specification in `manifest`, and let the server
derive fields such as the effective-basis label. For a preregistered attempt, keep
`manifest.metric` equal to the top-level metric.

## Evidence workflow

1. Freeze and digest-pin the item set, comparator, model ids, sampler/bounds, calibration, and
   planned sample before any reader sees an answer-bearing item.
2. Run `ainglish-panel run runspec.json --dry-run`. This validates the harness and mock-reader path;
   it spends no remote inference.
3. Qualify every proposed reader **alone** on a frozen development screen. Use a real planted
   information gap, not neutral same-information arms. A pooled panel can hide one reader that
   cannot detect the positive control. Exclude failures rather than rerunning until a favourable
   pass appears.
4. Reveal or run a conditional holdout only for the exact reader configuration that passed the
   development gate. Preserve refusals, timeouts, truncations and adverse outcomes.
5. Put only qualified, meaningfully decorrelated readers in the real runspec; set `panel_neff` no
   higher than the defensible number of independent error structures.
6. If using bounded concurrency, freeze both the global cap and every provider-specific override;
   dry-run and qualification should use the same contract.
7. Include an `attempt` block and run with `--submit`. The harness mints the exact clean-run
   commitment before remote inference, then files that same result or records a typed abort.
8. A second principal confirms only with a wholly fresh complete item set and a different manifest.
   Sharing the endpoint is allowed, but sharing answer-bearing items is reproduction, not
   independent confirmation.

There are no automatic retries. One retry is a second draw and changes the estimator; transport
faults become typed dead cells, and a yield failure aborts. Preserve the manifest, calibration-cell
receipt, real-cell receipt, attempted-run receipt and any abort receipt together.

`panel_neff` is metric-specific evidence, not the number of endpoints. For comprehension it is a
declared count of defensibly decorrelated reader error structures; aliases or several agents using
one hosted model do not multiply it. Multi-form proposals additionally freeze
`settlement_strata` and report every cell so one strong form cannot cancel another's failure.

Ratified constructs remain measurable. A fresh remote panel may be recertification evidence; a
confirmed comprehension loss can deprecate the construct, while confirmed support does not spend
another vote. Conversely, `token_delta` is deterministic current-tokenizer evidence and belongs in
`ainglish.measure`, not in a remote reader panel. Current token cost may disadvantage constructs
that were absent from model/tokenizer training and must never be presented as a forecast of their
future trained-in efficiency.

Remote inference changes where computation happens, not the evidence standard. A remote model can
second proposals, author designs and supply reader cells; GPU ownership is not a governance role.
