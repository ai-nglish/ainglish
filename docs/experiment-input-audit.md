# Audit the item set before spending compute

`ainglish-audit-items` is an offline preparation aid. It uses the panel's existing
item-field validator and adds complete-case duplicate, conflicting-gold,
declared answer-balance, calibration and literal train/evaluation overlap checks.
It buys no reader cells, calls no API, changes no files and creates no new
register or ratification gate.

```bash
ainglish-audit-items evaluation.items.json --training training.items.json --require-balanced
# Equivalent without installing a console script:
python -m ainglish.experiment_audit evaluation.items.json --require-balanced
```

Inputs are local JSON lists or JSONL files, at most 20 MiB each. Use the existing
panel shape: `id`, `english`, `ainglish`, `question`, `options` and `answer`.
Calibration rows carry `calibration: true`; do not include them in claimed real
sample size. Optional `settlement_stratum` and `boundary_case` declarations are
counted, not semantically certified.

The output includes row and distinct-case counts, repeated-case hashes/IDs,
conflicting answer keys, declared answer distributions, the constant-answer and
uniform-random-option baselines, and optional cross-split overlap. Reordering
options or changing IDs alone does not hide a training/evaluation overlap.
Diagnostics do not echo the input utterances. IDs and answer labels may still be
sensitive; inspect a report before publishing it.

Exit 0 means the requested local checks passed, 1 means the report found errors,
and 2 means an input file could not be read as bounded valid data. Answer-position
imbalance is a warning unless `--require-balanced` is chosen. Freeze that policy
before collecting target results. A report is never a submission payload.

```python
from ainglish.experiment_audit import audit_items

report = audit_items(items, training_items=training, require_balanced=True)
assert report["ok"]  # a chosen prospective local gate, not server eligibility
# Independently inspect the complete English mapping, gold meanings, boundary
# coverage, qualification and live work package before minting or inference.
```

## What it does not establish

- Equal strings are exact repeats; different strings need not be independent
  semantic frames. Names and template slots are not new concepts.
- Declared answer positions are not the SDK's opaque served choice-code order.
  The constant-answer baseline applies to decoded declared answers, not to one
  fixed opaque output code. Inspect retained wire prompts for actual label balance.
- Literal leakage checks cannot detect paraphrases or undisclosed training data.
- A complete English string is not proof of a complete or equivalent comparator.
- Declared boundary/stratum labels do not prove useful coverage or correct gold.
- Planned repetition can be legitimate for training or diagnostic controls, but
  the repeated rows must not be claimed as additional distinct observations.
- This audit does not qualify readers, mint attempts, certify independence,
  establish representative sampling, or change earlier results.

If a frozen study is already complete, retain its original inputs and analysis.
Publish an audit as a dated sensitivity or correction; do not quietly deduplicate
the data and replace a failed primary result. A new evaluation needs fresh items.
