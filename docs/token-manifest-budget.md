# Complete token evidence must fit the inline contract

The register currently limits a canonical manifest to 20,000 UTF-8 bytes. Token
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
separately reviewed server/API change. This patch does not increase public input
limits or implement remote input fetching.
