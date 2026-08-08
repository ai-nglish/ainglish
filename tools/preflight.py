#!/usr/bin/env python3
"""preflight — the checks that would have caught what a green test suite did not.

Every assertion here exists because of a dated incident, and each incident shares one shape: a
signal that was true of my working copy and false of the artefact I was pointing at. A test suite
cannot see any of them, because none of them is about the code.

    python3 tools/preflight.py            # all checks, network included
    python3 tools/preflight.py --offline  # skip the two that need the network

Exits non-zero on any failure, so it can gate a release.

A note on the shape, since it is the reason this file exists rather than a habit:

  2026-08-08  tests/ServedSurfaceTest.php was untracked through two commits. `git commit -a`
              stages modifications, not new files. phpunit discovers by directory, so the local
              suite ran the guard and went green while the repo did not contain it — CI would
              never have seen it and a fresh clone would have had the shipped fields with nothing
              pinning them. Green told me nothing.
  2026-08-07  v0.2.6 was tagged at the code commit, one before the version bump. The publish
              workflow checked out that tag, built ainglish-0.2.5-py3-none-any.whl, and PyPI
              correctly refused it as a duplicate. Re-running failed identically. Anyone
              installing from @v0.2.6 gets a package reporting __version__ 0.2.5, which stamps
              the wrong harness version into a measurement receipt.
  2026-08-07  I told a collaborator twice that PyPI was lagging. It was not: publishing is
              automated on release via OIDC, and the second claim went out fifteen seconds after
              the run finished. I had checked the served file and never the index — and I had
              documented that pipeline myself three days earlier.
  2026-08-08  A memory edit failed on a marker mismatch and the attest command in the same shell
              call ran anyway and reported success, anchoring a digest for a state that did not
              contain the change. Not checked here because it is not about this repo, but it is
              the same family and the reason multi-step shell calls need `set -euo pipefail`:
              a verify step that runs after its mutate step failed will confirm the old state.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import hashlib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The register no longer serves copies of these: those paths 302 to a pinned tag in THIS repo
# (see the RewriteRule in ainglish-symfony public/.htaccess). So the check is no longer
# "does a mirror match" but "does the redirect resolve to the bytes of the tag it names".
MIRRORED = ("measure.py", "panel.py", "corpus_slice.py", "empty_cell_guard.py")
REGISTER = "https://ainglish.org"
PKG = "ainglish"

# Tags known to violate the version check, with the ruling that left them alone. NEVER a silent
# skip: each one is printed as a note on every run, because an exception nobody sees is how a gate
# stops being a gate. Adding an entry here is a decision that has to be defended in the string.
KNOWN_BAD_TAGS = {
    "v0.2.6": "tagged at the pre-bump commit; tree declares 0.2.5. Ruled 2026-08-07 NOT to move it "
              "— 0.2.6 is superseded twice, its content is wholly inside 0.2.7, and moving a "
              "published tag to fix a superseded version costs more than the gap it closes. The "
              "release notes carry a do-not-install warning instead.",
}

failures: list[str] = []
notes: list[str] = []


def check(label: str, ok: bool, detail: str = "", info: str = "") -> None:
    """`detail` describes the FAILURE and is printed only when failing.

    Printing it beside `ok` produced lines like "ok ... have diverged" on the first run of this
    script — a success line carrying failure text, which is precisely the contradictory signal
    the rest of this file exists to catch. Anything worth showing on success goes in `info`.
    """
    suffix = f" — {info}" if (ok and info) else (f" — {detail}" if not ok and detail else "")
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}{suffix}")
    if not ok:
        failures.append(f"{label}: {detail}")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True).stdout.strip()


def declared_version() -> str:
    m = re.search(r'^version = "(.*)"', (ROOT / "pyproject.toml").read_text(), re.M)
    if m is None:
        raise SystemExit("pyproject.toml has no version line — preflight cannot proceed")
    return m.group(1)


def version_in_tree(ref: str) -> str | None:
    """The version pyproject.toml declares AT a given ref, not in the working copy."""
    out = subprocess.run(["git", "-C", str(ROOT), "show", f"{ref}:pyproject.toml"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        return None
    m = re.search(r'^version = "(.*)"', out.stdout, re.M)
    return m.group(1) if m else None


def get_json(url: str, timeout: int = 30):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def check_untracked() -> None:
    """A source or test file that git does not have is a file CI cannot run."""
    porcelain = git("status", "--porcelain")
    stray = [ln[3:] for ln in porcelain.splitlines()
             if ln.startswith("??") and ln[3:].startswith(("src/", "tests/", "tools/"))
             and not ln[3:].endswith((".pyc",)) and "__pycache__" not in ln[3:]]
    check("no untracked files under src/ tests/ tools/", not stray,
          f"{len(stray)} untracked: {', '.join(stray[:4])}" if stray else "")


def check_tags_declare_their_own_version() -> None:
    """A vX.Y.Z tag must point at a tree whose pyproject declares X.Y.Z.

    This is the whole v0.2.6 incident: the tag named a version its own tree did not.
    """
    tags = [t for t in git("tag", "--list", "v*").splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not tags:
        notes.append("no version tags yet — nothing to check")
        return
    bad, excused = [], []
    for t in tags:
        tree = version_in_tree(t)
        if tree == t[1:]:
            continue
        (excused if t in KNOWN_BAD_TAGS else bad).append(f"{t} -> tree declares {tree}")
    for t in sorted(KNOWN_BAD_TAGS):
        if t in git("tag", "--list", "v*").splitlines():
            notes.append(f"{t} is a KNOWN exception, still counted and still visible: {KNOWN_BAD_TAGS[t]}")
    check(f"every version tag's tree declares its own version ({len(tags)} tags, "
          f"{len(excused)} known exception(s))", not bad, "; ".join(bad))


def check_head_tag_matches_declared() -> None:
    """If HEAD carries a version tag, it must be the version the working tree declares."""
    dv = declared_version()
    at_head = [t for t in git("tag", "--points-at", "HEAD").splitlines()
               if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not at_head:
        notes.append(f"HEAD carries no version tag (declared {dv}) — tag it at THIS commit when releasing")
        return
    bad = [t for t in at_head if t[1:] != dv]
    check(f"the version tag on HEAD matches the declared version ({dv})", not bad, "; ".join(bad))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report the redirect instead of following it — the status and target ARE the assertion."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_mirror_parity(offline: bool) -> None:
    """Every harness URL the register advertises must 302 to a pinned tag carrying those bytes.

    This replaced a check hardcoded to panel.py, which left measure.py, corpus_slice.py and
    empty_cell_guard.py mirrored but unguarded. It also no longer compares against the working
    tree: the register pins a TAG, so comparing to HEAD would go red on every commit after a
    release. The honest question is whether the advertised URL resolves to the tag it names.
    """
    if offline:
        notes.append("--offline: harness redirects not checked")
        return
    for name in MIRRORED:
        url = f"{REGISTER}/{name}"
        try:
            req = urllib.request.Request(url, method="HEAD")
            opener = urllib.request.build_opener(_NoRedirect())
            resp = opener.open(req, timeout=40)
            code, loc = resp.status, resp.headers.get("Location", "")
        except urllib.error.HTTPError as e:
            code, loc = e.code, e.headers.get("Location", "")
        except (urllib.error.URLError, TimeoutError) as e:
            notes.append(f"{url} unreachable ({e}) — redirect not checked")
            continue
        # A 200 means a stale copy is being served and shadowing the rule: the precise failure
        # this design removes, so it is loud rather than quietly "in sync".
        if code != 302:
            check(f"{name} redirects to a pinned tag", False,
                  f"expected 302, got {code} — is a stale file still in the web root?")
            continue
        m = re.match(r"https://raw\.githubusercontent\.com/ai-nglish/ainglish/(v[^/]+)/src/"
                     + re.escape(PKG) + r"/" + re.escape(name) + r"$", loc)
        if not m:
            check(f"{name} redirects to a pinned tag", False, f"target not tag-pinned: {loc}")
            continue
        tag = m.group(1)
        try:
            served = urllib.request.urlopen(loc, timeout=40).read()
        except (urllib.error.URLError, TimeoutError) as e:
            check(f"{name} redirect target fetches", False, f"{loc} ({e})")
            continue
        try:
            tagged = subprocess.run(["git", "show", f"{tag}:src/{PKG}/{name}"], cwd=ROOT,
                                    capture_output=True, check=True).stdout
        except subprocess.CalledProcessError:
            check(f"{name} tag {tag} carries the file", False, f"{tag}:src/{PKG}/{name} missing")
            continue
        check(f"{name} -> {tag} serves the tagged bytes", served == tagged,
              detail=f"served {hashlib.sha256(served).hexdigest()[:12]}… vs "
                     f"{tag} {hashlib.sha256(tagged).hexdigest()[:12]}…",
              info=f"{tag} {hashlib.sha256(served).hexdigest()[:12]}…")


def check_index(offline: bool) -> None:
    """PyPI must actually have the declared version — the claim I got wrong twice."""
    if offline:
        notes.append("--offline: PyPI not checked")
        return
    dv = declared_version()
    try:
        data = get_json(f"https://pypi.org/pypi/{PKG}/json")
    except (urllib.error.URLError, TimeoutError) as e:
        notes.append(f"PyPI unreachable ({e}) — index not checked, do NOT claim it is current")
        return
    releases = set(data["releases"])
    tagged = declared_version() in {t[1:] for t in git("tag", "--list", "v*").splitlines()}
    if not tagged:
        notes.append(f"{dv} is not tagged yet, so PyPI is not expected to have it (latest {data['info']['version']})")
        return
    check(f"PyPI has the declared version {dv}", dv in releases,
          detail=f"latest on PyPI is {data['info']['version']}; releases {sorted(releases)[-4:]}",
          info=f"latest {data['info']['version']}")


def main(argv: list[str]) -> int:
    offline = "--offline" in argv
    print(f"preflight — {PKG} {declared_version()} at {git('rev-parse', '--short', 'HEAD')}\n")
    check_untracked()
    check_tags_declare_their_own_version()
    check_head_tag_matches_declared()
    check_mirror_parity(offline)
    check_index(offline)

    if notes:
        print("\nnotes (not failures):")
        for n in notes:
            print(f"  - {n}")
    if failures:
        print(f"\n{len(failures)} FAILED — a green test suite does not see any of these.")
        return 1
    print("\npreflight clear.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
