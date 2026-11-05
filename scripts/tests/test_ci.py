"""Policy guards, not a YAML parser: actionlint validates the actual workflows."""

import http.server
import importlib.util
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[2]
# The scripts import their shared request helper from their own folder.
sys.path.insert(0, str(ROOT / ".github/ci"))
SPEC = importlib.util.spec_from_file_location("template_test", ROOT / "scripts/template_test.py")
template_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(template_test)


def blocks(text, heading):
    """Split one top-level YAML block, such as `jobs:`, by its two-space keys."""
    body = text.split(f"\n{heading}:\n", 1)[1]
    # The block ends at the next top-level key.
    body = re.split(r"^\S", body, maxsplit=1, flags=re.M)[0]
    names = re.findall(r"^  ([\w-]+):$", body, re.M)
    parts = re.split(r"^  [\w-]+:$", body, flags=re.M)[1:]
    return dict(zip(names, parts))


def jobs(text):
    """Split a workflow's top-level jobs by their two-space keys."""
    return blocks(text, "jobs")


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
        for command in ("make ci-config-check", "make clippy", "make test FEATURES=\n",
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

    def test_template_build_keeps_its_cache_small(self):
        validate = jobs(self.workflow("template-ci.yml"))["validate"]
        settings = validate[:validate.index("    steps:\n")]
        # Incremental data and full debug info made the restored cache too big for some runners.
        self.assertIn("    env:\n", settings)
        env = settings[settings.index("    env:\n"):]
        self.assertIn('      CARGO_INCREMENTAL: "0"\n', env)
        self.assertIn("      CARGO_PROFILE_DEV_DEBUG: line-tables-only\n", env)

    def test_cache_key_version_skips_entries_built_before_the_small_cache(self):
        validate = jobs(self.workflow("template-ci.yml"))["validate"]
        self.assertEqual(re.findall(r"key: (.*)", validate), [
            "template-v2-${{ runner.os }}-${{ runner.arch }}-${{ hashFiles('rust-toolchain.toml', "
            "'Cargo.lock', 'Makefile', 'scripts/install_actionlint.py') }}"
        ])
        # A prefix match would restore the old, larger entries again.
        self.assertNotIn("restore-keys", validate)

    def test_free_disk_notices_surround_cache_restore(self):
        validate = jobs(self.workflow("template-ci.yml"))["validate"]
        steps = re.split(r"\n(?=      - )", validate)
        restore = next(i for i, step in enumerate(steps) if "uses: actions/cache@" in step)
        # A runner that fills its disk during the restore keeps only the notice before it.
        notices = {"before": steps[restore - 1], "after": steps[restore + 1]}
        self.assertEqual(sum("::notice" in step for step in steps), 2)
        self.assertNotIn("disk-report", validate)
        for stage, step in notices.items():
            with self.subTest(stage=stage):
                self.assertTrue(step.startswith(f"      - name: Report free disk {stage} cache restore\n"))
                script = re.search(r"\n        run: \|\n((?:          .*\n?)+)$", step).group(1)
                script = "".join(line[10:] for line in script.splitlines(keepends=True))
                with tempfile.TemporaryDirectory() as workspace:
                    result = self.run_step(script, workspace)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertRegex(
                    result.stdout,
                    rf"^::notice title=Disk {stage} restore::\S+ free on the workspace disk\n$",
                )
                missing = self.run_step(script, "/nonexistent-workspace")
                self.assertNotEqual(missing.returncode, 0)
                self.assertNotIn("::notice", missing.stdout)

    def run_step(self, script, workspace):
        # GitHub runs a step's script with `bash -e` when the step names no shell.
        return subprocess.run(["bash", "-e", "-c", script], capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "GITHUB_WORKSPACE": workspace})

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

    def test_release_runs_only_by_hand_and_writes_only_after_verification(self):
        text = self.workflow("release.yml")
        self.assertEqual(text.split("\non:\n", 1)[1].split("\n")[0], "  workflow_dispatch:")
        self.assertNotRegex(text, r"(?m)^  (push|pull_request|schedule|workflow_run|release):")
        self.assertIn("      dry_run:\n", text)
        self.assertRegex(text, r"dry_run:\n(        .*\n)*?        default: true\n")
        self.assertIn("permissions: {}", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("continue-on-error", text)
        self.assertNotIn("actions/cache", text)
        for action in re.findall(r"^\s+(?:- )?uses: ([^\s]+)", text, re.M):
            self.assertRegex(action, r"^[\w./-]+@[a-f0-9]{40}$")
        # Inputs reach scripts only through env, never shell text.
        for line in re.findall(r"^\s+run: .*$", text, re.M):
            self.assertNotIn("${{", line)
        sections = jobs(text)
        self.assertEqual(sorted(sections), ["publish", "verify"])
        verify, publish = sections["verify"], sections["publish"]
        self.assertEqual(re.findall(r"^      ([\w-]+): (read|write)$", verify, re.M), [
            ("contents", "read"), ("actions", "read"), ("checks", "read"), ("statuses", "read"),
        ])
        self.assertEqual(re.findall(r"^      ([\w-]+): (read|write)$", publish, re.M), [
            ("contents", "write"),
        ])
        self.assertIn("needs: verify", publish)
        self.assertIn("if: ${{ !inputs.dry_run }}", publish)
        self.assertIn("release.py publish", publish)
        self.assertNotIn("make ", publish)
        # A release is always the commit the run started on, so no input can choose another.
        self.assertNotIn("inputs.commit", text)
        self.assertNotIn("      commit:\n", text)
        for section in (verify, publish):
            self.assertEqual(section.count("actions/checkout@"), 1)
            self.assertIn("ref: ${{ github.sha }}", section)
        self.assertLess(verify.index("release.py verify"), verify.index("make lint"))
        for target in ("make lint", "make test FEATURES=\n", "make test FEATURES=api-doc"):
            self.assertIn(target, verify)
        for section in (verify, publish):
            self.assertIn("persist-credentials: false", section)
            self.assertRegex(section, r"timeout-minutes: \d+")

    def test_release_runs_real_database_tests_on_its_own_postgres(self):
        sections = jobs(self.workflow("release.yml"))
        verify, publish = sections["verify"], sections["publish"]
        self.assertRegex(verify, r"(?m)^    services:\n      postgres:\n"
                                 r"        image: postgres:18\.6@sha256:[a-f0-9]{64}$")
        # The temporary server accepts connections without a password, so only this runner reaches it.
        self.assertIn("- 127.0.0.1:5432:5432\n", verify)
        self.assertIn("--health-cmd", verify)
        self.assertEqual(verify.count("TEST_DATABASE_URL: postgres://postgres@127.0.0.1:5432/postgres\n"), 2)
        last_fast_test = verify.index("make test FEATURES=api-doc")
        for target in ("make test-db FEATURES=\n", "make test-db FEATURES=api-doc"):
            self.assertLess(last_fast_test, verify.index(target))
        for forbidden in ("services:", "test-db", "TEST_DATABASE_URL"):
            self.assertNotIn(forbidden, publish)

    def test_release_builds_the_image_after_the_database_tests(self):
        sections = jobs(self.workflow("release.yml"))
        verify, publish = sections["verify"], sections["publish"]
        step = verify.split("- name: Build the production image\n", 1)[1].split("\n      - name:", 1)[0]
        # Only a build: no container runs, so the step needs no database.
        self.assertEqual(step.strip(), "run: make image FEATURES=")
        for target in ("make test-db FEATURES=\n", "make test-db FEATURES=api-doc"):
            self.assertLess(verify.index(target), verify.index("make image"))
        for forbidden in ("make image", "docker", "compose"):
            self.assertNotIn(forbidden, publish)

    def test_only_the_release_workflow_builds_images(self):
        names = {path.name for path in (ROOT / ".github/workflows").glob("*.yml")}
        for name in names - {"release.yml"}:
            text = self.workflow(name)
            for forbidden in ("docker", "compose", "make image"):
                self.assertNotIn(forbidden, text, name)
        release = self.workflow("release.yml")
        for forbidden in ("docker", "compose"):
            self.assertNotIn(forbidden, release)
        hook = (ROOT / ".githooks/pre-commit").read_text()
        recipe = subprocess.run(
            ["make", "--no-print-directory", "-n", "test"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        for text in (hook, recipe):
            for forbidden in ("docker", "compose"):
                self.assertNotIn(forbidden, text)

    def test_only_the_release_workflow_starts_postgres(self):
        names = {path.name for path in (ROOT / ".github/workflows").glob("*.yml")}
        self.assertIn("release.yml", names)
        for name in names - {"release.yml"}:
            text = self.workflow(name)
            for forbidden in ("services:", "postgres", "test-db", "TEST_DATABASE_URL"):
                self.assertNotIn(forbidden, text, name)
        hook = (ROOT / ".githooks/pre-commit").read_text()
        for forbidden in ("postgres", "test-db", "TEST_DATABASE_URL"):
            self.assertNotIn(forbidden, hook)

    def test_fast_tests_do_not_run_database_tests(self):
        recipe = subprocess.run(
            ["make", "--no-print-directory", "-n", "test"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout
        self.assertIn("cargo test", recipe)
        # "ignored" covers both cargo flags: --ignored and --include-ignored.
        for forbidden in ("ignored", "test-db", "TEST_DATABASE_URL"):
            self.assertNotIn(forbidden, recipe)

    def test_agents_guide_says_to_extend_the_database_test_target(self):
        for name in ("AGENTS.md", "AGENTS.md.liquid"):
            self.assertIn("add its test binary to the `test-db` target", (ROOT / name).read_text(), name)

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
        # Docker covers the Dockerfile's pinned base images.
        for ecosystem in ("cargo", "github-actions", "docker"):
            self.assertIn(f"package-ecosystem: {ecosystem}", text)
        self.assertEqual(text.count("directory: /"), 3)
        self.assertEqual(text.count("interval: weekly"), 3)
        self.assertEqual(text.count("groups:"), 3)
        self.assertEqual(text.count('patterns: ["*"]'), 3)
        # rust-toolchain.toml decides the build's Rust, so the image tag moves only with it.
        docker = text.split("package-ecosystem: docker\n", 1)[1]
        self.assertIn("    ignore:\n      - dependency-name: rust\n", docker)

    def test_fork_approval_guidance_distinguishes_private_repositories(self):
        for name in ("README.md", "README.md.liquid"):
            text = (ROOT / name).read_text()
            self.assertIn("For public repositories,", text)
            self.assertIn("For private repositories", text)
            self.assertIn("without write permission", text)

    def test_release_guidance_says_fork_runs_do_not_count(self):
        for name in ("README.md", "README.md.liquid"):
            with self.subTest(name=name):
                text = (ROOT / name).read_text()
                self.assertIn("A run from a fork pull request does not count, nor do its checks", text)
                self.assertIn("can leave it waiting for approval", text)

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
                self.assertNotIn("writable token", text)
                self.assertNotIn("later CI runs restore", text)
                self.assertIn("default-branch cache entries", text)
                if name != "docs/architecture.md":
                    self.assertIn("read the pr code before posting a command", text.lower())
        self.assertNotIn("shared caches", (ROOT / ".github/workflows/ci-command-worker.yml").read_text())
        text = (ROOT / "docs/ci-commands.md").read_text().replace("\n", " ")
        self.assertIn("`ci-command /test` is not a required check", text)
        self.assertIn("GitHub matches required checks by name", text)
        self.assertIn("also reports as GitHub Actions", text)

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


class ContainerPolicyTest(unittest.TestCase):
    """Source files only: the template twin check proves each .liquid twin renders to them."""

    def test_no_local_settings_enter_the_build_context(self):
        lines = [line for line in (ROOT / ".dockerignore").read_text().splitlines()
                 if line and not line.startswith("#")]
        self.assertEqual(lines[0], "*")
        # The last matching line wins, so the exclusion must follow every re-admitted folder.
        last_admitted = max(index for index, line in enumerate(lines) if line.startswith("!"))
        # Every local settings name .gitignore keeps out of Git stays out of the image too.
        # Its section runs from the `# Local settings.` heading line to the next blank line.
        ignored = (ROOT / ".gitignore").read_text().split("# Local settings.", 1)[1]
        patterns = [line for line in ignored.split("\n\n", 1)[0].splitlines()[1:]
                    if line and not line.startswith(("!", "#"))]
        self.assertTrue(patterns)
        for pattern in patterns:
            self.assertIn(f"**/{pattern}", lines, pattern)
            self.assertGreater(lines.index(f"**/{pattern}"), last_admitted, pattern)

    def test_the_image_build_refuses_an_out_of_date_lock_file(self):
        build = re.search(r"^RUN cargo build .*$", (ROOT / "Dockerfile").read_text(), re.M)
        self.assertIn(" --locked ", build.group(0))

    def test_the_image_builds_with_the_pinned_toolchain(self):
        channel = re.search(r'^channel = "([^"]+)"$',
                            (ROOT / "rust-toolchain.toml").read_text(), re.M).group(1)
        tag = re.search(r"^FROM rust:(\d+\.\d+\.\d+)-", (ROOT / "Dockerfile").read_text(), re.M)
        self.assertEqual(tag.group(1), channel)

    def test_compose_serves_only_this_machine_and_outlasts_shutdown(self):
        service = blocks((ROOT / "compose.yaml").read_text(), "services")["service"]
        self.assertIn("      - 127.0.0.1:${PORT:-8080}:${PORT:-8080}\n", service)
        timeout = re.search(r"^pub const SHUTDOWN_TIMEOUT_SECS: u64 = (\d+);$",
                            (ROOT / "rust-backend-template/src/server.rs").read_text(), re.M)
        grace = re.search(r"^    stop_grace_period: (\d+)s$", service, re.M)
        # The pool closes after the server's shutdown timeout, so stopping needs more time.
        self.assertGreater(int(grace.group(1)), int(timeout.group(1)))

    def test_one_port_sets_the_listener_and_the_published_port(self):
        service = blocks((ROOT / "compose.yaml").read_text(), "services")["service"]
        # One interpolated PORT sets the listener too, so a shell PORT cannot split the two.
        self.assertIn("    environment:\n      PORT: ${PORT:-8080}\n", service)

    def test_a_deployed_service_restarts_and_keeps_its_logs_bounded(self):
        services = blocks((ROOT / "compose.yaml").read_text(), "services")
        self.assertIn("    restart: unless-stopped\n", services["service"])
        self.assertIn('    logging:\n      driver: json-file\n      options:\n'
                      '        max-size: 10m\n        max-file: "3"\n', services["service"])
        # migrate runs once and is removed, so it neither restarts nor needs rotation.
        for key in ("restart:", "logging:"):
            self.assertNotIn(key, services["migrate"])

    def test_the_readme_deploys_a_release_and_rolls_it_back(self):
        for name in ("README.md", "README.md.liquid"):
            text = (ROOT / name).read_text()
            deploy = text.split("\n## Deploy it\n", 1)[1].split("\n## ", 1)[0]
            release, rollback = deploy.split("\n### Roll back\n", 1)
            # Migrations run before the new release starts serving.
            self.assertLess(release.index("make compose-migrate"),
                            release.index("make compose-up"), name)
            # An older migrate refuses a newer schema, so rollback never runs it, in any form.
            self.assertIn("make compose-up", rollback, name)
            self.assertNotIn("compose-migrate", rollback.split("```", 2)[1], name)
            self.assertIn("Migrations are never undone", rollback, name)
            # nginx is checked and reloaded only after either Cloudflare mode has its files.
            nginx = release.split("\n### Put nginx in front\n", 1)[1]
            for setup in ("Cloudflare Origin Certificate", "ingress:"):
                self.assertLess(nginx.index(setup), nginx.index("nginx -t"), (name, setup))

    def test_compose_targets_stop_without_a_settings_file(self):
        makefile = (ROOT / "Makefile").read_text()
        for target in ("compose-up", "compose-migrate"):
            self.assertRegex(makefile, rf"(?m)^{target}: compose-settings image ##")
        # The settings file fills ${PORT} unless the shell sets it.
        settings = '--env-file "$${SERVICE_ENV_FILE:-.env}"'
        self.assertIn(f"\tdocker compose {settings} up --detach --no-build --wait\n", makefile)
        self.assertIn(f"\tdocker compose {settings} run --rm migrate\n", makefile)
        with tempfile.TemporaryDirectory() as directory:
            settings = Path(directory) / ".env.container"
            missing = self.compose_settings(settings)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn(f"{settings} is missing", missing.stderr)
            settings.write_text("DATABASE_URL=postgres://synthetic@host.docker.internal/db\n")
            self.assertEqual(self.compose_settings(settings).returncode, 0)

    def compose_settings(self, settings):
        return subprocess.run(
            ["make", "--no-print-directory", "compose-settings"],
            cwd=ROOT, capture_output=True, text=True,
            env={"PATH": os.environ["PATH"], "SERVICE_ENV_FILE": str(settings)},
        )


class ComposeHealthTest(unittest.TestCase):
    """`make compose-up` ends with compose-health, because a restarting container passes `--wait`."""

    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory)
        # A fake docker records what the target asks for, so no test needs Docker.
        bin_dir = self.directory / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text('#!/bin/sh\necho "$@" >> "$DOCKER_CALLS"\necho "service log line"\n')
        (bin_dir / "docker").chmod(0o755)
        self.path = f"{bin_dir}:{os.environ['PATH']}"
        self.calls = self.directory / "docker-calls"
        self.settings = self.directory / "service.env"

    def serve_health(self):
        class Health(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == "/health" else 404)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Health)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return server.server_address[1]

    def closed_port(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def compose_health(self, shell_port=None):
        env = {"PATH": self.path, "SERVICE_ENV_FILE": str(self.settings),
               "DOCKER_CALLS": str(self.calls), "COMPOSE_HEALTH_SECONDS": "2"}
        if shell_port is not None:
            env["PORT"] = str(shell_port)
        return subprocess.run(["make", "--no-print-directory", "compose-health"],
                              cwd=ROOT, capture_output=True, text=True, env=env)

    def test_compose_up_ends_with_the_health_check(self):
        recipe = subprocess.run(["make", "--no-print-directory", "-n", "compose-up"],
                                cwd=ROOT, capture_output=True, text=True,
                                env={"PATH": self.path, "SERVICE_ENV_FILE": str(self.settings)})
        # `make -n` still runs the recursive make, so its health check recipe follows `up`.
        lines = recipe.stdout.splitlines()
        up = next(i for i, line in enumerate(lines) if "up --detach --no-build --wait" in line)
        health = next(i for i, line in enumerate(lines) if '"http://127.0.0.1:$port/health"' in line)
        self.assertLess(up, health)

    def test_a_service_answering_health_passes(self):
        self.settings.write_text(f'PORT="{self.serve_health()}"\nDATABASE_URL=postgres://x@h/db\n')
        result = self.compose_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.calls.exists())

    def test_a_shell_port_wins_over_the_settings_file(self):
        self.settings.write_text(f"PORT={self.closed_port()}\n")
        result = self.compose_health(shell_port=self.serve_health())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_service_that_never_answers_fails_with_its_log(self):
        port = self.closed_port()
        self.settings.write_text(f"PORT={port}\n")
        result = self.compose_health()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"GET /health on 127.0.0.1:{port} did not return 200 within 2 s", result.stderr)
        self.assertIn("service log line", result.stderr)
        self.assertIn("logs --no-color --tail 50 service", self.calls.read_text())


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
            ".github/workflows/release.yml", ".github/ci/release.py", ".github/ci/github_client.py",
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
            ".github/workflows/release.yml", ".github/ci/release.py", ".github/ci/github_client.py",
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
