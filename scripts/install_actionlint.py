#!/usr/bin/env python3
"""Install one pinned upstream binary, verifying its published archive checksum."""

import argparse
import hashlib
import io
import os
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import tempfile
import urllib.request


ROOT = Path(__file__).resolve().parent.parent


def archive_name(version, system, machine):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("actionlint requires an exact numeric version")
    systems = {"Darwin": "darwin", "Linux": "linux"}
    machines = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}
    if system not in systems or machine not in machines:
        raise ValueError(f"Unsupported actionlint platform: {system} {machine}")
    return f"actionlint_{version}_{systems[system]}_{machines[machine]}.tar.gz"


def verified_binary(data, checksums, name):
    entries = [line.split() for line in checksums.splitlines()]
    expected = [parts[0] for parts in entries if len(parts) == 2 and parts[1] == name]
    if len(expected) != 1 or hashlib.sha256(data).hexdigest() != expected[0]:
        raise ValueError(f"actionlint checksum verification failed: {name}")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        try:
            member = archive.getmember("actionlint")
        except KeyError as error:
            raise ValueError("actionlint binary missing from archive") from error
        if not member.isfile():
            raise ValueError("actionlint binary is not a regular file")
        # Never extract archive paths, symlinks or unrelated files into the workspace.
        with archive.extractfile(member) as binary:
            return binary.read()


def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def version_matches(executable, version):
    if not executable.is_file():
        return False
    result = subprocess.run([str(executable), "-version"], capture_output=True, text=True,
                            check=False)
    return result.returncode == 0 and result.stdout.split("\n", 1)[0] == version


def install(version):
    name = archive_name(version, platform.system(), platform.machine())
    destination = ROOT / ".tools/bin/actionlint"
    if version_matches(destination, version):
        print(f"actionlint {version} is already installed.")
        return
    base = f"https://github.com/rhysd/actionlint/releases/download/v{version}/"
    data = fetch(base + name)
    checksums = fetch(base + f"actionlint_{version}_checksums.txt").decode("utf-8")
    binary = verified_binary(data, checksums, name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        candidate = Path(temporary) / "actionlint"
        candidate.write_bytes(binary)
        candidate.chmod(0o755)
        if not version_matches(candidate, version):
            raise ValueError("Downloaded actionlint reports an unexpected version")
        os.replace(candidate, destination)
    print(f"Installed checksum-verified actionlint {version}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    install(args.version)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from None
