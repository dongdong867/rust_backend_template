import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


CANONICAL = """name: test
on: push
jobs:
  control:
    concurrency:
      group: ci-command-control-${{ needs.route.outputs.group }}
      cancel-in-progress: false
      queue: max
"""
MESSAGE = 'unexpected key "queue" for "concurrency" section. expected one of "cancel-in-progress", "group"'


class CheckCiTests(unittest.TestCase):
    def run_check(self, text=CANONICAL, errors=None, code=1, stdout=None, failure=None):
        spec = importlib.util.spec_from_file_location(
            "check_ci", Path(__file__).resolve().parents[1] / "check_ci.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workflows = root / ".github/workflows"
            workflows.mkdir(parents=True)
            (workflows / "ci-command.yml").write_text(text)
            (workflows / "ci-command-completion.yml").write_text(text)
            (workflows / "other.yml").write_text("name: other\n")
            result = subprocess.CompletedProcess([], code, stdout if stdout is not None else json.dumps(errors or []), "")
            with patch.object(module.subprocess, "run", side_effect=failure, return_value=result) as run:
                status = module.check(root)
            if failure is None:
                self.assertEqual(run.call_args.args[0], [
                    str(root / ".tools/bin/actionlint"), "-format", "{{json .}}",
                    "-shellcheck=", "-pyflakes=", ".github/workflows/ci-command-completion.yml",
                    ".github/workflows/ci-command.yml",
                    ".github/workflows/other.yml",
                ])
            return status

    def diagnostic(self, **changes):
        error = dict(message=MESSAGE, filepath=".github/workflows/ci-command.yml",
                     line=8, column=7, kind="syntax-check", snippet="      queue: max", end_column=12)
        error.update(changes)
        return error

    def test_permitted_and_native_clean(self):
        self.assertEqual(self.run_check(errors=[self.diagnostic()]), 0)
        self.assertEqual(self.run_check(errors=[
            self.diagnostic(),
            self.diagnostic(filepath=".github/workflows/ci-command-completion.yml"),
        ]), 0)
        self.assertEqual(self.run_check(code=0), 0)

    def test_other_diagnostics_and_duplicate_exceptions_fail(self):
        error = self.diagnostic()
        completion = self.diagnostic(filepath=".github/workflows/ci-command-completion.yml")
        for errors in ([error, self.diagnostic(message="other")], [error, error],
                       [error, completion, completion],
                       [error, completion, self.diagnostic(filepath=".github/workflows/other.yml")]):
            with self.subTest(errors=errors):
                self.assertEqual(self.run_check(errors=errors), 1)

    def test_wrong_diagnostic_location_or_kind_fails(self):
        for changes in (dict(filepath=".github/workflows/other.yml"), dict(line=7),
                        dict(column=8), dict(kind="other")):
            with self.subTest(changes=changes):
                self.assertEqual(self.run_check(errors=[self.diagnostic(**changes)]), 1)

    def test_noncanonical_structures_fail(self):
        for text in (
            CANONICAL.replace("  control:", "  worker:"),
            CANONICAL.replace("ci-command-control-", "other-"),
            CANONICAL.replace("queue: max", "queue: single"),
            CANONICAL.replace("false", "true"),
            CANONICAL.replace("queue: max", "'queue': max"),
            CANONICAL.replace("queue: max", 'queue: max, "queue": max'),
            CANONICAL + "      queue: max\n",
            CANONICAL + CANONICAL,
            CANONICAL.replace("    concurrency:", "    concurrency: {queue: max}"),
        ):
            with self.subTest(text=text):
                self.assertEqual(self.run_check(text=text, errors=[self.diagnostic()]), 1)
                self.assertEqual(self.run_check(text=text, errors=[
                    self.diagnostic(filepath=".github/workflows/ci-command-completion.yml")
                ]), 1)

    def test_unknown_outputs_fail(self):
        for code, output in ((2, "[]"), (1, "[]"), (0, "garbage"),
                             (1, "{}"), (1, "[{}]"), (0, "null")):
            with self.subTest(code=code, output=output):
                self.assertEqual(self.run_check(code=code, stdout=output), 1)
        self.assertEqual(self.run_check(failure=OSError("not executable")), 1)


if __name__ == "__main__":
    unittest.main()
