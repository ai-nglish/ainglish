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
