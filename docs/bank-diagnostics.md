# Inspect answer cues before freezing a reader bank

Run the existing offline audit, with no credentials or model calls:

```sh
ainglish-audit-items items.json
```

`evaluation.bank_diagnostics` (and the training block when supplied) reports
target and calibration counts separately. Python callers can also use
`from ainglish.bank_diagnostics import audit; report = audit(items)`.
This is a report-only aid. It changes no item, manifest, mint, verdict or exit
status. Structural errors still have their existing effect on the enclosing audit.

## What the rules can see

The standard `panel.ask` presents one message, question and ordered choice list
per call. It assigns opaque choice codes in **stored option order**; it does not
shuffle the choices. Balance of correct positions is therefore useful but not
sufficient. Rotating `[correct, other, both, not specified]` can balance every
position while making “the option after the longest” recover every key.

The fixed diagnostic family uses absolute positions or a cyclic offset after
the unique longest, shortest, UTF-8-byte-first or UTF-8-byte-last choice. Offsets
run from zero to the bank's maximum option count minus one (up to 26). Predictors
see only one item's option strings, not its message, question, gold or metadata.
Gold scores the prediction afterward. Length ties abstain and stay in the full
scorable denominator; invalid rows are reported separately, not called passes.
The report shows every tested rule, its numerator, denominator and abstentions.
Warnings start at 90%; fewer than 20 scorable rows carry a small-sample indicator.

This is an exploratory family, not a significance test. Trying many rules and
selecting the best after seeing gold can produce impressive chance fits. A
within-item executable rule does not need a shared conversation, but finding it
afterward does **not** show a reader used it. Actual message-ablation evidence
would require a separately frozen, appropriately scoped experiment.

The two `posthoc_lookup_fits` group question/options with and without order and
fit the majority key on those very same rows. Unique questions can trivially
give a perfect fit. These are descriptive training-set fits, not held-out
predictors or universal message-blind bounds. They never create a warning or a
gate by themselves. No source strings, keys or IDs are included in this report.

## What to do with a warning

Inspect the exact prompt and scoring contract. Neutral references, genuinely
crossed meanings, prospective per-item permutations and independent design review
can help. A permutation only addresses order: it cannot remove a semantic cue
in the question, a distinctive answer label or an implausible English comparator.
Freeze any changes **before** mint/inference. Do not mutate a registered bank,
silently rerun it, select the most favourable shuffle or discard adverse outcomes.

For historical evidence, record the precise defect, affected claim and retained
results; use the native correction/retraction path when warranted. A warning is
not automatic invalidation, and a clear report is not scientific approval.
This diagnostic deliberately does not change the separately versioned, served
`message_blind_floor` contract or its cross-language parity.
