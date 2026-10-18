"""Validate original workflows with one narrow actionlint 1.7.12 exception."""

import json
from pathlib import Path
import re
import subprocess
import sys


CONTROLLER = ".github/workflows/ci-command.yml"
MESSAGE = 'unexpected key "queue" for "concurrency" section. expected one of "cancel-in-progress", "group"'
MAPPING = [
    "    concurrency:",
    "      group: ci-command-control-${{ needs.route.outputs.group }}",
    "      cancel-in-progress: false",
    "      queue: max",
]


def permitted_line(root):
    """Recognize only the canonical block directly inside jobs.control."""
    lines = (root / CONTROLLER).read_text().splitlines()
    sites = []
    job = None
    section = None
    for index, line in enumerate(lines):
        if re.match(r"^\S", line):
            section = line
            job = None
        if re.match(r"^  \S", line):
            job = line
        if section == "jobs:" and job == "  control:" and lines[index:index + 4] == MAPPING:
            sites.append(index + 4)
    # Extra queue keys cannot be validated by this narrow compatibility rule.
    # Actionlint still checks all YAML, including quoted/inline duplicate keys.
    if len(sites) != 1 or lines.count(MAPPING[-1]) != 1:
        return None
    return sites[0]


def check(root):
    root = Path(root).resolve()
    workflows = sorted(root.glob(".github/workflows/*.yml"))
    try:
        if not workflows:
            raise ValueError("no workflow files found")
        result = subprocess.run(
            [str(root / ".tools/bin/actionlint"), "-format", "{{json .}}",
             "-shellcheck=", "-pyflakes=",
             *(str(path.relative_to(root)) for path in workflows)],
            cwd=root, capture_output=True, text=True, check=False,
        )
        if result.returncode not in (0, 1) or result.stderr.strip():
            raise ValueError(f"actionlint failed (exit {result.returncode}): {result.stderr}")
        errors = json.loads(result.stdout)
        if not isinstance(errors, list):
            raise ValueError("actionlint output is not a diagnostic array")
        for error in errors:
            if not isinstance(error, dict) or any(
                not isinstance(error.get(key), str)
                for key in ("message", "filepath", "kind", "snippet")
            ) or any(
                type(error.get(key)) is not int or error[key] < 0
                for key in ("line", "column", "end_column")
            ):
                raise ValueError("unrecognized actionlint diagnostic")
        if (result.returncode == 0) != (len(errors) == 0):
            raise ValueError("actionlint exit code disagrees with diagnostic output")
        line = permitted_line(root) if errors else None
        permitted = [
            error for error in errors
            if error["message"] == MESSAGE and error["kind"] == "syntax-check"
            and error["filepath"] == CONTROLLER and error["line"] == line
            and error["column"] == 7
        ]
        remaining = errors if len(permitted) != 1 else [
            error for error in errors if error is not permitted[0]
        ]
        if remaining:
            for error in remaining:
                print(f'{error["filepath"]}:{error["line"]}:{error["column"]}: '
                      f'{error["message"]} [{error["kind"]}]', file=sys.stderr)
            return 1
        print("CI configuration passed: compatibility validation covers queue:max; "
              "actionlint covers all remaining syntax."
              if permitted else
              "CI configuration passed: actionlint natively accepts all syntax; "
              "queue:max compatibility suppression was not needed.")
        return 0
    except (OSError, ValueError) as error:
        print(f"CI configuration check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(check(Path(__file__).resolve().parents[1]))
