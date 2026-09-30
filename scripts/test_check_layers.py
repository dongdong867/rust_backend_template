"""Behavioral tests for the repository architecture source check."""
import pathlib
import subprocess
import sys
import tempfile
import unittest


SCRIPT = pathlib.Path(__file__).with_name("check_layers.py")


class CheckLayersTests(unittest.TestCase):
    def check(self, files, expected=1):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT)], cwd=root,
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
            return result.stdout

    def test_qualified_aliased_and_grouped_imports(self):
        for source in (
            "use sqlx::Pool;", "use sqlx as database;",
            "use std::env as settings;",
            "use std::{fmt, env::{var as setting, vars}};",
            'let x = ::std::env::var("SECRET");',
            "use environment::Config;", "dotenvy::dotenv().ok();",
            "use actix_web::{web, HttpResponse};",
            "use actix_server as server;",
            'let x = env!("SECRET");', 'let x = option_env!("SECRET");',
            "use r#sqlx::Pool;",
            "use std /* comment */ :: {env as settings};",
        ):
            with self.subTest(source=source):
                output = self.check({"crates/tasks/src/application/service/run.rs": source})
                self.assertIn("crates/tasks/src/application/service/run.rs:1:", output)

    def test_parent_modules_and_nested_files(self):
        for name in ("domain.rs", "application.rs", "domain/entity/task.rs",
                     "application/service/run.rs"):
            with self.subTest(name=name):
                output = self.check({f"crates/tasks/src/{name}": "\nuse sqlx::Pool;"})
                self.assertIn(f"crates/tasks/src/{name}:2:", output)

    def test_comments_and_literals_are_not_code(self):
        self.check({"crates/tasks/src/domain.rs": r'''
// sqlx actix_web std::env env! dotenvy environment
/* sqlx /* actix_web */ environment */
const S: &str = "sqlx \" actix_web std::env env! dotenvy";
const R: &str = r###" sqlx " actix_web std::env "###;
const B: &[u8] = br#"sqlx actix_web environment"#;
const C: char = '"';
fn borrow<'a>(x: &'a str) -> &'a str { x }
use std::fmt;
struct Envelope;
'''}, expected=0)

    def test_actix_everywhere_in_feature_source(self):
        for name in ("lib.rs", "adapter.rs", "adapter/controller/run.rs"):
            with self.subTest(name=name):
                self.check({f"crates/tasks/src/{name}": "use actix::Actor;"})

    def test_adapter_can_use_sqlx_and_environment_crate_is_exempt(self):
        self.check({
            "crates/tasks/src/adapter/repository.rs": "use sqlx::Pool;",
            "crates/environment/src/domain.rs": "use std::env;",
            "rust-backend-template/src/main.rs": "use actix_web::App;",
        }, expected=0)

    def test_feature_dependencies(self):
        for manifest in (
            '[dependencies]\nactix-web = "4"\n',
            '[dev-dependencies]\nactix = "0.13"\n',
            '[target.\'cfg(unix)\'.dependencies]\nactix-server = "2"\n',
            '[dependencies]\nhttp = { package = "actix-web", version = "4" }\n',
            '[dependencies.http]\npackage = "actix-web"\nversion = "4"\n',
            '[dependencies]\nhttp.workspace = true\n',
        ):
            with self.subTest(manifest=manifest):
                output = self.check({
                    "Cargo.toml": '[workspace.dependencies]\nhttp = { package = "actix-web", version = "4" }\n',
                    "crates/tasks/Cargo.toml": manifest,
                })
                self.assertIn("crates/tasks/Cargo.toml:", output)

    def test_allowed_dependencies_and_manifest_comments(self):
        self.check({
            "crates/tasks/Cargo.toml": '[package]\nname = "tasks"\ndescription = "actix-web"\n'
            '[dependencies]\nserde = "1" # actix-web\nsqlx = "0.9"\n',
            "crates/environment/Cargo.toml": '[dependencies]\nactix-web = "4"\n',
        }, expected=0)

    def test_target_dependency_aliases_are_checked_independently(self):
        self.check({
            "crates/tasks/Cargo.toml":
                '[dependencies]\nhttp = { package = "actix-web", version = "4" }\n'
                '[dev-dependencies]\nhttp = { package = "serde", version = "1" }\n',
        })

    def test_multiline_manifest_metadata_is_not_a_dependency(self):
        self.check({
            "crates/tasks/Cargo.toml": '[package]\nname = "tasks"\n'
            'description = """\n[dependencies]\nactix-web = "4"\n"""\n'
            '[dependencies]\nserde = "1"\n',
        }, expected=0)


if __name__ == "__main__":
    unittest.main()
