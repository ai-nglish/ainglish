# Review complete comparison inputs before spend

`token_measurement.prepare()` returns `input_diagnostics` without loading a tokenizer,
calling a reader or contacting the register. It exposes word-inventory differences;
it cannot establish that two sentences mean the same thing.

An optional **outer** `input_review` object makes the intended literal checks explicit:

```json
{
  "manifest": {"...": "your complete token specification"},
  "input_review": {
    "forms": [{"literal": "able-to", "followed_by": "whitespace"}],
    "references": ["jobs@v2"],
    "protected_terms": ["job", "data", "not"]
  }
}
```

For a parameterised prefix use, for example, `{"literal":"each-group("}`.
The default continuation is a word/hyphen boundary; `whitespace` checks a bare
form followed by whitespace, so `able-to(retry)` does not match `able-to retry`.
Literals are escaped, case-sensitive strings, not regexes. Prefix detection is
not validation of the full construct grammar or evidence of correct use.

Reference and protected-term counts are compared in both arms. They do not resolve
references, infer omitted shared context, or certify scope, negation or equivalence.
Word inventories are case-folded and capped at 40 distinct terms per side, with a
truncation flag. Legitimate paraphrases often differ; identical word inventories
can still reverse who did what. Inspect every relevant complete pair.

Diagnostics include source fragments. Treat the report with the same confidentiality
as its inputs; it is not automatically uploaded. They do not change the frozen
manifest, its commitment, the result, server acceptance or settlement. A warning
does not invalidate evidence; **no warnings is not approval**. A mistaken declaration
may miss defects. Change a genuinely defective prospective bank before minting,
not after seeing results. Correct already-filed evidence through the public correction
workflow rather than editing its bytes.

For other complete-pair carriers, use the same offline function:

```python
from ainglish.pair_content_audit import audit
report = audit(pairs, {"references": ["population@v3"]})
```

No timing claim about an earlier experiment follows from running this tool now.
