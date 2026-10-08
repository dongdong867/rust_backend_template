"""Fast checks of generation assertions; no generator, Cargo, or database is invoked."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "template_test", Path(__file__).resolve().parents[1] / "template_test.py"
)
template_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(template_test)


class TemplateChecksTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.output = self.root / "generated"
        self.source.mkdir()
        self.output.mkdir()

    def write(self, root, path, text=""):
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)

    def prepare_ignore_rules(self):
        subprocess.run(["git", "init", "--quiet", str(self.source)], check=True)
        self.write(self.source, ".gitignore", (template_test.ROOT / ".gitignore").read_text())

    def is_ignored(self, name):
        return subprocess.run(
            ["git", "-c", f"core.excludesFile={os.devnull}",
             "check-ignore", "--quiet", "--no-index", name],
            cwd=self.source,
        ).returncode == 0

    def test_local_environment_variants_are_ignored(self):
        self.prepare_ignore_rules()
        for name in (".env", ".env.local", ".env.production", ".envrc", "private.env"):
            with self.subTest(name=name):
                self.assertTrue(self.is_ignored(name))

    def test_template_sources_remain_trackable(self):
        self.prepare_ignore_rules()
        for name in (".example.env", ".example.env.liquid", "Cargo.lock",
                     "Cargo.toml.liquid", "cargo-generate.toml", ".template/post.rhai"):
            with self.subTest(name=name):
                self.assertFalse(self.is_ignored(name))

    def project(self, include_tasks=False):
        for path in (
            "AGENTS.md", ".example.env", "migrations/.gitkeep",
            "generated/src/bin/migrate.rs", "generated/tests/database_tls.rs",
            "generated/tests/postgres_database.rs",
        ):
            self.write(self.output, path)
        self.write(self.output, ".example.env", "DATABASE_URL=placeholder\nDATABASE_MAX_CONNECTIONS=10\n")
        self.write(self.output, template_test.WORKFLOW_PROBE, template_test.WORKFLOW_TEXT)
        (self.output / "CLAUDE.md").symlink_to("AGENTS.md")
        if include_tasks:
            self.write(self.output, "crates/tasks/Cargo.toml")
            self.write(self.output, "migrations/0001_create_tasks.sql")
            self.write(self.output, "generated/tests/tasks.rs")

    def check_project(self, include_tasks=False):
        names = ["generated", "environment"] + (["tasks"] if include_tasks else [])
        metadata = json.dumps({"packages": [
            {"name": name, "manifest_path": str(
                self.output / ("generated" if name == "generated" else f"crates/{name}") / "Cargo.toml"
            )} for name in names
        ]}).encode()
        with patch.object(template_test.subprocess, "check_output", return_value=metadata):
            template_test.check_project(self.output, "generated", include_tasks)

    def test_matching_canonical_twin_passes(self):
        self.write(self.source, "file.rs", "source\n")
        self.write(self.source, "file.rs.liquid", "template\n")
        self.write(self.output, "file.rs", "source\n")
        template_test.check_twins(self.source, self.output)

    def test_stale_twin_is_rejected(self):
        self.write(self.source, "file.rs", "changed source\n")
        self.write(self.source, "file.rs.liquid", "old template\n")
        self.write(self.output, "file.rs", "old source\n")
        with self.assertRaisesRegex(RuntimeError, "Template twin drift"):
            template_test.check_twins(self.source, self.output)

    def test_orphan_twin_is_rejected(self):
        self.write(self.source, "file.rs.liquid", "template\n")
        with self.assertRaisesRegex(RuntimeError, "no ordinary counterpart"):
            template_test.check_twins(self.source, self.output)

    def test_empty_twin_inventory_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "No .liquid twins"):
            template_test.check_twins(self.source, self.output)

    def test_silent_generation_requires_explicit_command_consent(self):
        command = template_test.generation_command("generator", self.source, "service", True)
        self.assertIn("--allow-commands", command)
        command = template_test.generation_command(
            "generator", self.source, "service", True, allow_commands=False
        )
        self.assertNotIn("--allow-commands", command)

    def test_both_project_shapes_pass(self):
        self.project()
        self.check_project()
        self.write(self.output, "crates/tasks/Cargo.toml")
        self.write(self.output, "migrations/0001_create_tasks.sql")
        self.write(self.output, "generated/tests/tasks.rs")
        self.check_project(True)

    def test_instruction_copy_is_not_a_symlink(self):
        self.project()
        (self.output / "CLAUDE.md").unlink()
        self.write(self.output, "CLAUDE.md")
        with self.assertRaisesRegex(RuntimeError, "working relative symlink"):
            self.check_project()

    def test_stale_crate_name_is_rejected(self):
        self.project()
        self.write(self.output, "generated/src/main.rs", "use rust_backend_template;")
        with self.assertRaisesRegex(RuntimeError, "Original service name remains"):
            self.check_project()

    def test_leftover_tasks_wiring_is_rejected(self):
        self.project()
        self.write(self.output, "generated/src/main.rs", "use tasks::Task;")
        with self.assertRaisesRegex(RuntimeError, "Tasks reference remains"):
            self.check_project()

    def test_workflow_expression_damage_is_rejected(self):
        self.project()
        self.write(self.output, template_test.WORKFLOW_PROBE, "run-name: \n")
        with self.assertRaisesRegex(RuntimeError, "expressions changed"):
            self.check_project()

    def test_database_configuration_must_remain(self):
        self.project()
        (self.output / ".example.env").unlink()
        with self.assertRaisesRegex(RuntimeError, "database settings"):
            self.check_project()

    def test_snapshot_does_not_read_local_settings_or_build_output(self):
        self.write(self.source, "Cargo.toml", "manifest\n")
        self.write(self.source, ".example.env", "example\n")
        for name in (".env", "private.env", ".env.local", ".ddlc/private", ".tools/tool", "target/binary"):
            self.write(self.source, name, "local file\n")
        inventory = b"Cargo.toml\0.example.env\0.env\0private.env\0.env.local\0.ddlc/private\0.tools/tool\0target/binary\0"
        with patch.object(template_test, "ROOT", self.source), patch.object(
            template_test.subprocess, "check_output", return_value=inventory
        ):
            template_test.snapshot(self.output)
        self.assertEqual((self.output / ".example.env").read_text(), "example\n")
        self.assertFalse((self.output / "private.env").exists())
        self.assertFalse((self.output / ".env.local").exists())
        # The exclusion probes are synthetic, not copies of the user's files.
        self.assertNotEqual((self.output / ".env").read_text(), "local file\n")
        self.assertFalse((self.output / ".ddlc/private").exists())


if __name__ == "__main__":
    unittest.main()
