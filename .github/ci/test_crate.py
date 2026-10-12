"""Run tests for one exact workspace package, without shell evaluation."""

import argparse
import json
import os
import re
import subprocess
import sys


def validate_crate(crate):
    """Restrict selectors to bounded ASCII package names, never Cargo patterns."""
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{0,63}", crate):
        raise ValueError(
            "CRATE is required: use an exact workspace package name "
            "(1-64 ASCII letters, digits, underscores or hyphens; "
            "no leading hyphen)."
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ci", action="store_true",
        help="run default tests, then api-doc tests when the package declares it",
    )
    options = parser.parse_args(argv)
    try:
        crate = os.environ.get("CRATE", "")
        validate_crate(crate)
        result = subprocess.run(
            ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"],
            check=True, capture_output=True, text=True,
        )
        metadata = json.loads(result.stdout)
        members = set(metadata["workspace_members"])
        package = next(
            (package for package in metadata["packages"]
             if package["id"] in members and package["name"] == crate),
            None,
        )
        if package is None:
            raise ValueError("CRATE must exactly name a Cargo workspace member.")

        command = ["cargo", "test", "--locked", "-p", crate]
        if options.ci:
            subprocess.run(command, check=True)
            if "api-doc" in package["features"]:
                subprocess.run(
                    [*command, "--features", f"{crate}/api-doc"], check=True,
                )
        else:
            features = ",".join(
                item for item in re.split(r"[,\s]+", os.environ.get("FEATURES", ""))
                if item
            )
            subprocess.run(
                [*command, "--features", features] if features else command,
                check=True,
            )
        return 0
    except subprocess.CalledProcessError as error:
        # Captured metadata diagnostics can contain paths or private configuration.
        print("test-crate: Cargo command failed.", file=sys.stderr)
        return error.returncode if error.returncode > 0 else 1
    except (ValueError, KeyError, TypeError, OSError) as error:
        # Do not echo malformed metadata, environment values or OS error details.
        message = str(error) if isinstance(error, ValueError) and not isinstance(
            error, json.JSONDecodeError
        ) else "Unable to read Cargo workspace metadata or execute Cargo."
        print(f"test-crate: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
