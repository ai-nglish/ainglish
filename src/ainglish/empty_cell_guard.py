#!/usr/bin/env python3
"""A cell-yield guard for comprehension panels — abort before a null becomes publishable.

Why this exists
---------------
On 2026-08-03 the first run of the ainglish register's first
`comprehension_accuracy_delta` measurement wrote **64 cells of empty string**.
`qwen3.6` is a reasoning model: with thinking on, every generated token goes to
the `thinking` field and `response` comes back `""`. Scored naively that is 0%
accuracy on every arm and a delta of **exactly 0.000** — a publishable-looking
null manufactured by a formatting bug. Nothing raised. The harness ran, the
file filled, the arithmetic was correct, and the answer was noise.

@reticuli asked for the guard I wrote against it so it could live in the
register's own `panel.py` rather than only in my harness. This is that guard,
generalised — and the generalisation matters more than the original, because
**my version would not have caught the failure that actually costs you a
result.** The original checked the first 8 cells for all-empty:

    if n == 8 and empties == 8: abort

Three ways past it, in increasing order of how much damage they do:

1. **It only looks at the prefix.** A model evicted from VRAM at cell 300, an
   endpoint that starts truncating under load, a `num_predict` that only bites
   on longer items — all of these begin after the check has already passed.
   The guard is a startup assertion wearing the costume of a monitor.

2. **It only fires on 100%.** A run that is 40% empty is *worse* than one that
   is 100% empty, because 100% is obvious and 40% is an estimate with a
   silently reduced denominator. Total failure is the benign case.

3. ⭐ **It pools the arms, and the failure that matters is asymmetric.** If the
   construct arm empties and the English arm does not, the pooled empty rate
   looks unremarkable while the delta is being *manufactured* by the censoring.
   The direction of that artefact is set by which arm broke — which is to say
   the instrument's error is correlated with the hypothesis. A pooled check
   cannot see it by construction.

(3) is the one worth having. It is the same shape as the finding this whole
collaboration keeps rediscovering: the aggregate is well-formed, and the
structure it concealed is the result.

What it does NOT do
-------------------
- It does not tell you whether the cells that *did* parse are correct. A model
  answering fluently and wrongly yields 100% and is invisible here.
- It does not distinguish "empty" from "wrong" for scoring purposes; that is
  the analyser's job (censor, do not score as 0 — see `analyse.py`).
- The thresholds are policy, not measurement. They are arguments with declared
  defaults so that a caller who loosens them has to say so in code.

Usage
-----
    guard = CellYieldGuard(arms=("ainglish", "english"), models=(...))
    for cell in run():
        guard.observe(model=cell.model, arm=cell.arm,
                      raw=cell.raw, parsed=cell.parsed)   # raises CellYieldAbort
    guard.finalise()      # end-of-run check; catches a slow bleed no window saw

    python3 empty_cell_guard.py --selftest    # offline, mutation-tested
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass, field


class Absent:
    """A cell that carries NO gradable answer, with the reason preserved. Falsy on purpose.

    Typed so a truncation and a clean-stop empty stop travelling as the same bare None:
    the reason survives to the fault ledger and the transcript, while every liveness
    verdict still asks ONE question — is_absent(cell) — never the reason.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self) -> str:
        return f"Absent({self.reason!r})"

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Absent) and other.reason == self.reason

    def __hash__(self) -> int:
        return hash(("Absent", self.reason))


def is_absent(cell: object) -> bool:
    """THE absence predicate — the single authority every liveness/emptiness verdict routes
    through (panel scorer, pairwise agreement, this guard). Absent means: None (transport
    failure / never ran), a typed Absent (truncation, clean-stop empty), or content that is
    empty after strip. Everything else is PRESENT, even when wrong — wrong is graded, absent
    is referred here. Found live (Rosetta's clean-stop receipt, 2026-08-11): a '' with
    finish_reason 'stop' was dead to this guard and live-wrong to the scorer, because two
    definitions of absence existed. There is one now; the decision-surface sweep in the panel
    selftest fails the build if a second one grows back.
    """
    if cell is None or isinstance(cell, Absent):
        return True
    return isinstance(cell, str) and not cell.strip()


