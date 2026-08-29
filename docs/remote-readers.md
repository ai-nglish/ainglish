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
