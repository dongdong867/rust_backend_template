"""Trusted command coordination. Never execute PR code with a writing token."""

import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import quote, urlsplit

import github_client


WORKFLOW = "ci-command-worker.yml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW}"
EXECUTE_JOB = "Run PR command"
# Equal to the execute job's timeout-minutes in the worker workflow.
EXECUTE_TIMEOUT_MINUTES = 30
REQUEST_FIELDS = ("pr", "command", "sha", "request", "controller", "base")
OUTCOME_REASONS = {
    "-": None, "h": "PR head changed", "c": "Cancelled", "t": "Timed out",
    "f": "Worker failed", "v": "Worker validation failed", "u": "Worker unavailable",
    "d": "Dispatch failed", "r": "Unsupported rerun", "?": "Reason unavailable",
}
RESULT_TEXT = {
    "pending": "Running the requested check.",
    "success": "Requested check passed.",
    "failure": "Requested check failed; see the worker run.",
}


def parse_command(text):
    if text in ("/lint", "/test"):
        return text[1:]
    if isinstance(text, str) and re.fullmatch(r"/test-[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", text):
        return text[1:]
    return None


def number(value):
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{0,19}", text):
        raise RuntimeError("Invalid GitHub identifier.")
    return text


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise RuntimeError("Invalid commit SHA.")
    return value


def validate_request(value):
    if set(value) != set(REQUEST_FIELDS) or parse_command("/" + value["command"]) is None:
        raise RuntimeError("Invalid command request record.")
    return {
        "pr": number(value["pr"]), "command": value["command"], "sha": sha(value["sha"]),
        "request": number(value["request"]), "controller": number(value["controller"]),
        "base": sha(value["base"]),
    }


def payload(request):
    return {key: request[key] for key in REQUEST_FIELDS}


def command_key(command):
    return hashlib.sha256(command.encode("ascii")).hexdigest()


def group(repo, command):
    # Preserve the existing writer group while serializing bookkeeping, cancellation and results.
    return f"ci-command-{repo}-{command_key(command)}"


def lock_group(repo, command):
    if parse_command("/" + command) is None:
        raise RuntimeError("Invalid command.")
    return group(repo, command)


def status_context(command):
    # Status contexts are case-insensitive; package selectors are exact, case-sensitive names.
    suffix = f"-{command_key(command)[:12]}" if command not in ("lint", "test") else ""
    return f"ci-command /{command}{suffix}"


def state_context(pr, command):
    return f"{github_client.COMMAND_STATE_PREFIX}{pr}/{command_key(command)}"


def run_title(request):
    return "ci-command " + " ".join(request[key] for key in REQUEST_FIELDS)


def parse_title(title):
    try:
        words = title.split(" ")
        if len(words) != 7 or words[0] != "ci-command":
            return None
        return validate_request(dict(zip(REQUEST_FIELDS, words[1:])))
    except (RuntimeError, TypeError, AttributeError):
        return None


def expected_error(code, method, path):
    # A missing collaborator/comment is not authorization.
    if code == 404 and method == "GET" and (
        "/collaborators/" in path or "/issues/comments/" in path
    ):
        return True, {}
    if code == 404 and method == "GET" and re.fullmatch(
        r"repos/[^/]+/[^/]+/actions/runs/[1-9][0-9]*", path
    ):
        return True, None
    if code == 409 and method == "POST" and path.endswith("/cancel"):
        # A bound run may have completed between reading its state and cancelling it.
        return True, None
    return False, None


def github_api(method, path, body=None):
    return github_client.request(method, path, body, expected_error)


def authorized(api, repo, login):
    if not isinstance(login, str) or not login:
        return False
    permission = api("GET", f"repos/{repo}/collaborators/{quote(login, safe='')}/permission")
    return permission.get("permission") in ("write", "maintain", "admin")