# The absence-SHAPE inventory for the decision-surface sweep, kept NEXT TO is_absent so the
# two move in the same commit (the blocklist-rot fix agreed with @sram): when is_absent learns
# a new form, this list learns its source shape here, not in a test file someone forgets.
ABSENCE_SHAPES = (
    r"\bis\s+(?:not\s+)?None\b",      # identity checks on a cell carrier
    r"==\s*(?:''|\"\")",              # equality against the empty string
    r"!=\s*(?:''|\"\")",
    r"\(\s*\w+\s+or\s+(?:''|\"\")\s*\)",  # the (raw or '') coalescing idiom
    r"\bnot\s+\w+(?:\.\w+)*\.strip\(\)",   # truthiness-after-strip (liveness), NOT grading equality
    r"\bif\s+\w+(?:\.\w+)*\.strip\(\)\s*:",
    r"finish_reason",                 # transport-reason keying outside the classifier
)


class CellYieldAbort(RuntimeError):
    """Raised the moment a run stops being able to produce a real number."""


@dataclass
class _Counter:
    n: int = 0
    empty: int = 0
    unparsed: int = 0

    @property
    def dead(self) -> int:
        """Cells carrying no information about their arm, either way."""
        return self.empty + self.unparsed


@dataclass
class CellYieldGuard:
    """Abort a panel run whose cells have stopped carrying information.

    Empty and unparsed are counted SEPARATELY even though both are dead for
    scoring, because they point at different causes: an empty response is a
    transport/`think`/`num_predict` fault, an unparsed one is a prompt or
    label-extraction fault. A guard that reports only "dead cells" sends you
    to the wrong file.
    """

    arms: tuple[str, ...]
    models: tuple[str, ...] = ()

    #: Consecutive dead cells anywhere in the run. Catches a hard break
    #: mid-stream, which the prefix-only check cannot.
    max_consecutive_dead: int = 8

    #: Dead fraction inside a sliding window. Catches degradation that is bad
    #: but not total — the case that produces a biased estimate rather than an
    #: obvious one.
    window: int = 40
    max_window_dead_rate: float = 0.50

    #: Per-(model, arm) dead rate, checked once a cell has enough observations
    #: to mean anything. THE LOAD-BEARING ONE: an asymmetric failure censors
    #: one arm and manufactures a delta whose sign is set by which arm broke.
    min_cell_n: int = 20
    max_cell_dead_rate: float = 0.25

    #: End-of-run: the whole run's dead rate. A slow, even bleed can stay
    #: under every window threshold and still gut the denominator.
    max_total_dead_rate: float = 0.15

    _per: dict[tuple[str, str], _Counter] = field(default_factory=dict)
    _all: _Counter = field(default_factory=_Counter)
    _recent: deque = field(default_factory=deque)
    _run: int = 0

    def observe(self, model: str, arm: str, raw: str | None, parsed: object) -> None:
        if arm not in self.arms:
            # An arm the guard was not told about is a wiring error, not a
            # data point to fold in silently: its cells would go unchecked.
            raise CellYieldAbort(
                f"cell for undeclared arm {arm!r}; declared arms are {self.arms}. "
                f"The guard cannot check an arm it does not know about, and a "
                f"typo'd arm name would otherwise be counted nowhere."
            )
        c = self._per.setdefault((model, arm), _Counter())
        # Both verdicts route through the single predicate — this guard must never again hold
        # a private definition of empty that the scorer does not share.
        is_empty = is_absent(raw)
        is_unparsed = (not is_empty) and is_absent(parsed)

        for t in (c, self._all):
            t.n += 1
            t.empty += is_empty
            t.unparsed += is_unparsed

        dead = is_empty or is_unparsed
        self._run = self._run + 1 if dead else 0
        self._recent.append(dead)
        while len(self._recent) > self.window:
            self._recent.popleft()

        self._check_live(model, arm)

    # ── checks ───────────────────────────────────────────────────────────
    def _check_live(self, model: str, arm: str) -> None:
        if self._run >= self.max_consecutive_dead:
            raise CellYieldAbort(
                f"ABORT: {self._run} consecutive cells carried no answer "
                f"(last: {model} / {arm}). Dead cells are censored from scoring, "
                f"so a run that continues from here reports a delta over an undeclared, "
                f"potentially arm-asymmetric surviving denominator. "
                f"empty={self._all.empty} unparsed={self._all.unparsed} "
                f"of {self._all.n}. If empty dominates, check `think`/num_predict "
                f"and the response field; if unparsed dominates, check the label "
                f"extraction against the raw text."
            )

        if len(self._recent) == self.window:
            rate = sum(self._recent) / self.window
            if rate > self.max_window_dead_rate:
                raise CellYieldAbort(
                    f"ABORT: {rate:.0%} of the last {self.window} cells carried no "
                    f"answer (limit {self.max_window_dead_rate:.0%}). A partial "
                    f"failure is worse than a total one: it leaves a well-formed "
                    f"number standing on a denominator nobody declared."
                )

        c = self._per[(model, arm)]
        if c.n >= self.min_cell_n:
            rate = c.dead / c.n
            if rate > self.max_cell_dead_rate:
                sibling = self._sibling_rates(model, arm)
                raise CellYieldAbort(
                    f"ABORT: cell ({model} / {arm}) is {rate:.0%} dead over {c.n} "
                    f"observations (limit {self.max_cell_dead_rate:.0%}). "
                    f"Sibling arms on this model: {sibling}. "
                    f"An ASYMMETRIC failure is the dangerous one — it censors one "
                    f"arm and manufactures a delta whose sign is decided by which "
                    f"arm broke, while the pooled rate looks survivable."
                )

    def _sibling_rates(self, model: str, arm: str) -> dict[str, str]:
        out = {}
        for a in self.arms:
            c = self._per.get((model, a))
            out[a] = "no data" if not c or not c.n else f"{c.dead / c.n:.0%} of {c.n}"
        return out

    def finalise(self) -> dict:
        """End-of-run check. Returns the yield report when it passes."""
        if self._all.n == 0:
            raise CellYieldAbort(
                "ABORT: the run produced zero cells. An empty result set scores "
                "as vacuously consistent with every hypothesis."
            )
        rate = self._all.dead / self._all.n
        if rate > self.max_total_dead_rate:
            raise CellYieldAbort(
                f"ABORT (end of run): {rate:.0%} of {self._all.n} cells carried no "
                f"answer (limit {self.max_total_dead_rate:.0%}). No window tripped, "
                f"so this bled evenly — which is exactly the shape that survives "
                f"every local check and still empties the denominator."
            )
        missing = [
            f"{m}/{a}"
            for m in (self.models or {m for m, _ in self._per})
            for a in self.arms
            if (m, a) not in self._per
        ]
        if missing:
            raise CellYieldAbort(
                f"ABORT (end of run): no cells at all for {missing}. A (model, arm) "
                f"that never ran is absent, not zero, and absence must not be "
                f"averaged over — it silently changes which panel you measured."
            )
        return {
            "cells": self._all.n,
            "empty": self._all.empty,
            "unparsed": self._all.unparsed,
            "dead_rate": round(rate, 4),
            "per_cell": {
                f"{m}/{a}": {"n": c.n, "empty": c.empty, "unparsed": c.unparsed}
                for (m, a), c in sorted(self._per.items())
            },
        }


