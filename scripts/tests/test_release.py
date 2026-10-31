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
        "main": MAIN, "compare": {MAIN: "identical", OLDER: "ahead"},
        "tags": {}, "check_runs": {}, "statuses": {}, "suite": 7, "calls": [],
        "release_suites": [7],
        "release_tag_sha": None,
    }

    def api(method, path, body=None):
        data["calls"].append((method, path, copy.deepcopy(body)))
        prefix = f"repos/{REPO}"
        if method == "GET" and path == f"{prefix}/commits/main":
            return {"sha": data["main"]}
        if method == "GET" and path.startswith(f"{prefix}/compare/"):
            base = path[len(f"{prefix}/compare/"):].split("...")[0]
            return {"status": data["compare"].get(base, "diverged")}
        if method == "GET" and path.startswith(f"{prefix}/git/ref/tags/"):
            tag = path.rsplit("/", 1)[1]
            sha = data["tags"].get(tag)
            return None if sha is None else {"object": {"sha": sha, "type": "commit"}}
        if method == "GET" and path == f"{prefix}/actions/runs/{RUN}":
            return {"id": int(RUN), "check_suite_id": data["suite"], "workflow_id": 31}
        if method == "GET" and path.startswith(f"{prefix}/actions/workflows/31/runs?"):
            sha = path.split("head_sha=")[1].split("&")[0]
            runs = [{"check_suite_id": s, "head_sha": sha} for s in data["release_suites"]]
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


def inputs(version="1.2.3", commit=""):
    return {"version": version, "commit": commit}


class VerifyTest(unittest.TestCase):
    def setUp(self):
        self.data, self.api = fixture()

    def verify(self, values=None, packages=SERVICE, ref="refs/heads/main"):
        return release.verify(self.api, REPO, values or inputs(), RUN, ref, lambda sha: packages)

    def assert_rejected(self, pattern, **kwargs):
        with self.assertRaisesRegex(RuntimeError, pattern):
            self.verify(**kwargs)
        self.assertFalse([c for c in self.data["calls"] if c[0] != "GET"])

    def test_default_commit_is_the_current_main_head(self):
        self.assertEqual(self.verify(), {"sha": MAIN, "tag": "v1.2.3"})

    def test_older_main_commit_can_be_selected(self):
        self.assertEqual(self.verify(inputs(commit=OLDER))["sha"], OLDER)

    def test_metadata_is_read_from_the_selected_commit(self):
        seen = []
        release.verify(self.api, REPO, inputs(commit=OLDER), RUN, "refs/heads/main",
                       lambda sha: seen.append(sha) or SERVICE)
        self.assertEqual(seen, [OLDER])

    def test_version_mismatch_fails_before_any_write(self):
        self.assert_rejected("does not match the service package version", values=inputs("9.9.9"))

    def test_internal_crate_versions_are_ignored(self):
        packages = metadata(("my-service", "2.0.0", ("bin",)), ("environment", "0.1.0", ("lib",)))
        self.assertEqual(self.verify(inputs("2.0.0"), packages=packages)["tag"], "v2.0.0")

    def test_invalid_versions_are_rejected(self):
        for version in ("1.2", "v1.2.3", "01.2.3", "1.2.3-rc.1", "1.2.3 ", "", "1.2.3\n"):
            with self.subTest(version=version):
                self.assert_rejected("Version must be", values=inputs(version))

    def test_invalid_commit_is_rejected(self):
        for commit in ("main", "A" * 40, "a" * 39, "a" * 40 + "\n"):
            with self.subTest(commit=commit):
                self.assert_rejected("Commit must be", values=inputs(commit=commit))

    def test_commit_outside_main_is_rejected(self):
        # compare/{commit}...main: "behind" means the commit is newer than main.
        for status in ("diverged", "behind"):
            with self.subTest(status=status):
                self.data["compare"]["c" * 40] = status
                self.assert_rejected("not on main", values=inputs(commit="c" * 40))

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
        # A newer push cancels main validation; the release run lints and tests itself.
        self.data["check_runs"][MAIN] = [
            check("a"), check("b", conclusion="neutral"), check("c", conclusion="skipped"),
            check("d", conclusion="cancelled"),
        ]
        self.assertEqual(self.verify()["sha"], MAIN)

    def test_this_runs_own_checks_are_ignored(self):
        self.data["check_runs"][MAIN] = [
            check("verify", status="in_progress", conclusion=None, suite=self.data["suite"]),
        ]
        self.assertEqual(self.verify()["sha"], MAIN)

    def test_earlier_release_runs_checks_are_ignored(self):
        # Dispatched runs report on the main head, so an earlier failed release sits there too.
        self.data["release_suites"] = [5, 7]
        self.data["check_runs"][MAIN] = [check("Verify release", conclusion="failure", suite=5)]
        self.assertEqual(self.verify()["sha"], MAIN)

    def test_release_runs_on_later_pages_are_read(self):
        self.data["release_suites"] = list(range(100, 200)) + [5]
        self.data["check_runs"][MAIN] = [check("Verify release", conclusion="failure", suite=5)]
        self.assertEqual(self.verify()["sha"], MAIN)

    def test_other_workflows_checks_still_count(self):
        self.data["release_suites"] = [5, 7]
        self.data["check_runs"][MAIN] = [check("Source and generated variants", conclusion="failure", suite=6)]
        self.assert_rejected("Source and generated variants")

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

    def test_statuses_on_later_pages_are_read(self):
        self.data["statuses"][MAIN] = [{"context": f"ok-{n}", "state": "success"} for n in range(100)] + [
            {"context": "late", "state": "failure"},
        ]
        self.assert_rejected("late")

    def test_successful_statuses_and_no_checks_are_accepted(self):
        self.data["statuses"][MAIN] = [{"context": "e2e", "state": "success"}]
        self.assertEqual(self.verify()["sha"], MAIN)


class PublishTest(unittest.TestCase):
    def setUp(self):
        self.data, self.api = fixture()

    def test_creates_the_tag_and_release_on_the_verified_commit(self):
        release.publish(self.api, REPO, "1.2.3", OLDER)
        posts = [c for c in self.data["calls"] if c[0] == "POST"]
        self.assertEqual(posts, [("POST", f"repos/{REPO}/releases", {
            "tag_name": "v1.2.3", "target_commitish": OLDER, "name": "v1.2.3",
            "generate_release_notes": True,
        })])
        self.assertEqual(self.data["tags"]["v1.2.3"], OLDER)

    def test_tag_created_after_verification_blocks_publication(self):
        self.data["tags"]["v1.2.3"] = MAIN
        with self.assertRaisesRegex(RuntimeError, "Tag v1.2.3 already exists"):
            release.publish(self.api, REPO, "1.2.3", OLDER)
        self.assertFalse([c for c in self.data["calls"] if c[0] == "POST"])

    def test_tag_on_another_commit_after_publication_is_reported(self):
        self.data["release_tag_sha"] = MAIN
        with self.assertRaisesRegex(RuntimeError, "does not point to the verified commit"):
            release.publish(self.api, REPO, "1.2.3", OLDER)

    def test_publish_revalidates_its_inputs(self):
        with self.assertRaisesRegex(RuntimeError, "Version must be"):
            release.publish(self.api, REPO, "1.2", OLDER)
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