def unedited_comment(api, comment):
    """Created events may carry edited bodies; second-resolution timestamps are insufficient."""
    node_id = comment.get("node_id")
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 200:
        return False
    response = api("POST", "graphql", {
        "query": (
            "query($id: ID!) { node(id: $id) { ... on IssueComment { "
            "id databaseId body lastEditedAt } } }"
        ),
        "variables": {"id": node_id},
    })
    if not isinstance(response, dict) or response.get("errors") not in (None, []):
        return False
    data = response.get("data")
    node = data.get("node") if isinstance(data, dict) else None
    return (
        isinstance(node, dict)
        and node.get("id") == node_id
        and type(node.get("databaseId")) is int
        and str(node["databaseId"]) == str(comment.get("id"))
        and node.get("body") == comment.get("body")
        and "lastEditedAt" in node
        and node["lastEditedAt"] is None
    )


def current_comment(api, repo, route):
    comment = api("GET", f"repos/{repo}/issues/comments/{route['comment']}")
    login = comment.get("user", {}).get("login")
    return (
        str(comment.get("id")) == route["comment"]
        and login == route["author"]
        and parse_command(comment.get("body")) == route["command"]
        and urlsplit(comment.get("issue_url", "")).path == f"/repos/{repo}/issues/{route['pr']}"
        and authorized(api, repo, login)
        and unedited_comment(api, comment)
    )


def trusted_run(api, repo, run):
    if not isinstance(run, dict):
        return False
    metadata = api("GET", f"repos/{repo}")
    workflow = api("GET", f"repos/{repo}/actions/workflows/{WORKFLOW}")
    return (
        run.get("workflow_id") == workflow["id"]
        and run.get("path") == WORKFLOW_PATH
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == metadata["default_branch"]
        and run.get("repository", {}).get("full_name") == repo
    )


def route_event(api, repo, event_name, event):
    """Read-only preflight: rejected comments never enter the acceptance lock."""
    if event_name == "issue_comment":
        issue = event.get("issue", {})
        comment = event.get("comment", {})
        command = parse_command(comment.get("body"))
        if event.get("action") != "created" or "pull_request" not in issue or command is None:
            return None
        login = comment.get("user", {}).get("login")
        if not authorized(api, repo, login):
            return None
        pr = number(issue["number"])
        if api("GET", f"repos/{repo}/pulls/{pr}")["state"] != "open" or not unedited_comment(api, comment):
            return None
        return {
            "kind": "comment", "pr": pr, "command": command,
            "comment": number(comment["id"]), "author": login,
        }
    return None


def anchor_commit():
    # A stable ledger across PR head and default-branch updates, with no external state service.
    # Use only the full-history checkout of trusted default-branch code, never the PR checkout.
    roots = subprocess.check_output(["git", "rev-list", "--max-parents=0", "HEAD"], text=True).split()
    if len(roots) != 1:
        raise RuntimeError("Command state requires one root commit in the trusted repository history.")
    return sha(roots[0])


def actions_status(status):
    creator = status.get("creator", {})
    return creator.get("type") == "Bot" and creator.get("login") == "github-actions[bot]"


def outcome_code(state, reason):
    if state not in ("pending", "success", "failure", "error") or (
        (state in ("pending", "success")) != (reason is None)
    ):
        raise RuntimeError("Invalid command outcome.")
    for code, label in OUTCOME_REASONS.items():
        if label == reason:
            return code
    raise RuntimeError("Unknown command outcome reason.")


def state_records(api, repo, anchor, pr, command):
    context = state_context(pr, command)
    for status in github_client.pages(api, f"repos/{repo}/commits/{anchor}/statuses"):
        if status["context"] != context:
            continue
        try:
            fields = status["description"].split("|")
            if len(fields) == 5 and fields[0] == "v2":
                _, comment, controller, head, base = fields
                reason = "Reason unavailable" if status["state"] in ("failure", "error") else None
            elif len(fields) == 6 and fields[0] == "v3":
                _, comment, controller, head, base, code = fields
                reason = OUTCOME_REASONS[code]
            else:
                raise RuntimeError("Unknown state version.")
            if not actions_status(status):
                raise RuntimeError("Unknown state version or writer.")
            outcome_code(status["state"], reason)
            request = validate_request({
                "pr": pr, "command": command, "request": comment,
                "controller": controller, "sha": head, "base": base,
            })
            prefix = run_url(repo, "")
            if not status["target_url"].startswith(prefix):
                raise RuntimeError("Invalid worker URL.")
            owner = number(status["target_url"][len(prefix):])
            request["worker"] = None if owner == controller else owner
            yield request, status["state"], reason
        except (RuntimeError, ValueError, TypeError, KeyError):
            raise RuntimeError("Invalid durable command request record; refusing to overwrite it.") from None


