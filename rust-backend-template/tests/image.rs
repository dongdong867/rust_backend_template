//! Runs the production image through Compose against an already-running PostgreSQL.
//!
//! `make image-check` builds the image first. Nothing here starts PostgreSQL.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::str::FromStr;
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use sqlx::{ConnectOptions, Connection, PgConnection, postgres::PgConnectOptions};
use uuid::Uuid;

const WAIT: Duration = Duration::from_secs(60);
/// The name containers use to reach the host. CI sets 127.0.0.1 with host networking.
const DEFAULT_DATABASE_HOST: &str = "host.docker.internal";

#[actix_web::test]
#[ignore = "requires Docker, the built image and TEST_DATABASE_URL; run make image-check"]
async fn the_image_serves_migrates_on_request_and_stops_cleanly() {
    let base_url = std::env::var("TEST_DATABASE_URL")
        .expect("TEST_DATABASE_URL must name an already-running dedicated test server");
    let options = PgConnectOptions::from_str(&base_url)
        .unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"))
        .disable_statement_logging();
    let mut admin = PgConnection::connect_with(&options)
        .await
        .unwrap_or_else(|_| panic!("could not connect to TEST_DATABASE_URL server"));
    // Only generated ASCII identifiers enter the creation/drop statements.
    let database_name = format!("image_check_{}", Uuid::new_v4().simple());
    sqlx::query(sqlx::AssertSqlSafe(format!(
        "CREATE DATABASE \"{database_name}\""
    )))
    .execute(&mut admin)
    .await
    .unwrap_or_else(|_| panic!("could not create isolated database; CREATEDB is required"));

    let isolated = options.clone().database(&database_name);
    let container_url = container_database_url(&base_url, &database_name);
    let outcome = exercise(container_url, isolated).await;

    let dropped = sqlx::query(sqlx::AssertSqlSafe(format!(
        "DROP DATABASE \"{database_name}\" WITH (FORCE)"
    )))
    .execute(&mut admin)
    .await
    .is_ok();
    let closed = admin.close().await.is_ok();
    // Never render SQLx or Docker errors: they may contain connection details.
    assert!(dropped, "could not remove isolated image-check database");
    assert!(
        closed,
        "could not close image-check administrative connection"
    );
    if let Err(stage) = outcome {
        panic!("image check failed: {stage}");
    }
}

async fn exercise(container_url: String, isolated: PgConnectOptions) -> Result<(), String> {
    let stack = blocking(move || {
        let stack = Stack::new(&container_url)?;
        stack.compose(&["up", "--detach", "--no-build"])?;
        stack.wait_for_health()?;
        stack.require_only_the_service_runs()?;
        stack.require_non_root_user()?;
        Ok(stack)
    })
    .await?;
    if migration_count(&isolated).await?.is_some() {
        return Err("starting the service applied migrations".into());
    }
    let stack = blocking(move || {
        stack.migrate()?;
        stack.exercise_requests()?;
        Ok(stack)
    })
    .await?;
    let expected = sqlx::migrate!("../migrations").iter().count();
    if migration_count(&isolated).await? != Some(expected) {
        return Err(format!(
            "the migration history does not list all {expected} migrations"
        ));
    }
    blocking(move || {
        stack.stop_cleanly()?;
        stack.require_no_local_settings()
    })
    .await
}

/// Runs Docker work off the async runtime; a panic becomes a failed stage, so cleanup still runs.
async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, String> + Send + 'static,
) -> Result<T, String> {
    tokio::task::spawn_blocking(work)
        .await
        .unwrap_or_else(|_| Err("image check step panicked".into()))
}

async fn migration_count(isolated: &PgConnectOptions) -> Result<Option<usize>, String> {
    let mut connection = PgConnection::connect_with(isolated)
        .await
        .map_err(|_| "could not connect to the isolated database")?;
    let table: Option<String> = sqlx::query_scalar("SELECT to_regclass('_sqlx_migrations')::text")
        .fetch_one(&mut connection)
        .await
        .map_err(|_| "could not read the migration table")?;
    let count = match table {
        None => None,
        Some(_) => {
            let rows: i64 = sqlx::query_scalar("SELECT count(*) FROM _sqlx_migrations")
                .fetch_one(&mut connection)
                .await
                .map_err(|_| "could not read the migration history")?;
            Some(usize::try_from(rows).map_err(|_| "negative migration count")?)
        }
    };
    let _ = connection.close().await;
    Ok(count)
}

fn container_database_url(base_url: &str, database_name: &str) -> String {
    let host = std::env::var("IMAGE_CHECK_DATABASE_HOST")
        .unwrap_or_else(|_| DEFAULT_DATABASE_HOST.to_owned());
    let mut url = url::Url::parse(base_url).unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"));
    url.set_host(Some(&host))
        .unwrap_or_else(|_| panic!("invalid IMAGE_CHECK_DATABASE_HOST"));
    url.set_path(&format!("/{database_name}"));
    url.into()
}

/// One Compose project with its own settings file; dropping it removes every container it made.
struct Stack {
    project: String,
    root: PathBuf,
    settings: tempfile::NamedTempFile,
    port: u16,
}

impl Stack {
    fn new(database_url: &str) -> Result<Self, String> {
        let port = free_port()?;
        let mut settings = tempfile::NamedTempFile::new().map_err(|_| "no settings file")?;
        writeln!(
            settings,
            "PORT={port}\nLOG_FORMAT=json\nRUST_LOG=info\nDATABASE_URL={database_url}\n\
             API_DOC_PASSWORD=documentation-test-password"
        )
        .map_err(|_| "could not write the settings file")?;
        Ok(Self {
            project: format!("image-check-{}", Uuid::new_v4().simple()),
            root: Path::new(env!("CARGO_MANIFEST_DIR")).join(".."),
            settings,
            port,
        })
    }

