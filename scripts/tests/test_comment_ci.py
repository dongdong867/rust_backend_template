"""Exercise acceptance, worker safety and result statuses without executing PR code."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch


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
        "comments": {}, "last_edited": {}, "statuses": {}, "runs": {}, "jobs": {},
        "next_run": 100, "calls": [],
    }

    def api(method, path, body=None):
        data["calls"].append((method, path, copy.deepcopy(body)))
        prefix = f"repos/{REPO}"
        if method == "POST" and path == "graphql":
            value = next(
                (c for c in data["comments"].values()
                 if c.get("node_id") == body["variables"]["id"]), None,
            )
            return {"data": {"node": None if value is None else {
                "id": value["node_id"], "databaseId": value["id"], "body": value["body"],
                "lastEditedAt": data["last_edited"].get(str(value["id"])),
            }}}
        if method == "GET" and path == prefix:
            return {"id": 42, "default_branch": "main"}
        if method == "GET" and "/collaborators/" in path:
            return {"permission": data["permission"]}
        if method == "GET" and path.startswith(f"{prefix}/pulls/"):
            pr = path.rsplit("/", 1)[1]
            return copy.deepcopy(data["pr"] if pr == "1" else data["prs"][pr])
        if method == "GET" and "/issues/comments/" in path:
            return copy.deepcopy(data["comments"].get(path.rsplit("/", 1)[1], {}))
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
                "status": "queued", "conclusion": None,
            }
            return {"workflow_run_id": run_id}
        if method == "POST" and path.endswith("/cancel"):
            run_id = path.split("/runs/")[1].split("/")[0]
            data["runs"][run_id]["status"] = "completed"
            data["runs"][run_id]["conclusion"] = "cancelled"
            return None
        if method == "GET" and "/attempts/1/jobs?" in path:
            run_id = path.split("/runs/")[1].split("/")[0]
            return {"jobs": copy.deepcopy(data["jobs"].get(run_id, []))}
        if method == "GET" and "/actions/runs/" in path:
            return copy.deepcopy(data["runs"][path.rsplit("/", 1)[1]])
        raise AssertionError(f"Unexpected API call: {method} {path}")

    return data, api


def comment(data, number=10, command="/test", pr=1):
    value = {
        "id": number, "body": command, "user": {"login": "alice"},
        "node_id": f"IC_{number}",
        "created_at": "2026-10-15T03:04:21Z", "updated_at": "2026-10-15T03:04:21Z",
        "issue_url": f"https://api.github.com/repos/{REPO}/issues/{pr}",
    }
    data["comments"][str(number)] = value
    return {
        "action": "created", "issue": {"number": pr, "pull_request": {}},
        "comment": copy.deepcopy(value),
    }


class CommentCiTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("comment_ci", SCRIPT)
        self.ci = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ci)
        self.data, self.api = fixture()

    def accept(self, number=10, command="/test", pr=1):
        event = comment(self.data, number, command, pr)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)
        return self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90")

    def mutations(self):
        return [call for call in self.data["calls"] if call[0] != "GET" and call[1] != "graphql"]

    def head_status(self, sha=HEAD, context="ci-command /test"):
        return next((s for s in self.data["statuses"].get(sha, []) if s["context"] == context), None)

    def report(self, request, validate="success", execute="success", run_id=None, lock=None,
               api=None):
        return self.ci.report_worker(
            api or self.api, REPO, ANCHOR, self.ci.payload(request), run_id or request["worker"],
            {"validate": validate, "execute": execute},
            self.ci.group(REPO, request["command"]) if lock is None else lock,
        )

    def execute_job(self, request, conclusion, minutes, seconds=5):
        end = 60 + minutes * 60 + seconds
        self.data["jobs"][request["worker"]] = [
            {"name": "Validate accepted request", "conclusion": "success",
             "started_at": "2026-10-20T00:00:00Z", "completed_at": "2026-10-20T00:00:10Z"},
            {"name": "Run PR command", "conclusion": conclusion,
             "started_at": "2026-10-20T00:01:00Z",
             "completed_at": f"2026-10-20T00:{end // 60:02d}:{end % 60:02d}Z"},
        ]

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
        self.assertFalse(self.mutations())

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
        self.assertFalse(self.mutations())

    def test_acceptance_binds_worker_then_marks_captured_head_pending(self):
        request = self.accept()
        self.assertEqual(request["sha"], HEAD)
        self.assertEqual(request["worker"], "100")
        posts = [(path, body) for method, path, body in self.mutations() if method == "POST"]
        self.assertEqual(len(posts), 4)
        self.assertTrue(posts[0][0].endswith(f"/statuses/{ANCHOR}"))
        self.assertTrue(posts[1][0].endswith("/dispatches"))
        self.assertEqual(posts[1][1], {"ref": "main", "inputs": self.ci.payload(request)})
        self.assertTrue(posts[2][0].endswith(f"/statuses/{ANCHOR}"))
        for path, body in (posts[0], posts[2]):
            self.assertTrue(body["context"].startswith("ci-command-state/"))
            self.assertLessEqual(len(body["description"]), 140)
        # The head status is published only after the worker is bound, so it links to that run.
        self.assertEqual(posts[3], (f"repos/{REPO}/statuses/{HEAD}", {
            "context": "ci-command /test", "state": "pending",
            "description": "Running the requested check.",
            "target_url": self.ci.run_url(REPO, "100"),
        }))
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (request, "pending"))
        # The fake API refuses all comment POST/PATCH calls.

    def test_authorization_comment_and_deleted_comment_are_rechecked_inside_controller(self):
        event = comment(self.data)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)
        self.data["permission"] = "read"
        self.assertIsNone(self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90"))
        self.data["permission"] = "write"
        self.data["comments"]["10"]["body"] = "/lint"
        self.assertIsNone(self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90"))
        del self.data["comments"]["10"]
        self.assertIsNone(self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90"))
        self.assertFalse(self.mutations())

    def test_created_event_with_edited_body_is_rejected_even_with_same_second_timestamps(self):
        for edited_at in ("2026-10-15T03:04:21Z", "2026-10-15T03:04:22Z"):
            with self.subTest(edited_at=edited_at):
                self.data, self.api = fixture()
                event = comment(self.data)
                self.data["last_edited"]["10"] = edited_at
                self.assertIsNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
                self.assertFalse(self.mutations())

    def test_comment_edited_after_routing_is_rejected_by_acceptance_recheck(self):
        event = comment(self.data)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)
        self.assertIsNotNone(route)
        self.data["last_edited"]["10"] = "2026-10-15T03:04:21Z"
        self.assertIsNone(self.ci.accept_comment(self.api, REPO, ANCHOR, route, "90"))
        self.assertFalse(self.mutations())

    def test_comment_edited_during_request_preparation_is_rechecked_before_bookkeeping(self):
        event = comment(self.data)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)

        def edited_during_lookup(method, path, body=None):
            if path == f"repos/{REPO}/commits/main":
                self.data["last_edited"]["10"] = "2026-10-15T03:04:21Z"
            return self.api(method, path, body)

        self.assertIsNone(self.ci.accept_comment(
            edited_during_lookup, REPO, ANCHOR, route, "90"
        ))
        self.assertFalse(self.mutations())

    def test_edit_query_is_read_only_and_binds_the_comment_node(self):
        event = comment(self.data)
        self.assertIsNotNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
        queries = [body for method, path, body in self.data["calls"]
                   if method == "POST" and path == "graphql"]
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0]["variables"], {"id": "IC_10"})
        self.assertIn("lastEditedAt", queries[0]["query"])
        self.assertNotIn("mutation", queries[0]["query"])
        self.assertFalse(self.mutations())

    def test_missing_or_invalid_comment_node_id_is_rejected(self):
        for node_id in (None, 10, "", "x" * 201):
            with self.subTest(node_id=node_id):
                self.data, self.api = fixture()
                event = comment(self.data)
                event["comment"]["node_id"] = node_id
                self.assertIsNone(self.ci.route_event(self.api, REPO, "issue_comment", event))
                self.assertFalse(self.mutations())

    def test_unknown_or_mismatched_edit_metadata_fails_closed_at_both_checks(self):
        valid = {"id": "IC_10", "databaseId": 10, "body": "/test", "lastEditedAt": None}
        bad_nodes = [
            None, [], {}, {k: v for k, v in valid.items() if k != "lastEditedAt"},
            {**valid, "id": "IC_other"}, {**valid, "databaseId": 11},
            {**valid, "databaseId": True}, {**valid, "databaseId": 10.0},
            {**valid, "body": "/lint"}, {**valid, "lastEditedAt": False},
        ]
        responses = [None, [], {}, {"data": None}, {"data": []}]
        responses += [{"data": {"node": node}} for node in bad_nodes]
        responses += [
            {"data": {"node": valid}, "errors": [{"message": "private API detail"}]},
            {"data": {"node": valid}, "errors": False},
        ]
        for response in responses:
            with self.subTest(response=response):
                self.data, self.api = fixture()
                event = comment(self.data)

                def bad_api(method, path, body=None):
                    if path == "graphql":
                        return copy.deepcopy(response)
                    return self.api(method, path, body)

                self.assertIsNone(self.ci.route_event(bad_api, REPO, "issue_comment", event))
                route = self.ci.route_event(self.api, REPO, "issue_comment", event)
                self.assertIsNotNone(route)
                self.assertIsNone(self.ci.accept_comment(bad_api, REPO, ANCHOR, route, "90"))
                self.assertFalse(self.mutations())

    def test_edit_api_failure_cannot_dispatch(self):
        event = comment(self.data)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)

        def unavailable(method, path, body=None):
            if path == "graphql":
                raise RuntimeError("GitHub API request failed.")
            return self.api(method, path, body)

        for stage in (
            lambda: self.ci.route_event(unavailable, REPO, "issue_comment", event),
            lambda: self.ci.accept_comment(unavailable, REPO, ANCHOR, route, "90"),
        ):
            with self.assertRaisesRegex(RuntimeError, "GitHub API request failed"):
                stage()
        self.assertFalse(self.mutations())

    def test_completion_events_are_ignored_without_api_calls(self):
        self.assertIsNone(self.ci.route_event(
            self.api, REPO, "workflow_run", {"action": "completed", "workflow_run": {"id": 100}}
        ))
        self.assertFalse(self.data["calls"])

    def test_cursor_is_monotonic_across_delayed_comments_and_controller_reruns(self):
        request = self.accept(11)
        self.data["pr"]["head"]["sha"] = "d" * 40
        writes = copy.deepcopy(self.mutations())
        self.assertIsNone(self.accept(10))
        self.assertIsNone(self.accept(11))
        self.assertEqual(self.mutations(), writes)
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (request, "pending"))

    def test_lint_and_packages_have_distinct_case_sensitive_locks_and_cursors(self):
        commands = ("lint", "test", "test-tasks", "test-environment", "test-Foo", "test-foo")
        self.assertEqual(len({self.ci.group(REPO, c) for c in commands}), len(commands))
        self.assertEqual(len({self.ci.state_context("1", c) for c in commands}), len(commands))

    def test_worker_gate_requires_current_record_open_head_and_trusted_base(self):
        request = self.accept()
        self.assertEqual(
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100"), request
        )
        self.data["pr"]["head"]["sha"] = "d" * 40
        with self.assertRaisesRegex(RuntimeError, "head"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")
        self.data["pr"]["head"]["sha"] = HEAD
        self.data["pr"]["state"] = "closed"
        with self.assertRaisesRegex(RuntimeError, "closed"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")
        self.data["pr"]["state"] = "open"
        self.accept(11)
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")

    def test_worker_gate_rejects_forged_identity_base_and_unsupported_rerun(self):
        for key, value in (
            ("workflow_id", 66), ("path", ".github/workflows/other.yml"),
            ("head_branch", "attacker"), ("event", "pull_request"),
            ("head_sha", "d" * 40), ("repository", {"full_name": "attacker/service"}),
            ("run_attempt", 2), ("display_title", "unbound dispatch"),
        ):
            with self.subTest(key=key):
                self.data, self.api = fixture()
                request = self.accept()
                self.data["runs"]["100"][key] = value
                with self.assertRaisesRegex(RuntimeError, "trusted"):
                    self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")

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
        with self.assertRaisesRegex(RuntimeError, "superseded"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(old), old["worker"])
        self.assertEqual(self.data["runs"][new["worker"]]["status"], "queued")
        self.assertEqual(
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(new), new["worker"]), new
        )

    def test_unverifiable_obsolete_worker_is_not_cancelled_or_replaced(self):
        old = self.accept()
        self.data["runs"][old["worker"]]["display_title"] = "unrelated worker"
        with self.assertRaisesRegex(RuntimeError, "dispatch"):
            self.accept(11)
        self.assertFalse(any(path.endswith("/cancel") for _, path, _ in self.data["calls"]))
        self.assertEqual(len(self.data["runs"]), 1)
        request, phase = self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")
        self.assertEqual(request["request"], "11")
        self.assertIsNone(request["worker"])
        self.assertEqual(phase, "error")

    def test_shared_sha_prs_execute_independently_and_share_the_head_status(self):
        old = self.accept()
        new = self.accept(11, pr=2)
        for request in (old, new):
            self.assertEqual(
                self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), request["worker"]),
                request,
            )
            self.assertEqual(self.data["runs"][request["worker"]]["status"], "queued")
        self.assertFalse(any(path.endswith("/cancel") for _, path, _ in self.data["calls"]))
        # Both PRs tested identical code, so the last writer's result is valid for that SHA.
        self.assertEqual(self.head_status()["target_url"], self.ci.run_url(REPO, new["worker"]))
        self.assertEqual(self.report(old), "success")
        self.assertEqual(self.head_status()["target_url"], self.ci.run_url(REPO, old["worker"]))

    def test_duplicate_dispatch_cannot_replace_bound_owner_even_after_original_rerun(self):
        request = self.accept()
        self.data["runs"]["100"]["run_attempt"] = 2
        self.data["runs"]["101"] = {**self.data["runs"]["100"], "id": 101, "run_attempt": 1}
        with self.assertRaisesRegex(RuntimeError, "owner"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "101")
        with self.assertRaisesRegex(RuntimeError, "trusted"):
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100")
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (request, "pending"))

    def test_legacy_records_remain_readable_and_pending_owners_are_admitted(self):
        for version in ("v2", "v3"):
            for phase in ("pending", "success", "failure", "error"):
                with self.subTest(version=version, phase=phase):
                    self.data, self.api = fixture()
                    request = self.accept()
                    fields = [version, *(request[k] for k in ("request", "controller", "sha", "base"))]
                    if version == "v3":
                        fields.append("-" if phase in ("pending", "success") else "f")
                    record = self.data["statuses"][ANCHOR][0]
                    record.update(description="|".join(fields), state=phase)
                    self.assertEqual(
                        self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (request, phase)
                    )
                    if phase == "pending":
                        self.assertEqual(
                            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100"),
                            request,
                        )

    def test_malformed_or_foreign_durable_records_fail_closed(self):
        request = self.accept()
        cursor = self.data["statuses"][ANCHOR][0]
        original = copy.deepcopy(cursor)
        for changes in (
            {"description": "not a controller record"},
            {"description": original["description"] + "|extra"},
            {"description": original["description"].rsplit("|", 1)[0] + "|unknown"},
            {"description": original["description"].rsplit("|", 1)[0] + "|h"},
            {"description": original["description"].replace("v3|", "v99|", 1)},
            {"creator": {"login": "other-app[bot]", "type": "Bot"}},
            {"target_url": "https://other.example/actions/runs/100"},
        ):
            with self.subTest(changes=changes):
                cursor.clear()
                cursor.update(copy.deepcopy(original), **changes)
                with self.assertRaisesRegex(RuntimeError, "record"):
                    self.accept(11)
        cursor.clear()
        cursor.update(original)
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")[0], request)

    def test_maximum_identifier_width_still_fits_bookkeeping_description(self):
        event = comment(self.data, number=10**20 - 1)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)
        request = self.ci.accept_comment(self.api, REPO, ANCHOR, route, str(10**20 - 2))
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")[0], request)
        for record in self.data["statuses"][ANCHOR]:
            self.assertLessEqual(len(record["description"]), 140)

    def test_completed_or_deleted_historical_workers_do_not_block_new_requests(self):
        for legacy_terminal in (False, True):
            with self.subTest(legacy_terminal=legacy_terminal):
                self.data, self.api = fixture()
                old = self.accept()
                if legacy_terminal:
                    self.ci.write_state(self.api, REPO, ANCHOR, old, "success")
                self.data["runs"][old["worker"]].update(status="completed", conclusion="success")
                self.accept(11)
                del self.data["runs"][old["worker"]]

                def missing_api(method, path, body=None):
                    if method == "GET" and path == f"repos/{REPO}/actions/runs/{old['worker']}":
                        return None
                    return self.api(method, path, body)

                event = comment(self.data, 12)
                route = self.ci.route_event(missing_api, REPO, "issue_comment", event)
                self.assertIsNotNone(self.ci.accept_comment(missing_api, REPO, ANCHOR, route, "90"))
                self.assertEqual(len(self.data["runs"]), 2)

    def test_missing_dispatch_id_or_api_failure_marks_head_error(self):
        for missing_id in (False, True):
            with self.subTest(missing_id=missing_id):
                self.data, self.api = fixture()
                event = comment(self.data)
                route = self.ci.route_event(self.api, REPO, "issue_comment", event)

                def failed_dispatch(method, path, body=None):
                    if path.endswith("/dispatches"):
                        if missing_id:
                            self.api(method, path, body)
                            return None
                        raise RuntimeError("GitHub API request failed.")
                    return self.api(method, path, body)

                with self.assertRaisesRegex(RuntimeError, "dispatch"):
                    self.ci.accept_comment(failed_dispatch, REPO, ANCHOR, route, "90")
                request, phase = self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")
                self.assertEqual(phase, "error")
                self.assertIsNone(request["worker"])
                self.assertEqual(self.head_status(), {
                    "context": "ci-command /test", "state": "error",
                    "description": "Dispatch failed; post a new command.",
                    "target_url": self.ci.run_url(REPO, "90"),
                    "creator": {"login": "github-actions[bot]", "type": "Bot"},
                })

    def test_report_maps_job_results_to_head_status_and_closes_the_request(self):
        cases = (
            ("success", "success", "success", "Requested check passed.", None),
            ("success", "failure", "failure", "Requested check failed; see the worker run.",
             "Worker failed"),
            ("success", "cancelled", "error", "Cancelled; post a new command.", "Cancelled"),
            ("cancelled", "skipped", "error", "Cancelled; post a new command.", "Cancelled"),
            ("failure", "skipped", "error", "Worker validation failed; post a new command.",
             "Worker validation failed"),
            ("success", "unexpected", "error", "Worker validation failed; post a new command.",
             "Worker validation failed"),
        )
        for validate, execute, state, description, reason in cases:
            with self.subTest(validate=validate, execute=execute):
                self.data, self.api = fixture()
                request = self.accept()
                self.assertEqual(self.report(request, validate, execute), state)
                self.assertEqual(self.head_status(), {
                    "context": "ci-command /test", "state": state, "description": description,
                    "target_url": self.ci.run_url(REPO, request["worker"]),
                    "creator": {"login": "github-actions[bot]", "type": "Bot"},
                })
                latest, phase, recorded = next(
                    self.ci.state_records(self.api, REPO, ANCHOR, "1", "test")
                )
                self.assertEqual((latest, phase, recorded), (request, state, reason))
                # A closed request cannot report again or admit its worker again.
                writes = len(self.mutations())
                self.assertIsNone(self.report(request))
                self.assertEqual(len(self.mutations()), writes)
                with self.assertRaisesRegex(RuntimeError, "completed"):
                    self.ci.check_worker(
                        self.api, REPO, ANCHOR, self.ci.payload(request), request["worker"]
                    )

    def test_report_names_a_changed_head_instead_of_a_failure(self):
        request = self.accept()
        self.data["pr"]["head"]["sha"] = "d" * 40
        self.assertEqual(self.report(request, "success", "failure"), "error")
        self.assertEqual(self.head_status()["description"], "PR head changed; post a new command.")
        self.assertNotIn("d" * 40, self.data["statuses"])
        _, _, reason = next(self.ci.state_records(self.api, REPO, ANCHOR, "1", "test"))
        self.assertEqual(reason, "PR head changed")

    def test_superseded_rerun_or_unbound_worker_reports_nothing(self):
        old = self.accept()
        new = self.accept(11)
        writes = len(self.mutations())
        # Same head: the newer request owns the head status, so the old run writes nothing.
        self.assertIsNone(self.report(old))
        self.assertIsNone(self.report(new, run_id="999"))
        self.data["runs"][new["worker"]]["run_attempt"] = 2
        self.assertIsNone(self.report(new))
        self.data["runs"][new["worker"]]["run_attempt"] = 1
        self.data["runs"][new["worker"]]["head_branch"] = "attacker"
        self.assertIsNone(self.report(new))
        self.assertEqual(len(self.mutations()), writes)
        self.assertEqual(self.head_status()["state"], "pending")
        self.assertEqual(self.head_status()["target_url"], self.ci.run_url(REPO, new["worker"]))

    def test_superseded_run_closes_its_own_old_head_only_while_it_still_owns_it(self):
        old = self.accept()
        self.data["pr"]["head"]["sha"] = "d" * 40
        new = self.accept(11)
        self.assertEqual(self.report(old, "success", "cancelled"), "error")
        self.assertEqual(self.head_status()["description"], "PR head changed; post a new command.")
        self.assertEqual(self.head_status()["target_url"], self.ci.run_url(REPO, old["worker"]))
        # The ledger still binds the newer request, and its head status is unchanged.
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (new, "pending"))
        self.assertEqual(self.head_status("d" * 40)["state"], "pending")
        # Once the old head is closed, or another run owns it, a superseded run writes nothing.
        writes = len(self.mutations())
        self.assertIsNone(self.report(old))
        self.data["statuses"][HEAD][0]["state"] = "pending"
        self.data["statuses"][HEAD][0]["target_url"] = self.ci.run_url(REPO, "555")
        self.assertIsNone(self.report(old))
        self.data["statuses"][HEAD][0]["target_url"] = self.ci.run_url(REPO, old["worker"])
        self.data["statuses"][HEAD][0]["creator"] = {"login": "someone", "type": "User"}
        self.assertIsNone(self.report(old))
        self.assertEqual(len(self.mutations()), writes)

    def test_superseded_report_requires_a_recorded_binding_and_first_attempt(self):
        old = self.accept()
        self.data["pr"]["head"]["sha"] = "d" * 40
        self.accept(11)
        writes = len(self.mutations())
        self.assertIsNone(self.report(old, run_id="999"))
        self.data["runs"][old["worker"]]["run_attempt"] = 2
        self.assertIsNone(self.report(old))
        self.assertEqual(len(self.mutations()), writes)
        self.assertEqual(self.head_status()["state"], "pending")

    def test_report_without_the_controller_lock_writes_nothing(self):
        request = self.accept()
        writes = len(self.mutations())
        for lock in ("", "ci-command-other", self.ci.group(REPO, "lint")):
            with self.subTest(lock=lock):
                with self.assertRaisesRegex(RuntimeError, "lock"):
                    self.report(request, lock=lock)
        self.assertEqual(len(self.mutations()), writes)
        self.assertEqual(self.head_status()["state"], "pending")

    def test_execute_timeout_is_reported_as_timed_out(self):
        timed_out = ("error", "Timed out; post a new command.", "Timed out")
        cancelled = ("error", "Cancelled; post a new command.", "Cancelled")
        failed = ("failure", "Requested check failed; see the worker run.", "Worker failed")
        for execute, conclusion, minutes, seconds, expected in (
            ("cancelled", "cancelled", 30, 5, timed_out),
            ("cancelled", "timed_out", 30, 5, timed_out),
            # The limit is inclusive: a full 30 minutes is a timeout, a second less is not.
            ("cancelled", "cancelled", 30, 0, timed_out),
            ("cancelled", "cancelled", 29, 59, cancelled),
            ("cancelled", "cancelled", 3, 5, cancelled),
            # Actions may also report a timed-out job as failed.
            ("failure", "failure", 30, 5, timed_out),
            ("failure", "timed_out", 30, 5, timed_out),
            ("failure", "failure", 3, 5, failed),
        ):
            with self.subTest(execute=execute, conclusion=conclusion, minutes=minutes, seconds=seconds):
                self.data, self.api = fixture()
                request = self.accept()
                self.execute_job(request, conclusion, minutes, seconds)
                state, description, reason = expected
                self.assertEqual(self.report(request, "success", execute), state)
                self.assertEqual(self.head_status()["description"], description)
                _, _, recorded = next(self.ci.state_records(self.api, REPO, ANCHOR, "1", "test"))
                self.assertEqual(recorded, reason)

    def test_failed_jobs_lookup_keeps_the_plain_outcome(self):
        for execute, state, description in (
            ("cancelled", "error", "Cancelled; post a new command."),
            ("failure", "failure", "Requested check failed; see the worker run."),
        ):
            with self.subTest(execute=execute):
                self.data, self.api = fixture()
                request = self.accept()

                def jobs_unavailable(method, path, body=None):
                    if "/jobs?" in path:
                        raise RuntimeError("GitHub API request failed.")
                    return self.api(method, path, body)

                self.assertEqual(self.report(request, "success", execute, api=jobs_unavailable), state)
                self.assertEqual(self.head_status()["description"], description)
                self.assertEqual(
                    self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")[1], state
                )

    def test_missing_execute_job_details_keep_the_cancelled_result(self):
        request = self.accept()
        self.data["jobs"][request["worker"]] = [{"name": "Run PR command", "conclusion": "cancelled"}]
        self.assertEqual(self.report(request, "success", "cancelled"), "error")
        self.assertEqual(self.head_status()["description"], "Cancelled; post a new command.")

    def test_lost_cursor_write_after_report_leaves_head_result_and_pending_request(self):
        request = self.accept()

        def anchor_write_fails(method, path, body=None):
            if method == "POST" and path.endswith(f"/statuses/{ANCHOR}"):
                raise RuntimeError("GitHub API request failed.")
            return self.api(method, path, body)

        with self.assertRaisesRegex(RuntimeError, "API"):
            self.report(request, api=anchor_write_fails)
        # The public result was written before the internal cursor.
        self.assertEqual(self.head_status()["state"], "success")
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (request, "pending"))
        # A newer command still replaces the request normally.
        newer = self.accept(11)
        self.assertEqual(self.head_status()["target_url"], self.ci.run_url(REPO, newer["worker"]))

    def test_lost_pending_head_write_still_admits_the_bound_worker(self):
        event = comment(self.data)
        route = self.ci.route_event(self.api, REPO, "issue_comment", event)

        def head_write_fails(method, path, body=None):
            if method == "POST" and path.endswith(f"/statuses/{HEAD}"):
                raise RuntimeError("GitHub API request failed.")
            return self.api(method, path, body)

        with self.assertRaisesRegex(RuntimeError, "API"):
            self.ci.accept_comment(head_write_fails, REPO, ANCHOR, route, "90")
        request, phase = self.ci.read_state(self.api, REPO, ANCHOR, "1", "test")
        self.assertEqual((request["worker"], phase), ("100", "pending"))
        self.assertIsNone(self.head_status())
        self.assertEqual(
            self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), "100"), request
        )
        self.assertEqual(self.report(request), "success")
        self.assertEqual(self.head_status()["state"], "success")

    def test_older_worker_cannot_replace_a_newer_head_result(self):
        old = self.accept()
        new = self.accept(11)
        self.assertEqual(self.report(new, "success", "failure"), "failure")
        self.assertIsNone(self.report(old))
        self.assertEqual(self.head_status()["state"], "failure")
        self.assertEqual(self.head_status()["target_url"], self.ci.run_url(REPO, new["worker"]))

    def test_head_status_contexts_are_distinct_for_case_sensitive_packages(self):
        self.assertEqual(self.ci.status_context("lint"), "ci-command /lint")
        self.assertEqual(self.ci.status_context("test"), "ci-command /test")
        packages = ("test-environment", "test-Foo", "test-foo")
        contexts = {self.ci.status_context(c).lower() for c in packages}
        self.assertEqual(len(contexts), len(packages))
        for command in packages:
            self.assertTrue(self.ci.status_context(command).startswith(f"ci-command /{command}-"))
        # Historical public statuses used a different context and are never rewritten.
        self.assertFalse(any(self.ci.status_context(c).startswith("ci-comment") for c in packages))

    def test_lock_stage_names_the_controller_lock_for_a_valid_command_only(self):
        self.assertEqual(self.ci.lock_group(REPO, "test"), self.ci.group(REPO, "test"))
        with self.assertRaisesRegex(RuntimeError, "Invalid command"):
            self.ci.lock_group(REPO, "test me")

    def test_registration_visibility_retry_never_promotes_a_duplicate(self):
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

    def test_graphql_uses_platform_endpoint_and_does_not_log_api_errors(self):
        from urllib.error import HTTPError
        with patch.dict("os.environ", {
            "GH_TOKEN": "private-token", "GITHUB_GRAPHQL_URL": "https://github.example/api/graphql",
        }):
            with patch.object(self.ci, "urlopen", side_effect=HTTPError(
                "https://github.example", 403, "private response", {}, None
            )) as opened:
                with self.assertRaisesRegex(RuntimeError, "HTTP 403") as raised:
                    self.ci.github_api("POST", "graphql", {"query": "query { viewer { login } }"})
        self.assertEqual(opened.call_args.args[0].full_url, "https://github.example/api/graphql")
        self.assertEqual(opened.call_args.kwargs["timeout"], 30)
        self.assertNotIn("private", str(raised.exception))

    def test_exact_missing_run_api_response_is_optional_but_other_errors_fail(self):
        from urllib.error import HTTPError
        with patch.dict("os.environ", {"GH_TOKEN": "private-token"}):
            with patch.object(self.ci, "urlopen", side_effect=HTTPError(
                "https://api.github.com", 404, "missing", {}, None
            )):
                self.assertIsNone(self.ci.github_api("GET", f"repos/{REPO}/actions/runs/100"))
                with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
                    self.ci.github_api("GET", f"repos/{REPO}/actions/runs/100/attempts/1/jobs")

    def test_execution_calls_only_the_approved_make_targets(self):
        with patch.object(self.ci.subprocess, "run") as run:
            self.ci.execute("lint")
            run.assert_called_once_with(["make", "lint"], check=True)
        with patch.object(self.ci.subprocess, "run") as run:
            self.ci.execute("test")
            self.assertEqual([call.args[0] for call in run.call_args_list], [
                ["make", "test", "FEATURES="], ["make", "test", "FEATURES=api-doc"],
            ])
            self.assertTrue(all(call.kwargs == {"check": True} for call in run.call_args_list))
        metadata = {
            "workspace_members": ["pkg"],
            "packages": [{"id": "pkg", "name": "tasks", "features": {"api-doc": []}}],
        }
        with patch.object(self.ci.subprocess, "check_output", return_value=json.dumps(metadata)):
            with patch.object(self.ci.subprocess, "run") as run:
                self.ci.execute("test-tasks")
                self.assertEqual([call.args[0] for call in run.call_args_list], [
                    ["make", "test-crate", "CRATE=tasks", "FEATURES="],
                    ["make", "test-crate", "CRATE=tasks", "FEATURES=tasks/api-doc"],
                ])
            metadata["packages"][0]["features"] = {}
        with patch.object(self.ci.subprocess, "check_output", return_value=json.dumps(metadata)):
            with patch.object(self.ci.subprocess, "run") as run:
                self.ci.execute("test-tasks")
                run.assert_called_once_with(
                    ["make", "test-crate", "CRATE=tasks", "FEATURES="], check=True
                )
                run.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "Unknown workspace"):
                    self.ci.execute("test-other")
                run.assert_not_called()

    def test_failed_make_stops_execution_and_propagates_failure(self):
        with patch.object(self.ci.subprocess, "run", side_effect=subprocess.CalledProcessError(
            2, ["make", "test", "FEATURES="]
        )) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                self.ci.execute("test")
        run.assert_called_once_with(["make", "test", "FEATURES="], check=True)
