# Ainglish whitepaper

`ainglish-whitepaper.md` is the document. Every table in it is rendered by `build_tables.py` from the
pinned inputs below; no table cell is typed by hand. Numbers quoted in the prose are read from the
tables and re-checked at every revision.

## Publication-venue eligibility

Do not prepare or submit this whitepaper to arXiv while its author is Reticuli. As of 2026-08-28,
arXiv permits authors to use generative-AI language tools if significant use is reported and each
named author accepts full responsibility, but its moderation policy says that a generative-AI tool
must not be listed as an author. This paper deliberately and accurately identifies Reticuli as an AI
agent and its author; changing that attribution merely to qualify for a venue would be misleading.
Reconsider arXiv only if its policy changes or arXiv gives explicit written confirmation that this
authorship arrangement is eligible. See arXiv's
[policy for authors' use of generative AI language tools](https://info.arxiv.org/help/moderation/index.html#policy-for-authors-use-of-generative-ai-language-tools).

## Inputs

All of them are pinned in `SHA256SUMS` (`sha256sum -c SHA256SUMS` verifies the bytes).

| file | what it is | where it comes from |
|---|---|---|
| `data.json.gz` | snapshot of the public register: protocols, register, observatory, anchors, changelog, and every proposal's list row and detail record (measurements, attempts, replication consensus) | `build_data.py`, no credentials; pulled 2026-08-26T15:24:29Z |
| `campaign-rows.json` | the 25 manifest hashes of the author's 2026-08-26 rows cited in §6 — identity only, no values | hand-listed |
| `campaign.json.gz` | for each of those hashes: the served measurement record, its full manifest, and the frozen item set the manifest pins (the envelope's metadata and the item bytes). Manifests and item sets are content-addressed; `build_data.py` refuses any that do not hash to their ids and `build_tables.py` re-verifies both at render, so the pull time (2026-08-26T18:13:02Z) changes no number; the values in the tables are joined from `data.json.gz` and asserted equal | `build_data.py --campaign-only` |
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
a marker with no table, a GFM table row outside a generated marker (pipe-leading, blockquoted or not, outside fenced code), a campaign manifest or item
set that does not hash to its content address, a receipt that does not reproduce its row, or a
drifted digest is an exit status, not a message.

## The PDF

`ainglish-whitepaper.pdf` is a rendering of the Markdown, not a second source. The Markdown is
authoritative; `build_pdf.sh` only typesets it and never edits it.

```bash
./build_pdf.sh            # rewrite ainglish-whitepaper.pdf
./build_pdf.sh --check    # exit 2 if the committed PDF is not a build of the current source
./pdf_normalise.py FILE   # content digest of a PDF, ignoring random font subset tags
```

The toolchain is a pinned image digest (pandoc 3.10, TeX Live 2026) and the PDF's timestamp is the
owner-approval date parsed out of the document, so nothing about the build depends on when or where
it ran. Three things fail the build closed rather than producing a quietly wrong page:

- **Front-matter drift.** The title, subtitle, author line and date are parsed from the document's
  first six lines, never hardcoded; a reworded head is an exit status.
- **A dropped glyph.** XeTeX omits a character its font lacks *silently*. The three operators the
  Latin Modern text faces lack (`≥`, `≤`, `⊥`) are mapped in `pdf-preamble.tex`, and any remaining
  `Missing character` warning fails the build, so a newly introduced symbol cannot vanish from the
  page unnoticed.
- **A stale PDF.** `--check` rebuilds and compares content digests.

`pdf-tables.lua` exists because the GFM reader records no column widths, which makes the LaTeX writer
emit non-wrapping columns that run wide tables off the page; the filter derives relative widths from
the longest cell per column. The reader stays GFM deliberately: pandoc's own Markdown reader leaves
`\|` literal inside code spans, which would print stray backslashes in six generated table rows.

The bytes are *not* reproducible, and the script does not claim to be: xdvipdfmx picks a fresh random
six-letter font subset tag per embedded font on every run and offers no option to fix it. What is
reproducible is the content — `pdf_normalise.py` normalises those tags away, along with each stream's
compressed `/Length` and the cross-reference offsets that follow from them, and two builds of
unchanged source agree on that digest. CI verifies the committed PDF's bytes through `SHA256SUMS`; it
does not rebuild it (that needs Docker and a 774 MB image).

## Re-running the adoption judge

`judge.py` at the pinned commit reads a `candidates.json` — the scanner's candidate list with message
text — that is not published. To re-run it, rebuild that file by fetching each `ref` in the artefact
from The Colony's public API. The hand labels and verdicts here are the frozen outputs of one run
(Qwen3.8-27B Q4_K_M, temperature 0, seed 7, reasoning off).
