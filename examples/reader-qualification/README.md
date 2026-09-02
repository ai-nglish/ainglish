# Reader qualification screen

This is a structural template, not a reusable scientific receipt. Replace the endpoint, exact model
id, lineage basis and all controls with a target-independent frozen positive-control screen suitable
for the intended reader class.

Validate without inference:

```bash
ainglish-qualify-reader check examples/reader-qualification/screen.json
```

After supplying the credential through the named environment variable, run every cell once:

```bash
ainglish-qualify-reader run examples/reader-qualification/screen.json -o qualification.json
```

The command writes `qualification.json` even when the exact count rule fails and exits 2 on that
scientific failure. Do not edit a failed result or rerun until it passes. Redesign and freeze a new
screen when the declared design is unsuitable.
