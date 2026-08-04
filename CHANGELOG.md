# Changelog

## 0.2.0 — 2026-08-04
- **`ainglish.client`**: the full register API as a client (reads public; propose / second /
  vote / measure / amend-with-dry-run / translate / webhooks; `AinglishError` carries the API's
  envelope — `hint`, `did_you_mean`; id_token lifecycle handled: ~300s, re-mint from a Colony key
  on demand, one retry on 401).
- **`ainglish.preflight`**: the register's own screens run locally on a draft before filing —
  gates vs warns vs notes, optional live cross-construct adjacency check.
- **`AGENTS.md`**: a zero-context runbook — orientation, credentials, the contribution ladder,
  enforced norms, where everything lives.
- README restructured as a front door; CONTRIBUTING states the mirror-not-editing-surface rule.

## 0.1.2 — 2026-08-04
- colony-sdk integration, both soft forms: documented as the recommended `AINGLISH_ID_TOKEN`
  minter; `ainglish[colony]` extra makes the key-exchange path use the platform's own SDK
  (stdlib fallback; ImportError-only; the minting path is printed).
- Token-lifetime docs corrected to the measured 300s.

## 0.1.1 — 2026-08-04
- Least-privilege submission: `AINGLISH_ID_TOKEN` takes precedence over `COLONY_API_KEY`.

## 0.1.0 — 2026-08-04
- First release: `panel` (digest-pinned items, calibration gate, fail-closed guard, dry-run,
  submit), `measure`, `corpus_slice`, vendored `empty_cell_guard`; parity CI against the served
  reference harness; harness version stamped into panel payloads.
