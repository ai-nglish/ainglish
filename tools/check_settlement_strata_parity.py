#!/usr/bin/env python3
"""Pin Python's settlement-strata acceptance boundary to the register's shared corpus."""

import json
from pathlib import Path

from ainglish.client import _settlement_strata_contract, _validate_measurement_strata


CORPUS = Path(__file__).parents[1] / "tests" / "fixtures" / "settlement-strata-parity-v1.json"


def main():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["kind"] == "ainglish.settlement-strata-parity.v1"

    for case in corpus["contract_cases"]:
        accepted = True
        try:
            contract = _settlement_strata_contract({"settlement_strata": case["settlement_strata"]})
            for got, expected in zip((row[2] for row in contract), case.get("shares", [])):
                assert abs(got - expected) <= 1e-9, case["name"]
        except ValueError:
            accepted = False
        assert accepted is case["accepted"], case["name"]

    for case in corpus["measurement_cases"]:
        accepted = True
        try:
            _validate_measurement_strata({
                key: value for key, value in case.items()
                if key not in {"name", "accepted"}
            })
        except ValueError:
            accepted = False
        assert accepted is case["accepted"], case["name"]

    sixty_four = [{"id": "cell-%d" % i, "weight": 1} for i in range(1, 65)]
    assert len(_settlement_strata_contract({"settlement_strata": sixty_four})) == 64
    try:
        _settlement_strata_contract({"settlement_strata": sixty_four + [{"id": "cell-65", "weight": 1}]})
        raise AssertionError("65 settlement strata must refuse")
    except ValueError:
        pass


if __name__ == "__main__":
    main()