def read_state(api, repo, anchor, pr, command):
    request, state, _ = next(state_records(api, repo, anchor, pr, command), (None, None, None))
    return request, state


def run_url(repo, run_id):
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server}/{repo}/actions/runs/{run_id}"


def write_state(api, repo, anchor, request, state, reason=None):
    # Internal ownership only, never a PR result. Keep v3 writes and v2/v3 readers compatible.
    # One reason code keeps even maximum-width IDs within GitHub's 140-character limit.
    code = outcome_code(state, reason)
    description = "|".join(["v3", *(request[k] for k in ("request", "controller", "sha", "base")), code])
    if len(description) > 140:
        raise RuntimeError("State description exceeds GitHub's limit.")
    api("POST", f"repos/{repo}/statuses/{anchor}", {
        "context": state_context(request["pr"], request["command"]),
        "state": state, "description": description,
        "target_url": run_url(repo, request["worker"] or request["controller"]),
    })


def write_result(api, repo, request, state, reason=None):
    """The PR-head result people read. Ownership stays in the root-commit ledger."""
    description = f"{reason}; post a new command." if state == "error" else RESULT_TEXT[state]
    api("POST", f"repos/{repo}/statuses/{request['sha']}", {
        "context": status_context(request["command"]), "state": state,
        "description": description,
        "target_url": run_url(repo, request["worker"] or request["controller"]),
    })


def cancel_obsolete(api, repo, anchor, request):
    """Cancel only previously bound owners for this PR/command, never concurrent other PRs."""
    seen = set()
    for previous, phase, _ in state_records(api, repo, anchor, request["pr"], request["command"]):
        owner = previous["worker"]
        if owner is None or owner in seen or int(previous["request"]) >= int(request["request"]):
            continue
        seen.add(owner)
        # Newest records are first. Completed owners need no lookup, even after run deletion.
        if phase in ("success", "failure"):
            continue
        run = api("GET", f"repos/{repo}/actions/runs/{owner}")
        if run is None:
            continue
        if parse_title(run.get("display_title")) != payload(previous) or not trusted_run(api, repo, run):
            raise RuntimeError("Cannot verify the recorded obsolete worker.")
        if run["status"] != "completed":
            api("POST", f"repos/{repo}/actions/runs/{owner}/cancel")


def accept_comment(api, repo, anchor, route, controller):
    """Accept and bind a request under the non-cancelling bookkeeping/cancellation lock."""
    previous, _ = read_state(api, repo, anchor, route["pr"], route["command"])
    if previous is not None and int(route["comment"]) <= int(previous["request"]):
        return None
    pr = api("GET", f"repos/{repo}/pulls/{route['pr']}")
    if pr["state"] != "open":
        return None
    default = api("GET", f"repos/{repo}")["default_branch"]
    base = api("GET", f"repos/{repo}/commits/{quote(default, safe='')}")["sha"]
    request = validate_request({
        "pr": route["pr"], "command": route["command"], "sha": pr["head"]["sha"],
        "request": route["comment"], "controller": controller, "base": base,
    })
    request["worker"] = None
    # Read-only preparation can take time. Validate permission and edit state last,
    # immediately before accepting the request, not before those lookups.
    if not current_comment(api, repo, route):
        return None
    # Persist acceptance before dispatch. Only trusted default-branch jobs write the ledger.
    write_state(api, repo, anchor, request, "pending")
    try:
        cancel_obsolete(api, repo, anchor, request)
        dispatched = api("POST", f"repos/{repo}/actions/workflows/{WORKFLOW}/dispatches", {
            "ref": default, "inputs": payload(request),
        })
        # API 2026-03-10 returns the exact created run. Never infer an owner by listing matching runs.
        request["worker"] = number(dispatched["workflow_run_id"])
        write_state(api, repo, anchor, request, "pending")
    except (RuntimeError, KeyError, TypeError):
        write_state(api, repo, anchor, request, "error", "Dispatch failed")
        write_result(api, repo, request, "error", "Dispatch failed")
        raise RuntimeError("Command worker dispatch failed.") from None
    # Publish after binding so the head status links to the run that owns it.
    write_result(api, repo, request, "pending")
    return request


