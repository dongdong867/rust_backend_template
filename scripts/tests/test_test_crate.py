"""Package test selection checks; Cargo is mocked except for Make dry runs."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "test_crate", ROOT / ".github/ci/test_crate.py"
)
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)
METADATA = ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"]


class PackageTests(unittest.TestCase):
    def metadata(self, api_doc=True):
        return json.dumps({
            "workspace_members": ["member-id"],
            "packages": [
                {"id": "dependency-id", "name": "dependency", "features": {"api-doc": []}},
                {"id": "member-id", "name": "tasks",
                 "features": {"api-doc": []} if api_doc else {}},
            ],
        })

    def invoke(self, crate="tasks", features="", ci=False, api_doc=True, failure=None):
        def result(args, **kwargs):
            if args == METADATA:
                return subprocess.CompletedProcess(args, 0, self.metadata(api_doc))
            if failure:
                raise subprocess.CalledProcessError(failure, args)
            return subprocess.CompletedProcess(args, 0)

        with patch.dict(os.environ, {"CRATE": crate, "FEATURES": features}), patch.object(
            helper.subprocess, "run", side_effect=result
        ) as run, contextlib.redirect_stderr(io.StringIO()) as errors:
            status = helper.main(["--ci"] if ci else [])
        return status, run.call_args_list, errors.getvalue()

    def test_invalid_names_never_invoke_cargo(self):
        for name in ("", " tasks", "tasks ", "tasks*", "./tasks", "tasks;id",
                     "tasks@1", "-tasks", "é", "a" * 65, "tasks\n"):
            with self.subTest(name=name):
                status, calls, error = self.invoke(name)
                self.assertNotEqual(status, 0)
                self.assertEqual(calls, [])
                self.assertIn("CRATE", error)

    def test_valid_restricted_names(self):
        for name in ("tasks", "rust-backend-template", "a_b", "A1", "a" * 64):
            with self.subTest(name=name):
                helper.validate_crate(name)

    def test_exact_workspace_members_only(self):
        for name in ("dependency", "task", "migrate"):
            with self.subTest(name=name):
                status, calls, error = self.invoke(name)
                self.assertNotEqual(status, 0)
                self.assertEqual(calls, [
                    call(METADATA, check=True, capture_output=True, text=True)
                ])
                self.assertIn("workspace", error)

    def test_default_exact_calls(self):
        status, calls, _ = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(calls, [
            call(METADATA, check=True, capture_output=True, text=True),
            call(["cargo", "test", "--locked", "-p", "tasks"], check=True),
        ])

    def test_features_are_one_argument_not_shell(self):
        status, calls, _ = self.invoke(features="api-doc, other $(touch /tmp/probe)")
        self.assertEqual(status, 0)
        self.assertEqual(calls[-1], call(
            ["cargo", "test", "--locked", "-p", "tasks", "--features",
             "api-doc,other,$(touch,/tmp/probe)"], check=True,
        ))

    def test_ci_api_doc_is_package_qualified_and_ignores_features(self):
        status, calls, _ = self.invoke(ci=True, features="other")
        self.assertEqual(status, 0)
        self.assertEqual(calls[1:], [
            call(["cargo", "test", "--locked", "-p", "tasks"], check=True),
            call(["cargo", "test", "--locked", "-p", "tasks",
                  "--features", "tasks/api-doc"], check=True),
        ])

    def test_ci_without_api_doc(self):
        status, calls, _ = self.invoke(ci=True, api_doc=False)
        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 2)

    def test_failure_stops_ci_and_propagates(self):
        status, calls, _ = self.invoke(ci=True, failure=7)
        self.assertEqual(status, 7)
        self.assertEqual(len(calls), 2)

    def test_metadata_failure_is_safe(self):
        with patch.dict(os.environ, {"CRATE": "tasks"}), patch.object(
            helper.subprocess, "run",
            side_effect=subprocess.CalledProcessError(9, METADATA, stderr="secret"),
        ), contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(helper.main([]), 9)
        self.assertNotIn("secret", errors.getvalue())

    def test_second_ci_failure_propagates(self):
        with patch.dict(os.environ, {"CRATE": "tasks"}), patch.object(
            helper.subprocess, "run", side_effect=[
                subprocess.CompletedProcess(METADATA, 0, self.metadata()),
                subprocess.CompletedProcess([], 0),
                subprocess.CalledProcessError(8, []),
            ],
        ), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(helper.main(["--ci"]), 8)

    def test_target_is_unconditional_in_both_makefiles(self):
        block = (
            "test-crate: export CRATE ?=\n"
            "test-crate: export FEATURES ?=\n"
            "test-crate: ## Test one workspace package (CRATE=<exact package name>)\n"
            "\tpython3 .github/ci/test_crate.py\n"
        )
        for name in ("Makefile", "Makefile.liquid"):
            text = (ROOT / name).read_text()
            self.assertIn(block, text)
            self.assertIn("test test-crate test-db", text)
        output = subprocess.check_output(
            ["make", "-n", "test-crate", "CRATE=tasks; echo injected",
             "FEATURES=api-doc; echo injected"], cwd=ROOT, text=True,
        )
        self.assertEqual(output.strip(), "python3 .github/ci/test_crate.py")


if __name__ == "__main__":
    unittest.main()
