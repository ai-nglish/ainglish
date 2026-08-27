#!/usr/bin/env python3
"""Validate CITATION.cff — the file GitHub reads to render "Cite this repository".

It exists because the project had DOIs for two days and no pasteable citation anywhere: no
CITATION.cff here, no BibTeX on the site, and /cite a 404. Metadata nobody validates is metadata
that rots quietly, and this file's failure mode is silent — GitHub simply stops offering the button,
and a reader hand-assembles a citation instead, usually wrongly.

Checks the shape a citation consumer actually depends on, plus one project rule: every citation
Ainglish emits discloses AI authorship, because the bibliography entry is the copy that travels
furthest from the site and is therefore the last place the disclosure should be droppable.

Exit 0 = valid. Exit 1 = a defect, named. Exit 2 = cannot check (missing dependency), which is a
failure too: a check that silently skips is not a check.
"""

from __future__ import annotations

import sys
from pathlib import Path

CFF = Path(__file__).resolve().parent.parent / "CITATION.cff"

CONCEPT_DOI = "10.5281/zenodo.22095467"

REQUIRED_TOP = ("cff-version", "title", "message", "type", "authors", "identifiers", "url")
REQUIRED_PREFERRED = ("type", "title", "authors", "year", "url", "notes")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> int:
    try:
        import yaml
    except ImportError:
        print("CANNOT CHECK: pyyaml is not installed (pip install pyyaml)")
        return 2

    if not CFF.exists():
        fail(f"{CFF.name} is missing; GitHub's 'Cite this repository' button needs it")

    try:
        doc = yaml.safe_load(CFF.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        fail(f"{CFF.name} is not parseable YAML: {exc}")

    if not isinstance(doc, dict):
        fail(f"{CFF.name} must be a mapping")

    for key in REQUIRED_TOP:
        if key not in doc:
            fail(f"missing required key: {key}")

    if doc["cff-version"] != "1.2.0":
        fail(f"cff-version must be 1.2.0, got {doc['cff-version']!r}")

    if doc["type"] not in ("software", "dataset"):
        fail(f"type must be software or dataset, got {doc['type']!r}")

    authors = doc["authors"]
    if not isinstance(authors, list) or not authors:
        fail("authors must be a non-empty list")
    for author in authors:
        if not isinstance(author, dict) or not (
            "name" in author or ("given-names" in author and "family-names" in author)
        ):
            fail(f"each author needs an entity name or given/family names: {author!r}")

    dois = [
        str(i.get("value"))
        for i in doc["identifiers"]
        if isinstance(i, dict) and i.get("type") == "doi"
    ]
    if CONCEPT_DOI not in dois:
        fail(f"the concept DOI {CONCEPT_DOI} must be listed; found {dois}")

    preferred = doc.get("preferred-citation")
    if not isinstance(preferred, dict):
        fail("preferred-citation must be present: the paper is what a reader should cite for findings")
    for key in REQUIRED_PREFERRED:
        if key not in preferred:
            fail(f"preferred-citation is missing {key}")

    notes = str(preferred.get("notes", ""))
    if "AI-authored" not in notes:
        fail("preferred-citation.notes must disclose AI authorship — the project does not present this work as human-authored")

    # The licence must match what the record's own identity claims. A `type: dataset` record whose
    # identifiers are the deposited language DOIs describes CC0 material; stamping the SDK's MIT on
    # it tells GitHub and every citation consumer the wrong thing about the dataset, and stays
    # schema-valid while doing so. That is exactly the failure this check exists to catch: the file
    # shipped valid-but-wrong until Dexagon read it (ai-nglish/ainglish#102).
    licence = str(doc.get("license", ""))
    expected = {"dataset": "CC0-1.0", "software": "MIT"}[doc["type"]]
    if licence != expected:
        fail(
            f"license must be {expected} for a {doc['type']} record, got {licence!r}. "
            "The language releases these DOIs identify are CC0-1.0; MIT covers the SDK software. "
            "If this record is meant to describe the software, change `type` as well as `license`."
        )

    print(
        f"CITATION.cff OK — {doc['type']} under {licence}, cff-version {doc['cff-version']}, "
        f"{len(dois)} DOI(s), preferred-citation is a {preferred['type']} with the AI-authorship note"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
