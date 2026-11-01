"""Exercise release verification and publication against a fake GitHub API."""

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[2]
# The scripts import their shared request helper from their own folder.
sys.path.insert(0, str(ROOT / ".github/ci"))
SPEC = importlib.util.spec_from_file_location("release", ROOT / ".github/ci/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

REPO = "owner/service"
MAIN = "a" * 40
OLDER = "b" * 40
RUN = "900"


def metadata(*packages):
    """Cargo metadata with (name, version, kinds) workspace packages."""
    values = [
        {"id": f"path+file:///w/{name}#{version}", "name": name, "version": version,
         "targets": [{"kind": [kind]} for kind in kinds]}
        for name, version, kinds in packages
    ]
    return {"packages": values, "workspace_members": [p["id"] for p in values]}


SERVICE = metadata(("my-service", "1.2.3", ("lib", "bin", "test")),
                   ("environment", "0.1.0", ("lib",)), ("tasks", "0.1.0", ("lib", "test")))


def fixture():
    data = {
        "main": MAIN,
        "tags": {}, "check_runs": {}, "statuses": {}, "suite": 7, "calls": [],
        "workflow_runs": {},
        "release_tag_sha": None,
    }

    def api(method, path, body=None):
        data["calls"].append((method, path, copy.deepcopy(body)))
        prefix = f"repos/{REPO}"
        if method == "GET" and path == f"{prefix}/commits/main":
            return {"sha": data["main"]}
        if method == "GET" and path.startswith(f"{prefix}/git/ref/tags/"):
            tag = path.rsplit("/", 1)[1]
            sha = data["tags"].get(tag)
            return None if sha is None else {"object": {"sha": sha, "type": "commit"}}
        if method == "GET" and path == f"{prefix}/actions/runs/{RUN}":
            return {"id": int(RUN), "check_suite_id": data["suite"]}
        if method == "GET" and path.startswith(f"{prefix}/actions/runs?"):
            sha = path.split("head_sha=")[1].split("&")[0]
            runs = data["workflow_runs"].get(sha, [])
            page = int(path.rsplit("page=", 1)[1])
            return {"total_count": len(runs), "workflow_runs": runs[(page - 1) * 100:page * 100]}
        if method == "GET" and "/check-runs?" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            runs = data["check_runs"].get(sha, [])
            page = int(path.rsplit("page=", 1)[1])
            return {"total_count": len(runs), "check_runs": copy.deepcopy(runs[(page - 1) * 100:page * 100])}
        if method == "GET" and "/status?" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            statuses = data["statuses"].get(sha, [])
            page = int(path.rsplit("page=", 1)[1])
            return {"statuses": copy.deepcopy(statuses[(page - 1) * 100:page * 100])}
        if method == "POST" and path == f"{prefix}/releases":
            data["tags"][body["tag_name"]] = data["release_tag_sha"] or body["target_commitish"]
            return {"id": 1, "tag_name": body["tag_name"]}
        raise AssertionError(f"Unexpected API call: {method} {path}")

    return data, api


def check(name, status="completed", conclusion="success", suite=1):
    return {"name": name, "status": status, "conclusion": conclusion, "check_suite": {"id": suite}}


def workflow_run(suite, event, status="completed", conclusion="success", name="Workflow"):
    return {"check_suite_id": suite, "event": event, "status": status, "conclusion": conclusion,
            "name": name, "head_sha": MAIN}


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.data, self.api = fixture()

    def verify(self, version="1.2.3", sha=MAIN, packages=SERVICE, ref="refs/heads/main"):
        release.verify(self.api, REPO, version, sha, RUN, ref, lambda _: packages)

    def assert_rejected(self, pattern, **kwargs):
        with self.assertRaisesRegex(RuntimeError, pattern):
            self.verify(**kwargs)
        self.assertFalse([c for c in self.data["calls"] if c[0] != "GET"])

    def test_the_current_main_head_is_accepted(self):
        self.verify()

    def test_metadata_is_read_from_the_release_commit(self):
        seen = []
        release.verify(self.api, REPO, "1.2.3", MAIN, RUN, "refs/heads/main",
                       lambda sha: seen.append(sha) or SERVICE)
        self.assertEqual(seen, [MAIN])

    def test_version_mismatch_fails_before_any_write(self):
        self.assert_rejected("does not match the service package version", version="9.9.9")

    def test_internal_crate_versions_are_ignored(self):
        packages = metadata(("my-service", "2.0.0", ("bin",)), ("environment", "0.1.0", ("lib",)))
        self.verify("2.0.0", packages=packages)

    def test_invalid_versions_are_rejected(self):
        for version in ("1.2", "v1.2.3", "01.2.3", "1.2.3-rc.1", "1.2.3 ", "", "1.2.3\n"):
            with self.subTest(version=version):
                self.assert_rejected("Version must be", version=version)

    def test_invalid_commit_is_rejected(self):
        for sha in ("main", "A" * 40, "a" * 39, "a" * 40 + "\n"):
            with self.subTest(sha=sha):
                self.assert_rejected("Commit must be", sha=sha)

    def test_a_commit_main_has_moved_past_is_rejected(self):
        # GITHUB_TOKEN cannot release a commit that differs from main in workflow files.
        self.assert_rejected("main has moved", sha=OLDER)

    def test_only_the_main_branch_can_release(self):
        self.assert_rejected("Run the release workflow from main", ref="refs/heads/feature")

    def test_existing_tag_is_rejected(self):
        self.data["tags"]["v1.2.3"] = OLDER
        self.assert_rejected("Tag v1.2.3 already exists")

    def test_service_package_must_be_the_single_binary_package(self):
        for packages in (metadata(("environment", "1.2.3", ("lib",))),
                         metadata(("one", "1.2.3", ("bin",)), ("two", "1.2.3", ("bin",)))):
            with self.subTest(packages=[p["name"] for p in packages["packages"]]):
                self.assert_rejected("exactly one workspace package with a binary", packages=packages)

    def test_failed_or_unfinished_checks_are_rejected(self):
        for run in (check("Source and generated variants", conclusion="failure"),
                    check("Source and generated variants", conclusion="timed_out"),
                    check("Source and generated variants", status="in_progress", conclusion=None),
                    check("Source and generated variants", status="queued", conclusion=None)):
            with self.subTest(run=run):
                self.data["check_runs"][MAIN] = [check("E2E"), run]
                self.assert_rejected("Source and generated variants")

    def test_passing_neutral_skipped_and_cancelled_checks_are_accepted(self):
        # Any cancelled check run passes; the release run repeats only lint, fast tests and database tests.
        self.data["check_runs"][MAIN] = [
            check("a"), check("b", conclusion="neutral"), check("c", conclusion="skipped"),
            check("d", conclusion="cancelled"),
        ]
        self.verify()

    def test_this_runs_own_checks_are_ignored(self):
        self.data["check_runs"][MAIN] = [
            check("verify", status="in_progress", conclusion=None, suite=self.data["suite"]),
        ]
        self.verify()

    def runs_on_main(self, *suites):
        self.data["workflow_runs"][MAIN] = [workflow_run(suite, event) for suite, event in suites]

    def test_earlier_release_runs_checks_are_ignored(self):
        self.runs_on_main((5, "workflow_dispatch"))
        self.data["check_runs"][MAIN] = [check("Verify release", conclusion="failure", suite=5)]
        self.verify()

    def test_failed_pr_commands_do_not_block_a_commit_that_passed_validation(self):
        # The shape of real commit 05e99b4: the worker reports on the main head, whatever it tests.
        self.runs_on_main((1, "push"), (5, "workflow_dispatch"), (6, "workflow_dispatch"), (8, "workflow_dispatch"))
        self.data["check_runs"][MAIN] = [
            check("Source and generated variants", suite=1),
            check("Validate accepted request", conclusion="failure", suite=5),
            check("Run PR command", conclusion="failure", suite=6),
            check("Run PR command", conclusion="failure", suite=8),
        ]
        self.verify()

    def test_runs_that_only_report_on_the_main_head_are_ignored(self):
        for event in ("workflow_dispatch", "issue_comment", "dynamic", "schedule"):
            for run in (check("unrelated", conclusion="failure", suite=5),
                        check("unrelated", status="in_progress", conclusion=None, suite=5)):
                with self.subTest(event=event, status=run["status"]):
                    self.runs_on_main((5, event))
                    self.data["check_runs"][MAIN] = [run]
                    self.verify()

    def test_runs_on_later_pages_are_read(self):
        self.runs_on_main(*[(n, "push") for n in range(100, 200)], (5, "issue_comment"))
        self.data["check_runs"][MAIN] = [check("unrelated", conclusion="failure", suite=5)]
        self.verify()

    def test_push_and_pull_request_runs_still_count(self):
        for event in ("push", "pull_request"):
            for run in (check("Source and generated variants", conclusion="failure", suite=5),
                        check("Source and generated variants", status="in_progress", conclusion=None, suite=5)):
                with self.subTest(event=event, status=run["status"]):
                    self.runs_on_main((5, event))
                    self.data["check_runs"][MAIN] = [run]
                    self.assert_rejected("Source and generated variants")

    def test_checks_from_other_apps_still_count(self):
        self.runs_on_main((5, "issue_comment"))
        self.data["check_runs"][MAIN] = [check("External scan", conclusion="failure", suite=6)]
        self.assert_rejected("External scan")

    def test_commit_runs_with_no_check_runs_still_count(self):
        # A run that fails to start, or has not created its first job, has no check runs.
        for event in ("push", "pull_request"):
            for status, conclusion in (("completed", "startup_failure"), ("completed", "failure"),
                                       ("queued", None), ("in_progress", None)):
                with self.subTest(event=event, status=status, conclusion=conclusion):
                    self.data["workflow_runs"][MAIN] = [
                        workflow_run(5, event, status, conclusion, "Template validation"),
                    ]
                    self.assert_rejected("Template validation")

    def test_commit_runs_that_pass_or_were_cancelled_are_accepted(self):
        for conclusion in ("success", "neutral", "skipped", "cancelled"):
            with self.subTest(conclusion=conclusion):
                self.data["workflow_runs"][MAIN] = [workflow_run(5, "push", "completed", conclusion)]
                self.verify()

    def test_other_runs_with_no_check_runs_are_ignored(self):
        self.data["workflow_runs"][MAIN] = [
            workflow_run(5, "workflow_dispatch", "in_progress", None),
            workflow_run(6, "issue_comment", "completed", "failure"),
        ]
        self.verify()

    def test_checks_on_later_pages_are_read(self):
        self.data["check_runs"][MAIN] = [check(f"ok-{n}") for n in range(100)] + [
            check("late", conclusion="failure"),
        ]
        self.assert_rejected("late")

    def test_failed_or_pending_statuses_are_rejected(self):
        for state in ("failure", "error", "pending"):
            with self.subTest(state=state):
                self.data["statuses"][MAIN] = [{"context": "e2e", "state": state}]
                self.assert_rejected("e2e")

    def test_command_bookkeeping_statuses_are_ignored(self):
        # The command ledger writes these to the root commit, which can be a new service's first release.
        for state in ("failure", "error", "pending"):
            with self.subTest(state=state):
                self.data["statuses"][MAIN] = [{"context": "ci-command-state/10/abc", "state": state}]
                self.verify()

    def test_failed_command_statuses_still_block(self):
        self.data["statuses"][MAIN] = [
            {"context": "ci-command-state/10/abc", "state": "failure"},
            {"context": "ci-command /test", "state": "error"},
        ]
        self.assert_rejected("ci-command /test")

    def test_statuses_on_later_pages_are_read(self):
        self.data["statuses"][MAIN] = [{"context": f"ok-{n}", "state": "success"} for n in range(100)] + [
            {"context": "late", "state": "failure"},
        ]
        self.assert_rejected("late")

    def test_successful_statuses_and_no_checks_are_accepted(self):
        self.data["statuses"][MAIN] = [{"context": "e2e", "state": "success"}]
        self.verify()


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.data, self.api = fixture()

    def test_creates_the_tag_and_release_on_the_verified_commit(self):
        release.publish(self.api, REPO, "1.2.3", MAIN)
        posts = [c for c in self.data["calls"] if c[0] == "POST"]
        self.assertEqual(posts, [("POST", f"repos/{REPO}/releases", {
            "tag_name": "v1.2.3", "target_commitish": MAIN, "name": "v1.2.3",
            "generate_release_notes": True,
        })])
        self.assertEqual(self.data["tags"]["v1.2.3"], MAIN)

    def test_tag_created_after_verification_blocks_publication(self):
        self.data["tags"]["v1.2.3"] = OLDER
        with self.assertRaisesRegex(RuntimeError, "Tag v1.2.3 already exists"):
            release.publish(self.api, REPO, "1.2.3", MAIN)
        self.assertFalse([c for c in self.data["calls"] if c[0] == "POST"])

    def test_main_moving_after_verification_blocks_publication(self):
        self.data["main"] = OLDER
        with self.assertRaisesRegex(RuntimeError, "main has moved"):
            release.publish(self.api, REPO, "1.2.3", MAIN)
        self.assertFalse([c for c in self.data["calls"] if c[0] == "POST"])

    def test_tag_on_another_commit_after_publication_is_reported(self):
        self.data["release_tag_sha"] = OLDER
        with self.assertRaisesRegex(RuntimeError, "does not point to the verified commit"):
            release.publish(self.api, REPO, "1.2.3", MAIN)

    def test_publish_revalidates_its_inputs(self):
        with self.assertRaisesRegex(RuntimeError, "Version must be"):
            release.publish(self.api, REPO, "1.2", MAIN)
        with self.assertRaisesRegex(RuntimeError, "Commit must be"):
            release.publish(self.api, REPO, "1.2.3", "main")
        self.assertFalse(self.data["calls"])


class RequestTest(unittest.TestCase):
    def failing(self, code):
        return patch.object(release.github_client, "urlopen", side_effect=HTTPError(
            "https://api.github.com", code, "private response", {}, None))

    def test_missing_tag_reference_reads_as_absent(self):
        with patch.dict("os.environ", {"GH_TOKEN": "token"}), self.failing(404):
            self.assertIsNone(release.github_api("GET", f"repos/{REPO}/git/ref/tags/v1.2.3"))

    def test_other_missing_resources_and_failed_writes_raise(self):
        with patch.dict("os.environ", {"GH_TOKEN": "token"}), self.failing(404):
            for method, path in (("GET", f"repos/{REPO}/commits/main"),
                                 ("GET", f"repos/{REPO}/actions/runs/{RUN}"),
                                 ("POST", f"repos/{REPO}/releases")):
                with self.subTest(method=method, path=path):
                    with self.assertRaisesRegex(RuntimeError, "HTTP 404") as raised:
                        release.github_api(method, path, {} if method == "POST" else None)
                    self.assertNotIn("private", str(raised.exception))

    def test_tag_lookup_errors_other_than_missing_raise(self):
        with patch.dict("os.environ", {"GH_TOKEN": "token"}), self.failing(403):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                release.github_api("GET", f"repos/{REPO}/git/ref/tags/v1.2.3")


class CheckoutTest(unittest.TestCase):
    def test_metadata_requires_the_checkout_to_be_the_verified_commit(self):
        with patch.object(release.subprocess, "check_output", return_value=OLDER + "\n") as run:
            with self.assertRaisesRegex(RuntimeError, "not the verified commit"):
                release.checkout_metadata(Path("release"))(MAIN)
        run.assert_called_once()

    def test_metadata_is_read_from_the_matching_checkout(self):
        outputs = [MAIN + "\n", json.dumps(SERVICE)]
        with patch.object(release.subprocess, "check_output", side_effect=outputs) as run:
            self.assertEqual(release.checkout_metadata(Path("release"))(MAIN), SERVICE)
        self.assertEqual(run.call_args.args[0][:2], ["cargo", "metadata"])
        self.assertEqual(run.call_args.kwargs["cwd"], Path("release"))


if __name__ == "__main__":
    unittest.main()
