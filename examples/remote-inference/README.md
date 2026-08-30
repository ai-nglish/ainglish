# Remote-panel starter fixture

This fixture proves the remote-panel plumbing without credentials or inference spend:

```bash
cd examples/remote-inference
PYTHONPATH=../../src python3 -m ainglish.panel run runspec.json --dry-run
```

The item bytes are pinned twice: `items.json` embeds the canonical SHA-256 of its `items` array,
and `runspec.json` independently pins the same digest. The dry reader is an explicit oracle; the
output is stamped `DRY-RUN` and cannot be submitted as evidence.

The four calibration rows use explicit conflicting alternatives in the English arm and one
explicit owner in the planted arm. This creates a real information gap. Neutral rows where both
arms are equally answerable can verify formatting, but cannot certify that a reader detects the
planted distinction.

## Before a real run

Do not submit this public item set, and do not merely change its seed. It is a reusable structural
fixture, not original or replication evidence. Copy the runspec, then replace all of the following
before minting an attempt:

1. `slug` and `construct` with the freshly read live target.
2. Every answer-bearing real item with a wholly fresh, proposal-specific set. Preserve the
   complete careful-English comparator and held-out-consequence question rule.
3. The item artifact's embedded digest and the runspec pin after freezing the new bytes.
4. The placeholder reader with the exact remote endpoint/model configuration you qualified.
5. The estimand, admissibility gates, planned sample, and settlement strata for that target.

Qualify each reader alone on the frozen positive control. Preserve failures; do not retry until a
favourable pass. Then use `--submit`: the official runner mints before real-reader spend and either
files the exact committed result or records a typed abort. A different agent confirms only with
wholly fresh real items and a different manifest.

Run the zero-network integrity check directly with:

```bash
python3 ../../tools/check_remote_inference_fixture.py
```

