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


# A newer push cancels main validation; this run lints and tests the commit itself.
PASSING = ("success", "neutral", "skipped", "cancelled")


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


def pages(api, path, key):
    page = 1
    while True:
        items = api("GET", f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")[key]
        yield from items
        if len(items) < 100:
            break
        page += 1


def tag_commit(api, repo, tag):
    ref = api("GET", f"repos/{repo}/git/ref/tags/{quote(tag, safe='')}")
    return None if ref is None else ref["object"]["sha"]


def require_tag_absent(api, repo, tag):
    if tag_commit(api, repo, tag) is not None:
        raise RuntimeError(f"Tag {tag} already exists; choose a new version.")


def resolve_commit(api, repo, commit):
    if commit == "":
        return parse_sha(api("GET", f"repos/{repo}/commits/main")["sha"])
    sha = parse_sha(commit)
    # compare/{commit}...main is identical or ahead only when main contains the commit.
    if api("GET", f"repos/{repo}/compare/{sha}...main")["status"] not in ("identical", "ahead"):
        raise RuntimeError("Commit is not on main.")
    return sha


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


def release_suites(api, repo, sha, run_id):
    """Check suites of every release run on the commit, including this one.

    Dispatched runs report on the main head, so an earlier failed release would otherwise
    block every later release of that commit.
    """
    run = api("GET", f"repos/{repo}/actions/runs/{run_id}")
    runs = pages(api, f"repos/{repo}/actions/workflows/{run['workflow_id']}/runs?head_sha={sha}",
                 "workflow_runs")
    return {run["check_suite_id"]} | {other["check_suite_id"] for other in runs}


def require_passing_checks(api, repo, sha, run_id):
    """Every existing check and status on the commit must pass, except release runs' own jobs."""
    ignored = release_suites(api, repo, sha, run_id)
    failing = []
    for run in pages(api, f"repos/{repo}/commits/{sha}/check-runs", "check_runs"):
        if run["check_suite"]["id"] in ignored:
            continue
        if run["status"] != "completed" or run["conclusion"] not in PASSING:
            failing.append(run["name"])
    for status in pages(api, f"repos/{repo}/commits/{sha}/status", "statuses"):
        if status["state"] != "success":
            failing.append(status["context"])
    if failing:
        raise RuntimeError(
            "Checks have not passed on the commit: " + ", ".join(sorted(set(failing)))
            + ". Wait for running checks or fix failures, then run the release again."
        )


def verify(api, repo, inputs, run_id, ref, read_metadata):
    """Read-only checks; any failure stops the run before a tag can exist."""
    if ref != "refs/heads/main":
        raise RuntimeError("Run the release workflow from main.")
    version = parse_version(inputs["version"])
    tag = f"v{version}"
    sha = resolve_commit(api, repo, inputs["commit"])
    found = service_version(read_metadata(sha))
    if found != version:
        raise RuntimeError(f"Version {version} does not match the service package version {found}.")
    require_tag_absent(api, repo, tag)
    require_passing_checks(api, repo, sha, run_id)
    return {"sha": sha, "tag": tag}


def publish(api, repo, version, sha):
    tag = f"v{parse_version(version)}"
    sha = parse_sha(sha)
    require_tag_absent(api, repo, tag)
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
    parser.add_argument("stage", choices=("resolve", "verify", "publish"))
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise RuntimeError("Invalid repository identity.")
    if args.stage == "resolve":
        sha = resolve_commit(github_api, repo, os.environ["COMMIT"])
        output = {"sha": sha}
    elif args.stage == "verify":
        inputs = {"version": os.environ["VERSION"], "commit": os.environ["SHA"]}
        output = verify(
            github_api, repo, inputs, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_REF"],
            checkout_metadata(Path(os.environ["RELEASE_PATH"])),
        )
    else:
        publish(github_api, repo, os.environ["VERSION"], os.environ["SHA"])
        print(f"Released v{os.environ['VERSION']} at {os.environ['SHA']}.")
        return
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in output.items():
            stream.write(f"{key}={value}\n")


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
