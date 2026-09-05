# Executable prospective experiment gates

Use this **optional** runspec block when a new experiment requires stricter answer handling
than the ordinary panel defaults. Declare it before preregistration and before any reader sees
target items. Do not add it retrospectively to old evidence or retry a failed study under a
looser policy.

```json
"admissibility": {
  "kind": "ainglish.panel.admissibility.v1",
  "max_off_option_cells": 0,
  "max_absent_cells": 0,
  "max_truncated_cells": 0,
  "max_transport_fault_cells": 0,
  "per_reader_calibration": true
}
```

All six fields are required. Unknown fields, non-integer or negative budgets, booleans used
as counts, and an unsupported kind refuse before reader spend or attempt mint. This version
covers comprehension, interpretation entropy and learnability, **not robustness quartets**;
the latter explicitly refuses rather than ignoring the declaration.

## What the budgets mean

Each budget is an absolute number of cells across **all started calibration and real calls**,
including already-running calls drained after a concurrent stop. A truncation or transport
failure also counts as an absent cell; those categories intentionally overlap. An off-option
answer is a live returned string that is not exactly one of the declared option labels. It is
not silently repaired, re-prompted or converted into an absence. A legal but wrong answer is
not an off-option failure: it remains an ordinary scored observation.

The run stops at the first exceeded budget in deterministic plan order. No further calls are
scheduled; any already-running calls are retained without entering an estimator. The existing
yield guard still applies. The `max_` names in the observation's `counts` map identify the
corresponding configured budget; the values there are observed counts, not new limits.

`per_reader_calibration: true` requires each reader to pass the **same effective calibration
rule already declared for the study**, before any target cell is bought. It adds an individual
check, not a new threshold or a reader-qualification certificate. The pooled check still applies.
False preserves the historical pooled-competence rule and its existing per-reader live-cell checks.

These rules can only add safeguards. A nonzero fault or truncation budget does **not** relax
the preregistered harness's existing clean-manifest commitment: such a run still cannot file
under a clean-run hash. Free-text `attempt.admissibility_gates` remains an operator declaration,
not a natural-language program. Translate applicable rules into structured fields and manually
check any remaining scientific constraints; the SDK cannot certify arbitrary prose.

## Identity, filing and retained failure

The normalized policy is included in `measurement.manifest.admissibility`, so changing a
budget or calibration scope changes the manifest commitment. The exact policy is also added
to the minted attempt's gate statements. Observed counts and per-reader calibration verdicts
are result-side diagnostics under `measurement.calibration`, not outcome-dependent manifest
fields. They are harness reports, not independent verification or server attestation.

With `attempt` plus `--submit`, an exceeded gate produces no measurement POST. The attempt
is closed with its structured abort reason; calibration and partial real-cell sidecars plus
the abort receipt are saved beside the runspec. A transport-caused stop retains its transport
classification. Preserve these files and the original inputs. Do not remove cells, loosen the
budget or rerun for a more convenient result. A genuinely new design needs a separate,
prospective rationale and fresh inputs, while the failure remains visible.

Successful saved payloads can use the existing [no-inference recovery workflow](work-packages.md).
Recovery is for the exact completed payload, not a way to turn a failed run into a result by
editing the policy or counts. The optional block does not change server settlement rules or
retroactively invalidate studies that never declared it.
