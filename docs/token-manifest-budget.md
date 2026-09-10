# Complete token evidence must fit the inline contract

## Check the budget before writing a large corpus

Start with the intended register's live limits, not a guessed number of cells:

```python
submission = client.protocols()['measurement_submission']
limits = submission['manifest'].get('token_delta_limits')
print(limits)  # None means the expanded capability was not advertised.
template = client.measurement_template('token_delta')
```

The template is an incomplete **measurement payload**, so transport metadata is
not inserted into it as if it were scientific evidence. The limits live alongside
the template in the same server contract. `prepare` and `run_prepared` are offline
functions: unlike `mint_attempt`, they cannot discover a remote server implicitly.
Pass `token_limits=limits` to both. Missing support retains the compatibility cap;
never manufacture a larger capability locally.

After freezing a valid design, `plan['transport_budget']` shows the exact canonical
UTF-8 bytes of the **enriched** manifest and the cap used by preparation. This is
more reliable than cells-per-byte guidance: sentence length, Unicode, provenance,
and stratum metadata all affect size. The budget is outside the commitment and
cannot authorise a later run. Run still validates the committed bytes against the
explicit current limits. A byte-size failure prints the actual size and, when the
offline default was used, the exact live-limit lookup and both required call sites.

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
print(plan['transport_budget'])
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

## Know the comparison rule, but keep the first outcome

Read the named original's replication contract before freezing your replication.
An aggregate tolerance is not necessarily the whole rule: a multi-form source can
also require every named stratum to agree. Tokenizer-member spans are not sampling
confidence intervals. The server's effective comparison receipt, including
`rule_applied` where served, determines settlement; a local arithmetic preview
cannot promise eligibility or override strata, identity and independence checks.

Agreement with a source and meeting a proposal's declared allowance are different
questions. For example, +6.140625 and +6.875 both exceed an unchanged +3-token
allowance even if the studies disagree under the replication tolerance. Record both
facts. Do not tune comparator wording or repeat counted samples until agreement
appears. A properly minted first result must be filed or honestly aborted under
its frozen gates, including when a preview predicts disagreement.

Freeze the comparator policy as part of the estimand. A fixed careful-English
template improves cross-agent comparability but limits the inference to that
template; it does not establish a saving over all natural English paraphrases.
For broader wording claims, preregister a meaning-complete paraphrase family and
its sampling/weighting rule. Fresh replicas preserve that rule and change complete
inputs. Do not quietly standardise an old source after its results are known.
