#!/usr/bin/env python3
"""rehearse_release: run a release's behaviour assertion before the tag, and again after it.

    python3 tools/rehearse_release.py ASSERTION.py                    # RELEASING.md step 3a
    python3 tools/rehearse_release.py ASSERTION.py --candidate 0.2.69 # step 6, the published wheel

ASSERTION.py is a script that exits 0 only when the behaviour the changelog entry describes is
present. It is run twice, each time by a clean virtual environment outside the checkout:

  control    the previous release, installed from PyPI (default: the newest tag that is not the
             version pyproject.toml declares)
  candidate  the wheel built from this tree, or with --candidate a published version or a wheel

The rehearsal passes only when the control FAILS and the candidate PASSES. Both halves matter.
A candidate that fails means the wheel does not do what the note says. A control that passes
means the assertion cannot tell this release from the last one, so it shows nothing about the
change: either it tests the wrong thing or the note describes behaviour that was already there.

Why this exists. Every other rehearsal in RELEASING.md checks that the artifact is well formed:
stamps agree, served files stand alone. None of them checks that the release NOTE is true, and
after the tag the note cannot be corrected. In 0.2.68 the step 6 assertion was written from a
changelog sentence and first run against the published wheel, where it failed: the code was
right and the sentence was wrong (it named a default the code does not have). Run before the
merge, the same failure costs one edit to the release commit.

Needs the network (PyPI for the control and for the build backend). Exits non-zero unless the
rehearsal passes. Prints one JSON receipt for the release PR description.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import venv

REPO = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"\d+\.\d+\.\d+")

# Run by each venv before the assertion: which ainglish is this, and is it the installed one?
PROBE = r"""
import importlib.metadata, json, ainglish
print(json.dumps({"version": importlib.metadata.version("ainglish"), "module": ainglish.__version__,
                  "file": ainglish.__file__}))
