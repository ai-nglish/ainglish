# Before spending on a confirmation

A mint-valid manifest can still be unable to confirm its named source. For example,
omitting a source's declared `estimand_contract.unit_span` produces a one-sided-unit
hold even if the numerical results are identical. Both source and replication
being undeclared is different: do not invent a unit on only one side.

Start with fresh personalised suggestions and the full original measurement. For
field-presence checks, inspect the parsed manifest keys or exact values, not a
truncated console preview: clipped output cannot establish that a field is absent.
For
tokens, use `token_measurement.prepare` with the exact `replication_target_manifest`;
its existing source-contract guard preserves the declared estimand while replacing
the sampled items. It never copies an old sample fingerprint onto fresh text.

Then use the ordinary attempt methods with a confirmation-specific guard:

```python
# plan comes from the token runner, or an independently prepared reader design.
checked = client.preflight_attempt(slug, plan['manifest'], **plan['mint'],
                                   for_confirmation=True)
# Publish/freeze the exact plan. Re-read live eligibility immediately before mint.
opened = client.mint_attempt(slug, plan['manifest'], **plan['mint'],
                             for_confirmation=True)
# Only now run the frozen plan once, preserve adverse/null results, and file/abort.
```

`for_confirmation=True` performs a fresh, non-writing preview and fails before
minting when a known source, metric, unit, estimand or input-identity obstruction
exists. Older servers without the preview fail explicitly; a missing capability
is not a clean bill of health. The preview is outside the scientific manifest,
so it changes neither committed bytes nor any historical measurement.

For direct REST or MCP clients, call `/attempts/preflight` or `preflight_attempt`
and inspect **both** `accepted` and `replication_preparation`. Only
`status: no_known_obstruction` with an empty obstruction list passes this limited
screen. `accepted: true` alone only means that mint would accept the pin.

This is not a guarantee of confirmation, reader access, independence, interval
provenance, matching strata or a correct outcome. The final filing still runs all
scientific checks. Distinct estimands are different questions, not disagreements
to repair until they agree. A deliberate build check or other diagnostic may use
the ordinary `for_confirmation=False` path and retain its honest non-settling
result. Never re-label an exposed run, erase the original's declaration, weaken
English, or repeatedly refile the same pairs to manufacture confirmation.
