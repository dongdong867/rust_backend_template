"""Bounded advisory test reporting; never a source of authorization or status."""

import json
import math
import re
import subprocess
import sys


MARKER = "CI_COMMAND_REPORT_V1 "
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_LINE = 16 * 1024
MAX_COUNT = 10_000_000
STAGES = (None, "lint", "default tests", "api-doc tests", "package selection")
REASONS = (
    "Cancelled", "Timed out", "Worker failed", "Worker validation failed",
    "PR head changed", "Worker unavailable", "Dispatch failed", "Unsupported rerun",
)
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*")


def _command(value):
    return isinstance(value, str) and re.fullmatch(
        r"(?:lint|test|test-[A-Za-z0-9_][A-Za-z0-9_-]{0,63})", value
    ) is not None


def _name(value):
    return isinstance(value, str) and len(value) <= 200 and NAME.fullmatch(value) is not None


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def capture_make(args, mode):
    """Stream trusted argv without a shell; retain only bounded parsed metadata."""
    if mode not in ("default", "api-doc", "lint"):
        raise ValueError("Invalid mode.")
    totals = [0, 0, 0]
    observed = False
    unreliable = False
    pending_python = None
    names = []

    def add(values):
        nonlocal observed, unreliable
        if not all(_count(v) for v in values):
            unreliable = True
            return
        observed = True
        for index, value in enumerate(values):
            totals[index] += value
        if not all(_count(v) for v in totals):
            unreliable = True

    def line(text):
        nonlocal pending_python, unreliable
        text = text.rstrip("\r\n")
        rust = re.fullmatch(
            r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored(?:;.*)?",
            text,
        )
        if rust:
            values = rust.groups()
            if any(len(v) > 8 for v in values):
                unreliable = True
            else:
                add([int(v) for v in values])
        ran = re.fullmatch(r"Ran (\d+) tests? in .+", text)
        if ran:
            if pending_python is not None:
                unreliable = True
            pending_python = int(ran[1]) if len(ran[1]) <= 8 else MAX_COUNT + 1
        final = re.fullmatch(r"(OK|FAILED)(?: \(([^()]*)\))?", text)
        if final and pending_python is not None:
            fields = {}
            valid = True
            if final[2]:
                for part in final[2].split(", "):
                    item = re.fullmatch(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d{1,8})", part)
                    if not item or item[1] in fields:
                        valid = False
                        break
                    fields[item[1]] = int(item[2])
            failed = sum(fields.get(k, 0) for k in ("failures", "errors", "unexpected successes"))
            ignored = sum(fields.get(k, 0) for k in ("skipped", "expected failures"))
            if valid and (final[1] == "FAILED") == (failed > 0):
                add([pending_python - failed - ignored, failed, ignored])
            else:
                unreliable = True
            pending_python = None
        failure = re.fullmatch(r"test (\S+) \.\.\. FAILED", text)
        python = re.fullmatch(r"(?:FAIL|ERROR): \S+ \(([^()]+)\)", text)
        name = failure[1] if failure else python[1] if python else None
        if _name(name) and name not in names and len(names) < 20:
            names.append(name)

    process = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    # readline(size) bounds memory even for adversarial unterminated output.
    continuation = False
    try:
        while True:
            chunk = process.stdout.readline(MAX_LINE + 1)
            if not chunk:
                break
            sys.stdout.write(chunk)
            sys.stdout.flush()
            ends = chunk.endswith("\n")
            if not continuation and len(chunk.encode("utf-8")) <= MAX_LINE:
                line(chunk)
            elif not continuation:
                unreliable = True
            continuation = not ends
    finally:
        process.stdout.close()
    code = process.wait()
    counters = totals if observed and not unreliable and pending_python is None else [None]*3
    return dict(
        mode=mode, status="passed" if code == 0 else "failed",
        passed=counters[0], failed=counters[1], ignored=counters[2],
        failing_tests=names, exit_code=code,
    )


def emit_summary(command, runs, failed_stage=None):
    data = dict(version=1, command=command, runs=runs, failed_stage=failed_stage)
    encoded = json.dumps(data, separators=(",", ":"), allow_nan=False)
    if parse_summary(MARKER + encoded, command) is None:
        raise ValueError("Invalid summary.")
    print(MARKER + encoded)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key.")
        result[key] = value
    return result


