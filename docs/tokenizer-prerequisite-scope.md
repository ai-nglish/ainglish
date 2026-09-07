# Matching a token prerequisite to its declared population

On a supporting server, a bounded token prerequisite may declare an exact roster:

```python
contract = {
    "claim_carrier": ["comprehension_accuracy_delta"],
    "prerequisites": [{
        "metric": "token_delta", "at_most": 4,
        "tokenizer_roster": ["cl100k_base", "o200k_base"],
    }],
}
# Include this in the complete draft and inspect server validation before writing.
draft["evidence_contract"] = contract
preview = client.preflight(draft)
```

This is server-negotiated functionality: read the live `/openapi.json` and
preflight result. Older servers reject the field. Do not strip it and proceed,
because that would change the intended requirement. The SDK passes the explicit
declaration through ordinary `propose`/`amend`; changing an existing author's
contract is a visible amendment, never an automatic repair by a measurer.

The live `evidence_readiness.work_items` supplies `scope.tokenizer_roster`, exact
matching semantics and `out_of_scope_hashes`. Prepare a measurement whose
`manifest.models` names exactly that population. A maximum over three tokenizers
is a different quantity from a maximum over two; no client should project a
favourable subset and copy the original's confirmation flag. A matching new
original still requires eligible fresh-input independent confirmation.

Only bounded `token_delta` prerequisites support this optional field. The names
must be a nonempty unique list of server-supported tokenizer encodings; order is
irrelevant. Omitted scope keeps the previous whole-metric interpretation. A
different, missing or wider measured population remains visible evidence, not
invalid data, but does not satisfy that explicitly scoped requirement.

Keep the acceptance bound unchanged. Explain scope from the prospective claim,
not from favourable observed members. The advisory scope does not alter scores,
formal verdicts, ballot eligibility, or current-tokenizer versus future-trained
performance distinctions. No existing proposal is amended by installing the SDK.
