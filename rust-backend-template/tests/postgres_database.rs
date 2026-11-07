//! Real-server smoke coverage for the feature-independent database scaffold.

use std::io;
use std::process::{Child, Command, ExitStatus, Output, Stdio};
use std::str::FromStr;
use std::time::{Duration, Instant};

use environment::{Config, DatabaseConfig};
use rust_backend_template::database::{create_migration_pool, create_pool};
use sqlx::{ConnectOptions, Connection, PgConnection, PgPool, postgres::PgConnectOptions};
use uuid::Uuid;

/// Creates a database named `<prefix>_<random suffix>` on the test server, runs `body` on what
/// `open` builds from its name and URL, then drops the database.
///
/// `body` runs in its own task, so cleanup still runs when it panics. Before the drop, `close`
/// releases a retained copy of what `open` built, such as pools. A failure panics with `label`
/// and its stage.
async fn with_isolated_database<R, Body, Closed>(
    prefix: &str,
    label: &str,
    open: impl FnOnce(&str, &url::Url) -> R,
    body: impl FnOnce(R) -> Body,
    close: impl FnOnce(R) -> Closed,
) where
    R: Clone,
    Body: Future<Output = Result<(), &'static str>> + Send + 'static,
    Closed: Future<Output = ()>,
{
    let base_url = std::env::var("TEST_DATABASE_URL")
        .expect("TEST_DATABASE_URL must name an already-running dedicated test server");
    let options = PgConnectOptions::from_str(&base_url)
        .unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"))
        .disable_statement_logging();
    let mut isolated_url =
        url::Url::parse(&base_url).unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"));
    // Only generated ASCII identifiers enter the creation/drop statements.
    assert!(
        prefix
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte == b'_'),
        "the database name prefix must be lowercase ASCII letters and underscores"
    );
    let database_name = format!("{prefix}_{}", Uuid::new_v4().simple());
    isolated_url.set_path(&format!("/{database_name}"));
    let resources = open(&database_name, &isolated_url);
    let mut admin = PgConnection::connect_with(&options)
        .await
        .unwrap_or_else(|_| panic!("could not connect to TEST_DATABASE_URL server"));
    let created = sqlx::query(sqlx::AssertSqlSafe(format!(
        "CREATE DATABASE \"{database_name}\""
    )))
    .execute(&mut admin)
    .await
    .is_ok();
    if !created {
        close(resources).await;
        let _ = admin.close().await;
        panic!("could not create isolated database; CREATEDB permission is required");
    }

    let outcome = tokio::spawn(body(resources.clone())).await;
    close(resources).await;
    let dropped = sqlx::query(sqlx::AssertSqlSafe(format!(
        "DROP DATABASE \"{database_name}\""
    )))
    .execute(&mut admin)
    .await
    .is_ok();
    let closed = admin.close().await.is_ok();

    // Never render SQLx errors: they may contain SQL or connection details.
    assert!(dropped, "{label}: could not remove the isolated database");
    assert!(
        closed,
        "{label}: could not close the administrative connection"
    );
    match outcome {
        Ok(Ok(())) => {}
        Ok(Err(stage)) => panic!("{label} failed: {stage}"),
        Err(_) => panic!("{label} panicked"),
    }
}

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL and CREATEDB permission; run make test-db"]
async fn postgres_pool_connects_and_migrations_are_repeatable() {
    with_isolated_database(
        "scaffold_smoke",
        "database scaffold smoke",
        |database_name, url| {
            let config = Config::from_lookup(|name| {
                (name == "DATABASE_URL")
                    .then(|| url.as_str().to_owned())
                    .or_else(|| {
                        (name == "API_DOC_PASSWORD")
                            .then(|| "documentation-test-password".to_owned())
                    })
            })
            .unwrap_or_else(|_| panic!("isolated DATABASE_URL failed environment validation"));
            let pool = create_pool(&config.database)
                .unwrap_or_else(|_| panic!("isolated database pool construction failed"));
            let migration_pool = create_migration_pool(&config.database)
                .unwrap_or_else(|_| panic!("isolated migration pool construction failed"));
            (pool, migration_pool, database_name.to_owned())
        },
        |(pool, migration_pool, database_name)| async move {
            exercise_scaffold(&pool, &migration_pool, &database_name).await
        },
        |(pool, migration_pool, _)| async move {
            pool.close().await;
            migration_pool.close().await;
        },
    )
    .await;
}

