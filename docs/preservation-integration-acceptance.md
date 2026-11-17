# Preservation integration: independent acceptance checklist

This is a handoff for the proposed profile, not an activation decision or a claim
that the unfinished integration passes. Use the ratified protocol and reviewed
wire schema when they exist; do not implement a second schema from this checklist.
The numeric core and `balanced-world-hash-v1` are pure helpers at this snapshot.

The allocation review checked 300 additional seeded Unicode/numeric-name rosters,
1,800 reader/form allocations and 300 cross-language commitments, using separately
constructed canonical preimages. The ordinary fixture generator and both language
test suites remain the maintained allocation checks. Hash parity establishes byte
agreement, not semantic correctness, random sampling or experimental eligibility.

Reproduce these extra checks against an explicitly trusted Symfony checkout with
its locked dependencies installed (no server, database or model is contacted):

```bash
PYTHONPATH=src python tools/check_allocation_parity.py --symfony /path/to/ainglish-symfony
```

The check fails on missing/duplicate allocation identities as well as mismatched
arms or commitments. It supplements, rather than replaces, the shared fixed vectors
and invalid-input tests. It does not exercise a future mint or journal endpoint.

## Before any mint route opens

Give every case below a retained request, response, before/after state and explicit
pass/fail result against the implementation. These are acceptance scenarios, not
tests already executed against a future endpoint.

| ID | Exercise | Required boundary |
| --- | --- | --- |
| RAW-1 | Same JSON key twice, with equal values | Reject before lossy decoding |
| RAW-2 | Same key twice with different values, including a matching last-value digest | Reject; digest agreement cannot legalize duplicates |
| RAW-3 | Escaped alias of a decoded key, and nested duplicates | Reject at every nesting level |
| RAW-4 | Excess raw bytes/depth | Bounded refusal before canonicalization or persistence |
| PLAN-1 | Attempt to mutate an accepted plan in place | Immutable old plan and review scopes retained |
| PLAN-2 | Change only a reader name or lineage, same family size | Reader inventory participates in the required stored scopes; old sampling acceptance cannot silently carry |
| PLAN-3 | Reorder readers/forms/worlds without changing membership | Canonical allocation commitment unchanged |
| PLAN-4 | Change seed, world membership or endpoint coverage | Correct new scope/commitment; old acceptance does not certify the changed design |
| REVIEW-1 | accepts → withdraws → accepts with the same explanation | New deliberate acceptance appends; historical idempotency does not leave withdrawal current |
| REVIEW-2 | accepts → rejects → accepts, including equal wall-clock timestamps | Latest monotonic server sequence owns current state |
| REVIEW-3 | Retry the identical current request | No duplicate act or revival of an older judgment |
| EXPOSURE-1 | Supply entry exposure to the cold-only v1 profile | Refuse; a generic schema's representational ability is not profile permission |
| BIND-1 | Token manifest omits or changes the comparison-plan exposure/comparator binding | Fail closed; optional omission must not bypass correspondence |
| ALLOC-1 | Multiple forms/readers, 2N worlds per form | Exactly N worlds in each reader/form/arm, using the reviewed hash rank |
| ALLOC-2 | Several separately called endpoints on one reader/world | All inherit that world's arm; never allocate each endpoint separately |
| JOURNAL-1 | Missing, duplicate or unknown reader/world/endpoint cell | Refuse incomplete coverage, not a reduced denominator |
| JOURNAL-2 | Replay a complete journal | Recompute exact assignment, keys, denominators and estimator; do not trust reported summary counts |
| MINT-1 | Missing required independent scope review, stale scope or inactive protocol | Refuse before target inference; no public helper implies activation |
| LEGACY-1 | Existing panel, manifest and settlement paths | Legacy assignment and existing evidence remain unchanged |

Do not assert that a seed/name change flips every individual arm. It must change
the relevant commitment/scope, while some individual assignments can coincide.
Do not pass this design through the legacy hash-bit `run_panel` allocator.

## Scientific review is still separate

The reviewer must inspect both complete banks and shortest complete English,
declared independent sampling units and every load-bearing form/endpoint. One
world's endpoints can share dependence; a familywise union bound does not require
endpoint independence, but the binomial cell assumptions still need justification.
Reordering or hashing templates does not make them independent observations.

The proposer, implementation author, evidence preparer and independent acceptance
reviewer are distinct roles where the operative rules require that distinction.
This checklist and the allocation review do not fill an independent seat for a
future language bank. Keep no-activation status explicit in each implementation PR.