    fn command(&self, args: &[&str]) -> Command {
        let mut command = Command::new("docker");
        command
            .current_dir(&self.root)
            .env("SERVICE_ENV_FILE", self.settings.path())
            .args(["compose", "--project-name", &self.project, "--env-file"])
            .arg(self.settings.path())
            .args(args)
            .stdin(Stdio::null());
        command
    }

    fn compose(&self, args: &[&str]) -> Result<Output, String> {
        let output = self
            .command(args)
            .output()
            .map_err(|_| "could not run docker compose")?;
        if output.status.success() {
            Ok(output)
        } else {
            Err(format!("docker compose {} failed", args[0]))
        }
    }

    fn container(&self) -> Result<String, String> {
        let output = self.compose(&["ps", "--all", "--quiet", "service"])?;
        let id = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        if id.is_empty() {
            Err("the service container does not exist".into())
        } else {
            Ok(id)
        }
    }

    /// The service container's `docker inspect` record.
    fn inspect(&self) -> Result<Value, String> {
        let output = Command::new("docker")
            .arg("inspect")
            .arg(self.container()?)
            .output()
            .map_err(|_| "could not run docker inspect")?;
        let records: Value =
            serde_json::from_slice(&output.stdout).map_err(|_| "unreadable docker inspect")?;
        Ok(records[0].clone())
    }

    fn wait_for_health(&self) -> Result<(), String> {
        let deadline = Instant::now() + WAIT;
        while Instant::now() < deadline {
            if request(self.port, "GET", "/health", None).is_ok_and(|(status, _)| status == 200) {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(format!("GET /health did not return 200 within {WAIT:?}"))
    }

    fn require_only_the_service_runs(&self) -> Result<(), String> {
        let output = self.compose(&["ps", "--services"])?;
        let running = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        if running == "service" {
            Ok(())
        } else {
            Err("Compose started something other than the service".into())
        }
    }

    fn require_non_root_user(&self) -> Result<(), String> {
        let record = self.inspect()?;
        let user = record["Config"]["User"].as_str().unwrap_or_default();
        if user.is_empty() || ["root", "0"].contains(&user.split(':').next().unwrap_or("")) {
            Err("the service runs as root".into())
        } else {
            Ok(())
        }
    }

    fn migrate(&self) -> Result<(), String> {
        let output = self.compose(&["run", "--rm", "migrate"])?;
        if String::from_utf8_lossy(&output.stdout).contains("migrations applied") {
            Ok(())
        } else {
            Err("migrate did not print \"migrations applied\"".into())
        }
    }

    fn exercise_requests(&self) -> Result<(), String> {
        let (status, body) = request(
            self.port,
            "POST",
            "/v1/tasks",
            Some(&json!({"title": "Check the image"})),
        )?;
        if status != 201 {
            return Err(format!("creating a task returned {status}"));
        }
        let id = body["id"].as_str().ok_or("the created task has no id")?;
        let (status, body) = request(self.port, "GET", &format!("/v1/tasks/{id}"), None)?;
        if status != 200 || body["title"] != "Check the image" {
            return Err(format!("fetching the created task returned {status}"));
        }
        Ok(())
    }

    fn stop_cleanly(&self) -> Result<(), String> {
        self.compose(&["stop", "service"])?;
        let code = &self.inspect()?["State"]["ExitCode"];
        if code == 0 {
            Ok(())
        } else {
            Err(format!(
                "the service exited with status {code} after docker stop"
            ))
        }
    }

    fn require_no_local_settings(&self) -> Result<(), String> {
        let export = Command::new("docker")
            .args(["export", &self.container()?])
            .stdout(Stdio::piped())
            .spawn()
            .map_err(|_| "could not run docker export")?;
        let listing = Command::new("tar")
            .arg("-t")
            .stdin(export.stdout.ok_or("no export stream")?)
            .output()
            .map_err(|_| "could not list the image files")?;
        let files = String::from_utf8_lossy(&listing.stdout);
        if files.lines().any(|path| {
            Path::new(path)
                .file_name()
                .is_some_and(|name| name.to_string_lossy().starts_with(".env"))
        }) {
            Err("the image contains a .env file".into())
        } else {
            Ok(())
        }
    }
}

impl Drop for Stack {
    fn drop(&mut self) {
        let _ = self
            .command(&["down", "--volumes", "--remove-orphans"])
            .output();
    }
}

fn free_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|_| "no free port")?;
    Ok(listener.local_addr().map_err(|_| "no free port")?.port())
}

fn request(
    port: u16,
    method: &str,
    path: &str,
    body: Option<&Value>,
) -> Result<(u16, Value), String> {
    let mut stream =
        TcpStream::connect(("127.0.0.1", port)).map_err(|_| "could not connect to the service")?;
    stream
        .set_read_timeout(Some(WAIT))
        .map_err(|_| "no read timeout")?;
    let body = body.map(Value::to_string).unwrap_or_default();
    write!(
        stream,
        "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    )
    .map_err(|_| "could not send the request")?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|_| "could not read the response")?;
    let status = response
        .split(' ')
        .nth(1)
        .and_then(|code| code.parse().ok())
        .ok_or("the response has no status")?;
    let payload = response.split_once("\r\n\r\n").map_or("", |(_, rest)| rest);
    Ok((status, serde_json::from_str(payload).unwrap_or(Value::Null)))
}
