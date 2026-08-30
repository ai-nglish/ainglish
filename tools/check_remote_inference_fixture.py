#!/usr/bin/env python3
"""Zero-network integrity and safety checks for examples/remote-inference."""

import hashlib
import json
import pathlib
import re


ROOT = pathlib.Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "remote-inference"


def main():
    items_doc = json.loads((EXAMPLE / "items.json").read_text(encoding="utf-8"))
    runspec = json.loads((EXAMPLE / "runspec.json").read_text(encoding="utf-8"))
    items = items_doc["items"]
    canonical = json.dumps(
        items, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    assert items_doc["sha256"] == digest, "embedded item digest drifted"
    assert runspec["items_sha256"] == digest, "runspec item pin drifted"
    assert runspec["slug"].startswith("REPLACE-"), \
        "the public fixture must not accidentally target a live proposal"
    assert runspec["construct"].startswith("REPLACE-"), \
        "the public fixture must remain visibly non-evidence"
    assert "attempt" in runspec, "starter must exercise mint-before-spend validation in dry-run"

    calibration = [row for row in items if row.get("calibration")]
    real = [row for row in items if not row.get("calibration")]
    assert len(calibration) == 4 and len(real) == 4
    assert all(row["english"] != row["ainglish"] for row in calibration), \
        "a byte-identical calibration arm cannot carry a planted effect"
    assert all("either" in row["english"].casefold() for row in calibration), \
        "the cold arm must retain explicit conflicting alternatives"
    assert all("not" in row["ainglish"].casefold() for row in calibration), \
        "the planted arm must resolve the conflict explicitly"
    strata = {row["settlement_stratum"] for row in real}
    declared = {row["id"] for row in runspec["settlement_strata"]}
    assert strata == declared == {"inclusive", "exclusive"}
    def carries_answer_word(row, arm):
        return re.search(
            r"(?<![A-Za-z0-9_])" + re.escape(row["answer"]) + r"(?![A-Za-z0-9_])",
            row[arm], flags=re.IGNORECASE,
        ) is not None

    assert all(not carries_answer_word(row, arm) for row in real
               for arm in ("english", "ainglish")), \
        "real answer vocabulary must remain held out of both arm texts"
    print("remote-inference fixture OK: %s (%d calibration, %d real)" %
          (digest, len(calibration), len(real)))


if __name__ == "__main__":
    main()
