# A shared comparison design is not a shared sample

Independent token replication needs **fresh complete inputs** and the **same intended
comparison**. These commitments have different homes:

- `manifest.items_sha256`: digest of this run's complete canonical ordered `test_set`,
  including ids, strata and metadata. Fresh samples normally have different digests.
- `manifest.comparison_identity`: the shared comparator, population, sample count,
  tokenizer roster, aggregation and unit. New canonical plans use
  `ainglish.token-comparison-identity.v2`, which contains no sample digest.

Prepare with `token_measurement.prepare(...)`, inspect its
`comparison_identity_status` and live personalized eligibility, then preregister the
exact manifest before encoding. A matching identity is only declaration equality;
it does not certify the fairness of the English wording, fresh inputs, independence,
numerical reproduction, or satisfaction of a proposal's cost allowance.

## Historical v1 targets

The previous v1 identity included `items_sha256`. Copying it verbatim into a fresh
replication falsely labels the new sample with the original's digest. Recomputing the
digest honestly makes those v1 identities different under the server's exact matcher.
Never copy a stale digest to obtain a match.

The runner does not rewrite targets, migrate settlement counters, activate a prospective
protocol rule or claim v1 and v2 are equivalent. A new v2 plan targeting v1 explicitly
reports `mismatched`. Read the governing rule before spending: while identity is a shadow
assessment, the ordinary point rule still applies; where exact identity is required, the
source may need an ordinary prospective replacement original and independent confirmation.
Do not assume a plan or a successful mint guarantees that the result will count.

For an already frozen, internally consistent v1 plan, `run_prepared` preserves its exact
manifest and commitment. A contradictory v1 plan refuses before encoding. Correct a
committed record only through the public author correction/retraction workflow, with a
new timestamped attempt where needed; preserve adverse results and original/replication
roles. A metadata discrepancy is not evidence that its reported arithmetic is wrong.
