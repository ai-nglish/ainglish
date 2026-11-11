"""Separate-call twin studies: frozen plans, complete journals and paired replay.

This is a report-only instrument, NOT the register's ordinary single-answer
comprehension harness. It never mints, measures, votes or changes a gate. A real
experiment still requires an admitted prospective protocol and preregistration.
The caller-supplied registration reference is retained, not authenticated here.
"""
from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import hashlib

from .client import _canonical_json

KIND = "ainglish.separate-call-pairs.v1"
ARMS = ("english", "ainglish")
_POLICY = {
    "exposure": "isolated-stateless-calls",
    "assignment": "both-arms-per-reader-pair",
    "point_weighting": "equal-pair-equal-reader",
    "missing_policy": "abort-study",
    "reuse_policy": "fresh-call-per-occurrence",
}


def _bytes(value):
    return _canonical_json(value).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be non-empty text")
    return value


def _closed(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(name + " has missing or unknown fields")


def validate(plan):
    """Validate all answer-bearing inputs and policies before constructing calls.

    ``world_id`` is the author's dependence declaration, not a proof of IID
    sampling. Exact repeated content is also checked so renaming IDs alone does
    not permit known shared worlds to be independently resampled.
    """
    _closed(plan, {"kind", "proposal_id", "seed", "population_scope", "readers", "pairs",
                   "resampling_unit", *_POLICY}, "plan")
    if plan["kind"] != KIND:
        raise ValueError("unknown paired-study kind")
    for key, value in _POLICY.items():
        if plan[key] != value:
            raise ValueError("unsupported " + key)
    if type(plan["seed"]) is not int or not 0 <= plan["seed"] < 2**63:
        raise ValueError("seed must be a nonnegative signed-64-bit integer")
    for key in ("proposal_id", "population_scope"):
        _text(plan[key], key)
    if plan["resampling_unit"] not in ("pair", "family"):
        raise ValueError("resampling_unit must be pair or family")
    readers = plan["readers"]
    if not isinstance(readers, list) or not readers or len(readers) > 32:
        raise ValueError("readers must contain 1..32 public instrument identities")
    reader_ids = []
    for reader in readers:
        _closed(reader, ("id", "instrument_ref"), "reader")
        reader_ids.append(_text(reader["id"], "reader.id"))
        _text(reader["instrument_ref"], "reader.instrument_ref")
    if len(set(reader_ids)) != len(reader_ids):
        raise ValueError("duplicate reader id")
    pairs = plan["pairs"]
    if not isinstance(pairs, list) or not 2 <= len(pairs) <= 4096:
        raise ValueError("pairs must contain 2..4096 frozen pairs")
    if len(readers) * len(pairs) * 4 > 16384:
        raise ValueError("one plan may schedule at most 16384 separate calls")
    ids, worlds, contents, families = set(), {}, {}, set()
    for pair in pairs:
        _closed(pair, ("id", "family", "twins"), "pair")
        pair_id = _text(pair["id"], "pair.id")
        family = _text(pair["family"], "pair.family")
        if pair_id in ids:
            raise ValueError("duplicate pair id")
        ids.add(pair_id)
        families.add(family)
        twins = pair["twins"]
        if not isinstance(twins, list) or len(twins) != 2:
            raise ValueError("each pair must have exactly two twins")
        twin_ids = set()
        for twin in twins:
            _closed(twin, ("id", "world_id", "english", "ainglish", "question", "options", "answer"), "twin")
            for key in ("id", "world_id", "english", "ainglish", "question", "answer"):
                _text(twin[key], "twin." + key)
            if twin["id"] in twin_ids:
                raise ValueError("duplicate twin id within a pair")
            twin_ids.add(twin["id"])
            options = twin["options"]
            if not isinstance(options, list) or not 2 <= len(options) <= 26:
                raise ValueError("each twin needs 2..26 opaque answer options")
            for option in options:
                _text(option, "option")
                if option != option.strip():
                    raise ValueError("options must not have surrounding whitespace")
            if len({x.casefold() for x in options}) != len(options) or twin["answer"] not in options:
                raise ValueError("options must be distinct and contain the exact gold label")
            # A repeated occurrence is a fresh call, never a reused response. It
            # cannot simultaneously count as a newly independent bootstrap unit.
            for arm in ARMS:
                # Fingerprint what the reader actually sees, not the hidden gold,
                # the other arm, or author-assigned IDs. Cross-arm repeats count too.
                content = _digest({"text": twin[arm], "question": twin["question"], "options": options})
                previous = contents.get(content)
                if previous is not None:
                    if previous[2] != twin["answer"]:
                        raise ValueError("identical reader inputs have conflicting gold answers")
                    if previous[0] != pair_id and (plan["resampling_unit"] != "family" or previous[1] != family):
                        raise ValueError("repeated worlds/content across pairs require one shared resampling family")
                contents[content] = (pair_id, family, twin["answer"])
            for index, key in ((worlds, twin["world_id"]),):
                previous = index.get(key)
                if previous is not None and previous[0] != pair_id:
                    if plan["resampling_unit"] != "family" or previous[1] != family:
                        raise ValueError("repeated worlds/content across pairs require one shared resampling family")
                index[key] = (pair_id, family)
    if plan["resampling_unit"] == "family" and len(families) < 2:
        raise ValueError("family bootstrap requires at least two declared families")
    # Deliberately no float-bearing unbound metadata or hidden execution knobs.
    _bytes(plan)
    return _digest(plan)


def planned_calls(plan):
    """Return deterministic calls containing one twin only, no gold/other twin.

    The wrapper identifies calls for journalling; only ``prompt`` is passed to
    the reader. Every call must start a fresh conversation. Order is hashed,
    independent of golds, and does not adapt to previous answers.
    """
    validate(plan)
    calls = []
    for reader in plan["readers"]:
        for pair in plan["pairs"]:
            for arm in ARMS:
                for twin in pair["twins"]:
                    key = [reader["id"], pair["id"], arm, twin["id"]]
                    calls.append({"key": key, "reader": deepcopy(reader),
                                  "prompt": {"text": twin[arm], "question": twin["question"],
                                             "options": list(twin["options"])}})
    return sorted(calls, key=lambda c: (_digest([plan["seed"], c["key"]]), c["key"]))


class StudyAborted(RuntimeError):
    """A failed/absent response ends this run; all started records are retained."""
    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("paired study aborted; preserve the complete started-call journal")


def execute(plan, ask_fn, *, registration, record):
    """Execute a fixed plan without adaptive retries or early success stopping.

    ``ask_fn(reader, text, question, options)`` must be stateless per call.
    ``record(row)`` MUST durably append each started/completed event; an exception
    stops execution. Registration is a caller-attested external reference whose
    exact plan digest must match; this module does not authenticate that reference
    or authorize live inference. Never put credentials in the plan/reference.
    """
    frozen = deepcopy(plan)
    registration = deepcopy(registration)
    digest = validate(frozen)
    _closed(registration, ("reference", "plan_sha256"), "registration")
    _text(registration["reference"], "registration.reference")
    if registration["plan_sha256"] != digest:
        raise ValueError("registration does not commit to this exact plan")
    if not callable(ask_fn) or not callable(record):
        raise ValueError("a stateless reader and durable journal callback are required")
    rows = []
    for call in planned_calls(frozen):
        key = call["key"]
        record({"event": "started", "plan_sha256": digest, "key": list(key)})
        prompt = call["prompt"]
        try:
            answer = ask_fn(deepcopy(call["reader"]), prompt["text"], prompt["question"], list(prompt["options"]))
        except BaseException as exc:
            # Do not include exception text: a transport exception may contain a key.
            row = {"key": key, "status": "fault", "answer": None, "fault": type(exc).__name__}
            rows.append(row)
            try:
                record({"event": "completed", "plan_sha256": digest, **deepcopy(row)})
            except BaseException as journal_error:
                # Suppress the transport error's potentially secret-bearing context
                # even when writing its sanitized completion event also fails.
                raise journal_error from None
            receipt = _receipt(digest, registration, rows, "aborted")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                exc.paired_study_receipt = receipt
                raise
            raise StudyAborted(receipt) from None
        good = isinstance(answer, str) and answer in prompt["options"]
        row = {"key": key, "status": "answered" if good else "invalid_response",
               "answer": answer if isinstance(answer, str) else None, "fault": None}
        rows.append(row)
        record({"event": "completed", "plan_sha256": digest, **deepcopy(row)})
        if not good:
            raise StudyAborted(_receipt(digest, registration, rows, "aborted"))
    receipt = _receipt(digest, registration, rows, "complete")
    # Replay verifies the whole expected roster before any report can be returned.
    receipt["report"] = replay(frozen, receipt)
    return receipt


def _receipt(digest, registration, rows, state):
    return {"kind": "ainglish.separate-call-pairs.journal.v1", "plan_sha256": digest,
            "registration": deepcopy(registration), "registration_verified": False,
            "state": state, "rows": deepcopy(rows), "rows_sha256": _digest(rows)}


def replay(plan, receipt):
    """Score complete pairs, then bootstrap declared units, holding readers fixed.

    Point weighting stays equal-pair/equal-reader for either resampling unit.
    Cluster draws concatenate all pairs in each sampled family (including repeat
    draws). This is NOT equal-family weighting, pairwise IID by assertion, a
    per-answer accuracy estimate, or independent replication.
    """
    digest = validate(plan)
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    if receipt.get("kind") != "ainglish.separate-call-pairs.journal.v1" or receipt.get("state") != "complete":
        raise ValueError("only a complete paired-study journal can be scored")
    registration = receipt.get("registration")
    _closed(registration, ("reference", "plan_sha256"), "registration")
    _text(registration["reference"], "registration.reference")
    if receipt.get("plan_sha256") != digest or registration["plan_sha256"] != digest:
        raise ValueError("plan digest mismatch")
    rows = receipt.get("rows")
    if not isinstance(rows, list) or receipt.get("rows_sha256") != _digest(rows):
        raise ValueError("response journal digest mismatch")
    expected = {tuple(c["key"]): c for c in planned_calls(plan)}
    answers = {}
    for row in rows:
        _closed(row, ("key", "status", "answer", "fault"), "response")
        if not isinstance(row["key"], list) or len(row["key"]) != 4 or any(not isinstance(k, str) for k in row["key"]):
            raise ValueError("invalid response key")
        key = tuple(row["key"])
        if key not in expected or key in answers:
            raise ValueError("unexpected or duplicated response key")
        if row["status"] != "answered" or row["fault"] is not None or row["answer"] not in expected[key]["prompt"]["options"]:
            raise ValueError("failed, absent or off-option response cannot be dropped or scored")
        answers[key] = row["answer"]
    if set(answers) != set(expected):
        raise ValueError("incomplete reader/pair/arm/twin roster")
    by_pair, cells = {}, []
    for pair in plan["pairs"]:
        delta = 0
        for reader in plan["readers"]:
            correct = {}
            for arm in ARMS:
                correct[arm] = all(answers[(reader["id"], pair["id"], arm, t["id"])] == t["answer"] for t in pair["twins"])
            delta += int(correct["ainglish"]) - int(correct["english"])
            cells.append({"reader": reader["id"], "pair": pair["id"], "correct": correct})
        by_pair[pair["id"]] = Fraction(100 * delta, len(plan["readers"]))
    groups = {}
    for pair in plan["pairs"]:
        unit = pair["id"] if plan["resampling_unit"] == "pair" else pair["family"]
        groups.setdefault(unit, []).append(pair["id"])
    units = sorted(groups)
    samples = []
    # Domain-separated SHA-256 counter stream. Version and draw count are fixed.
    indices = _bootstrap_indices(plan["seed"], len(units))
    for _ in range(2000):
        selected = []
        for _ in units:
            selected.extend(groups[units[next(indices)]])
        samples.append(sum((by_pair[p] for p in selected), Fraction()) / len(selected))
    samples.sort()
    n = len(samples)
    low, high = _interval(samples)
    point = sum(by_pair.values(), Fraction()) / len(by_pair)
    constant_pairs = [p["id"] for p in plan["pairs"] if p["twins"][0]["answer"] == p["twins"][1]["answer"]]
    return {"kind": "ainglish.separate-call-pairs.report.v1", "governance_effect": "report_only",
            "plan_sha256": digest, "journal_sha256": receipt["rows_sha256"],
            "pair_accuracy_delta_pp": float(point), "bootstrap_95_percentile_interval_pp": [float(low), float(high)],
            "correctness_rule": "both-twins-correct", "point_weighting": plan["point_weighting"],
            "resampling_unit": plan["resampling_unit"], "resampling_units": len(units),
            "readers_held_fixed": True, "bootstrap_draws": n, "quantiles": "nearest-rank-0.025-0.975",
            "pairs": len(by_pair), "reader_pair_scores": len(cells), "separate_calls": len(rows),
            "constant_response_compatible_pairs": constant_pairs,
            "pair_scores": cells, "population_scope": plan["population_scope"],
            "limitations": ["External registration reference is not authenticated by this instrument.",
                            "Sampling/reader independence and semantic golds are author declarations, not verified here.",
                            "Pairs listed as constant-response-compatible can be correct without distinguishing the twins.",
                            "Percentile bootstrap can degenerate at boundaries; zero width is not zero uncertainty.",
                            "No official comprehension measurement or gate completion is emitted."]}


def _bootstrap_indices(seed, unit_count):
    """Portable stream, with one counter across all draws, not reset per draw."""
    counter = 0
    while True:
        yield int(_digest([KIND, "bootstrap", seed, counter]), 16) % unit_count
        counter += 1


def _interval(sorted_samples):
    """Fixed nearest-rank 2.5/97.5 percentiles of a nonempty sorted sample."""
    n = len(sorted_samples)
    return sorted_samples[(n * 25 + 999) // 1000 - 1], sorted_samples[(n * 975 + 999) // 1000 - 1]
