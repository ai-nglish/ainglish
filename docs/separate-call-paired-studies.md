# Separate-call paired studies (report-only)

Some meanings are tested by two contrasting worlds. Showing both worlds in one
prompt can give the reader a contrast cue that an isolated message would not
provide. `ainglish.paired_study` executes each twin in a separate stateless call
and scores a reader/pair/arm correct **only when both answers are correct**.
It never multiplies marginal accuracies. Null and adverse outcomes are retained.

This is an executable method-development instrument, **not yet an official
measurement submission path**. It does not mint, file a comprehension measurement,
replicate evidence, qualify readers, establish independence or change a gate.
Do not use its report as `comprehension_accuracy_delta` or relabel pairs as
ordinary single-answer items to make a submission pass. A prospective author
contract and admitted server replay are still needed before live study spending.

## Frozen plan

The public synthetic example in `tests/test_paired_study.py` is runnable without
network access. It is exposed software-test material, not a held-out item bank.
The strict `ainglish.separate-call-pairs.v1` schema binds:

- Proposal, population scope, seed, reader IDs and public instrument references.
- Both complete careful-English and marked messages, questions, options and gold
  labels for exactly two twins per pair, plus world and family IDs.
- Both arms for each fixed reader/pair, each twin in a fresh stateless call. This
  is a supported assignment design, not an assumption about every future study.
- Equal-pair/equal-reader **point weighting**, separately from pair or family
  **resampling**. Family bootstrap concatenates all pairs in each sampled family;
  it does not silently switch the point estimate to equal-family weighting.
- No response reuse: a repeated world appearing in another pair is another call.
  Repeated worlds or exact reader inputs across pairs require a shared family
  and family resampling. Each arm is fingerprinted separately as message,
  question and options, excluding golds and IDs; changing only the other arm or
  a hidden answer cannot evade the check. Identical inputs with conflicting
  golds are refused. IDs and literal uniqueness cannot prove that differently
  worded worlds are independent.
- No retries or incomplete-pair deletion. A fault, absent answer or off-option
  response stops the study and leaves every started/completed event visible.

The reader receives only its public instrument identity, the single message,
question and option list. The callback must start a fresh conversation each time;
the library cannot inspect a remote provider's hidden state. It receives no gold,
second twin, previous response or joint prompt. Calls are ordered by a seed/key
hash independent of the gold labels. A wrong answer does not skip the other twin.
Twins with the same gold are admitted but listed in the report's
`constant_response_compatible_pairs`: a constant response can get both right,
so their pair accuracy alone does not demonstrate distinguishing the worlds.
The synthetic example uses contrasting golds, on which an all-`x` reader fails
every pair. Semantic distinctions and answer validity still need review.

## Execution and durable receipts

`validate(plan)` returns the exact plan SHA-256. `planned_calls(plan)` is a
zero-cost execution roster. `execute(plan, ask_fn, registration=..., record=...)`
requires a registration object containing `reference` and that exact
`plan_sha256`. This reference is caller-attested, **not authenticated by this
module**. It is not itself proof of mint-before-spend. Only use real readers after
the applicable protocol accepts the study and its preregistration is confirmed.
All hashes use the SDK's shared PHP-compatible canonical JSON bytes, including
escaped U+2028/U+2029, encoded as UTF-8.

`record(event)` must durably append each event (including flushing/syncing when
required) before returning. A start is recorded before asking; an outcome is
recorded before scoring. Failure to record stops further calls. A crash after a
start but before a completion is an incomplete study, not permission to rerun
that cell. `StudyAborted.receipt` retains the partial response roster. Operator
interrupts propagate with `paired_study_receipt` attached. No automatic resume,
retry, submission or abort write is made against Ainglish.

`replay(plan, receipt)` rejects missing, duplicate, extra, wrong-reader, wrong-arm,
wrong-twin, faulty or off-option records and changed digests. It rebuilds pair
correctness from raw component answers. Journal hashes are integrity checks,
not signatures or evidence that a claimed model generated those answers.

The report uses 2,000 deterministic SHA-256-counter bootstrap draws with fixed
readers and nearest-rank 2.5/97.5 percentiles. It explicitly states its resampling
unit and limitations. Boundary results can have degenerate bootstrap intervals;
zero width does not establish certainty. This is not the ordinary harness's
single-item attestation or an exact population guarantee.

For an independent replay, sort unit IDs lexically. For each of 2,000 draws,
select `number_of_units` indices with replacement. Index `counter` is
`int(SHA256(canonical_json(["ainglish.separate-call-pairs.v1", "bootstrap",
seed, counter])), 16) % number_of_units`; the counter starts at zero once and
continues across draws. Concatenate the pairs in the selected units (including
repeated selections), average their equal-reader pair contrasts, then sort the
2,000 draw means. The interval endpoints are zero-based positions 49 and 1949
(nearest ranks 50 and 1950), not interpolated quantiles or the extreme draws.

## Remaining admission work

Before a scientific run: settle the author's assignment and independent-world
sampling contract, qualify the declared reader instruments, freeze an unseen
bank and comparator, add the corresponding server-side paired journal replay
and protocol-specific success rule, then preregister through the authenticated
SDK. A public-method review or passing software tests completes none of those
scientific gates. Do not change a declared family estimator to obtain a narrower
interval or change comparator/point weighting after seeing answers.
