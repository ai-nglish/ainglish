# Review a reader instrument before inference

```python
from ainglish import instrument_audit

# Complete local manifest with items and panel; this function never fetches URLs.
report = instrument_audit.check(
    manifest, axes=("settlement_stratum", "probe", "domain")
)
```

This optional, **report-only** helper exposes assignment counts before inference.
Global wording balance can hide a confound: one reader may see every causal-relation
question in marked form and every support question in English. Inspect counts within
reader, form, probe and domain, not just the overall totals. Both empty intersections
and intersections with only one wording are listed. Choose axes prospectively; an
axis missing from any target item is explicitly unassessed, not silently filled in.

It also reports duplicate complete inputs, repeated author-declared `semantic_frame`
labels, malformed answer keys and gold phrases occurring verbatim in either arm.
Frame labels cannot prove independence. Lexical overlap is only a review flag: a valid
answer may occur naturally in a message, while a semantic shortcut may share no exact
words. Short/opaque answers are outside that heuristic. Review operational consequences
and equally explicit comparators manually; this helper cannot validate them.

Nothing here qualifies a reader, approves a bank, mints an attempt, buys inference,
changes a gate, or automatically excludes evidence. Sparse cells may be legitimate.
Fix a design prospectively when warranted, freeze the resulting bank and assignment,
then follow the normal qualification and mint-before-spend protocol. After exposure,
report a problem publicly and preserve raw outcomes; do not silently rebalance, edit
keys, or retry a result. A clean report is not scientific approval.

## Check the design you actually committed

Optional `requirements` express checks chosen before inference. They are kept outside
the scientific manifest and do not add server-side rules. For example:

```python
report = instrument_audit.check(
    manifest,
    axes=("settlement_stratum", "frame_class"),
    requirements={
        "required_options": [
            "receipt=yes; agreement=unasserted",
            "receipt=yes; agreement=yes",
        ],
        "arm_quotas": [
            {"reader": "my-qualified-reader", "where": {"settlement_stratum": "receipt"},
             "english": 40, "ainglish": 40},
        ],
        "opposite_arm_frame_axis": "world_id",
    },
)
```

Use the **actual** reader names, seed, ids, metadata and complete answer texts in your
frozen design, not these illustrative names/counts. Target items exclude calibration.
The helper accepts `panel` in a local run design or `readers` in a committed manifest;
if both are provided they must agree. For a URL-pinned bank, verify its canonical item
digest yourself and supply the local `items` in a detached audit copy, never edit the
committed artifact. The helper does not download or authenticate external bytes.
Quota `where={}` selects all target items. Non-empty `where` matches exact string-valued
metadata; missing classification, an absent reader or an empty selection is unassessed,
never a reassuring zero. Duplicate/invalid identities prevent assignment checks.
Required options match the harness's whitespace trimming and case folding. They are
global requirements for every target menu; omit them when different probe types need
different answer spaces, or audit homogeneous sub-banks separately.

The frame check expects exactly two items per named frame and checks opposite-arm
assignment separately for each reader. It does not rename ids, search for a favourable
seed or assume those two observations are independent. A valid study need not use this
pairing design; only request this check if it is part of the prospective design.
Frame ids may be non-empty strings or integers, including zero; boolean/null/missing
identities are unassessed rather than silently grouped together.

Three recent failure modes motivate these optional checks:

- Receipt/agreement menus omitted the other clean answer, letting menu membership
  recover every gold key without the message. Requiring both choices catches that
  omission, but does not rule out other shortcuts.
- A retries study was built for one seed and committed with another: its declared
  per-form 36/36 quota and 72 opposite-arm worlds no longer held. Compare the committed
  assignment, not the builder's intentions or an all-correct result after exposure.
- A mean/median bank was globally balanced but separated frame classes entirely by
  arm. Choose `frame_class` as an explicit audit axis. A passing marginal quota does
  not repair empty class-by-arm cells. If within-class balance was not preregistered,
  describe a design limitation rather than inventing a retrospective gate violation.

Inspect `findings` and `requirements` together. Unknown/missing requirements remain
unassessed; malformed declarations raise `ValueError`. There is deliberately no
`passed` or `approved` result. A retrospective audit remains an audit: preserve the
old bank, allocation, outputs and null/adverse results; correct or retire an invalid
contribution through the public workflow rather than rerunning until it improves.
