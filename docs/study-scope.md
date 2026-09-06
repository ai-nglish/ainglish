# Say which question an experiment answers

Metric names do not establish claim coverage. A comprehension panel about missing
arguments is not a test of numeric conversion, even if both report accuracy.
Use existing comparator, estimand and condition declarations for the details;
these two optional fields add a concise reading label, not another admission gate.

```python
from ainglish import study_scope

spec = study_scope.attach(
    spec,
    purpose="boundary_check",
    scope="Tests whether omitted units cause a clarification request. Does not test numeric conversion or comprehension in ordinary complete messages.",
)
```

- `claim_test`: intended to test the advertised benefit; it does not certify that
  every form, condition or falsifier is covered.
- `boundary_check`: tests scope limits or invalid inputs.
- `diagnostic`: investigates a mechanism or failure without being the primary test.

Declare both `study_purpose` and `study_scope`, before the final manifest is
derived, preregistered or run. Scope is non-empty text, at most1000 characters;
say both what is tested and what remains untested. The panel retains the exact
fields in its planned and observed manifests, including robustness results.
Token-runner manifests also retain these ordinary metadata fields.

`study_scope.inspect_manifest(manifest)` reports declared, undeclared or malformed
scope. Absence is unknown, never assumed to mean a primary claim test. No label
changes settlement comparability, evidence readiness, validity, or ratification.
A diagnostic can still expose genuine harm. Do not use a label to dismiss adverse
results or to promote boundary success into general comprehension evidence.

This feature is report-only. The server remains the authority for present gates.
Changing which experiments qualify would require a separate, explicitly reviewed
rule change. Stored legacy manifests stay immutable; discuss retrospective scope
assessments in public rather than relabelling historical bytes.
