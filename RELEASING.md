# RELEASING.md — how a version number becomes a release

Two invariants make version numbers expensive, and every rule in this file exists to spend them
only on proven artifacts:

1. **A pushed tag never moves.** (Ruled 2026-08-07 over v0.2.6: moving a published tag costs more
   than the gap it leaves.)
2. **PyPI never accepts the same version twice**, even after a delete.

Together they mean any defect discovered *after* `git push origin vX.Y.Z` is unfixable in place —
the number is burned and the fix takes the next one. The changelog gaps are the scar tissue:
0.2.6 and 0.2.10 are missing from PyPI, and 0.2.22 was published and superseded by 0.2.23 the
same day when the register's served-harness gate (which runs *after* tag + publish) caught a
standalone regression the SDK's packaged CI cannot see.

## The rule that prevents most burns: NO PRE-BUMPS

**Feature PRs never touch the version.** Concretely, a PR must not:

- change `version` in `pyproject.toml`,
- change `__version__` in `src/ainglish/__init__.py`,
- add a `## X.Y.Z — date` heading to `CHANGELOG.md`.

Changelog entries for merged-but-unreleased work go under a `## Unreleased` heading at the top of
`CHANGELOG.md`. The release commit — and only the release commit — renames that heading to
`## X.Y.Z — YYYY-MM-DD` and moves both version stamps, so the number is claimed at the moment the
whole chain below is about to prove it, not days earlier at merge time. A number that was never
claimed can never be burned.

(0.2.21 and 0.2.22 were both pre-bumped by their feature PRs. 0.2.21 got lucky; 0.2.22 did not.)

## Why the last gate is late, and what that implies

The register serves `measure.py`, `panel.py`, `corpus_slice.py` and `empty_cell_guard.py` as
**single standalone files** pinned to a release tag: its `.htaccess` 302s to
`raw.githubusercontent.com/ai-nglish/ainglish/vX.Y.Z/...`, and its `HarnessFixtureParityTest`
pins the tag plus each file's sha256. That sync mechanically requires the tag to exist — so the
register's own gates (including `ServedHarnessSelftestTest`, which runs the served bytes with
**no package installed**) can only run at the *end* of the chain.

The CI job `standalone` in `.github/workflows/ci.yml` is that gate moved to PR time: it copies
the four served files into a directory, asserts the `ainglish` package is NOT importable, and
runs each file's selftest. If your change makes a served file depend on the installed package,
that job — not the register, three steps after an immutable publish — is where you find out.
Served files may *use* the package when it is present, but must degrade loudly and pass their
selftests without it (see `panel.py`'s attempt-lifecycle guard for the pattern).

## Branch protection

`master` is protected (2026-08-12): merging requires a pull request with **one approving review**
and green `selftests (3.9)` / `selftests (3.12)` / `parity` / `standalone` checks. Force pushes
and deletions are blocked. `enforce_admins` is off, which is what keeps step 1 below a direct
push: release commits are made by an admin and carry nothing but the version claim over
already-merged, already-reviewed content. Everything with actual content in it goes through a PR.

## The release checklist

Run on `master`, clean tree (`git status --porcelain` empty), in this order. Stop at the first
failure — nothing before the tag push has spent anything.

1. **Release commit**: rename `## Unreleased` to `## X.Y.Z — YYYY-MM-DD`; set both stamps
   (`pyproject.toml`, `src/ainglish/__init__.py`). One commit, nothing else in it.
2. **`make test`** — selftests, live smoke, preflight. Preflight will note "declared X.Y.Z but
   not tagged yet"; that is the expected pre-tag state, not a failure.
3. **Rehearse the register gate**: run the four served files' selftests in a bare venv (no
   `ainglish` installed — note a system-wide install will mask failures; CI's `standalone` job is
   the reference environment).
4. **Push the commit, then tag it**: `git tag -a vX.Y.Z <commit> && git push origin vX.Y.Z`.
   The tag IS the release decision — `publish.yml` fires on it (never on the GitHub release,
   which is one manual step nobody remembers; that is how 0.2.10/0.2.11 sat unpublished).
5. **Watch publish.yml to success**, then create the GitHub release with notes. Before upload, the
   workflow installs the built wheel in a clean venv outside the checkout and requires its
   distribution metadata, `ainglish.__version__`, client User-Agent stamp and panel harness stamp
   all to equal the tag. CI rehearses the same artifact check on every PR.
6. **Fresh-venv wheel verification**: `pip install ainglish==X.Y.Z` in a new venv (expect up to a
   few minutes of PyPI propagation; retry, don't panic) and assert the release's actual behavior
   change, not just the version string.
7. **Register fixture sync** (ai-nglish/ainglish-symfony): byte-copy the four served files from
   the tag (`git show vX.Y.Z:src/ainglish/<f>.py`), update `TAG` + the changed sha256 constants
   in `tests/HarnessFixtureParityTest.php`, and move the `.htaccess` redirect pin — all three
   together, the parity test enforces that. `make test` there runs the served-harness gate in
   the register's own bare container.
8. **Deploy the register**, then verify on the wire: the redirect 302s to the new tag, the served
   bytes hash-match the parity constants, and `make preflight` here closes fully clear (its
   pin-staleness check reads the live redirect; give raw.githubusercontent a minute after the
   tag push before diagnosing a mismatch).

A release is **done** when step 8 is green — "tagged and on PyPI" is the middle of the chain,
not the end. If anything fails between tag and step 8, the number is spent: fix forward, take
the next number, and leave a superseded warning on the burned release's notes (the v0.2.6
precedent).