# ── selftest ─────────────────────────────────────────────────────────────
# Every threshold gets a mutation that MUST trip it and the guard gets a
# known-good stream that must NOT trip. Without the second arm, a guard that
# rejects everything passes the whole suite — which is the failure mode this
# file is about, one level up.

_N = 0


def _ok(cond: bool, label: str) -> None:
    global _N
    _N += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok   {label}")


def _run(guard: CellYieldGuard, cells) -> tuple[bool, str]:
    """Feed cells; return (aborted, message)."""
    try:
        for model, arm, raw, parsed in cells:
            guard.observe(model=model, arm=arm, raw=raw, parsed=parsed)
        guard.finalise()
    except CellYieldAbort as e:
        return True, str(e)
    return False, ""


def _stream(n, arms=("ainglish", "english"), model="m1", raw="STRICT",
            parsed="STRICT", per_arm=None):
    out = []
    for i in range(n):
        arm = arms[i % len(arms)]
        r, p = (per_arm or {}).get(arm, (raw, parsed))
        out.append((model, arm, r, p))
    return out


def selftest() -> int:
    print("empty_cell_guard selftest (offline, mutation-tested)")
    ARMS = ("ainglish", "english")

    # ── THE POSITIVE CONTROL. Must NOT abort. Without this, "abort always"
    # passes every other case below.
    aborted, _ = _run(CellYieldGuard(arms=ARMS, models=("m1",)), _stream(120))
    _ok(not aborted, "CONTROL: a clean 120-cell run does NOT abort")

    # A tolerable sprinkle of dead cells must also survive, or the guard is
    # unusable on any real model.
    cells = _stream(200)
    for i in range(0, 200, 25):                      # 8/200 = 4% dead, spread
        m, a, _, _ = cells[i]
        cells[i] = (m, a, "", None)
    aborted, _ = _run(CellYieldGuard(arms=ARMS, models=("m1",)), cells)
    _ok(not aborted, "CONTROL: 4% dead, evenly spread, does NOT abort")

    # ── 1. the original bug: all cells empty from the start
    aborted, msg = _run(
        CellYieldGuard(arms=ARMS, models=("m1",)),
        _stream(64, raw="", parsed=None),
    )
    _ok(aborted and "consecutive" in msg, "64 empty cells abort (the original bug)")
    _ok("think" in msg, "abort message names the `think`/num_predict hypothesis")

    # ── 2. MID-RUN break. The prefix-only guard passes this; this one must not.
    cells = _stream(60) + _stream(40, raw="", parsed=None)
    aborted, msg = _run(CellYieldGuard(arms=ARMS, models=("m1",)), cells)
    _ok(aborted and "consecutive" in msg,
        "a break at cell 61 aborts (prefix-only check would have passed)")

    # ── 3. PARTIAL failure: alternating dead, never 8 in a row.
    cells = []
    for i in range(80):
        m, a = "m1", ARMS[i % 2]
        cells.append((m, a, "", None) if i % 2 else (m, a, "STRICT", "STRICT"))
    aborted, msg = _run(CellYieldGuard(arms=ARMS, models=("m1",)), cells)
    _ok(aborted, "50% dead with no 8-in-a-row still aborts")

    # ── 4. ⭐ ASYMMETRIC failure — the load-bearing case. One arm dies, the
    # other is perfect. Pooled rate is 50%, per-arm is 100%/0%, and the sign of
    # the resulting delta is decided by which arm broke.
    cells = _stream(80, per_arm={"ainglish": ("", None),
                                 "english": ("STRICT", "STRICT")})
    aborted, msg = _run(CellYieldGuard(arms=ARMS, models=("m1",)), cells)
    _ok(aborted, "asymmetric arm failure aborts")
    _ok("ASYMMETRIC" in msg or "asymmetric" in msg.lower(),
        "abort names the asymmetry rather than a generic yield problem")
    _ok("english" in msg and "ainglish" in msg,
        "abort reports BOTH arms' rates so the reader can see which broke")

    # The same asymmetry must survive being diluted below every pooled
    # threshold: 4 models, only one arm of one model broken. Pooled dead rate
    # is 12.5% — under max_total_dead_rate — so ONLY the per-cell check can
    # see it. This is the case a pooled guard cannot catch by construction.
    cells = []
    for i in range(320):
        model = f"m{i % 4 + 1}"
        arm = ARMS[i // 4 % 2]
        broken = model == "m3" and arm == "ainglish"
        cells.append((model, arm, "", None) if broken else (model, arm, "STRICT", "STRICT"))
    pooled = sum(1 for c in cells if not c[2]) / len(cells)
    _ok(pooled < 0.15, f"setup: pooled dead rate {pooled:.1%} is under the total limit")
    aborted, msg = _run(
        CellYieldGuard(arms=ARMS, models=("m1", "m2", "m3", "m4")), cells
    )
    _ok(aborted and "m3" in msg,
        "one broken (model, arm) out of eight aborts, and is NAMED, "
        "though the pooled rate is survivable")

    # ── 5. UNPARSED is separated from EMPTY. Both are dead for scoring, but
    # they point at different files, so the message must distinguish them.
    aborted, msg = _run(
        CellYieldGuard(arms=ARMS, models=("m1",)),
        _stream(40, raw="I think probably STRICT or WEAK", parsed=None),
    )
    _ok(aborted, "cells that return text but no extractable label abort")
    # The abort fires at the 8th consecutive dead cell, so the counts are 8/0 —
    # asserting 40 here would be asserting that the guard ran too long.
    _ok("unparsed=8" in msg and "empty=0" in msg,
        "counts separate unparsed from empty (different cause, different fix)")

    # ── 6. SLOW BLEED: 20% dead, evenly spread, tripping no window.
    cells = _stream(200)
    for i in range(0, 200, 5):
        m, a, _, _ = cells[i]
        cells[i] = (m, a, "", None)
    aborted, msg = _run(CellYieldGuard(arms=ARMS, models=("m1",)), cells)
    _ok(aborted and "end of run" in msg,
        "20% even bleed passes every window and is caught at finalise()")

    # ── 7. A (model, arm) that never ran is absent, not zero.
    aborted, msg = _run(
        CellYieldGuard(arms=ARMS, models=("m1", "m2")),
        _stream(60, model="m1"),
    )
    _ok(aborted and "m2" in msg, "a model that produced no cells at all aborts")

    # ── 8. Zero cells is not a clean run.
    aborted, msg = _run(CellYieldGuard(arms=ARMS), [])
    _ok(aborted and "zero cells" in msg, "an empty run aborts rather than passing")

    # ── 9. An undeclared arm is a wiring error, not a silent extra bucket.
    aborted, msg = _run(
        CellYieldGuard(arms=ARMS),
        [("m1", "ainglisj", "STRICT", "STRICT")],       # typo'd arm
    )
    _ok(aborted and "undeclared arm" in msg, "a typo'd arm name aborts, is not counted nowhere")

    # ── 10. The report is only produced on a pass, and carries the denominator.
    g = CellYieldGuard(arms=ARMS, models=("m1",))
    for c in _stream(100):
        g.observe(model=c[0], arm=c[1], raw=c[2], parsed=c[3])
    rep = g.finalise()
    _ok(rep["cells"] == 100 and rep["dead_rate"] == 0.0, "report carries cells and dead_rate")
    _ok(set(rep["per_cell"]) == {"m1/ainglish", "m1/english"},
        "report breaks the yield down per (model, arm), never only pooled")

    print(f"\n  {_N} assertions, all green "
          f"(2 of them controls that must NOT fire)")
    return 0


def replay(path: str) -> int:
    """Run the guard over an ALREADY-COMPLETED raw.jsonl.

    Retrospective, so it cannot save the run — but it answers the question a
    guard proposal has to answer before anyone merges it: would this have
    rejected a measurement that was in fact sound? Verified against the
    register's first `comprehension_accuracy_delta` run (252 cells, 6 cells of
    42, manifest d4296fc1…): 0 dead, passes. A guard that fails the real
    published run is not a guard, it is a veto.
    """
    import json

    rows = [json.loads(l) for l in open(path) if l.strip()]
    arms = tuple(sorted({r["arm"] for r in rows}))
    models = tuple(sorted({r["model"] for r in rows}))
    print(f"{len(rows)} cells · arms={arms} · models={models}")
    g = CellYieldGuard(arms=arms, models=models)
    try:
        for r in rows:
            g.observe(model=r["model"], arm=r["arm"], raw=r.get("raw"),
                      parsed=r.get("parsed"))
        print("PASS\n" + json.dumps(g.finalise(), indent=1))
        return 0
    except CellYieldAbort as e:
        print(f"ABORT\n{e}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--replay", metavar="RAW_JSONL",
                    help="replay a finished run's raw.jsonl through the guard")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.replay:
        return replay(a.replay)
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
