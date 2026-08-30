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
over live cells only. Per-cell records, outcomes and costs are pinned at
`reticuli-labs/panel-artifacts/nous-reasoning-effort-2026-08-30` (`cells.json`).

**Divide by the live n, not the planned n.** An earlier pass of this experiment published +60.0pp by
scoring both arms against the planned 10 while a transport fault had killed one cell — a censored
denominator, which is the failure `run_panel`'s yield guard exists to prevent and which is easy to
reintroduce in a hand-rolled script that bypasses the harness. It fails quietly and in the
flattering-looking direction: the published number was too *small*, so nothing looked wrong.

Treat the direction as established and the point estimate as a pilot: two passes gave +67.8pp and
+77.8pp for reasoning-on, while the deterministic `"none"` condition gave +30.0pp both times. Set a
`max_tokens` that leaves room to think — 1024 was ample — and do not disable reasoning to save
tokens. (Guidance elsewhere in this project to send
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

## Evidence workflow

1. Freeze and digest-pin the item set, comparator, model ids, sampler/bounds, calibration, and
   planned sample before any reader sees an answer-bearing item.
2. Run `ainglish-panel run runspec.json --dry-run`. This validates the harness and mock-reader path;
   it spends no remote inference.
3. Qualify every proposed reader **alone** on a frozen development screen. A pooled panel can hide
   one reader that cannot detect the positive control. Exclude failures rather than rerunning until
   a favourable pass appears.
4. Reveal or run a conditional holdout only for the exact reader configuration that passed the
   development gate. Preserve refusals, timeouts, truncations and adverse outcomes.
5. Put only qualified, meaningfully decorrelated readers in the real runspec; set `panel_neff` no
   higher than the defensible number of independent error structures.
6. Include an `attempt` block and run with `--submit`. The harness mints the exact clean-run
   commitment before remote inference, then files that same result or records a typed abort.
7. A second principal confirms only with a wholly fresh complete item set and a different manifest.
   Sharing the endpoint is allowed, but sharing answer-bearing items is reproduction, not
   independent confirmation.

Remote inference changes where computation happens, not the evidence standard. A remote model can
second proposals, author designs and supply reader cells; GPU ownership is not a governance role.
