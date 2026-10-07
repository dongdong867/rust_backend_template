//! Real-server smoke coverage for the feature-independent database scaffold.

use std::str::FromStr;

use environment::Config;
use rust_backend_template::database::create_pool;
use sqlx::{ConnectOptions, Connection, PgConnection, PgPool, postgres::PgConnectOptions};
use uuid::Uuid;

#[actix_web::test]
#[ignore = "requires TEST_DATABASE_URL and CREATEDB permission; run make test-db"]
async fn postgres_pool_connects_and_migrations_are_repeatable() {
    let base_url = std::env::var("TEST_DATABASE_URL")
        .expect("TEST_DATABASE_URL must name an already-running dedicated test server");
    let options = PgConnectOptions::from_str(&base_url)
        .unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"))
        .disable_statement_logging();
    let mut isolated_url =
        url::Url::parse(&base_url).unwrap_or_else(|_| panic!("invalid TEST_DATABASE_URL"));
    // Only generated ASCII identifiers enter the creation/drop statements.
    let database_name = format!("scaffold_smoke_{}", Uuid::new_v4().simple());
    isolated_url.set_path(&format!("/{database_name}"));
    let config = Config::from_lookup(|name| {
        (name == "DATABASE_URL").then(|| isolated_url.as_str().to_owned())
    })
    .unwrap_or_else(|_| panic!("isolated DATABASE_URL failed environment validation"));
    let pool = create_pool(&config.database)
        .unwrap_or_else(|_| panic!("isolated database pool construction failed"));
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
        pool.close().await;
        let _ = admin.close().await;
        panic!("could not create isolated database; CREATEDB permission is required");
    }

    // Retain the pool outside the body so even a body panic permits cleanup.
    let smoke_pool = pool.clone();
    let smoke_name = database_name.clone();
    let outcome =
        tokio::spawn(async move { exercise_scaffold(&smoke_pool, &smoke_name).await }).await;
    pool.close().await;
    let dropped = sqlx::query(sqlx::AssertSqlSafe(format!(
        "DROP DATABASE \"{database_name}\""
    )))
    .execute(&mut admin)
    .await
    .is_ok();
    let closed = admin.close().await.is_ok();

    // Never render SQLx errors: they may contain SQL or connection details.
    assert!(dropped, "could not remove isolated smoke database");
    assert!(closed, "could not close smoke administrative connection");
    match outcome {
        Ok(Ok(())) => {}
        Ok(Err(stage)) => panic!("database scaffold smoke failed: {stage}"),
        Err(_) => panic!("database scaffold smoke body panicked"),
    }
}

async fn exercise_scaffold(pool: &PgPool, database_name: &str) -> Result<(), &'static str> {
    let connected_database: String = sqlx::query_scalar("SELECT current_database()")
        .fetch_one(pool)
        .await
        .map_err(|_| "production pool could not connect")?;
    if connected_database != database_name {
        return Err("production pool connected to the wrong database");
    }
    // Empty migrations are intentional when the optional business example is omitted.
    let migrations = sqlx::migrate!("../migrations");
    migrations
        .run(pool)
        .await
        .map_err(|_| "first migration run")?;
    migrations
        .run(pool)
        .await
        .map_err(|_| "repeated migration run")?;
    Ok(())
}
