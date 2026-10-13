"""Exercise command acceptance and reporting through a fake GitHub API, not PR code."""

import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github/ci/comment_ci.py"
REPO = "owner/service"
ANCHOR = "a" * 40
HEAD = "b" * 40
BASE = "c" * 40
WORKFLOW = ".github/workflows/ci-command-worker.yml"


def fixture():
    data = {
        "permission": "write",
        "pr": {"state": "open", "head": {"sha": HEAD}},
        "prs": {"2": {"state": "open", "head": {"sha": HEAD}}},
        "comments": {},
        "result_comments": [],
        "next_comment": 2000,
        "statuses": {},
        "runs": {},
        "next_run": 100,
        "calls": [],
    }

    def api(method, path, body=None):
        data["calls"].append((method, path, copy.deepcopy(body)))
        prefix = f"repos/{REPO}"
        if method == "GET" and path == prefix:
            return {"id": 42, "default_branch": "main"}
        if method == "GET" and "/collaborators/" in path:
            return {"permission": data["permission"]}
        if method == "GET" and path.startswith(f"{prefix}/pulls/"):
            pr = path.rsplit("/", 1)[1]
            return copy.deepcopy(data["pr"] if pr == "1" else data["prs"][pr])
        if method == "GET" and "/issues/comments/" in path:
            return copy.deepcopy(data["comments"][path.rsplit("/", 1)[1]])
        if method == "GET" and "/issues/" in path and "/comments?" in path:
            pr = path.split("/issues/")[1].split("/")[0]
            return copy.deepcopy([c for c in data["result_comments"] if c["pr"] == pr])
        if method == "POST" and "/issues/" in path and path.endswith("/comments"):
            pr = path.split("/issues/")[1].split("/")[0]
            value = {
                "id": data["next_comment"], "pr": pr, "body": body["body"],
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
            data["next_comment"] += 1
            data["result_comments"].append(value)
            data["comments"][str(value["id"])] = value
            return copy.deepcopy(value)
        if method == "PATCH" and "/issues/comments/" in path:
            value = data["comments"][path.rsplit("/", 1)[1]]
            value["body"] = body["body"]
            return copy.deepcopy(value)
        if method == "GET" and path == f"{prefix}/commits/main":
            return {"sha": BASE}
        if method == "GET" and path == f"{prefix}/actions/workflows/ci-command-worker.yml":
            return {"id": 55, "path": WORKFLOW}
        if method == "GET" and "/statuses?" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            return copy.deepcopy(data["statuses"].get(sha, []))
        if method == "POST" and "/statuses/" in path:
            sha = path.rsplit("/", 1)[1]
            value = copy.deepcopy(body)
            value["creator"] = {"login": "github-actions[bot]", "type": "Bot"}
            data["statuses"].setdefault(sha, []).insert(0, value)
            return body
        if method == "POST" and path.endswith("/dispatches"):
            run_id = data["next_run"]
            data["next_run"] += 1
            inputs = body["inputs"]
            data["runs"][str(run_id)] = {
                "id": run_id, "workflow_id": 55, "path": WORKFLOW,
                "event": "workflow_dispatch", "head_branch": "main", "head_sha": BASE,
                "repository": {"full_name": REPO}, "run_attempt": 1,
                "display_title": "ci-command " + " ".join(
                    inputs[key] for key in ("pr", "command", "sha", "request", "controller", "base")
                ),
                "status": "queued", "conclusion": None, "jobs": [],
            }
            return {"workflow_run_id": run_id}
        if method == "POST" and path.endswith("/cancel"):
            run_id = path.split("/runs/")[1].split("/")[0]
            data["runs"][run_id]["status"] = "completed"
            data["runs"][run_id]["conclusion"] = "cancelled"
            return None
        if method == "GET" and "/attempts/1/jobs?" in path:
            run_id = path.split("/runs/")[1].split("/")[0]
            return {"jobs": copy.deepcopy(data["runs"][run_id]["jobs"])}
        if method == "GET" and "/actions/runs/" in path:
            return copy.deepcopy(data["runs"][path.rsplit("/", 1)[1]])
        if method == "GET" and "/actions/workflows/ci-command-worker.yml/runs?" in path:
            return {"workflow_runs": copy.deepcopy(list(data["runs"].values()))}
        raise AssertionError(f"Unexpected API call: {method} {path}")

    return data, api


def comment(data, number=10, command="/test", pr=1):
    value = {
        "id": number, "body": command, "user": {"login": "alice"},
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/{pr}",
    }
    data["comments"][str(number)] = value
    return {
        "action": "created", "issue": {"number": pr, "pull_request": {}},
        "comment": copy.deepcopy(value),
    }


def worker(data, ci, request, number=None, conclusion="success"):
    number = int(request["worker"]) if number is None else number
    value = {
        "id": number, "workflow_id": 55, "path": WORKFLOW,
        "event": "workflow_dispatch", "head_branch": "main", "head_sha": BASE,
        "repository": {"full_name": REPO}, "run_attempt": 1,
        "display_title": ci.run_title(request),
        "status": "completed", "conclusion": conclusion,
        "jobs": [
            {"name": "Validate accepted request", "conclusion": "success"},
            {"name": "Run PR command", "conclusion": conclusion},
        ],
    }
    data["runs"][str(number)] = value
    return value


class CommentCiTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), "Trusted command controller is missing")
        spec = importlib.util.spec_from_file_location("comment_ci", SCRIPT)
        self.ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ci)
        self.data, self.api = fixture()

    def accept(self, number=10, command="/test", pr=1):
        event = comment(self.data, number, command, pr)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)
        return self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90")

    def complete(self, run):
        route = self.ci.route_event(
            self.api, REPO, "workflow_run", {"action": "completed", "workflow_run": run}
        )
        return self.ci.complete_worker(self.api, REPO, ANCHOR, route)

    def head_status(self, command="test", sha=HEAD):
        context = self.ci.status_context(command)
        return next(
            status for status in self.data["statuses"][sha] if status["context"] == context
        )

    def test_only_three_exact_command_forms(self):
        for text, expected in (
            ("/lint", "lint"), ("/test", "test"), ("/test-tasks", "test-tasks"),
            ("/test-rust-backend-template", "test-rust-backend-template"),
        ):
            self.assertEqual(self.ci.parse_command(text), expected)
        for text in (
            "/test\n", " /test", "/test ", "/test tasks", "/test-*", "/test-",
            "/test-../tasks", "/test-$(id)", "/test-tasks;id", "/test-💣",
            "/test-" + "x" * 65, "/build", "/help", None,
        ):
            self.assertIsNone(self.ci.parse_command(text), repr(text))

    def test_authorization_uses_repository_permission(self):
        event = comment(self.data)
        for permission in ("read", "triage", "none"):
            self.data["permission"] = permission
            self.assertIsNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
        for permission in ("write", "maintain", "admin"):
            self.data["permission"] = permission
            self.assertIsNotNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
        self.assertFalse(any(method == "POST" for method, _, _ in self.data["calls"]))

    def test_issues_edits_and_closed_prs_do_not_accept(self):
        event = comment(self.data)
        event["action"] = "edited"
        self.assertIsNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
        event["action"] = "created"
        del event["issue"]["pull_request"]
        self.assertIsNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
        event["issue"]["pull_request"] = {}
        self.data["pr"]["state"] = "closed"
        self.assertIsNone(self.ci.route_event(self.api, REPO, "issue_comment", event))

    def test_acceptance_records_exact_head_before_pending_and_dispatch(self):
        request = self.accept()
        self.assertEqual(request["sha"], HEAD)
        posts = [(path, body) for method, path, body in self.data["calls"] if method == "POST"]
        self.assertTrue(posts[0][0].endswith(f"/statuses/{ANCHOR}"))
        self.assertTrue(posts[1][0].endswith(f"/statuses/{HEAD}"))
        self.assertEqual(self.head_status()["state"], "pending")
        self.assertEqual(posts[2][1], {"ref": "main", "inputs": self.ci.payload(request)})
        self.assertEqual(request["worker"], "100")
        record, state = self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")
        self.assertEqual(record, request)
        self.assertEqual(state, "pending")
        self.assertLessEqual(len(posts[0][1]["description"]), 140)

    def test_authorization_and_comment_are_rechecked_inside_controller(self):
        event = comment(self.data)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)
        self.data["permission"] = "read"
        self.assertIsNone(self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90"))
        self.data["permission"] = "write"
        self.data["comments"]["10"]["body"] = "/lint"
        self.assertIsNone(self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90"))
        self.assertNotIn(HEAD, self.data["statuses"])

    def test_success_requires_both_expected_jobs(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        self.complete(run)
        self.assertEqual(self.head_status()["state"], "success")

    def test_completed_worker_posts_emoji_free_comment_and_replay_is_idempotent(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        self.complete(run)
        self.assertEqual(len(self.data["result_comments"]), 1)
        body = self.data["result_comments"][0]["body"]
        self.assertIn("[Passed] `/test`", body)
        self.assertIn(HEAD, body)
        self.assertIn("/actions/runs/100", body)
        self.assertIn("Unavailable", body)
        self.assertNotIn("✅", body)
        calls = len([c for c in self.data["calls"] if c[0] in ("POST", "PATCH")])
        self.complete(run)
        self.assertEqual(len([c for c in self.data["calls"] if c[0] in ("POST", "PATCH")]), calls)

    def test_new_result_updates_one_comment_and_old_completion_cannot_replace_it(self):
        old = self.accept()
        old_run = worker(self.data, self.ci, old)
        self.complete(old_run)
        new = self.accept(11)
        self.complete(worker(self.data, self.ci, new, conclusion="failure"))
        self.assertEqual(len(self.data["result_comments"]), 1)
        body = self.data["result_comments"][0]["body"]
        self.assertIn("[Failed] `/test`", body)
        self.assertIn("request=11", body)
        self.complete(old_run)
        self.assertEqual(self.data["result_comments"][0]["body"], body)

    def test_comment_failure_preserves_status_and_retries_without_duplicate(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        route = self.ci.route_event(
            self.api, REPO, "workflow_run", {"action": "completed", "workflow_run": run}
        )

        def fail_comment(method, path, body=None):
            if method == "POST" and path.endswith("/comments"):
                raise RuntimeError("Comment API unavailable")
            return self.api(method, path, body)

        with self.assertRaisesRegex(RuntimeError, "Comment API unavailable"):
            self.ci.complete_worker(fail_comment, REPO, ANCHOR, route)
        self.assertEqual(self.head_status()["state"], "success")
        self.ci.complete_worker(self.api, REPO, ANCHOR, route)
        self.assertEqual(len(self.data["result_comments"]), 1)

    def test_missing_or_invalid_run_dates_do_not_block_cancelled_result_comment(self):
        for date in (None, 123, "not-a-timestamp"):
            with self.subTest(date=date):
                self.data, self.api = fixture()
                request = self.accept()
                run = worker(self.data, self.ci, request, conclusion="cancelled")
                run["run_started_at"] = date
                run["updated_at"] = date
                self.complete(run)
                self.assertEqual(self.head_status()["state"], "error")
                self.assertIn("Duration: Unavailable", self.data["result_comments"][0]["body"])
                self.assertIn("Cancelled", self.data["result_comments"][0]["body"])

    def test_untrusted_metrics_are_advisory_and_no_raw_diagnostics_enter_comments(self):
        from unittest.mock import patch
        request = self.accept()
        run = worker(self.data, self.ci, request, conclusion="failure")
        run["jobs"][1]["id"] = 10002
        run["run_started_at"] = "2026-01-01T00:00:00Z"
        run["updated_at"] = "2026-01-01T00:00:42Z"
        forged = self.ci.report.MARKER + (
            '{"version":1,"command":"test","runs":[{"mode":"default","status":"passed",'
            '"passed":150,"failed":0,"ignored":3,"failing_tests":[],"exit_code":0}],'
            '"failed_stage":null}'
        )
        with patch.object(self.ci.job_logs, "read_job_log", return_value=forged):
            self.complete(run)
        self.assertEqual(self.head_status()["state"], "failure")
        body = self.data["result_comments"][0]["body"]
        self.assertIn("[Failed] `/test`", body)
        self.assertIn("42s", body)
        self.assertIn("150", body)
        self.assertNotIn("<script>", body)

    def test_partial_lint_and_cancelled_comments_are_distinct_and_nonpassing_when_needed(self):
        for number, command, conclusion in (
            (10, "/test-environment", "success"), (11, "/lint", "success"),
            (12, "/test", "cancelled"),
        ):
            request = self.accept(number, command)
            self.complete(worker(self.data, self.ci, request, conclusion=conclusion))
        self.assertEqual(len(self.data["result_comments"]), 3)
        texts = [c["body"] for c in self.data["result_comments"]]
        self.assertTrue(any("Partial test run" in text for text in texts))
        self.assertTrue(any("[Passed] `/lint`" in text for text in texts))
        self.assertTrue(any("[Failed] `/test`" in text and "Cancelled" in text for text in texts))

    def test_skipped_execution_does_not_turn_workflow_success_into_test_success(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        run["jobs"][1]["conclusion"] = "skipped"
        self.complete(run)
        self.assertEqual(self.head_status()["state"], "error")

    def test_failures_cancellations_and_timeouts_are_nonpassing(self):
        for conclusion, expected in (
            ("failure", "failure"), ("cancelled", "error"), ("timed_out", "error"),
        ):
            with self.subTest(conclusion=conclusion):
                self.data, self.api = fixture()
                request = self.accept()
                self.complete(worker(self.data, self.ci, request, conclusion=conclusion))
                self.assertEqual(self.head_status()["state"], expected)

    def test_new_request_pending_cannot_be_overwritten_by_old_success(self):
        old = self.accept()
        new = self.accept(11)
        self.complete(worker(self.data, self.ci, old, 100))
        self.assertEqual(self.head_status()["state"], "pending")
        self.complete(worker(self.data, self.ci, new, 101))
        self.assertEqual(self.head_status()["state"], "success")
        count = len(self.data["statuses"][HEAD])
        self.complete(self.data["runs"]["100"])
        self.assertEqual(len(self.data["statuses"][HEAD]), count)

    def test_cursor_is_monotonic_even_across_head_changes_and_delayed_comments(self):
        new = self.accept(11)
        self.data["pr"]["head"]["sha"] = "d" * 40
        self.assertIsNone(self.accept(10))
        self.assertNotIn("d" * 40, self.data["statuses"])
        self.assertEqual(new["request"], "11")

    def test_head_change_never_gets_an_old_success(self):
        request = self.accept()
        self.data["pr"]["head"]["sha"] = "d" * 40
        self.complete(worker(self.data, self.ci, request))
        self.assertNotIn("d" * 40, self.data["statuses"])
        self.assertEqual(self.head_status()["state"], "error")

    def test_terminal_result_is_idempotent(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        self.complete(run)
        count = len(self.data["statuses"][HEAD])
        self.complete(run)
        self.assertEqual(len(self.data["statuses"][HEAD]), count)
        self.assertIsNone(self.accept())

    def test_displaced_completion_event_reconciles_current_request(self):
        old = self.accept()
        new = self.accept(11)
        worker(self.data, self.ci, new, 101)
        self.complete(worker(self.data, self.ci, old, 100, "cancelled"))
        self.assertEqual(self.head_status()["state"], "success")

    def test_lint_and_packages_have_distinct_statuses_and_locks(self):
        for number, command in enumerate(("/test", "/lint", "/test-tasks", "/test-environment"), 10):
            self.accept(number, command)
        contexts = {s["context"] for s in self.data["statuses"][HEAD]}
        self.assertEqual(len(contexts), 4)
        keys = {self.ci.group(REPO, c) for c in ("test", "lint", "test-tasks", "test-environment")}
        self.assertEqual(len(keys), 4)
        self.assertNotEqual(
            self.ci.status_context("test-Foo").lower(),
            self.ci.status_context("test-foo").lower(),
        )

    def test_forged_worker_identity_is_ignored_and_base_mismatch_is_an_error(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        for key, value in (
            ("workflow_id", 66), ("path", ".github/workflows/other.yml"),
            ("head_branch", "attacker"), ("event", "pull_request"),
        ):
            original = run[key]
            run[key] = value
            self.assertIsNone(self.ci.route_event(
                self.api, REPO, "workflow_run", {"action": "completed", "workflow_run": run}
            ))
            run[key] = original
        run["head_sha"] = "d" * 40
        self.complete(run)
        self.assertEqual(self.head_status()["state"], "error")
        record, state = self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")
        self.assertEqual(record["worker"], request["worker"])
        self.assertEqual(state, "error")

    def test_worker_gate_requires_current_record_open_head_and_trusted_base(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        self.assertEqual(
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100"), request
        )
        self.data["pr"]["head"]["sha"] = "d" * 40
        with self.assertRaisesRegex(RuntimeError, "head"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")
        self.data["pr"]["head"]["sha"] = HEAD
        self.accept(11)
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")
        run["head_sha"] = BASE

    def test_malformed_durable_record_fails_closed(self):
        self.accept()
        self.data["statuses"][ANCHOR][0]["description"] = "not a controller record"
        with self.assertRaisesRegex(RuntimeError, "record"):
            self.accept(11)

    def test_dispatch_failure_is_not_left_passing_or_pending(self):
        def failing_api(method, path, body=None):
            if path.endswith("/dispatches"):
                raise RuntimeError("API unavailable")
            return self.api(method, path, body)
        event = comment(self.data)
        route = self.ci.route_event(failing_api, REPO, "issue_comment", event)
        with self.assertRaisesRegex(RuntimeError, "dispatch"):
            self.ci.accept_comment(failing_api, REPO, ANCHOR, route, "90")
        self.assertEqual(self.head_status()["state"], "error")

    def test_only_controller_cancels_bound_older_workers_for_same_pr_and_command(self):
        old = self.accept()
        self.accept(11, "/lint")
        self.accept(12, "/test", pr=2)
        new = self.accept(13)
        cancels = [path for method, path, _ in self.data["calls"]
                   if method == "POST" and path.endswith("/cancel")]
        self.assertEqual(cancels, [f"repos/{REPO}/actions/runs/{old['worker']}/cancel"])
        self.assertEqual(self.data["runs"][new["worker"]]["status"], "queued")
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(old), old["worker"])

    def test_delayed_old_worker_cannot_cancel_or_replace_new_owner(self):
        old = self.accept()
        self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(old), old["worker"])
        new = self.accept(11)
        # Simulate delayed execution admission of the already validated older worker.
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(old), old["worker"])
        self.assertEqual(self.data["runs"][new["worker"]]["status"], "queued")
        self.complete(worker(self.data, self.ci, new))
        self.assertEqual(self.head_status()["state"], "success")

    def test_shared_sha_old_pr_cannot_replace_new_pr_pending_or_success(self):
        old = self.accept()
        new = self.accept(11, pr=2)
        self.complete(worker(self.data, self.ci, old))
        self.assertEqual(self.head_status()["state"], "pending")
        self.complete(worker(self.data, self.ci, new))
        self.assertEqual(self.head_status()["state"], "success")
        self.assertIn("|11|2|", self.head_status()["description"])

    def test_closed_old_pr_cannot_invalidate_new_pr_result_on_shared_sha(self):
        old = self.accept()
        new = self.accept(11, pr=2)
        self.complete(worker(self.data, self.ci, new))
        self.data["pr"]["state"] = "closed"
        self.complete(worker(self.data, self.ci, old))
        self.assertEqual(self.head_status()["state"], "success")

    def test_delayed_cross_pr_comment_cannot_replace_shared_sha_newer_owner(self):
        self.accept(11, pr=2)
        old = self.accept(10)
        self.assertEqual(self.head_status()["state"], "pending")
        self.assertIn("|11|2|", self.head_status()["description"])
        self.complete(worker(self.data, self.ci, old))
        self.assertEqual(self.head_status()["state"], "pending")

    def test_duplicate_dispatch_never_takes_ownership_after_original_rerun(self):
        request = self.accept()
        original = worker(self.data, self.ci, request, conclusion="failure")
        original["run_attempt"] = 2
        duplicate = worker(self.data, self.ci, request, number=101)
        with self.assertRaisesRegex(RuntimeError, "owner"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "101")
        self.complete(duplicate)
        self.assertEqual(self.head_status()["state"], "error")
        self.assertTrue(self.head_status()["target_url"].endswith("/100"))

    def test_rerun_after_published_success_invalidates_only_current_owner_once(self):
        request = self.accept()
        run = worker(self.data, self.ci, request)
        self.complete(run)
        self.assertEqual(self.head_status()["state"], "success")
        run["run_attempt"] = 2
        run["conclusion"] = "failure"
        self.complete(run)
        self.assertEqual(self.head_status()["state"], "error")
        count = len(self.data["statuses"][HEAD])
        self.complete(run)
        self.assertEqual(len(self.data["statuses"][HEAD]), count)

    def test_terminal_old_pr_rerun_cannot_invalidate_newer_shared_sha_success(self):
        old = self.accept()
        run = worker(self.data, self.ci, old)
        self.complete(run)
        new = self.accept(11, pr=2)
        self.complete(worker(self.data, self.ci, new))
        run["run_attempt"] = 2
        run["conclusion"] = "failure"
        self.complete(run)
        self.assertEqual(self.head_status()["state"], "success")
        self.assertIn("|11|2|", self.head_status()["description"])

    def test_deleted_completed_history_is_not_fetched_and_does_not_block_new_requests(self):
        old = self.accept()
        self.complete(worker(self.data, self.ci, old))
        del self.data["runs"][old["worker"]]
        self.accept(11)
        self.accept(12)
        dispatches = [path for method, path, _ in self.data["calls"]
                      if method == "POST" and path.endswith("/dispatches")]
        self.assertEqual(len(dispatches), 3)
        self.assertEqual(self.head_status()["state"], "pending")

    def test_missing_unreconciled_obsolete_owner_is_no_longer_cancellable(self):
        old = self.accept()

        def missing_api(method, path, body=None):
            if method == "GET" and path == f"repos/{REPO}/actions/runs/{old['worker']}":
                return None
            return self.api(method, path, body)

        for number in (11, 12):
            event = comment(self.data, number)
            route = self.ci.route_event(missing_api, REPO, "issue_comment", event)
            result = self.ci.accept_comment(missing_api, REPO, ANCHOR, route, "90")
            self.assertIsNotNone(result["worker"])
        self.assertEqual(self.head_status()["state"], "pending")

    def test_exact_missing_run_api_response_is_optional_but_other_errors_fail(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        with patch.dict("os.environ", {"GH_TOKEN": "private-token"}):
            with patch.object(self.ci, "urlopen", side_effect=HTTPError(
                "https://api.github.com", 404, "missing", {}, None
            )):
                self.assertIsNone(self.ci.github_api("GET", f"repos/{REPO}/actions/runs/100"))
                with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                    self.ci.github_api("GET", f"repos/{REPO}/actions/runs/100/attempts/1/jobs")

    def test_missing_dispatch_run_id_fails_closed_without_inferred_ownership(self):
        def legacy_api(method, path, body=None):
            result = self.api(method, path, body)
            return None if path.endswith("/dispatches") else result
        event = comment(self.data)
        route = self.ci.route_event(legacy_api, REPO, "issue_comment", event)
        with self.assertRaisesRegex(RuntimeError, "dispatch"):
            self.ci.accept_comment(legacy_api, REPO, ANCHOR, route, "90")
        self.assertEqual(self.head_status()["state"], "error")

    def test_foreign_reserved_state_fails_closed(self):
        self.accept()
        self.data["statuses"][ANCHOR][0]["creator"] = {"login": "other-app[bot]", "type": "Bot"}
        with self.assertRaisesRegex(RuntimeError, "record"):
            self.accept(11)

    def test_registration_visibility_retry_never_promotes_a_duplicate(self):
        from unittest.mock import patch
        request = self.accept()
        pending = self.data["statuses"][ANCHOR][0]
        saved_url = pending["target_url"]
        pending["target_url"] = self.ci.run_url(REPO, request["controller"])

        def registration_arrives(_delay):
            pending["target_url"] = saved_url

        with patch.object(self.ci.time, "sleep", side_effect=registration_arrives) as sleep:
            self.assertEqual(
                self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), request["worker"]),
                request,
            )
        sleep.assert_called_once()

    def test_network_errors_do_not_echo_response_or_credentials(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        error = HTTPError("https://api.github.com", 403, "secret response", {}, None)
        with patch.dict("os.environ", {"GH_TOKEN": "private-token"}):
            with patch.object(self.ci, "urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "GitHub API request failed") as raised:
                    self.ci.github_api("GET", f"repos/{REPO}")
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("private-token", str(raised.exception))

    def test_execution_calls_only_the_approved_make_targets(self):
        import json
        from unittest.mock import patch

        def captured(_args, mode):
            return dict(mode=mode, status="passed", passed=None, failed=None, ignored=None,
                        failing_tests=[], exit_code=0)

        with patch.object(self.ci.report, "capture_make", side_effect=captured) as run:
            self.ci.execute("lint")
            run.assert_called_once_with(["make", "lint"], "lint")
        with patch.object(self.ci.report, "capture_make", side_effect=captured) as run:
            self.ci.execute("test")
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [["make", "test", "FEATURES="], ["make", "test", "FEATURES=api-doc"]],
            )
        metadata = {
            "workspace_members": ["pkg"],
            "packages": [{"id": "pkg", "name": "tasks", "features": {"api-doc": []}}],
        }
        with patch.object(self.ci.subprocess, "check_output", return_value=json.dumps(metadata)):
            with patch.object(self.ci.report, "capture_make", side_effect=captured) as run:
                self.ci.execute("test-tasks")
                self.assertEqual(
                    [call.args[0] for call in run.call_args_list],
                    [["make", "test-crate", "CRATE=tasks", "FEATURES="],
                     ["make", "test-crate", "CRATE=tasks", "FEATURES=tasks/api-doc"]],
                )
            metadata["packages"][0]["features"] = {}
        with patch.object(self.ci.subprocess, "check_output", return_value=json.dumps(metadata)):
            with patch.object(self.ci.report, "capture_make", side_effect=captured) as run:
                self.ci.execute("test-tasks")
                run.assert_called_once_with(
                    ["make", "test-crate", "CRATE=tasks", "FEATURES="], "default"
                )
                with self.assertRaisesRegex(RuntimeError, "Unknown workspace"):
                    self.ci.execute("test-other")