async fn exercise_scaffold(
    pool: &PgPool,
    migration_pool: &PgPool,
    database_name: &str,
) -> Result<(), &'static str> {
    let connected_database: String = sqlx::query_scalar("SELECT current_database()")
        .fetch_one(pool)
        .await
        .map_err(|_| "production pool could not connect")?;
    if connected_database != database_name {
        return Err("production pool connected to the wrong database");
    }
    // Empty migrations are intentional when the optional business example is omitted.
    // `migrate` runs them on the migration pool, so this does too.
    let migrations = sqlx::migrate!("../migrations");
    migrations
        .run(migration_pool)
        .await
        .map_err(|_| "first migration run")?;
    migrations
        .run(migration_pool)
        .await
        .map_err(|_| "repeated migration run")?;
    Ok(())
}

/// Settings for a one-connection pool on the test server with a 1-second statement timeout.
///
/// `url_options` are added to the test server's URL as query parameters.
fn one_second_statement_timeout_config(url_options: &[(&str, &str)]) -> DatabaseConfig {
    let base_url = std::env::var("TEST_DATABASE_URL")
        .expect("TEST_DATABASE_URL must name an already-running dedicated test server");
    let mut url =
        url::Url::parse(&base_url).unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"));
    url.query_pairs_mut().extend_pairs(url_options);
    Config::from_lookup(|name| match name {
        "DATABASE_URL" => Some(url.as_str().to_owned()),
        "DATABASE_MAX_CONNECTIONS" => Some("1".to_owned()),
        "DATABASE_STATEMENT_TIMEOUT_SECS" => Some("1".to_owned()),
        "API_DOC_PASSWORD" => Some("documentation-test-password".to_owned()),
        _ => None,
    })
    .unwrap_or_else(|_| panic!("test DATABASE_URL failed environment validation"))
    .database
}

/// Runs a 2-second sleep and checks that PostgreSQL cancels it after about 1 second.
async fn assert_slow_query_is_cancelled(pool: &PgPool) {
    // Open the connection first, so the timed window holds only the slow query.
    sqlx::query("SELECT 1")
        .execute(pool)
        .await
        .unwrap_or_else(|_| panic!("the first query failed"));
    let started = Instant::now();
    let result = sqlx::query("SELECT pg_sleep(2)").execute(pool).await;
    let elapsed = started.elapsed();
    // Never render SQLx errors: they may contain SQL or connection details.
    let code = result.err().and_then(|error| {
        error
            .as_database_error()?
            .code()
            .map(|code| code.into_owned())
    });
    assert_eq!(
        code.as_deref(),
        Some("57014"),
        "expected a statement timeout"
    );
    assert!(
        (Duration::from_millis(900)..Duration::from_millis(1900)).contains(&elapsed),
        "cancelled after {elapsed:?}"
    );
}

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL; run make test-db"]
async fn the_service_pool_cancels_a_slow_query_and_runs_the_next_one() {
    let pool = create_pool(&one_second_statement_timeout_config(&[]))
        .unwrap_or_else(|_| panic!("pool construction failed"));
    assert_slow_query_is_cancelled(&pool).await;
    // The same pool runs the next query after the cancellation.
    let next: i32 = sqlx::query_scalar("SELECT 1")
        .fetch_one(&pool)
        .await
        .unwrap_or_else(|_| panic!("the next query failed"));
    assert_eq!(next, 1);
    pool.close().await;
}

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL; run make test-db"]
async fn a_fast_query_returns_its_result_under_the_statement_timeout() {
    let pool = create_pool(&one_second_statement_timeout_config(&[]))
        .unwrap_or_else(|_| panic!("pool construction failed"));
    let result: String = sqlx::query_scalar("SELECT 'done' FROM pg_sleep(0.1)")
        .fetch_one(&pool)
        .await
        .unwrap_or_else(|_| panic!("the fast query failed"));
    assert_eq!(result, "done");
    pool.close().await;
}

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL; run make test-db"]
async fn the_statement_timeout_setting_wins_over_the_url_option() {
    let config = one_second_statement_timeout_config(&[("options[statement_timeout]", "60000")]);
    let pool = create_pool(&config).unwrap_or_else(|_| panic!("pool construction failed"));
    assert_slow_query_is_cancelled(&pool).await;
    pool.close().await;
    // The URL option reaches PostgreSQL on a pool without the setting.
    let migration_pool =
        create_migration_pool(&config).unwrap_or_else(|_| panic!("pool construction failed"));
    assert_eq!(statement_timeout(&migration_pool).await, "1min");
    migration_pool.close().await;
}

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL; run make test-db"]
async fn the_migration_pool_runs_a_statement_longer_than_the_statement_timeout() {
    let pool = create_migration_pool(&one_second_statement_timeout_config(&[]))
        .unwrap_or_else(|_| panic!("pool construction failed"));
    assert_eq!(statement_timeout(&pool).await, "0");
    let started = Instant::now();
    sqlx::query("SELECT pg_sleep(2)")
        .execute(&pool)
        .await
        .unwrap_or_else(|_| panic!("the 2-second statement failed on the migration pool"));
    assert!(started.elapsed() >= Duration::from_secs(2));
    pool.close().await;
}

