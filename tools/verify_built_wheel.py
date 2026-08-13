#!/usr/bin/env python3
"""Install one built wheel in a clean venv and prove every shipped version stamp agrees."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


PROBE = r"""
import importlib.metadata
import json
import ainglish
from ainglish import client, corpus_slice, measure, panel

print(json.dumps({
    "distribution metadata": importlib.metadata.version("ainglish"),
    "ainglish.__version__": ainglish.__version__,
    "client User-Agent stamp": client.USER_AGENT.removeprefix("ainglish-python/"),
    "corpus User-Agent stamp": corpus_slice.USER_AGENT.removeprefix("ainglish-python/"),
    "measure User-Agent stamp": measure.USER_AGENT.removeprefix("ainglish-python/"),
    "panel harness stamp": panel.HARNESS_VERSION,
    "panel User-Agent stamp": panel.USER_AGENT.removeprefix("ainglish-python/"),
}, sort_keys=True))
"""


def verify(expected, wheel):
    wheel = Path(wheel).resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit("expected exactly one built .whl file, got %s" % wheel)
    with tempfile.TemporaryDirectory(prefix="ainglish-wheel-") as tmp:
        root = Path(tmp)
        env_dir = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        clean_env = os.environ.copy()
        clean_env.pop("PYTHONPATH", None)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)],
            cwd=root,
            env=clean_env,
            check=True,
        )
        result = subprocess.run(
            [str(python), "-c", PROBE],
            cwd=root,
            env=clean_env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
    stamps = json.loads(result.stdout)
    wrong = {name: value for name, value in stamps.items() if value != expected}
    if wrong:
        detail = ", ".join("%s=%s" % item for item in sorted(wrong.items()))
        raise SystemExit("built wheel does not consistently carry %s: %s" % (expected, detail))
    print("built wheel verified: %s carries %s in %s" % (wheel.name, expected, ", ".join(sorted(stamps))))


def main(argv):
    if len(argv) != 3:
        raise SystemExit("usage: verify_built_wheel.py EXPECTED_VERSION dist/ainglish-*.whl")
    verify(argv[1], argv[2])


if __name__ == "__main__":
    main(sys.argv)
