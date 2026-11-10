"""Offline checks of tools/rehearse_release.py: the decision table, the default control, and the
isolated assertion runner. The network half (PyPI, wheel build) is exercised by running the tool."""
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
