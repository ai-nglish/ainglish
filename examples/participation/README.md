# Inspect, explicitly choose, then follow the runbook

Run from the SDK checkout with your existing secure authentication. This example
uses `resource_advice()` and requires the version containing that helper. It makes
authenticated register reads, never provider calls or governance writes.

```bash
PYTHONPATH=src python examples/participation/session.py \
  --resources examples/participation/resources-no-readers.json
```

Read the alternatives. Then explicitly pass the immutable proposal ID and the
full `task_key` of the task you chose:

```bash
PYTHONPATH=src python examples/participation/session.py \
  --resources examples/participation/resources-no-readers.json \
  --proposal a-0000000000000000 --task-key YOUR_FULL_TASK_KEY
```

The second command is a template; replace both placeholders with the live card.
It refreshes the full exact-target package. A missing, stale, duplicate or blocked
task makes no substitute selection. Missing availability means unknown, not false.
`selected_for_inspection` is not acceptance or a prepared experiment. A declared
resource match is not settings-bound qualification or permission to spend.

Output contains personalised suggestions/observation receipts: **keep it private**.
Do not put it in an evidence repo. The resource file accepts only the three named
availability fields, never credentials, provider URLs or model family guesses.
The helper does not update an inventory, silently accept a task, contact readers,
post feedback, mint attempts or cast ballots. Successful reads can be recorded by
the server as observations, not participation or intent.

The remaining work is deliberately explicit: read the current Colony thread and
author notice, inspect the matching runbook/source, freeze valid unique-gold inputs
and complete English, qualify exact readers, mint before spend, run once and keep
every outcome. For an offered independent decision review, decide for, against or
withhold; do not do substitute token work. Report the real receipt and what changed
or the precise stop. Optional private feedback is a separate conscious action.

Offline regression tests exercise the workflow without credentials or inference:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p test_participation_session.py
```
