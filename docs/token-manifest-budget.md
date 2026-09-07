# Complete token evidence must fit the inline contract

The compatibility default limits a canonical manifest to 20,000 UTF-8 bytes. Token
measurements must carry complete `test_set` pairs inline for server recounting;
an external URL alone is not accepted for this metric.

`token_measurement.prepare()` now checks the final enriched manifest, including
comparison identity and tokenizer provenance, before returning a mint-ready plan.
`run_prepared()` also rejects oversized plans made with older SDKs before encoding.
This is a deterministic pre-spend check, not a new scientific sample-size gate.

If an already frozen design exceeds this limit, retain it and report the blocked
transport contract. Do not truncate strings, omit qualifications, silently reduce
the sample, split one scientific claim into apparently independent successes, or
claim that a URL was recounted. A scientifically justified changed design needs a
new prospective declaration; support for larger countable manifests needs a
separately reviewed server/API change.

## Larger inline corpora on supporting deployments

The matching server change advertises a bounded 131,072-byte token-only allowance
at `client.protocols()['measurement_submission']['manifest']['token_delta_limits']`.
Other metrics, abort receipts and panel diagnostics retain their existing limits.
The server still recounts exact inline text: at most 512 pairs, 4,096 UTF-8 bytes
per arm, 512 bytes per tokenizer regex piece, and a bounded aggregate squared-piece
work budget across both arms and all encodings. It never fetches arbitrary URLs,
splits pieces approximately, truncates strings or changes the manifest hash.

```python
limits = client.protocols()['measurement_submission']['manifest']['token_delta_limits']
plan = token_measurement.prepare(spec, token_limits=limits)  # no encoding
checked = client.preflight_attempt(slug, plan['manifest'], **plan['mint'])
attempt = client.mint_attempt(slug, plan['manifest'], **plan['mint'])
# Re-read support before executing a plan that exceeds the compatibility limit.
limits = client.protocols()['measurement_submission']['manifest']['token_delta_limits']
result = token_measurement.run_prepared(
    plan, attempt['attempt']['attempt_id'], token_limits=limits)
client.measure(slug, result['payload'])
```

The CLI accepts `--token-limits limits.json` on both `prepare` and `run`; save that
exact capability object from the intended server. It is transport metadata outside
the scientific manifest. Merely caching it in a plan does not opt a later run in:
`run_prepared` needs the explicit argument. A pre-existing frozen manifest can be
used unchanged; support does not require adding fields or reducing its population.

Mint/preflight and large-result filing discover current support automatically;
old servers without the advertised capability refuse before POST or recount.
Preparation's byte check does not certify every server resource gate. Run the
non-consuming server preflight before mint/spend, including for unsupported
tokenizers or unusually long pieces. The separate 256-KiB whole-request transport
ceiling still applies, including JSON escaping and non-manifest fields.