def _validate(data, command):
    if not _command(command) or type(data) is not dict:
        return None
    if set(data) != {"version", "command", "runs", "failed_stage"}:
        return None
    if type(data["version"]) is not int or data["version"] != 1 or data["command"] != command:
        return None
    if data["failed_stage"] not in STAGES:
        return None
    runs = data["runs"]
    if type(runs) is not list or len(runs) > 2:
        return None
    seen = set()
    allowed = ("lint",) if command == "lint" else ("default", "api-doc")
    for run in runs:
        if type(run) is not dict or set(run) != {
            "mode", "status", "passed", "failed", "ignored", "failing_tests", "exit_code",
        }:
            return None
        if run["mode"] not in allowed or run["mode"] in seen:
            return None
        seen.add(run["mode"])
        code = run["exit_code"]
        if type(code) is not int or not -255 <= code <= 255:
            return None
        if run["status"] != ("passed" if code == 0 else "failed"):
            return None
        counts = [run[k] for k in ("passed", "failed", "ignored")]
        if not (all(v is None for v in counts) or all(_count(v) for v in counts)):
            return None
        names = run["failing_tests"]
        if type(names) is not list or len(names) > 20 or not all(_name(n) for n in names):
            return None
        if len(set(names)) != len(names):
            return None
        if code == 0 and (names or run["failed"] not in (None, 0)):
            return None
    return json.loads(json.dumps(data))


def parse_summary(log_text, expected_command):
    """Untrusted advisory input only; reject rather than echo malformed data."""
    try:
        if not isinstance(log_text, str) or len(log_text) > MAX_LOG_BYTES:
            return None
        if len(log_text.encode("utf-8")) > MAX_LOG_BYTES:
            return None
        position = log_text.rfind(MARKER)
        if position < 0:
            return None
        payload = log_text[position + len(MARKER):].split("\n", 1)[0]
        if len(payload.encode("utf-8")) + len(MARKER) > MAX_LINE:
            return None
        data = json.loads(payload, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        return _validate(data, expected_command)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return None


def render_comment(request, state, url, duration_seconds=None, summary=None, reason=None):
    """Render safe facts, never worker diagnostics; state is caller-authoritative."""
    command = request.get("command")
    sha = request.get("sha")
    if not _command(command) or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError("Invalid request.")
    if not isinstance(url, str) or not re.fullmatch(
        r"https://github\.com/[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+/actions/runs/[1-9][0-9]*", url
    ):
        raise ValueError("Invalid run URL.")
    if state not in ("success", "failure", "error"):
        raise ValueError("Invalid state.")
    duration = "Unavailable"
    if type(duration_seconds) in (int, float) and 0 <= duration_seconds <= 31_536_000 and math.isfinite(duration_seconds):
        seconds = int(duration_seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration = (f"{hours}h " if hours else "") + (f"{minutes}m " if minutes or hours else "") + f"{seconds}s"
    scope = "lint" if command == "lint" else "full workspace" if command == "test" else f"package `{command[5:]}`"
    lines = [
        f"### [{'Passed' if state == 'success' else 'Failed'}] `/{command}`",
        "", f"- Scope: {scope}", f"- Commit: `{sha}`", f"- Duration: {duration}",
        f"- Log: [GitHub Actions run]({url})", "- Coverage: Not collected",
    ]
    if state != "success":
        lines.append(f"- Reason: {reason if reason in REASONS else 'Worker failed'}")
    data = _validate(summary, command)
    if data and data["failed_stage"] is not None:
        lines.append(f"- Failed stage: {data['failed_stage']}")
    lines += ["", "| Mode | Result | Passed | Failed | Ignored |",
              "| --- | --- | --- | --- | --- |"]
    runs = {r["mode"]: r for r in data["runs"]} if data else {}
    modes = ("lint",) if command == "lint" else ("default", "api-doc")
    for mode in modes:
        run = runs.get(mode)
        not_run = mode == "api-doc" and command == "test" and runs.get("default", {}).get("status") == "failed"
        values = [str(run[k]) if run[k] is not None else "Unavailable" for k in ("passed", "failed", "ignored")] if run else ["Unavailable"]*3
        result = run["status"].capitalize() if run else "Not run" if not_run else "Unavailable"
        lines.append(f"| {mode} | {result} | {' | '.join(values)} |")
    lines += ["", "Counts represent test executions, not unique tests; feature modes are reported separately."]
    names = [name for run in runs.values() for name in run["failing_tests"]]
    if names:
        lines += ["", "Failing tests: " + ", ".join(f"`{name}`" for name in names)]
    if command.startswith("test-"):
        lines += ["", "Partial test run: this does not satisfy `/test`."]
    return "\n".join(lines)
