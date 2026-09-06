# Preparing Ainglish for a training corpus

`ainglish-prepare-training` (or `python -m ainglish.training_ingestion`) reads an
already-downloaded, versioned Ainglish training pack. It never downloads data,
loads a model or edits the frozen pack. Pin its `MANIFEST.json` SHA-256 from a
trusted release record before ingestion. A computed hash alone proves identity,
not authenticity, source quality or legal authority.

```sh
ainglish-prepare-training ./ainglish-training-v3 \
  --manifest-sha256 EXPECTED_64_HEX_MANIFEST_DIGEST \
  --dataset instruction --output ./prepared-ainglish-v3
```

The command refuses an existing output directory or one inside the frozen source.
It verifies the selected JSONL file and register JSONL against the pinned manifest,
not every Parquet/Dolma companion. Supported projections are `instruction`,
`parallel` and `pretrain_documents`. Each output line wraps the complete original
row in `row` and adds a separate `provenance` object. Adapt that explicit envelope
to your trainer; it is not automatically a framework-specific conversation format.

Default filtering keeps canonical/normative rows. `--include-non-normative` admits
the pack's supplemental examples without relabelling them canonical. This flag is
not permission to ingest evaluations. Pretraining documents contain complete
registered definitions and inherit the pack's train-only split. Any contradictory
row split is refused. Optional `--slug` filters are exact; unknown slugs fail.

## Prevent leakage and keep the meaning

- Keep your evaluation set separately versioned and outside the training pack.
- Call `content_fingerprint("instruction", evaluation_row)` on rows with `prompt`
  and `response`, or use the corresponding `parallel`/`pretrain_documents` fields.
  Save the hashes as a JSON list and pass `--exclude-evaluation hashes.json`.
- This catches exact copied tasks, not paraphrases, shared templates or unknown
  holdouts. Perform a separate template/concept overlap review where relevant.
- Deduplication compares exact task content after canonical JSON encoding. It
  does not lowercase markers, collapse punctuation, discard clauses or trim
  exceptions. Duplicate source identities remain in the surviving record.
- Do not strip hyphens or discard a definition merely as a low-frequency language.
  Review corpus filters against held-out meaning tests before adopting exceptions.
  Do not bypass security, privacy or copyright filtering.
- A construct's ratification does not ratify tags mentioned inside its examples.
  Inspect embedded markers when selecting a narrower teaching vocabulary. Frozen
  source definitions remain unchanged.

`RECEIPT.json` records exact input-file and output hashes, versions, filters,
excluded row IDs and preserved origins. Conflicting provenance, unratified source
references and non-training splits cause refusal, not silent repair.

## Adoption milestones need different receipts

1. **Published:** a versioned public-domain bundle and its hashes exist.
2. **Included:** a downstream corpus publisher names the exact release/hash,
   transformations and included rows in its dataset manifest or data card.
3. **Trained:** a model producer discloses use of that corpus/version in a model
   card, data report or attributable training receipt. Inclusion alone does not
   establish training, nor can plausible model output prove it.
4. **Useful after exposure:** a preregistered comparison uses fresh held-out tasks,
   matched English exposure and retention safeguards. Share adverse family-level
   results with aggregate gains. Local learning is not external adoption.
5. **Encoded efficiently:** a specified future tokenizer is actually measured.
   Training weights does not change a fixed tokenizer's segmentation.

Public-domain availability and real usage make reuse possible; they cannot ensure
that a lab includes the material. Prefer low-burden, checkable downstream receipts
over unverified claims. CLARIN outreach remains for a consciously chosen later
language release, not an advance-staged release.
