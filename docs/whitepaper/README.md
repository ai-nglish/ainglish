# Ainglish whitepaper

`ainglish-whitepaper.md` is the document. Every table in it is rendered from a snapshot of the public
register by `build_tables.py`; the snapshot is `data.json.gz` (pulled by `build_data.py`, no
credentials) and `SHA256SUMS` pins it.

Regenerate against the live register:

```bash
pip install ainglish
python3 build_data.py            # writes data.json (≈7.5 MB) from the public API
python3 build_tables.py          # re-renders every <!-- table:… --> block in the document
python3 build_tables.py --check  # exit 1 if the document has drifted from the data
```

`my-0826-rows.json` carries the comparator kinds of the 2026-08-26 campaign rows (the list
envelope omits manifests); `adoption-judge-table.md` is the calibration table copied from
`reticuli-labs/panel-artifacts/adoption-judge-2026-08-25`.
