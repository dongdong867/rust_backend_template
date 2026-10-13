import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest


SPEC = importlib.util.spec_from_file_location(
    "command_report", Path(__file__).resolve().parents[2] / ".github/ci/command_report.py"
)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)


class CommandReportTests(unittest.TestCase):
    def capture(self, text, code=0, mode="default"):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            run = report.capture_make(
                [sys.executable, "-c", f"import sys; sys.stdout.write({text!r}); sys.exit({code})"],
                mode,
            )
        self.assertEqual(output.getvalue(), text)
        return run

    def summary(self, runs, command="test", stage=None):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            report.emit_summary(command, runs, stage)
        return output.getvalue()

    def test_execution_totals(self):
        run = self.capture("test result: ok. 3 passed; 0 failed; 2 ignored; 0 measured\n"
                           "Ran 4 tests in 0.1s\n\nOK (skipped=1)\n")
        self.assertEqual((run["passed"], run["failed"], run["ignored"]), (6, 0, 3))
        api = self.capture("test result: ok. 2 passed; 0 failed; 0 ignored\n", mode="api-doc")
        parsed = report.parse_summary(self.summary([run, api]), "test")
        self.assertEqual([r["passed"] for r in parsed["runs"]], [6, 2])

    def test_failed_and_missing(self):
        run = self.capture("test safe::name ... FAILED\nprivate assertion @someone\n"
                           "test result: FAILED. 1 passed; 1 failed; 0 ignored\n", 1)
        self.assertEqual(run["failing_tests"], ["safe::name"])
        self.assertEqual(run["status"], "failed")
        self.assertNotIn("private", self.summary([run], stage="default tests"))
        missing = self.capture("compiler failed\n", 2)
        self.assertEqual([missing[k] for k in ("passed", "failed", "ignored")], [None]*3)
        oversized = self.capture("x"*17000 + "\ntest result: ok. 10000001 passed; 0 failed; 0 ignored\n")
        self.assertIsNone(oversized["passed"])

    def test_python_failure(self):
        run = self.capture("FAIL: test_one (suite.Tests.test_one)\nRan 5 tests in 1s\n"
                           "FAILED (failures=1, errors=1, skipped=1)\n", 1)
        self.assertEqual((run["passed"], run["failed"], run["ignored"]), (2, 2, 1))
        self.assertEqual(run["failing_tests"], ["suite.Tests.test_one"])

    def test_parser_strict(self):
        run = self.capture("test result: ok. 1 passed; 0 failed; 0 ignored\n")
        text = self.summary([run])
        self.assertIsNotNone(report.parse_summary("2026-01-01T00:00:00Z " + text, "test"))
        self.assertIsNone(report.parse_summary(text, "lint"))
        self.assertIsNone(report.parse_summary(text + report.MARKER + "{}\n", "test"))
        self.assertIsNone(report.parse_summary("x"*(report.MAX_LOG_BYTES+1), "test"))
        self.assertIsNone(report.parse_summary(report.MARKER + "x"*17000, "test"))
        data = json.loads(text[len(report.MARKER):])
        mutations = [
            ("passed", True), ("failed", -1), ("ignored", 10000001),
            ("exit_code", 1), ("failing_tests", ["@user"]),
            ("failing_tests", ["../../file"]), ("extra", "raw"),
        ]
        for key, value in mutations:
            changed = json.loads(json.dumps(data))
            changed["runs"][0][key] = value
            self.assertIsNone(report.parse_summary(report.MARKER + json.dumps(changed), "test"))
        self.assertIsNone(report.parse_summary(text.replace('"version":1', '"version":1,"version":1'), "test"))
        data["runs"] *= 2
        self.assertIsNone(report.parse_summary(report.MARKER + json.dumps(data), "test"))

    def test_untrusted_shapes(self):
        run = self.capture("test result: ok. 1 passed; 0 failed; 0 ignored\n")
        data = json.loads(self.summary([run])[len(report.MARKER):])
        for key, value in (
            ("version", True), ("failed_stage", "@someone"), ("runs", {}),
            ("extra", "<script>"), ("command", "test-../../file"),
        ):
            changed = {**data, key: value}
            self.assertIsNone(report.parse_summary(report.MARKER + json.dumps(changed), "test"))
        for key, value in (
            ("passed", None), ("failed", float("nan")), ("mode", []),
            ("status", {}), ("exit_code", True), ("failing_tests", ["https://evil"]),
            ("failing_tests", ["test\nname"]), ("failing_tests", ["<html>"]),
        ):
            changed = json.loads(json.dumps(data))
            changed["runs"][0][key] = value
            self.assertIsNone(report.parse_summary(report.MARKER + json.dumps(changed), "test"))
        for text in ("", "raw error", self.summary([run]) + report.MARKER + "{} junk"):
            self.assertIsNone(report.parse_summary(text, "test"))

    def test_streaming_safe_names_and_incomplete_python(self):
        text = "".join(f"test suite::test_{i} ... FAILED\n" for i in range(30))
        text += "test @mention ... FAILED\ntest https://evil ... FAILED\n"
        text += "Ran 5 tests in 1s\n"
        run = self.capture(text, 1)
        self.assertEqual(len(run["failing_tests"]), 20)
        self.assertIsNone(run["passed"])
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stderr):
            combined = report.capture_make(
                [sys.executable, "-c", "import sys; sys.stderr.write('diagnostic\\n')"],
                "lint",
            )
        self.assertEqual(stderr.getvalue(), "diagnostic\n")
        self.assertIsNone(combined["passed"])
        self.assertIsNotNone(report.parse_summary(self.summary([combined], "lint"), "lint"))

    def test_render(self):
        request = {"command": "test", "sha": "a"*40}
        url = "https://github.com/owner/repo/actions/runs/123"
        failed = self.capture("test result: FAILED. 1 passed; 1 failed; 2 ignored\n", 1)
        summary = report.parse_summary(self.summary([failed], stage="default tests"), "test")
        text = report.render_comment(request, "failure", url, 65, summary)
        for expected in ("[Failed]", "Not run", "Not collected", "default tests", "1m 5s", "executions"):
            self.assertIn(expected, text)
        for reason in ("Cancelled", "Timed out", "@raw <html>"):
            text = report.render_comment(request, "error", url, reason=reason)
            self.assertNotIn("@raw", text)
            self.assertIn("Unavailable", text)
            self.assertIn(reason if reason in ("Cancelled", "Timed out") else "Worker failed", text)
        successful = self.capture("test result: ok. 3 passed; 0 failed; 0 ignored\n")
        passed = report.render_comment(
            request, "success", url, summary=report.parse_summary(
                self.summary([successful]), "test"
            ),
        )
        self.assertIn("### [Passed] `/test`", passed)
        self.assertIn("| default | Passed | 3 | 0 | 0 |", passed)
        self.assertNotIn("Not run", passed)
        partial = report.render_comment({**request, "command": "test-users"}, "success", url)
        self.assertIn("[Passed]", partial)
        self.assertIn("does not satisfy", partial)
        self.assertTrue(partial.isascii())
        for bad in ("http://github.com/o/r/actions/runs/1", "https://evil.com/o/r/actions/runs/1",
                    url + "?x=@user"):
            with self.assertRaises(ValueError):
                report.render_comment(request, "success", bad)
        with self.assertRaises(ValueError):
            report.render_comment({**request, "command": "@evil"}, "success", url)


if __name__ == "__main__":
    unittest.main()
