#!/usr/bin/env python3
"""Check the actual cargo-generate outputs, separately from ordinary fast tests."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent.parent
GENERATOR_VERSION = "cargo generate 0.25.0"
CANONICAL_NAME = "rust-backend-template"
WORKFLOW_PROBE = ".github/workflows/template-expression-probe.yml"
WORKFLOW_TEXT = "name: expression probe\nrun-name: ${{ github.sha }}\n"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(command, cwd):
    print("+", " ".join(map(str, command)), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def generator():
    executable = (
        os.environ.get("CARGO_GENERATE")
        or (str(ROOT / ".tools/bin/cargo-generate")
            if (ROOT / ".tools/bin/cargo-generate").is_file() else None)
        or shutil.which("cargo-generate")
    )
    require(executable, "cargo-generate 0.25.0 is required; run make template-tools.")
    executable = shutil.which(executable) or str(Path(executable).resolve())
    version = subprocess.check_output([executable, "--version"], text=True).strip()
    require(version == GENERATOR_VERSION, f"Expected {GENERATOR_VERSION}; found {version}.")
    return executable


def snapshot(destination):
    """Copy versioned and new source, never local settings or generated build output."""
    paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT
    ).decode().split("\0")
    for name in sorted(set(paths) - {""}):
        relative = Path(name)
        if relative.parts[0] in {".ddlc", ".delta", ".tools", "target"}:
            continue
        if relative.name.endswith(".env") and relative.name != ".example.env":
            continue
        if relative.name.startswith(".env"):
            continue
        source = ROOT / relative
        if not source.exists() and not source.is_symlink():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
    # Synthetic fixtures prove exclusions and expression preservation without copying secrets.
    probe = destination / WORKFLOW_PROBE
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(WORKFLOW_TEXT)
    for name in (".env", ".ddlc/probe", ".delta/probe", ".tools/probe", "target/probe"):
        probe = destination / name
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text("synthetic generation exclusion probe\n")


def generation_command(executable, source, name, include_tasks, *, template_source=False,
                       allow_commands=True):
    command = [
        executable, "generate", "--path", str(source), "--name", name,
        "--silent", "--define", f"include_tasks={str(include_tasks).lower()}",
        "--no-workspace", "--vcs", "none",
    ]
    if allow_commands:
        command.append("--allow-commands")
    if template_source:
        command.extend(["--define", "template_source=true"])
    return command


def generate(executable, source, destination, name, include_tasks, *, template_source=False):
    destination.mkdir(parents=True, exist_ok=True)
    command = generation_command(executable, source, name, include_tasks,
                                 template_source=template_source)
    run(command, destination)
    project = destination / name
    require(project.is_dir(), f"Generator did not create {name}.")
    return project


def check_twins(source, canonical):
    twins = sorted(source.rglob("*.liquid"))
    require(twins, "No .liquid twins were found.")
    for twin in twins:
        counterpart = twin.relative_to(source).with_suffix("")
        plain = source / counterpart
        rendered = canonical / counterpart
        require(plain.is_file(), f"Twin has no ordinary counterpart: {counterpart}.")
        require(rendered.is_file(), f"Twin was not generated: {counterpart}.")
        require(
            plain.read_bytes() == rendered.read_bytes(),
            f"Template twin drift: {counterpart}; update its .liquid twin with the ordinary file.",
        )


def check_project(project, name, include_tasks):
    service = project / name
    require(service.is_dir(), f"Service folder was not renamed to {name}.")
    require(not (project / CANONICAL_NAME).exists() or name == CANONICAL_NAME,
            "The original service folder remains.")
    link = project / "CLAUDE.md"
    require(link.is_symlink() and os.readlink(link) == "AGENTS.md" and link.is_file(),
            "CLAUDE.md must be a working relative symlink to AGENTS.md.")
    for path in (".ddlc", ".delta", ".tools", ".env", "target", ".template",
                 "scripts", "cargo-generate.toml", ".genignore"):
        require(not (project / path).exists(), f"Generator-only or local path leaked: {path}.")
    require((project / WORKFLOW_PROBE).read_text() == WORKFLOW_TEXT,
            "GitHub Actions expressions changed during generation.")
    require(not list(project.rglob("*.liquid")), "Unrendered Liquid twins remain.")
    require((service / "src/bin/migrate.rs").is_file(), "The migration entry point was removed.")
    require((project / "migrations/.gitkeep").is_file(), "The migration directory was lost.")
    require((service / "tests/database_tls.rs").is_file(), "Generic TLS coverage was removed.")
    require((service / "tests/postgres_database.rs").is_file(),
            "The PostgreSQL scaffold smoke check was removed.")
    example = project / ".example.env"
    require(example.is_file()
            and "DATABASE_URL=" in example.read_text()
            and "DATABASE_MAX_CONNECTIONS=" in example.read_text(),
            "The PostgreSQL database settings were removed.")
    require((project / "crates/tasks").is_dir() == include_tasks,
            "The tasks crate does not match the prompt.")
    require((project / "migrations/0001_create_tasks.sql").is_file() == include_tasks,
            "The tasks migration does not match the prompt.")
    require((service / "tests/tasks.rs").is_file() == include_tasks,
            "The tasks HTTP tests do not match the prompt.")
    for path in project.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        require(
            not re.search(r"{{\s*(project-name|crate_name|include_tasks|template_source)\b", text),
            f"Unrendered placeholder in {path.relative_to(project)}.",
        )
        if name != CANONICAL_NAME:
            require("rust_backend_template" not in text and CANONICAL_NAME not in text,
                    f"Original service name remains in {path.relative_to(project)}.")
        if not include_tasks and name != "tasks":
            # A service itself called tasks legitimately uses that word as its own identity.
            require(not re.search(r"\btasks\b", text, flags=re.IGNORECASE),
                    f"Tasks reference remains in {path.relative_to(project)}.")
    metadata = subprocess.check_output(
        ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"], cwd=project
    )
    packages = json.loads(metadata)["packages"]
    example_manifest = (project / "crates/tasks/Cargo.toml").resolve()
    example_present = any(
        Path(package["manifest_path"]).resolve() == example_manifest for package in packages
    )
    require(name in {package["name"] for package in packages} and example_present == include_tasks,
            "The locked Cargo workspace does not match the generated variant.")


def check_input_failures(executable, source, destination):
    for name, include_tasks in (
        ("environment", True), ("tasks", True), ("migrate", False),
        ("crates", False), ("docs", True), ("migrations", False),
    ):
        output = destination / name
        output.mkdir(parents=True)
        result = subprocess.run(
            generation_command(executable, source, name, include_tasks),
            cwd=output, capture_output=True, text=True,
        )
        require(result.returncode != 0 and "Reserved service name:" in result.stderr,
                f"Expected clear pre-render name rejection for {name}.")
    output = destination / "consent"
    output.mkdir()
    result = subprocess.run(
        generation_command(executable, source, "consent-probe", True, allow_commands=False),
        cwd=output, capture_output=True, text=True,
    )
    require(result.returncode != 0 and "Cannot prompt for system command confirmation" in result.stderr,
            "Silent generation must fail without command approval.")
    # This name is valid once the separate example package is removed.
    project = generate(executable, source, destination / "allowed", "tasks", False)
    check_project(project, "tasks", False)
    print("Name collision guards and explicit command consent passed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    checks = parser.add_mutually_exclusive_group()
    checks.add_argument("--structure-only", action="store_true",
                        help="Check generation and locks only; do not claim full make check coverage.")
    checks.add_argument("--fast-only", action="store_true",
                        help="Also lint and test generated code, but omit real PostgreSQL checks.")
    args = parser.parse_args()
    if not args.structure_only and not args.fast_only:
        require(os.environ.get("TEST_DATABASE_URL"),
                "TEST_DATABASE_URL is required for full template checks; use an existing dedicated server.")
    executable = generator()
    # Spaces and apostrophes also exercise the hook's shell quoting of its destination.
    with tempfile.TemporaryDirectory(prefix="template-test with 'quotes' ") as temporary:
        temporary = Path(temporary)
        source = temporary / "source"
        source.mkdir()
        snapshot(source)
        check_input_failures(executable, source, temporary / "input-checks")
        canonical = generate(executable, source, temporary / "canonical", CANONICAL_NAME, True,
                             template_source=True)
        check_twins(source, canonical)
        for include_tasks in (True, False):
            name = "generated-with-example" if include_tasks else "generated-without-example"
            project = generate(executable, source, temporary / "variants", name, include_tasks)
            check_project(project, name, include_tasks)
            if not args.structure_only:
                # Share build cache, not manifests/locks or generated source, across variants.
                environment = dict(os.environ, CARGO_TARGET_DIR=str(ROOT / "target/template-test"))
                command = ["make", "lint", "test"] if args.fast_only else ["make", "check"]
                subprocess.run(command, cwd=project, env=environment, check=True)
    if args.structure_only:
        print("Generation structure and locked metadata passed; full make check was NOT run.")
    elif args.fast_only:
        print("Template twins, generation, lint and fast tests passed; PostgreSQL checks were NOT run.")
    else:
        print("Template twins and both generated variants passed full make check.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