/// The `statement_timeout` a connection from `pool` reports.
async fn statement_timeout(pool: &PgPool) -> String {
    sqlx::query_scalar("SHOW statement_timeout")
        .fetch_one(pool)
        .await
        .unwrap_or_else(|_| panic!("SHOW statement_timeout failed"))
}

/// Builds the command for the built `migrate` on `database_url` with a 1-second statement timeout.
fn migrate_command(database_url: &str) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_migrate"));
    command
        .env_clear()
        .env("DATABASE_URL", database_url)
        .env("DATABASE_STATEMENT_TIMEOUT_SECS", "1")
        .env("API_DOC_PASSWORD", "documentation-test-password")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    // Keep what SQLx reads to fill in the URL and find `~/.pgpass`. `PGOPTIONS` stays out,
    // since it could set a statement timeout of its own.
    for name in [
        "PGUSER",
        "PGPASSWORD",
        "PGPORT",
        "PGSSLMODE",
        "PGSSLROOTCERT",
        "PGPASSFILE",
        "HOME",
    ] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    command
}

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL and CREATEDB permission; run make test-db"]
async fn migrate_finishes_a_step_that_waits_longer_than_the_statement_timeout() {
    with_isolated_database(
        "migrate_wait",
        "migrate behind a lock",
        |_, url| url.as_str().to_owned(),
        |database_url| async move { migrate_behind_a_lock(&database_url).await },
        |_| async {},
    )
    .await;
}

/// A running `migrate` that is killed and reaped if the test returns before waiting for it, so it
/// cannot keep a session on the database the test drops.
struct MigrateRun(Option<Child>);

impl MigrateRun {
    fn try_wait(&mut self) -> io::Result<Option<ExitStatus>> {
        self.0.as_mut().map_or(Ok(None), Child::try_wait)
    }

    fn wait_with_output(mut self) -> io::Result<Output> {
        self.0
            .take()
            .ok_or_else(|| io::Error::other("migrate was already waited for"))?
            .wait_with_output()
    }
}

impl Drop for MigrateRun {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Runs `migrate` once, then again while another session holds a lock on `_sqlx_migrations`
/// for 2 seconds, so a migration step waits longer than the 1-second statement timeout.
async fn migrate_behind_a_lock(database_url: &str) -> Result<(), &'static str> {
    let first = migrate_command(database_url)
        .output()
        .map_err(|_| "could not run migrate")?;
    if !first.status.success() {
        return Err("first migrate run failed");
    }
    let options = PgConnectOptions::from_str(database_url)
        .map_err(|_| "invalid isolated database URL")?
        .disable_statement_logging();
    let mut holder = PgConnection::connect_with(&options)
        .await
        .map_err(|_| "lock session could not connect")?;
    let mut lock = holder.begin().await.map_err(|_| "lock transaction")?;
    sqlx::query("LOCK TABLE _sqlx_migrations IN ACCESS EXCLUSIVE MODE")
        .execute(&mut *lock)
        .await
        .map_err(|_| "could not lock _sqlx_migrations")?;
    let mut child = MigrateRun(Some(
        migrate_command(database_url)
            .spawn()
            .map_err(|_| "could not run migrate")?,
    ));
    actix_web::rt::time::sleep(Duration::from_secs(2)).await;
    let waited = child
        .try_wait()
        .map_err(|_| "could not poll migrate")?
        .is_none();
    lock.commit()
        .await
        .map_err(|_| "could not release the lock")?;
    holder
        .close()
        .await
        .map_err(|_| "could not close the lock session")?;
    let output = child
        .wait_with_output()
        .map_err(|_| "could not wait for migrate")?;
    if !output.status.success() {
        return Err("migrate failed behind the lock");
    }
    if output.stdout != b"migrations applied\n" {
        return Err("migrate did not print \"migrations applied\"");
    }
    if !waited {
        return Err("migrate finished before the lock was released");
    }
    Ok(())
}