def matching_run(api, repo, request, run):
    return (
        isinstance(run, dict)
        and str(run.get("id")) == request["worker"]
        and parse_title(run.get("display_title")) == payload(request)
        and run.get("head_sha") == request["base"]
        and trusted_run(api, repo, run)
        and run.get("run_attempt") == 1
    )


def check_worker(api, repo, anchor, inputs, run_id):
    request = validate_request(inputs)
    run_id = number(run_id)
    # Runner startup can beat the controller's registration POST becoming visible.
    for attempt in range(10):
        latest, state = read_state(api, repo, anchor, request["pr"], request["command"])
        if latest is None or payload(latest) != request or state != "pending":
            raise RuntimeError("Request was superseded or already completed; post a new command.")
        if latest["worker"] is not None:
            break
        if attempt == 9:
            raise RuntimeError("Worker ownership registration is unavailable; post a new command.")
        time.sleep(2)
    if latest["worker"] != run_id:
        raise RuntimeError("This dispatch is not the recorded request owner; post a new command.")
    run = api("GET", f"repos/{repo}/actions/runs/{number(run_id)}")
    if not matching_run(api, repo, latest, run):
        raise RuntimeError("Worker is not the accepted trusted default-branch workflow; post a new command.")
    pr = api("GET", f"repos/{repo}/pulls/{request['pr']}")
    if pr["state"] != "open" or pr["head"]["sha"] != request["sha"]:
        raise RuntimeError("PR is closed or its head changed; post a new command.")
    return latest


def timed_out(api, repo, run_id):
    """Actions may report a job timeout as cancelled or failed; its duration tells them apart."""
    try:
        jobs = github_client.pages(api, f"repos/{repo}/actions/runs/{run_id}/attempts/1/jobs", "jobs")
        job = next((job for job in jobs if job.get("name") == EXECUTE_JOB), None)
        if job is None:
            return False
        if job.get("conclusion") == "timed_out":
            return True
        start, end = (
            datetime.fromisoformat(job[key].replace("Z", "+00:00"))
            for key in ("started_at", "completed_at")
        )
    except (RuntimeError, KeyError, TypeError, AttributeError, ValueError):
        # Missing details keep the plain outcome; it is still nonpassing.
        return False
    return end - start >= timedelta(minutes=EXECUTE_TIMEOUT_MINUTES)


def outcome(api, repo, request, run_id, results):
    validate, execute_result = results.get("validate"), results.get("execute")
    if validate == "success" and execute_result == "success":
        return "success", None
    if validate == "success" and execute_result in ("cancelled", "failure") and timed_out(api, repo, run_id):
        state, reason = "error", "Timed out"
    elif "cancelled" in (validate, execute_result):
        state, reason = "error", "Cancelled"
    elif validate == "success" and execute_result == "failure":
        state, reason = "failure", "Worker failed"
    else:
        state, reason = "error", "Worker validation failed"
    pr = api("GET", f"repos/{repo}/pulls/{request['pr']}")
    if pr["head"]["sha"] != request["sha"]:
        state, reason = "error", "PR head changed"
    return state, reason


def owns_pending_head(api, repo, request, run_id):
    for status in github_client.pages(api, f"repos/{repo}/commits/{request['sha']}/statuses"):
        if status["context"] == status_context(request["command"]):
            return (
                actions_status(status) and status["state"] == "pending"
                and status["target_url"] == run_url(repo, run_id)
            )
    return False


