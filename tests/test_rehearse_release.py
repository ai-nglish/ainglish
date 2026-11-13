"""Offline checks of tools/rehearse_release.py: the decision table, the default control, and the
isolated assertion runner. The network half (PyPI, wheel build) is exercised by running the tool."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "rehearse_release", Path(__file__).resolve().parent.parent / "tools" / "rehearse_release.py")
rehearse_release = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rehearse_release)


class VerdictTest(unittest.TestCase):
    def test_only_control_fails_and_candidate_passes_is_a_pass(self):
        table = {(1, 0): True, (2, 0): True, (0, 0): False, (0, 1): False, (1, 1): False}
        for (control, candidate), expected in table.items():
            passed, reason = rehearse_release.verdict(control, candidate)
            self.assertIs(passed, expected, (control, candidate, reason))
            self.assertTrue(reason)

    def test_each_failure_names_its_own_cause(self):
        self.assertIn("does not test this release's change", rehearse_release.verdict(0, 0)[1])
        self.assertIn("removes the behaviour", rehearse_release.verdict(0, 1)[1])
        self.assertIn("fails on both", rehearse_release.verdict(1, 1)[1])


class DefaultControlTest(unittest.TestCase):
    def test_newest_other_tag_by_number_not_by_text(self):
        tags = ["v0.2.9", "v0.2.67", "v0.2.68", "v0.2.10", "not-a-release", "v1.0.0rc1", "v0.2.68-notes"]
        self.assertEqual(rehearse_release.default_control(tags, "0.2.68"), "0.2.67")
        self.assertEqual(rehearse_release.default_control(tags, "0.2.69"), "0.2.68")
        self.assertEqual(rehearse_release.default_control(["v0.2.9", "v0.2.10"], "0.3.0"), "0.2.10")

    def test_a_later_tag_is_never_the_control(self):
        # Found in review: with a newer release tagged, a rerun of step 6 for an older one took the
        # newer release as its "previous" one.
        tags = ["v0.2.67", "v0.2.68", "v0.2.69"]
        self.assertEqual(rehearse_release.default_control(tags, "0.2.68"), "0.2.67")
        self.assertEqual(rehearse_release.default_control(tags, "0.2.69"), "0.2.68")
        self.assertEqual(rehearse_release.default_control(tags, "0.2.70"), "0.2.69")
        self.assertIsNone(rehearse_release.default_control(tags, "0.2.67"))
        self.assertIsNone(rehearse_release.default_control(["v0.2.68", "v0.2.69"], "0.2.68"))
        # A pre-release of .69 is still controlled by .68, and a version with no numbers by nothing.
        self.assertEqual(rehearse_release.default_control(tags, "0.2.69rc1"), "0.2.68")
        self.assertIsNone(rehearse_release.default_control(tags, "unversioned"))

    def test_no_earlier_tag_is_none_not_a_guess(self):
        self.assertIsNone(rehearse_release.default_control(["v0.2.68"], "0.2.68"))
        self.assertIsNone(rehearse_release.default_control([], "0.1.0"))

    def test_declared_version_reads_this_checkout(self):
        self.assertRegex(rehearse_release.declared_version(), r"^\d+\.\d+\.\d+$")


class RunAssertionTest(unittest.TestCase):
    def _run(self, body, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "assertion.py"
            script.write_text(body, encoding="utf-8")
            old = {k: os.environ.get(k) for k in (env or {})}
            os.environ.update(env or {})
            try:
                return rehearse_release.run_assertion(sys.executable, script, tmp)
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_a_file_beside_the_assertion_cannot_shadow_the_installed_package(self):
        # Isolated mode keeps the script's own directory off the path, so an `ainglish.py` left
        # in the working directory can never be what the assertion imports.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sibling_rehearsal_module.py").write_text("VALUE = 1\n", encoding="utf-8")
            script = Path(tmp) / "assertion.py"
            script.write_text("import sibling_rehearsal_module\n", encoding="utf-8")
            code, tail = rehearse_release.run_assertion(sys.executable, script, tmp)
        self.assertEqual(code, 1)
        self.assertIn("ModuleNotFoundError", tail[-1])

    def test_exit_codes_and_the_last_lines_are_returned(self):
        self.assertEqual(self._run("print('present')\n"), (0, ["present"]))
        code, tail = self._run("assert 1 == 2, 'absent'\n")
        self.assertEqual(code, 1)
        self.assertIn("AssertionError: absent", tail[-1])
        code, tail = self._run("import sys\nsys.exit('no such behaviour')\n")
        self.assertEqual((code, tail), (1, ["no such behaviour"]))

    def test_the_callers_python_path_cannot_reach_the_assertion(self):
        with tempfile.TemporaryDirectory() as planted:
            (Path(planted) / "planted_rehearsal_module.py").write_text("VALUE = 1\n", encoding="utf-8")
            code, tail = self._run("import planted_rehearsal_module\n", env={"PYTHONPATH": planted})
        self.assertEqual(code, 1)
        self.assertIn("ModuleNotFoundError", tail[-1])


class ReceiptTest(unittest.TestCase):
    """rehearse() with the network half replaced: no venv is built, nothing is installed. What is
    under test is which bytes the receipt names and which release may stand as the control."""

    def _rehearse(self, body, runner, versions=("0.2.67", "0.2.68"), candidate="0.2.68"):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "assertion.py"
            source.write_text(body, encoding="utf-8")
            seen = {"control": versions[0], "candidate": versions[1]}
            with mock.patch.object(rehearse_release, "_make_venv", lambda root, name: Path(root) / name / "python"), \
                 mock.patch.object(rehearse_release, "_install", lambda *a, **k: None), \
                 mock.patch.object(rehearse_release, "_probe", lambda python, cwd, venv_root: seen[Path(venv_root).name]), \
                 mock.patch.object(rehearse_release, "run_assertion", lambda python, script, cwd: runner(Path(python).parent.name, Path(script), source)):
                return rehearse_release.rehearse(source, versions[0], candidate), source.read_bytes() if source.exists() else None

    def test_the_receipt_names_the_bytes_that_ran_and_an_unchanged_source_passes(self):
        ran = []

        def runner(side, script, source):
            ran.append(script.read_bytes())
            return (1 if side == "control" else 0), [side]

        receipt, _ = self._rehearse("assert True\n", runner)
        self.assertEqual(ran, [b"assert True\n", b"assert True\n"])
        self.assertEqual(receipt["assertion_sha256"], hashlib.sha256(b"assert True\n").hexdigest())
        self.assertEqual((receipt["rehearsal"], receipt["assertion_source"]), ("passed", "unchanged since it was read"))

    def test_a_source_edited_during_the_run_fails_and_is_never_what_the_receipt_names(self):
        # The reviewer's reproducer: the copy that runs says `assert True`; while it runs, the
        # file it was copied from is rewritten to `assert False`.
        ran = []

        def runner(side, script, source):
            ran.append(script.read_bytes())
            source.write_text("assert False\n", encoding="utf-8")
            return (1 if side == "control" else 0), [side]

        receipt, on_disk = self._rehearse("assert True\n", runner)
        self.assertEqual(on_disk, b"assert False\n")
        self.assertEqual(ran, [b"assert True\n", b"assert True\n"])            # the edit never reached what ran
        self.assertEqual(receipt["assertion_sha256"], hashlib.sha256(b"assert True\n").hexdigest())
        self.assertNotEqual(receipt["assertion_sha256"], hashlib.sha256(on_disk).hexdigest())
        self.assertEqual((receipt["rehearsal"], receipt["assertion_source"]), ("FAILED", "CHANGED during the run"))
        self.assertIn("changed while the rehearsal ran", receipt["reason"])

    def test_a_source_deleted_during_the_run_fails_too(self):
        def runner(side, script, source):
            if source.exists():
                source.unlink()
            return (1 if side == "control" else 0), [side]

        receipt, on_disk = self._rehearse("assert True\n", runner)
        self.assertIsNone(on_disk)
        self.assertEqual((receipt["rehearsal"], receipt["assertion_source"]), ("FAILED", "CHANGED during the run"))

    def test_a_copy_tampered_with_between_the_two_runs_stops_the_rehearsal(self):
        def runner(side, script, source):
            script.write_text("assert False\n", encoding="utf-8")
            return 1, [side]

        with self.assertRaises(SystemExit) as stopped:
            self._rehearse("assert True\n", runner)
        self.assertIn("copy of the assertion changed", str(stopped.exception))

    def test_a_control_that_is_not_an_earlier_release_is_refused(self):
        runner = lambda side, script, source: ((1 if side == "control" else 0), [side])
        for control, candidate in (("0.2.69", "0.2.68"), ("0.2.68", "0.2.68"), ("0.3.0", "0.2.99"), ("nightly", "0.2.68")):
            with self.assertRaises(SystemExit, msg=(control, candidate)) as stopped:
                self._rehearse("assert True\n", runner, versions=(control, candidate), candidate=candidate)
            self.assertIn("not an earlier release", str(stopped.exception))
        receipt, _ = self._rehearse("assert True\n", runner, versions=("0.2.9", "0.2.10"), candidate="0.2.10")
        self.assertEqual(receipt["rehearsal"], "passed")                       # compared as numbers: 9 is below 10


if __name__ == "__main__":
    unittest.main()
