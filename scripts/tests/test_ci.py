"""Policy guards, not a YAML parser: actionlint validates the actual workflows."""

import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("template_test", ROOT / "scripts/template_test.py")
template_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(template_test)


def jobs(text):
    """Split a workflow's top-level jobs by their two-space keys."""
    body = text.split("\njobs:\n", 1)[1]
    names = re.findall(r"^  ([\w-]+):$", body, re.M)
    parts = re.split(r"^  [\w-]+:$", body, flags=re.M)[1:]
    return dict(zip(names, parts))


class CiPolicyTest(unittest.TestCase):
    def workflow(self, name):
        return (ROOT / ".github/workflows" / name).read_text()

    def test_formatting_is_one_unconditional_pr_job(self):
        text = self.workflow("format.yml")
        self.assertIn("types: [opened, synchronize, reopened]", text)
        self.assertNotIn("push:", text)
        self.assertNotIn("if:", text)
        self.assertEqual(text.count("runs-on:"), 1)
        self.assertIn("make fmt-check fmt-toml-check", text)
        for command in ("make test", "make clippy", "template-test", "DATABASE_URL"):
            self.assertNotIn(command, text)

    def test_source_validation_covers_review_and_main(self):
        text = self.workflow("template-ci.yml")
        self.assertIn("branches: [main]", text)
        self.assertIn(
            "types: [opened, synchronize, reopened, ready_for_review, converted_to_draft]", text
        )
        self.assertIn("github.event_name == 'push' || !github.event.pull_request.draft", text)
        for command in ("make ci-config-check", "make clippy", "make test FEATURES=",
                        "make test FEATURES=api-doc", "make template-test-fast"):
            self.assertIn(command, text)
        for command in ("make build", "cargo check", "make test-db", "DATABASE_URL"):
            self.assertNotIn(command, text)

    def test_permissions_pins_concurrency_and_timeouts(self):
        for name in ("format.yml", "template-ci.yml"):
            with self.subTest(name=name):
                text = self.workflow(name)
                self.assertIn("contents: read", text)
                self.assertIn("persist-credentials: false", text)
                self.assertIn("cancel-in-progress: true", text)
                self.assertIn("github.workflow", text)
                self.assertIn("github.event.pull_request.number || github.ref", text)
                self.assertRegex(text, r"timeout-minutes: \d+")
                self.assertNotIn("pull_request_target", text)
                self.assertNotIn("secrets.", text)
                self.assertNotIn("continue-on-error", text)
                for action in re.findall(r"uses: ([^\s]+)", text):
                    self.assertRegex(action, r"^[\w./-]+@[a-f0-9]{40}$")
        text = self.workflow("format.yml")
        self.assertIn("taplo@${{ steps.tools.outputs.taplo }}", text)
        self.assertIn("fallback: none", text)

    def test_generator_cache_keeps_cargo_install_metadata(self):
        text = self.workflow("template-ci.yml")
        self.assertIn(".tools/bin", text)
        # Without these, cargo install refuses to overwrite the restored generator binary.
        self.assertIn(".tools/.crates.toml", text)
        self.assertIn(".tools/.crates2.json", text)

    def test_command_controller_never_checks_out_or_executes_pr_code(self):
        text = self.workflow("ci-command.yml")
        self.assertIn("issue_comment:\n    types: [created]", text)
        self.assertNotIn("workflow_run:", text)
        self.assertNotIn("pull-requests: write", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("queue: max", text)
        self.assertIn("if: needs.route.outputs.enabled == 'true'", text)
        self.assertIn("statuses: write", text)
        self.assertIn("actions: write", text)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertNotIn("inputs.sha", text)
        self.assertNotIn("make ", text)
        self.assertNotIn("download-artifact", text)
        self.assertNotIn("persist-credentials: true", text)
        self.assertIn("ci-command-control-", text)

    def test_actions_only_controller_keeps_its_trusted_acceptance_lock(self):
        workflow = self.workflow("ci-command.yml")
        self.assertEqual(workflow.count(
            "    concurrency:\n"
            "      group: ci-command-control-${{ needs.route.outputs.group }}\n"
            "      cancel-in-progress: false\n"
            "      queue: max\n"), 1)
        self.assertEqual(workflow.count("ref: ${{ github.sha }}"), 2)
        self.assertIn("comment_ci.py route", workflow)
        self.assertIn("comment_ci.py control", workflow)
        for forbidden in ("secrets.", "download-artifact", "inputs.sha", "make ",
                          "workflow_run:", "pull-requests: write"):
            self.assertNotIn(forbidden, workflow)

    def test_command_worker_has_non_cancelling_admission_and_no_write_permissions(self):
        text = self.workflow("ci-command-worker.yml")
        sections = jobs(text)
        self.assertEqual(list(sections), ["validate", "execute", "report"])
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("name: Validate accepted request", text)
        self.assertIn("name: Run PR command", text)
        self.assertIn("needs: validate", text)
        self.assertNotIn(": write", text.split("\njobs:\n", 1)[0])
        for name in ("validate", "execute"):
            with self.subTest(job=name):
                self.assertNotIn("concurrency:", sections[name])
                self.assertNotIn("cancel-in-progress:", sections[name])
                self.assertNotIn(": write", sections[name])
        self.assertIn("contents: read", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("actions/cache", text)
        self.assertNotIn("upload-artifact", text)
        self.assertIn("ref: ${{ inputs.sha }}", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("worker-check", text)
        self.assertEqual(text.count("comment_ci.py worker-check"), 2)
        self.assertIn("comment_ci.py execute", text)
        # Expressions belong in step environment, not shell text.
        self.assertNotRegex(text, r"run:.*\$\{\{")

    def test_command_report_job_writes_statuses_without_pr_code(self):
        sections = jobs(self.workflow("ci-command-worker.yml"))
        validate, report = sections["validate"], sections["report"]
        self.assertIn("group: ${{ steps.lock.outputs.group }}", validate)
        self.assertIn("comment_ci.py lock", validate)
        self.assertLess(validate.index("comment_ci.py lock"), validate.index("comment_ci.py worker-check"))
        self.assertIn("needs: [validate, execute]", report)
        self.assertIn("if: always()", report)
        self.assertEqual(re.findall(r"^      ([\w-]+): (read|write)$", report, re.M), [
            ("contents", "read"), ("actions", "read"), ("pull-requests", "read"), ("statuses", "write"),
        ])
        # Result writes share the controller's request lock.
        self.assertEqual(report.count(
            "    concurrency:\n"
            "      group: ci-command-control-${{ needs.validate.outputs.group }}\n"
            "      cancel-in-progress: false\n"
            "      queue: max\n"), 1)
        self.assertIn("ref: ${{ github.sha }}", report)
        self.assertIn("persist-credentials: false", report)
        self.assertIn("comment_ci.py report", report)
        self.assertIn("LOCK_GROUP: ${{ needs.validate.outputs.group }}", report)
        self.assertIn("VALIDATE_RESULT: ${{ needs.validate.result }}", report)
        self.assertIn("EXECUTE_RESULT: ${{ needs.execute.result }}", report)
        # Timeout detection compares job duration with the execute job's limit.
        spec = importlib.util.spec_from_file_location("comment_ci", ROOT / ".github/ci/comment_ci.py")
        comment_ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(comment_ci)
        self.assertIn(f"timeout-minutes: {comment_ci.EXECUTE_TIMEOUT_MINUTES}\n", sections["execute"])
        for forbidden in ("inputs.sha", "path: pr", "make ", "comment_ci.py execute",
                          "needs.execute.outputs", "download-artifact", "actions/cache",
                          "secrets.", "pull-requests: write"):
            self.assertNotIn(forbidden, report)

    def test_only_source_workflow_is_excluded_from_generation(self):
        excluded = (ROOT / ".genignore").read_text().splitlines()
        self.assertIn("/.github/workflows/template-ci.yml", excluded)
        self.assertNotIn("/.github/workflows/format.yml", excluded)
        self.assertNotIn("/.github/dependabot.yml", excluded)
        config = (ROOT / "cargo-generate.toml").read_text()
        self.assertNotIn('".github/', config)

    def test_weekly_ecosystem_groups(self):
        # This deliberately fixes the small, plain config shape; it does not parse arbitrary YAML.
        text = (ROOT / ".github/dependabot.yml").read_text()
        self.assertIn("version: 2", text)
        for ecosystem in ("cargo", "github-actions"):
            self.assertIn(f"package-ecosystem: {ecosystem}", text)
        self.assertEqual(text.count("directory: /"), 2)
        self.assertEqual(text.count("interval: weekly"), 2)
        self.assertEqual(text.count("groups:"), 2)
        self.assertEqual(text.count('patterns: ["*"]'), 2)
        self.assertNotIn("docker", text)

    def test_fork_approval_guidance_distinguishes_private_repositories(self):
        for name in ("README.md", "README.md.liquid"):
            text = (ROOT / name).read_text()
            self.assertIn("For public repositories,", text)
            self.assertIn("For private repositories", text)
            self.assertIn("without write permission", text)

    def test_guidance_describes_head_statuses_without_bot_comments(self):
        for name in ("README.md", "README.md.liquid", "docs/ci-commands.md"):
            with self.subTest(name=name):
                text = (ROOT / name).read_text()
                self.assertIn("`ci-command /test`", text)
                self.assertNotIn("bot result comment is published", text.replace("\n", " "))
                self.assertNotIn("Actions-only", text)
                self.assertNotIn("Completed runs receive one updated bot result comment", text)
                self.assertIn("Historical `ci-comment` statuses", text)

    def test_guidance_names_the_command_cache_write_risk(self):
        # The worker runs on the default branch, so PR code can write its cache scope.
        for name in ("README.md", "README.md.liquid", "docs/ci-commands.md", "docs/architecture.md"):
            with self.subTest(name=name):
                text = (ROOT / name).read_text().replace("\n", " ")
                self.assertNotIn("shared writable cache", text)
                self.assertIn("default-branch cache entries", text)
        text = (ROOT / "docs/ci-commands.md").read_text().replace("\n", " ")
        self.assertIn("`ci-command /test` is not a required check", text)

    def test_tool_versions_come_from_make(self):
        result = subprocess.check_output(
            ["make", "--no-print-directory", "tool-versions"], cwd=ROOT, text=True
        )
        versions = dict(line.split("=", 1) for line in result.splitlines())
        self.assertEqual(versions, {
            "taplo": "0.10.0", "cargo-generate": "0.25.0", "actionlint": "1.7.12",
        })

    def test_fast_template_target_has_no_database_checks(self):
        text = subprocess.check_output(
            ["make", "-n", "template-test-fast"], cwd=ROOT, text=True
        )
        self.assertIn("python3 scripts/template_test.py --fast-only", text)
        self.assertNotIn("test-db", text)


class GeneratedCiTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source"
        self.project = Path(self.temporary.name) / "project"
        self.files = (
            ".github/workflows/format.yml", ".github/dependabot.yml",
            ".github/workflows/ci-command.yml", ".github/workflows/ci-command-worker.yml",
            ".github/ci/comment_ci.py", ".github/ci/test_crate.py",
        )
        for name in self.files:
            for root in (self.source, self.project):
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text("literal ${{ github.sha }}\n")
        (self.project / "Makefile").write_text("test:\n\tcargo test --locked\ntest-crate:\n\tpython3 helper\n")

    def check(self):
        template_test.check_ci_files(self.project, self.source)

    def test_plain_configs_pass(self):
        self.check()

    def test_actual_shared_command_support_exists(self):
        for name in (
            ".github/workflows/ci-command.yml", ".github/workflows/ci-command-worker.yml",
            ".github/ci/comment_ci.py", ".github/ci/test_crate.py",
        ):
            self.assertTrue((ROOT / name).is_file(), f"Missing shared command support: {name}")
        for name in (
            ".github/workflows/ci-command-completion.yml", ".github/ci/command_report.py",
            ".github/ci/github_log.py",
        ):
            self.assertFalse((ROOT / name).exists(), f"Deferred reporting support remains: {name}")

    def test_missing_or_changed_config_is_rejected(self):
        for name in self.files:
            with self.subTest(name=name):
                file = self.project / name
                file.write_text("damaged expression\n")
                with self.assertRaisesRegex(RuntimeError, "CI configuration changed"):
                    self.check()
                file.unlink()
                with self.assertRaisesRegex(RuntimeError, "CI configuration missing"):
                    self.check()
                file.write_bytes((self.source / name).read_bytes())

    def test_source_workflow_is_rejected(self):
        (self.project / ".github/workflows/template-ci.yml").write_text("source-only\n")
        with self.assertRaisesRegex(RuntimeError, "Source-only workflow leaked"):
            self.check()

    def test_source_targets_are_rejected(self):
        for target in ("template-test", "template-test-fast", "template-tools",
                       "ci-tools", "ci-config-check"):
            with self.subTest(target=target):
                (self.project / "Makefile").write_text(f"{target}:\n\tfalse\n")
                with self.assertRaisesRegex(RuntimeError, "Source-only Make target leaked"):
                    self.check()


if __name__ == "__main__":
    unittest.main()
