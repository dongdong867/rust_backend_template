"""Trusted command coordination. Never execute PR code with the controller token."""

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


WORKFLOW = "ci-command-worker.yml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW}"
REQUEST_FIELDS = ("pr", "command", "sha", "request", "controller", "base")


def sibling(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report = sibling("command_report")
job_logs = sibling("github_log")


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
    # Commit statuses are shared by every PR with that SHA. One command writer owns all of them.
    return f"ci-command-{repo}-{command_key(command)}"


def status_context(command):
    # Status contexts are case-insensitive; package selectors are exact, case-sensitive names.
    suffix = f"-{command_key(command)[:12]}" if command not in ("lint", "test") else ""
    return f"ci-comment /{command}{suffix}"


def state_context(pr, command):
    return f"ci-command-state/{pr}/{command_key(command)}"


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


def github_api(method, path, body=None):
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("The GitHub job token is required.")
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    request = Request(
        f"{base}/{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "Content-Type": "application/json", "X-GitHub-Api-Version": "2026-03-10",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            content = response.read()
            return json.loads(content) if content else None
    except HTTPError as error:
        # A missing collaborator/comment is not authorization. Never echo API error bodies.
        if error.code == 404 and method == "GET" and (
            "/collaborators/" in path or "/issues/comments/" in path
        ):
            return {}
        if error.code == 404 and method == "GET" and re.fullmatch(
            r"repos/[^/]+/[^/]+/actions/runs/[1-9][0-9]*", path
        ):
            return None
        if error.code == 409 and method == "POST" and path.endswith("/cancel"):
            # A bound run may have completed between reading its state and cancelling it.
            return None
        raise RuntimeError(f"GitHub API request failed (HTTP {error.code}).") from None
    except (URLError, OSError, ValueError):
        raise RuntimeError("GitHub API request failed.") from None


def pages(api, path, key=None):
    page = 1
    while True:
        separator = "&" if "?" in path else "?"
        response = api("GET", f"{path}{separator}per_page=100&page={page}")
        items = response[key] if key else response
        yield from items
        if len(items) < 100:
            break
        page += 1


def authorized(api, repo, login):
    if not isinstance(login, str) or not login:
        return False
    permission = api("GET", f"repos/{repo}/collaborators/{quote(login, safe='')}/permission")
    return permission.get("permission") in ("write", "maintain", "admin")


def current_comment(api, repo, route):
    comment = api("GET", f"repos/{repo}/issues/comments/{route['comment']}")
    login = comment.get("user", {}).get("login")
    return (
        str(comment.get("id")) == route["comment"]
        and login == route["author"]
        and parse_command(comment.get("body")) == route["command"]
        and urlsplit(comment.get("issue_url", "")).path == f"/repos/{repo}/issues/{route['pr']}"
        and authorized(api, repo, login)
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
    """Read-only preflight: rejected comments never enter either cancellation group."""
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
        if api("GET", f"repos/{repo}/pulls/{pr}")["state"] != "open":
            return None
        return {
            "kind": "comment", "pr": pr, "command": command,
            "comment": number(comment["id"]), "author": login,
        }
    if event_name == "workflow_run" and event.get("action") == "completed":
        run_id = number(event["workflow_run"]["id"])
        run = api("GET", f"repos/{repo}/actions/runs/{run_id}")
        if run is None:
            return None
        request = parse_title(run.get("display_title"))
        if request is not None and run.get("status") == "completed" and trusted_run(api, repo, run):
            return {
                "kind": "completion", "pr": request["pr"],
                "command": request["command"], "run": run_id,
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


def state_records(api, repo, anchor, pr, command):
    context = state_context(pr, command)
    for status in pages(api, f"repos/{repo}/commits/{anchor}/statuses"):
        if status["context"] != context:
            continue
        try:
            version, comment, controller, head, base = status["description"].split("|")
            if version != "v2" or not actions_status(status):
                raise RuntimeError("Unknown state version or writer.")
            request = validate_request({
                "pr": pr, "command": command, "request": comment,
                "controller": controller, "sha": head, "base": base,
            })
            prefix = run_url(repo, "")
            if not status["target_url"].startswith(prefix):
                raise RuntimeError("Invalid worker URL.")
            owner = number(status["target_url"][len(prefix):])
            request["worker"] = None if owner == controller else owner
            yield request, status["state"]
        except (RuntimeError, ValueError, TypeError, KeyError):
            raise RuntimeError("Invalid durable command request record; refusing to overwrite it.") from None


def read_state(api, repo, anchor, pr, command):
    return next(state_records(api, repo, anchor, pr, command), (None, None))


def run_url(repo, run_id):
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server}/{repo}/actions/runs/{run_id}"


def write_state(api, repo, anchor, request, state):
    # The description fits GitHub's 140-character limit. The URL binds the dispatch's actual ID.
    description = "|".join(["v2", *(request[k] for k in ("request", "controller", "sha", "base"))])
    api("POST", f"repos/{repo}/statuses/{anchor}", {
        "context": state_context(request["pr"], request["command"]),
        "state": state, "description": description,
        "target_url": run_url(repo, request["worker"] or request["controller"]),
    })


def write_result(api, repo, request, state, description, target):
    # Called only inside the repository/command lock. Monotonic ownership is SHA-wide, not PR-wide.
    for previous in pages(api, f"repos/{repo}/commits/{request['sha']}/statuses"):
        if previous["context"] != status_context(request["command"]):
            continue
        try:
            version, owner, pr, _ = previous["description"].split("|", 3)
            if version != "v1" or not actions_status(previous):
                raise RuntimeError("Unknown result writer.")
            number(pr)
            if int(number(owner)) > int(request["request"]):
                return False
            if owner == request["request"] and pr != request["pr"]:
                raise RuntimeError("Conflicting result owner.")
        except (RuntimeError, KeyError, ValueError, TypeError):
            raise RuntimeError("Invalid commit status owner record; refusing to overwrite it.") from None
        break
    description = f"v1|{request['request']}|{request['pr']}|{description}"
    if len(description) > 140:
        raise RuntimeError("Status description exceeds GitHub's limit.")
    api("POST", f"repos/{repo}/statuses/{request['sha']}", {
        "context": status_context(request["command"]), "state": state,
        "description": description, "target_url": target,
    })
    return True


def cancel_obsolete(api, repo, anchor, request):
    """Cancel only previously bound owners for this PR/command, never concurrent other PRs."""
    seen = set()
    for previous, phase in state_records(api, repo, anchor, request["pr"], request["command"]):
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
    """Must run under the same non-cancelling lock as every completion/status write."""
    if not current_comment(api, repo, route):
        return None
    previous, _ = read_state(api, repo, anchor, route["pr"], route["command"])
    if previous is not None and int(route["comment"]) <= int(previous["request"]):
        reconcile(api, repo, anchor, previous)
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
    target = run_url(repo, controller)
    # Persist acceptance before publishing pending. No worker has permission to write either.
    write_state(api, repo, anchor, request, "pending")
    write_result(api, repo, request, "pending", "Maintainer-requested check is pending.", target)
    try:
        cancel_obsolete(api, repo, anchor, request)
        dispatched = api("POST", f"repos/{repo}/actions/workflows/{WORKFLOW}/dispatches", {
            "ref": default, "inputs": payload(request),
        })
        # API 2026-03-10 returns the exact created run. Never infer an owner by listing matching runs.
        request["worker"] = number(dispatched["workflow_run_id"])
        write_state(api, repo, anchor, request, "pending")
        write_result(
            api, repo, request, "pending", "Maintainer-requested check is pending.",
            run_url(repo, request["worker"]),
        )
    except (RuntimeError, KeyError, TypeError):
        write_result(api, repo, request, "error", "Worker dispatch failed; post a new command.", target)
        write_state(api, repo, anchor, request, "error")
        raise RuntimeError("Command worker dispatch failed.") from None
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


def result_comment(api, repo, anchor, request, state, run, reason=None):
    """Latest per-PR result only; commit statuses remain the independently verified merge gate."""
    latest, _ = read_state(api, repo, anchor, request["pr"], request["command"])
    if latest != request or request["worker"] is None:
        return
    marker_prefix = f"<!-- ci-command-result:{command_key(request['command'])};"
    marker = f"{marker_prefix}request={request['request']};worker={request['worker']} -->"
    existing = []
    for comment in pages(api, f"repos/{repo}/issues/{request['pr']}/comments"):
        author = comment.get("user", {})
        if author.get("login") != "github-actions[bot]" or author.get("type") != "Bot":
            continue
        body = comment.get("body", "")
        match = re.search(
            re.escape(marker_prefix) + r"request=([1-9][0-9]{0,19});worker=([1-9][0-9]{0,19}) -->$",
            body,
        )
        if match:
            if int(match[1]) > int(request["request"]):
                return
            existing.append(comment)
    duration = None
    summary = None
    if isinstance(run, dict):
        try:
            start = datetime.fromisoformat(run["run_started_at"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00"))
            duration = (end - start).total_seconds()
        except (KeyError, ValueError, TypeError):
            pass
        if matching_run(api, repo, request, run) and run["status"] == "completed":
            jobs = list(pages(
                api, f"repos/{repo}/actions/runs/{request['worker']}/attempts/1/jobs", "jobs"
            ))
            execution = next((job for job in jobs if job["name"] == "Run PR command"), None)
            if execution is not None and execution.get("id") is not None:
                text = job_logs.read_job_log(repo, number(execution["id"]))
                summary = report.parse_summary(text, request["command"])
    if reason is None and isinstance(run, dict):
        reason = {
            "cancelled": "Cancelled", "timed_out": "Timed out", "failure": "Worker failed",
        }.get(run.get("conclusion"))
        if run.get("run_attempt") != 1:
            reason = "Unsupported rerun"
    body = report.render_comment(
        request, state, run_url(repo, request["worker"]),
        duration_seconds=duration, summary=summary, reason=reason,
    ) + "\n\n" + marker
    # Numeric metadata and validated names are advisory only, never a reason to change `state`.
    selected = max(existing, key=lambda item: int(item["id"])) if existing else None
    if selected is not None:
        if selected["body"] != body:
            api("PATCH", f"repos/{repo}/issues/comments/{number(selected['id'])}", {"body": body})
    else:
        api("POST", f"repos/{repo}/issues/{request['pr']}/comments", {"body": body})


def reconcile(api, repo, anchor, request, comments=False):
    """Recompute the latest accepted request, even if Actions replaced a pending event."""
    latest, state = read_state(api, repo, anchor, request["pr"], request["command"])
    if latest != request:
        return
    if request["worker"] is None:
        return
    run = api("GET", f"repos/{repo}/actions/runs/{request['worker']}")
    if state == "error":
        if comments:
            result_comment(api, repo, anchor, request, state, run)
        return
    if run is None:
        if state == "pending":
            write_result(
                api, repo, request, "error", "Worker result unavailable; post a new command.",
                run_url(repo, request["worker"]),
            )
            write_state(api, repo, anchor, request, "error")
            state = "error"
        if comments:
            result_comment(api, repo, anchor, request, state, None, "Worker unavailable")
        return
    # Initial-attempt completions stay idempotent, but an unsupported rerun invalidates a green owner.
    if state != "pending" and run["run_attempt"] == 1:
        if comments:
            result_comment(api, repo, anchor, request, state, run)
        return
    if run["status"] != "completed" and run["run_attempt"] == 1:
        return
    target = run_url(repo, request["worker"])
    pr = api("GET", f"repos/{repo}/pulls/{request['pr']}")
    conclusion = run["conclusion"]
    state = "failure" if conclusion == "failure" else "error"
    description = "Check did not pass; see the worker run."
    if pr["state"] != "open" or pr["head"]["sha"] != request["sha"]:
        description = "PR closed or head changed; post a new command for its current head."
        state = "error"
    elif not matching_run(api, repo, request, run):
        # Mutable dispatch refs or manual reruns are errors on the bound owner, not missing candidates.
        description = "Worker identity or workflow commit changed; post a new command."
        state = "error"
    elif conclusion == "success":
        jobs = list(pages(
            api, f"repos/{repo}/actions/runs/{run['id']}/attempts/1/jobs", "jobs"
        ))
        required = {"Validate accepted request", "Run PR command"}
        if {job["name"] for job in jobs} == required and all(
            job["conclusion"] == "success" for job in jobs
        ):
            state = "success"
            description = "Full tests passed." if request["command"] == "test" else "Requested check passed."
    # A newer request on any PR with this SHA owns its result; this PR's cursor still closes.
    write_result(api, repo, request, state, description, target)
    write_state(api, repo, anchor, request, state)
    if comments:
        reason = "PR head changed" if pr["state"] != "open" or pr["head"]["sha"] != request["sha"] else None
        result_comment(api, repo, anchor, request, state, run, reason)


def complete_worker(api, repo, anchor, route):
    if route is None:
        return
    request, _ = read_state(api, repo, anchor, route["pr"], route["command"])
    if request is not None:
        reconcile(api, repo, anchor, request, comments=True)


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


def execute(command):
    """Used only by the read-only worker, after every privileged step has finished."""
    if parse_command("/" + command) is None:
        raise RuntimeError("Invalid command.")
    runs = []
    stage = "package selection" if command.startswith("test-") else "lint" if command == "lint" else "default tests"
    try:
        if command == "lint":
            commands = [(["make", "lint"], "lint")]
        elif command == "test":
            commands = [(["make", "test", f"FEATURES={feature}"], "api-doc" if feature else "default")
                        for feature in ("", "api-doc")]
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
                (["make", "test-crate", f"CRATE={crate}", f"FEATURES={feature}"],
                 "api-doc" if feature else "default") for feature in features
            ]
        for args, mode in commands:
            stage = "lint" if mode == "lint" else "api-doc tests" if mode == "api-doc" else "default tests"
            result = report.capture_make(args, mode)
            runs.append(result)
            if result["exit_code"]:
                raise subprocess.CalledProcessError(result["exit_code"], args)
        stage = None
    finally:
        report.emit_summary(command, runs, stage)


def output(values):
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("route", "control", "worker-check", "execute"))
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
        if route["kind"] == "comment":
            accept_comment(github_api, repo, anchor, route, number(os.environ["GITHUB_RUN_ID"]))
        else:
            complete_worker(github_api, repo, anchor, route)
    elif args.stage == "worker-check":
        inputs = json.loads(os.environ["COMMAND_INPUTS"])
        check_worker(github_api, repo, anchor_commit(), inputs, os.environ["GITHUB_RUN_ID"])
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
