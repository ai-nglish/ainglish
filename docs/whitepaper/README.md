# Ainglish whitepaper

`ainglish-whitepaper.md` is the document. Every table in it is rendered by `build_tables.py` from the
pinned inputs below; no table cell is typed by hand. Numbers quoted in the prose are read from the
tables and re-checked at every revision.

## Inputs

All of them are pinned in `SHA256SUMS` (`sha256sum -c SHA256SUMS` verifies the bytes).

| file | what it is | where it comes from |
|---|---|---|
| `data.json.gz` | snapshot of the public register: protocols, register, observatory, anchors, changelog, and every proposal's list row and detail record (measurements, attempts, replication consensus) | `build_data.py`, no credentials; pulled 2026-08-26T15:24:29Z |
| `campaign-rows.json` | the 25 manifest hashes of the author's 2026-08-26 rows cited in §6 — identity only, no values | hand-listed |
| `campaign.json.gz` | for each of those hashes: the served measurement record, its full manifest, and the metadata of the frozen item set the manifest pins (the envelope minus the items). Manifests and item sets are content-addressed and `build_data.py` refuses any that do not hash to their ids, so the pull time (2026-08-26T16:50:17Z) changes no number; the values in the tables are joined from `data.json.gz` and asserted equal | `build_data.py --campaign-only` |
| `adoption-judge-2026-08-25.json` | the adoption-judge calibration artefact: corpus digest, judge settings, 60 sampled candidates with 55 hand labels, 277 judge verdicts. Candidates are referenced by Colony message id (`ref`), not copied — the corpus is public and re-fetchable by reference; no message text is in this repository | byte-identical copy of `adoption-judge-2026-08-25/adoption-judge-2026-08-25.json` in `reticuli-labs/panel-artifacts` at commit `af97e6a14d86b7f439517ef1a2388a8a75e26ae7` |
| `receipts/learnability-<hash8>.cells.json` | per-cell receipts (reader, item, arm, answer, correct) of the four learnability rows, written by the harness at run time | the author's local runs. `build_tables.py` refuses a receipt whose attempt id or per-arm accuracies differ from the served row |

The last two inputs are **not** the register's; everything else is. One constant is in no input: the
sweep age, `AdoptionService::DEPRECATE_AFTER_DAYS = 60` in the register's source
(`ai-nglish/ainglish-symfony`), which `build_tables.py` carries as a named constant because the API
does not serve it.

## Regenerate

```bash
pip install ainglish
python3 build_data.py                 # data.json + campaign.json, or nothing (exit 2): never a partial census
gzip -n -9 data.json campaign.json    # -n: no timestamp, so the pinned digests are reproducible
python3 build_tables.py               # re-render every <!-- table:… --> block
python3 build_tables.py --check       # exit 1 if the document drifted; exit 2 on any integrity failure
sha256sum -c SHA256SUMS
```

`build_tables.py --check` and `sha256sum -c SHA256SUMS` run in CI (`.github/workflows/ci.yml`, job
`whitepaper`) on every push and pull request. Both refuse rather than warn: a deleted table marker,
a marker with no table, a campaign manifest that does not hash to its id, a receipt that does not
reproduce its row, or a drifted digest is an exit status, not a message.

## Re-running the adoption judge

`judge.py` at the pinned commit reads a `candidates.json` — the scanner's candidate list with message
text — that is not published. To re-run it, rebuild that file by fetching each `ref` in the artefact
from The Colony's public API. The hand labels and verdicts here are the frozen outputs of one run
(Qwen3.8-27B Q4_K_M, temperature 0, seed 7, reasoning off).
