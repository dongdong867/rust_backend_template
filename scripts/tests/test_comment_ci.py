"""Exercise Actions-only acceptance and worker safety without executing PR code."""

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
        "comments": {}, "last_edited": {}, "statuses": {}, "runs": {},
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

    def test_actions_only_acceptance_records_head_and_binds_worker_without_public_status(self):
        request = self.accept()
        self.assertEqual(request["sha"], HEAD)
        self.assertEqual(request["worker"], "100")
        posts = [(path, body) for method, path, body in self.mutations() if method == "POST"]
        self.assertEqual(len(posts), 3)
        self.assertTrue(posts[0][0].endswith(f"/statuses/{ANCHOR}"))
        self.assertTrue(posts[1][0].endswith("/dispatches"))
        self.assertEqual(posts[1][1], {"ref": "main", "inputs": self.ci.payload(request)})
        self.assertTrue(posts[2][0].endswith(f"/statuses/{ANCHOR}"))
        for path, body in (posts[0], posts[2]):
            self.assertTrue(body["context"].startswith("ci-command-state/"))
            self.assertLessEqual(len(body["description"]), 140)
        self.assertNotIn(HEAD, self.data["statuses"])
        self.assertEqual(self.ci.read_state(self.api, REPO, ANCHOR, "1", "test"), (request, "pending"))
        # The fake API refuses all comment POST/PATCH and PR-head status calls are explicitly absent.

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

    def test_shared_sha_prs_execute_independently_without_public_status(self):
        old = self.accept()
        new = self.accept(11, pr=2)
        for request in (old, new):
            self.assertEqual(
                self.ci.check_worker(self.api, REPO, ANCHOR, self.ci.payload(request), request["worker"]),
                request,
            )
            self.assertEqual(self.data["runs"][request["worker"]]["status"], "queued")
        self.assertFalse(any(path.endswith("/cancel") for _, path, _ in self.data["calls"]))
        self.assertNotIn(HEAD, self.data["statuses"])

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

    def test_missing_dispatch_id_or_api_failure_leaves_only_internal_error(self):
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
                self.assertNotIn(HEAD, self.data["statuses"])

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
