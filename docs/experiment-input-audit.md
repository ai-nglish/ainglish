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

### Read the review warnings even when `ok` is true

The audit also checks each visible arm separately. If an identical English (or
Ainglish) message and question has contradictory keys, changing the hidden other
arm or reordering answer options does not remove the warning. Controls and real
items are analysed separately. An intentionally ambiguous English baseline can
be legitimate, but its score is not automatically a meaning-matched comparison.

Controls that explicitly announce the keyed answer are flagged for review. They
may demonstrate transport or answer copying, not sensitivity to the intended
language distinction. This is a narrow text heuristic: it does not detect every
kind of leakage, and a warning does not invalidate an experiment by itself.

For token studies, inspect a local JSON list of complete pairs before preparation:

```bash
ainglish-audit-items complete-pairs.json --token-pairs
```

This loads no tokenizer and flags the concrete shape of a long paragraph paired
with a short placeholder heading. A definition-versus-heading length study may
be intentional; it cannot silently stand in for the cost of equivalent messages
in use. Length differences alone are not flagged as bad comparators. Both this
check and the panel warnings are report-only, not additional server gates.

### Limits that remain

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

## Inspect a source bank referenced by URL

A server receipt with `side_overlap: null` has not established zero reuse. If an
original only names `items_url` and `items_sha256`, retrieve that public artifact
explicitly, then inspect the saved file. The auditor never follows a URL or loads
a model. It now accepts a local `{items: [...], sha256: ...}` envelope as well as
a list/JSONL, and checks any embedded identity before inspecting it.

```bash
python -m ainglish.experiment_audit candidate.items.json \
  --replication-of downloaded-original.items.json \
  --source-sha256 SOURCE_ITEMS_SHA256 \
  --items-sha256 CANDIDATE_ITEMS_SHA256
```

Replace the digest placeholders with actual 64-character lowercase hashes. The
source pin must come from the exact original manifest fetched with
`client.measurement(replicates_hash)`, not an unrelated proposal-level summary.
These are SHA-256 digests of parsed item JSON serialized with sorted keys,
compact separators, UTF-8 and `ensure_ascii=False`, **not downloaded file-byte
hashes**. A mismatched or malformed pin exits 2 before inspection or inference.

`replication_inputs` reports complete-pair occurrence overlap and per-arm reuse
against either side of the source, using exact bytes and preserving multiplicity.
All supplied rows, including controls, are compared. Different IDs, questions or
bank digests do not prove new pairs. Zero literal overlap does not prove semantic
independence, faithful comparators, source-matched readers or eligibility. A reuse
report is not an automatic refusal: the caller must apply the actual live protocol.

For already loaded data use `audit_replication_items(source, candidate)`. This
pure helper does not certify where the data came from; the CLI's source pin check
is separate. No existing manifest, scoring rule or settlement state is changed.

## Compare the planned population with the labelled bank

An optional sidecar catches bookkeeping mismatches before minting, without
guessing what a sentence means. For example, a plan may say one negative case
per form while its actual labelled bank contains two for one form.

```bash
ainglish-audit-items pairs.json --token-pairs --declarations declarations.json
```

```json
{
  "kind": "ainglish.study-declarations.v1",
  "expected_target_rows": 8,
  "expected_control_rows": 0,
  "counts": {
    "population_cell": {
      "stat-positive": 3, "stat-negative": 1,
      "practical-positive": 3, "practical-negative": 1
    }
  },
  "strata": [
    {"id": "statistical", "count": 4, "weight": 1},
    {"id": "practical", "count": 4, "weight": 1}
  ],
  "reference_bindings": {
    "analysis-7": {
      "status": "resolved", "locator": "retained-context.json#analysis-7",
      "aliases": ["the seventh analysis"]
    }
  }
}
```

Only `kind` is required; include only checks you intend to make. Counts apply to
non-calibration rows and exact nonempty string metadata labels, not text parsed
from either arm. Each `counts` field describes its complete expected distribution;
missing labels, unplanned labels and wrong counts are visible. `strata` compares
the rows' `settlement_stratum` values, retaining the declared order and positive
weights without inferring their meaning or applying them to a metric. It does not
compare against a replication source manifest. That remains a separate check.

Use labelled item objects, not two-string arrays, for this diagnostic. Optional
`reference_ids` per row declare which records it relies on; an explicit `[]`
differs from absent usage metadata. Binding status can be `resolved`, `unknown`
or `deliberately_unresolved` (a planned clarification case). A resolved binding
needs a nonempty locator; no URL/file is fetched and resolution is **asserted,
not verified**. Aliases are declared, never inferred from spelling. Missing or
ambiguous bindings and unknown status are warnings; a paraphrased reference is
not automatically wrong. Reference truth, equality, scope, and prose coverage
still need review of the actual retained records and both complete arms.

Optional `world_id` and `template_id` counts expose declared clustering. More
labels do not prove independence. A metadata-only `context` field is flagged:
the standard panel does not serve it. Embed every answer-bearing fact inside
each arm before auditing complete visible inputs and contradictory golds.
The tool does not rewrite or silently append context.

Python callers can use `audit_declarations(items, declarations)`. Mismatches are
warnings and do **not** alter the enclosing report's `ok`, its exit status, or
any server gate. Malformed/unknown sidecar fields raise `ValueError` (CLI exit 2),
so a typo cannot silently suppress a requested diagnostic. Reports may expose
metadata labels and reference names: inspect before publication. Historical
banks are never relabelled in place; retain an explicitly dated audit sidecar.
