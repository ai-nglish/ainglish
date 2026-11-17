#!/usr/bin/env python3
"""Extra deterministic cross-language reviewer cases; no network or inference.

Run from this checkout with PYTHONPATH=src and --symfony /path/to/trusted/checkout.
The Symfony checkout needs its locked vendor dependencies installed. This check
executes its PHP class; it is not a sandbox for untrusted source code.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess

from ainglish import allocation


def check(symfony, php="php"):
    rng = random.Random(20261117)
    cases = []
    for i in range(300):
        n = 1 + i % 21
        worlds = [f"{i}-世界-{j}-é" for j in range(2 * n)]
        rng.shuffle(worlds)
        cases.append({"seed": rng.randrange(2**53), "readers": ["1", "01", "reader-é"],
                      "forms": [{"id": "1.0", "worlds": worlds},
                                {"id": "forme-世界", "worlds": list(reversed(worlds))}],
                      "per_cell": n})
    process = subprocess.run(
        [php, str(Path(__file__).with_suffix(".php")), str(Path(symfony).resolve())],
        input=json.dumps(cases, ensure_ascii=False), text=True, encoding="utf-8",
        capture_output=True, check=True, timeout=60)
    outputs = json.loads(process.stdout)
    if len(outputs) != len(cases):
        raise AssertionError("PHP returned the wrong number of rosters")
    checks = 0
    for case, actual in zip(cases, outputs):
        forms = {form["id"]: form["worlds"] for form in case["forms"]}
        expected_commitment = allocation.commitment(case["seed"], case["readers"], forms, case["per_cell"])
        if actual["commitment"] != expected_commitment:
            raise AssertionError("PHP/Python commitment mismatch")
        python = allocation.allocate_roster(case["seed"], case["readers"], forms, case["per_cell"])
        seen = set()
        for row in actual["allocation"]:
            identity = (row["reader"], row["form"])
            if identity in seen or row["reader"] not in case["readers"] or row["form"] not in forms:
                raise AssertionError("Duplicate or unknown PHP allocation identity")
            seen.add(identity)

            # Independent preimages: do not call the allocator's canonicalizer/rank.
            def key(world):
                raw = json.dumps(["balanced-world-hash-v1", case["seed"],
                                  row["reader"], row["form"], world],
                                 ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                return hashlib.sha256(raw).hexdigest(), world.encode("utf-8")

            ranked = sorted(forms[row["form"]], key=key)
            reference = {"english": ranked[:case["per_cell"]],
                         "ainglish": ranked[case["per_cell"]:]}
            if python[row["reader"]][row["form"]] != reference or any(
                    row[arm] != reference[arm] for arm in reference):
                raise AssertionError("Allocation disagrees with independent rank")
            checks += 1
        if seen != {(reader, form) for reader in case["readers"] for form in forms}:
            raise AssertionError("Incomplete PHP reader/form coverage")
    return {"rosters": len(cases), "reader_form_allocations": checks,
            "matching_commitments": len(cases), "target_inference_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symfony", required=True)
    parser.add_argument("--php", default="php")
    args = parser.parse_args()
    print(json.dumps(check(args.symfony, args.php), sort_keys=True))