"""


def verdict(control_rc, candidate_rc):
    """(passed, reason) from the two exit codes. The only pass is control non-zero, candidate zero."""
    if candidate_rc != 0 and control_rc != 0:
        return False, ("the assertion fails on both. The built wheel does not do what the assertion "
                       "says, or the assertion itself is broken; fix whichever is wrong before the merge.")
    if candidate_rc != 0:
        return False, ("the assertion passes on the previous release and fails on the candidate: "
                       "the release removes the behaviour it claims to add.")
    if control_rc == 0:
        return False, ("the assertion passes on the previous release too, so it does not test this "
                       "release's change. Either it checks the wrong thing or the changelog describes "
                       "behaviour that already existed.")
    return True, "the assertion fails on the previous release and passes on the candidate."


def declared_version(repo=REPO):
    match = re.search(r'^version = "(.*)"', (Path(repo) / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    if not match:
        raise SystemExit("pyproject.toml declares no version")
    return match.group(1)


def default_control(tags, declared):
    """The newest vX.Y.Z tag that is not the declared version; None when there is none."""
    versions = []
    for tag in tags:
        name = tag.strip()
        if name.startswith("v") and VERSION.fullmatch(name[1:]) and name[1:] != declared:
            versions.append(tuple(int(part) for part in name[1:].split(".")))
    return ".".join(str(part) for part in max(versions)) if versions else None


def _clean_env():
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def run_assertion(python, assertion, cwd):
    """Run one assertion file with one interpreter, isolated from the caller's paths."""
    result = subprocess.run([str(python), "-I", str(assertion)], cwd=str(cwd), env=_clean_env(),
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return result.returncode, lines[-12:]


def _make_venv(root, name):
    env_dir = Path(root) / name
    venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
    return env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _install(python, spec, extras, cwd):
    """spec is a wheel path or a bare version. Extras, when asked for, go on both sides alike."""
    wheel = str(spec).endswith(".whl")
    target = str(spec) if wheel else "ainglish==%s" % spec
    if extras:
        target = "%s[%s]" % (target, extras) if wheel else "ainglish[%s]==%s" % (extras, spec)
    command = [str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    if not extras:
        command.append("--no-deps")  # the package itself has no required dependencies
    subprocess.run(command + [target], cwd=str(cwd), env=_clean_env(), check=True)


def _probe(python, cwd, venv_root):
    out = subprocess.run([str(python), "-I", "-c", PROBE], cwd=str(cwd), env=_clean_env(), check=True,
                         text=True, stdout=subprocess.PIPE).stdout
    seen = json.loads(out)
    if not Path(seen["file"]).resolve().is_relative_to(Path(venv_root).resolve()):
        raise SystemExit("the assertion would run against %s, not the installed package" % seen["file"])
    if seen["version"] != seen["module"]:
        raise SystemExit("distribution %s and module %s disagree" % (seen["version"], seen["module"]))
    return seen["version"]


def build_wheel(repo, out_dir):
    subprocess.run([sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--disable-pip-version-check",
                    "-w", str(out_dir), str(repo)], cwd=str(out_dir), env=_clean_env(), check=True)
    wheels = sorted(Path(out_dir).glob("ainglish-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("expected exactly one built wheel, found %d" % len(wheels))
    return wheels[0]


def rehearse(assertion, control, candidate=None, extras="", repo=REPO):
    assertion = Path(assertion).resolve()
    if not assertion.is_file():
        raise SystemExit("no assertion file at %s" % assertion)
    with tempfile.TemporaryDirectory(prefix="ainglish-rehearsal-") as tmp:
        root = Path(tmp)
        script = root / "assertion.py"
        shutil.copyfile(assertion, script)  # run from outside the checkout, like everything else here
        if candidate is None:
            candidate_spec = build_wheel(repo, root)
            candidate_label = "built from this tree: %s" % candidate_spec.name
            candidate_sha = hashlib.sha256(candidate_spec.read_bytes()).hexdigest()
        elif str(candidate).endswith(".whl"):
            candidate_spec = Path(candidate).resolve()
            candidate_label = "wheel: %s" % candidate_spec.name
            candidate_sha = hashlib.sha256(candidate_spec.read_bytes()).hexdigest()
        else:
            candidate_spec, candidate_label, candidate_sha = candidate, "published: ainglish==%s" % candidate, None
        sides = {}
        for name, spec in (("control", control), ("candidate", candidate_spec)):
            python = _make_venv(root, name)
            _install(python, spec, extras, root)
            version = _probe(python, root, root / name)
            code, tail = run_assertion(python, script, root)
            sides[name] = {"version": version, "exit": code, "output_tail": tail}
    if sides["control"]["version"] == sides["candidate"]["version"]:
        raise SystemExit("control and candidate are both %s; the control must be the previous release"
                         % sides["control"]["version"])
    passed, reason = verdict(sides["control"]["exit"], sides["candidate"]["exit"])
    sides["candidate"]["source"] = candidate_label
    if candidate_sha:
        sides["candidate"]["wheel_sha256"] = candidate_sha
    sides["control"]["source"] = "published: ainglish==%s" % control if not str(control).endswith(".whl") else "wheel: %s" % Path(control).name
    return {"rehearsal": "passed" if passed else "FAILED", "reason": reason,
            "assertion_sha256": hashlib.sha256(assertion.read_bytes()).hexdigest(),
            "control": sides["control"], "candidate": sides["candidate"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rehearse a release's behaviour assertion against the previous release and the candidate.")
    parser.add_argument("assertion", help="script that exits 0 only when the release's behaviour change is present")
    parser.add_argument("--control", help="previous release: a version on PyPI or a wheel file (default: newest other tag)")
    parser.add_argument("--candidate", help="a published version or a wheel file (default: build this tree)")
    parser.add_argument("--extras", default="", help="comma-separated optional extras to install on both sides, e.g. tokens")
    args = parser.parse_args(argv)
    control = args.control
    if control is None:
        tags = subprocess.run(["git", "tag", "--list", "v*"], cwd=str(REPO), check=True, text=True, stdout=subprocess.PIPE).stdout.splitlines()
        skip = args.candidate if args.candidate and VERSION.fullmatch(args.candidate) else declared_version()
        control = default_control(tags, skip)
        if control is None:
            raise SystemExit("no earlier release tag found; name the control with --control")
    receipt = rehearse(args.assertion, control, args.candidate, args.extras)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["rehearsal"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
