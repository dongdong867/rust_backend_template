"""Verify and publish a hand-started release. Only the publish stage writes."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

import github_client


# Any cancelled check run passes; this run repeats only lint, fast tests and database tests.
PASSING = ("success", "neutral", "skipped", "cancelled")
# These runs report on their own push or PR head. Manual, comment and scheduled runs
# report on the main head at start time, whatever they test.
COMMIT_EVENTS = ("push", "pull_request")


def parse_version(value):
    # Plain X.Y.Z only: no prefix, pre-release or build metadata, no leading zeros.
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,8})(\.(0|[1-9][0-9]{0,8})){2}", value):
        raise RuntimeError("Version must be X.Y.Z, for example 1.2.3.")
    return value


def parse_sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{40}", value):
        raise RuntimeError("Commit must be a full 40-character lowercase SHA.")
    return value


def expected_error(code, method, path):
    # A missing tag reference is the expected answer before publication.
    if code == 404 and method == "GET" and "/git/ref/tags/" in path:
        return True, None
    return False, None


def github_api(method, path, body=None):
    return github_client.request(method, path, body, expected_error)


def tag_commit(api, repo, tag):
    ref = api("GET", f"repos/{repo}/git/ref/tags/{quote(tag, safe='')}")
    return None if ref is None else ref["object"]["sha"]


def require_tag_absent(api, repo, tag):
    if tag_commit(api, repo, tag) is not None:
        raise RuntimeError(f"Tag {tag} already exists; choose a new version.")


def require_main_head(api, repo, sha):
    # GITHUB_TOKEN cannot release a commit that differs from main in workflow files.
    if api("GET", f"repos/{repo}/commits/main")["sha"] != sha:
        raise RuntimeError("main has moved past this run's commit; start the release again.")


def service_version(metadata):
    """The service is the one workspace package that builds a binary; internal crates are libraries."""
    members = set(metadata["workspace_members"])
    services = [
        package for package in metadata["packages"]
        if package["id"] in members
        and any("bin" in target["kind"] for target in package["targets"])
    ]
    if len(services) != 1:
        raise RuntimeError("Expected exactly one workspace package with a binary target.")
    return services[0]["version"]


def passed(item):
    return item["status"] == "completed" and item["conclusion"] in PASSING


def require_passing_checks(api, repo, sha, run_id):
    """Every run, check and status about the commit must pass."""
    own = api("GET", f"repos/{repo}/actions/runs/{run_id}")["check_suite_id"]
    runs = list(github_client.pages(api, f"repos/{repo}/actions/runs?head_sha={sha}", "workflow_runs"))
    ignored = {own} | {run["check_suite_id"] for run in runs if run["event"] not in COMMIT_EVENTS}
    # A run that fails to start, or has not created its first job, has no check runs to read.
    failing = [run["name"] for run in runs
               if run["check_suite_id"] not in ignored and not passed(run)]
    for check in github_client.pages(api, f"repos/{repo}/commits/{sha}/check-runs", "check_runs"):
        if check["check_suite"]["id"] not in ignored and not passed(check):
            failing.append(check["name"])
    for status in github_client.pages(api, f"repos/{repo}/commits/{sha}/status", "statuses"):
        if status["state"] != "success" and not status["context"].startswith(github_client.COMMAND_STATE_PREFIX):
            failing.append(status["context"])
    if failing:
        raise RuntimeError(
            "Checks have not passed on the commit: " + ", ".join(sorted(set(failing)))
            + ". Wait for running checks or fix failures, then run the release again."
        )


def verify(api, repo, version, sha, run_id, ref, read_metadata):
    """Read-only checks; any failure stops the run before a tag can exist."""
    if ref != "refs/heads/main":
        raise RuntimeError("Run the release workflow from main.")
    version = parse_version(version)
    sha = parse_sha(sha)
    require_main_head(api, repo, sha)
    found = service_version(read_metadata(sha))
    if found != version:
        raise RuntimeError(f"Version {version} does not match the service package version {found}.")
    require_tag_absent(api, repo, f"v{version}")
    require_passing_checks(api, repo, sha, run_id)


def publish(api, repo, version, sha):
    tag = f"v{parse_version(version)}"
    sha = parse_sha(sha)
    require_tag_absent(api, repo, tag)
    require_main_head(api, repo, sha)
    api("POST", f"repos/{repo}/releases", {
        "tag_name": tag, "target_commitish": sha, "name": tag, "generate_release_notes": True,
    })
    if tag_commit(api, repo, tag) != sha:
        raise RuntimeError(f"Tag {tag} does not point to the verified commit; inspect the release.")


def checkout_metadata(path):
    def read(sha):
        if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip() != sha:
            raise RuntimeError("The release checkout is not the verified commit.")
        return json.loads(subprocess.check_output(
            ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"], cwd=path, text=True
        ))
    return read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("verify", "publish"))
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("Invalid repository identity.")
    version, sha = os.environ["VERSION"], os.environ["GITHUB_SHA"]
    if args.stage == "verify":
        verify(
            github_api, repo, version, sha, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_REF"],
            checkout_metadata(Path.cwd()),
        )
    else:
        publish(github_api, repo, version, sha)
        print(f"Released v{version} at {sha}.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        # Messages contain only validated values and trusted check names, never API bodies.
        print(f"Release: {error}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError, TypeError, OSError, subprocess.CalledProcessError):
        print("Release failed; inspect the failed step.", file=sys.stderr)
        sys.exit(1)
