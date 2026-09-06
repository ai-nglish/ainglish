"""Offline, pinned ingestion of published Ainglish training packs; no inference or downloads."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re

MAX_MANIFEST_BYTES = 256 * 1024
MAX_DATA_BYTES = 32 * 1024 * 1024
MAX_ROWS = 50000
DATASETS = {"instruction": ("prompt", "response"), "parallel": ("ainglish", "english"), "pretrain_documents": ("text",)}


def _json(raw):
    def pairs(items):
        if len({key for key, _ in items}) != len(items):
            raise ValueError("duplicate JSON keys are not an unambiguous source")
        return dict(items)
    def constant(value):
        raise ValueError("non-finite constants are not JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def _sha(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("expected a lowercase SHA-256")
    return value


def _read(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("local source exceeds the bounded ingestion limit")
    return raw


def _lines(raw):
    rows = []
    for line in io.BytesIO(raw):
        if not line.strip():
            continue
        if len(rows) >= MAX_ROWS:
            raise ValueError("source exceeds the bounded row limit")
        row = _json(line)
        if not isinstance(row, dict):
            raise ValueError("expected JSONL objects")
        rows.append(row)
    return rows


def content_fingerprint(dataset, row):
    """Exact task-content fingerprint, independent of row ID; no whitespace/meaning rewrite.

    Use this on evaluation rows to build an exclusion list. It detects exact copied
    tasks, not paraphrases, near duplicates, shared templates or unknown leakage.
    """
    if not isinstance(dataset, str) or dataset not in DATASETS or not isinstance(row, dict):
        raise ValueError("unknown dataset or invalid row")
    payload = {}
    for key in DATASETS[dataset]:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("missing non-empty task content")
        payload[key] = value
    raw = json.dumps({"dataset": dataset, "content": payload}, sort_keys=True,
                     ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def prepare_training_records(pack_dir, *, expected_manifest_sha256, dataset="instruction",
                             include_non_normative=False, slugs=(), evaluation_fingerprints=()):
    """Verify selected files, filter and exact-deduplicate without editing the source pack.

    Caller pins MANIFEST.json independently. Hashes establish byte identity, not source
    authenticity or legal authority. Only manifest, register JSONL and selected JSONL
    are inspected; not every companion format. Complete source rows are preserved.
    """
    if not isinstance(dataset, str) or dataset not in DATASETS or type(include_non_normative) is not bool:
        raise ValueError("choose a supported dataset and explicit boolean filter")
    if not isinstance(slugs, (tuple, list)) or len(slugs) > 1000 or any(not isinstance(s, str) or not s for s in slugs):
        raise ValueError("slugs must be a bounded sequence of exact source slugs")
    if not isinstance(evaluation_fingerprints, (list, tuple, set, frozenset)) or len(evaluation_fingerprints) > MAX_ROWS:
        raise ValueError("evaluation exclusion list exceeds its bound")
    excluded_fingerprints = {_sha(value) for value in evaluation_fingerprints}
    root = Path(pack_dir)
    manifest_raw = _read(root / "MANIFEST.json", MAX_MANIFEST_BYTES)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    if manifest_sha != _sha(expected_manifest_sha256):
        raise ValueError("manifest pin mismatch; do not silently update the expected digest")
    manifest = _json(manifest_raw)
    if not isinstance(manifest, dict) or manifest.get("kind") != "ainglish.language.training-pack" \
            or manifest.get("license") != "CC0-1.0" or manifest.get("splits") != ["train"] \
            or manifest.get("scope") != "train-only-projection-of-frozen-language-release":
        raise ValueError("not a supported train-only public-domain language pack")
    version, source = manifest.get("version"), manifest.get("source")
    if not isinstance(version, str) or not version or not isinstance(source, dict) or not isinstance(source.get("bundle"), str):
        raise ValueError("missing versioned source identity")
    register_digest = _sha(source.get("register_digest"))
    verified = {}

    def read_dataset(name):
        # Whitelisted paths only: no manifest-controlled traversal or archive extraction.
        rel = "data/" + name + ".jsonl"
        files, counts = manifest.get("files"), manifest.get("counts")
        if not isinstance(files, dict) or not isinstance(counts, dict):
            raise ValueError("missing file/count declarations")
        meta = files.get(rel)
        if not isinstance(meta, dict) or type(meta.get("bytes")) is not int or not 0 <= meta["bytes"] <= MAX_DATA_BYTES:
            raise ValueError("missing or oversized dataset declaration")
        raw = _read(root / rel, MAX_DATA_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        if len(raw) != meta["bytes"] or digest != _sha(meta.get("sha256")):
            raise ValueError("dataset does not match its pinned manifest")
        records = _lines(raw)
        if type(counts.get(name)) is not int or counts[name] != len(records):
            raise ValueError("declared and observed row counts disagree")
        verified[rel] = {"sha256": digest, "bytes": len(raw), "rows": len(records)}
        return records

    entries = {}
    for row in read_dataset("register"):
        slug = row.get("slug")
        if not isinstance(slug, str) or not slug or slug in entries or row.get("split") != "train" \
                or row.get("status") != "current" or row.get("kind") not in ("lexical", "grammatical", "notational", "discourse") \
                or not isinstance(row.get("ratified_version"), str) or not row["ratified_version"] \
                or not isinstance(row.get("ratified_at"), str) or not row["ratified_at"] \
                or row.get("register_digest") != register_digest or row.get("source_release_version") != version:
            raise ValueError("register must contain unique current ratified language from this source")
        entries[slug] = row
        _sha(row.get("content_digest"))
    if set(slugs) - entries.keys():
        raise ValueError("a selected slug is not in this pinned ratified register")
    rows = read_dataset(dataset)
    kept, by_fingerprint, seen_ids, dropped = [], {}, set(), []
    for row in rows:
        identity = row.get("id")
        if not isinstance(identity, str) or not identity or identity in seen_ids:
            raise ValueError("source row IDs must be unique non-empty strings")
        seen_ids.add(identity)
        if dataset == "pretrain_documents":
            meta = row.get("metadata")
            if not isinstance(meta, dict) or meta.get("license") != "CC0-1.0" or meta.get("release_version") != version \
                    or meta.get("register_digest") != register_digest:
                raise ValueError("document provenance differs from the pinned source")
            slug = meta.get("slug")
            # Legacy documents have no row split: they inherit the verified train-only pack.
            if row.get("split", "train") != "train" or meta.get("split", "train") != "train":
                raise ValueError("evaluation/holdout rows are not training data")
            normative = True  # Complete registered definitions, not the supplemental pair pool.
        else:
            slug, normative = row.get("slug"), row.get("normative")
            if type(normative) is not bool or row.get("split") != "train" or row.get("register_digest") != register_digest \
                    or row.get("source_release_version") != version:
                raise ValueError("row split or provenance differs from the pinned source")
        if not isinstance(slug, str) or slug not in entries:
            raise ValueError("task refers outside this ratified source")
        if dataset == "pretrain_documents" and meta.get("content_digest") != entries[slug]["content_digest"]:
            raise ValueError("document definition digest differs from its registered source")
        fingerprint = content_fingerprint(dataset, row)
        reason = None
        if slugs and slug not in slugs:
            reason = "not_selected"
        elif not normative and not include_non_normative:
            reason = "non_normative_excluded"
        elif fingerprint in excluded_fingerprints:
            reason = "exact_evaluation_overlap"
        elif fingerprint in by_fingerprint:
            reason = "exact_duplicate"
            kept[by_fingerprint[fingerprint]]["provenance"]["source_rows"].append({"id": identity, "slug": slug, "normative": normative})
        if reason:
            dropped.append({"id": identity, "reason": reason, "content_sha256": fingerprint})
            continue
        by_fingerprint[fingerprint] = len(kept)
        kept.append({"row": row, "provenance": {"manifest_sha256": manifest_sha, "source_bundle": source["bundle"],
            "license": "CC0-1.0", "split": "train", "dataset": dataset, "content_sha256": fingerprint,
            "source_rows": [{"id": identity, "slug": slug, "normative": normative}]}})
    receipt = {"kind": "ainglish.training-ingestion.v1", "source_manifest_sha256": manifest_sha,
        "source": source, "version": version, "dataset": dataset, "verified_files": verified,
        "input_rows": len(rows), "output_rows": len(kept), "dropped": dropped,
        "filters": {"slugs": list(slugs), "include_non_normative": include_non_normative,
                    "evaluation_fingerprint_count": len(excluded_fingerprints)},
        "transformation": "Exact task-content deduplication only. Whole source rows and every merged source identity retained.",
        "boundaries": ["Pinned source status, not a fresh check of today's register.",
            "Ratifying a definition does not ratify every other marker mentioned inside its examples.",
            "Exact overlap checks do not exclude paraphrase leakage or unseen evaluation data.",
            "Publication or ingestion is not evidence that an external lab trained on this data."]}
    return {"records": kept, "receipt": receipt}


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pack", type=Path)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--dataset", choices=sorted(DATASETS), default="instruction")
    parser.add_argument("--include-non-normative", action="store_true")
    parser.add_argument("--slug", action="append", default=[])
    parser.add_argument("--exclude-evaluation", type=Path, help="Local JSON list of exact content fingerprints")
    parser.add_argument("--output", type=Path, required=True, help="New directory outside the frozen source; never overwritten")
    args = parser.parse_args(argv)
    try:
        target = args.output.resolve()
        if args.pack.resolve() == target or args.pack.resolve() in target.parents or target.exists():
            raise ValueError("output must be new and outside the frozen source")
        fingerprints = _json(_read(args.exclude_evaluation, MAX_DATA_BYTES)) if args.exclude_evaluation else []
        result = prepare_training_records(args.pack, expected_manifest_sha256=args.manifest_sha256,
            dataset=args.dataset, include_non_normative=args.include_non_normative, slugs=args.slug,
            evaluation_fingerprints=fingerprints)
        data = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in result["records"]).encode("utf-8")
        result["receipt"]["output_sha256"] = hashlib.sha256(data).hexdigest()
        result["receipt"]["output_bytes"] = len(data)
        target.mkdir(parents=True, exist_ok=False)
        (target / "records.jsonl").write_bytes(data)
        (target / "RECEIPT.json").write_text(json.dumps(result["receipt"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (ValueError, TypeError, OSError, KeyError):
        print(json.dumps({"error": "ingestion_failed", "message": "Invalid source, pin, row, filter or output. No complete output is claimed; inspect any partial output directory before reuse."}))
        return 2
    print(json.dumps({"output_rows": len(result["records"]), "dropped_rows": len(result["receipt"]["dropped"]), "output_sha256": result["receipt"]["output_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