def report_worker(api, repo, anchor, inputs, run_id, results, lock):
    """Publish a bound worker's result under the controller lock; others write nothing."""
    request = validate_request(inputs)
    run_id = number(run_id)
    if lock != group(repo, request["command"]):
        raise RuntimeError("Report lock is unavailable; post a new command.")
    records = state_records(api, repo, anchor, request["pr"], request["command"])
    latest, phase, _ = next(records, (None, None, None))

    def bound(record):
        return record is not None and payload(record) == request and record["worker"] == run_id

    owner = latest if bound(latest) else next((r for r, _, _ in records if bound(r)), None)
    # The live run's attempt rejects re-run jobs, including a re-run of this report job.
    if owner is None or not matching_run(
        api, repo, owner, api("GET", f"repos/{repo}/actions/runs/{run_id}")
    ):
        return None
    current = owner is latest and phase == "pending"
    # A superseded run may close only its own head status, never a newer request's.
    if not current and not owns_pending_head(api, repo, owner, run_id):
        return None
    state, reason = outcome(api, repo, owner, run_id, results)
    write_result(api, repo, owner, state, reason)
    if current:
        # Write the public result first. If this write fails, the request stays pending
        # until a new command replaces it.
        write_state(api, repo, anchor, owner, state, reason)
    return state


def execute(command):
    """Used only by the read-only worker, after every privileged step has finished."""
    if parse_command("/" + command) is None:
        raise RuntimeError("Invalid command.")
    if command == "lint":
        commands = [["make", "lint"]]
    elif command == "test":
        commands = [["make", "test", f"FEATURES={feature}"] for feature in ("", "api-doc")]
    else:
        crate = command[len("test-"):]
        metadata = json.loads(subprocess.check_output(
            ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"], text=True
        ))
        package = next(
            (p for p in metadata["packages"]
             if p["id"] in metadata["workspace_members"] and p["name"] == crate), None
        )
        if package is None:
            raise RuntimeError("Unknown workspace package; partial tests never fall back to full tests.")
        features = [""] + ([f"{crate}/api-doc"] if "api-doc" in package["features"] else [])
        commands = [
            ["make", "test-crate", f"CRATE={crate}", f"FEATURES={feature}"] for feature in features
        ]
    for args in commands:
        subprocess.run(args, check=True)


def output(values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("route", "control", "lock", "worker-check", "execute", "report"))
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("Invalid repository identity.")
    if args.stage == "route":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        route = route_event(github_api, repo, os.environ["GITHUB_EVENT_NAME"], event)
        if route is not None:
            output({
                "enabled": "true", "group": group(repo, route["command"]),
                "route": json.dumps(route, separators=(",", ":")),
            })
    elif args.stage == "control":
        route = json.loads(os.environ["CONTROL_ROUTE"])
        anchor = anchor_commit()
        if route.get("kind") != "comment":
            raise RuntimeError("Invalid controller route.")
        accept_comment(github_api, repo, anchor, route, number(os.environ["GITHUB_RUN_ID"]))
    elif args.stage == "lock":
        output({"group": lock_group(repo, os.environ["COMMAND"])})
    elif args.stage == "worker-check":
        inputs = json.loads(os.environ["COMMAND_INPUTS"])
        check_worker(github_api, repo, anchor_commit(), inputs, os.environ["GITHUB_RUN_ID"])
    elif args.stage == "report":
        inputs = json.loads(os.environ["COMMAND_INPUTS"])
        results = {"validate": os.environ["VALIDATE_RESULT"], "execute": os.environ["EXECUTE_RESULT"]}
        report_worker(
            github_api, repo, anchor_commit(), inputs, os.environ["GITHUB_RUN_ID"], results,
            os.environ["LOCK_GROUP"],
        )
    else:
        execute(os.environ["COMMAND"])


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        # These messages are fixed by trusted code, never API bodies or workspace metadata.
        print(f"CI command: {error}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError, TypeError, OSError, subprocess.CalledProcessError):
        print("CI command failed; inspect the failed step and post a new command.", file=sys.stderr)
        sys.exit(1)
