//! Pool composition is tested without connecting to a PostgreSQL server.

use std::net::TcpListener;
use std::sync::Arc;
use std::time::{Duration, Instant};

use actix_web::http::header::CONTENT_TYPE;
use actix_web::{http::StatusCode, test};
use environment::Config;
use rust_backend_template::{
    container::Container, create_app::create_app, database::create_pool, providers::Providers,
};
use serde_json::{Value, json};
use sqlx::PgPool;
use tasks::framework::storage::PostgresTaskStorageProvider;
use tokio::net::TcpSocket;

/// Settings whose database address has no server, so no PostgreSQL server is contacted.
///
/// The returned socket holds the port without listening, so every connection fails
/// (Linux refuses it; macOS drops it) and nothing else can bind the port while the caller holds it.
fn closed_port_config(acquire_timeout: Option<&str>) -> (Config, TcpSocket) {
    let socket = TcpSocket::new_v4().unwrap();
    socket.bind("127.0.0.1:0".parse().unwrap()).unwrap();
    let address = socket.local_addr().unwrap();
    let url = format!("postgres://closed_probe@{address}/closed_probe");
    let config = Config::from_lookup(|name| match name {
        "DATABASE_URL" => Some(url.clone()),
        "DATABASE_ACQUIRE_TIMEOUT_SECS" => acquire_timeout.map(str::to_owned),
        "API_DOC_PASSWORD" => Some("documentation-test-password".to_owned()),
        _ => None,
    })
    .unwrap();
    (config, socket)
}

fn container_for(database: &PgPool) -> Arc<Container> {
    let provider = Arc::new(PostgresTaskStorageProvider::new(database.clone()));
    Arc::new(Container::with_providers(Providers {
        task_storage: provider,
    }))
}

#[::core::prelude::v1::test]
fn the_closed_port_stays_reserved_while_the_test_holds_it() {
    let (config, _port) = closed_port_config(None);
    let url = config.database.url.as_str();
    let address = &url[url.find('@').unwrap() + 1..url.rfind('/').unwrap()];
    assert!(
        TcpListener::bind(address).is_err(),
        "another process could bind {address}"
    );
}

#[actix_web::test]
async fn the_pool_uses_the_configured_acquire_timeout_and_10_seconds_by_default() {
    for (value, seconds) in [(None, 10), (Some("1"), 1), (Some("300"), 300)] {
        let (config, _port) = closed_port_config(value);
        let database = create_pool(&config.database).unwrap();
        assert_eq!(
            database.options().get_acquire_timeout(),
            Duration::from_secs(seconds)
        );
        assert_eq!(database.size(), 0);
        database.close().await;
    }
}

#[actix_web::test]
async fn acquiring_from_a_closed_port_times_out_after_the_configured_wait() {
    let (config, _port) = closed_port_config(Some("1"));
    let database = create_pool(&config.database).unwrap();
    let started = Instant::now();
    let error = database.acquire().await.unwrap_err();
    let elapsed = started.elapsed();
    assert!(
        matches!(error, sqlx::Error::PoolTimedOut),
        "expected a pool timeout, got {error:?}"
    );
    assert!(
        elapsed >= Duration::from_secs(1),
        "gave up after {elapsed:?}"
    );
    assert!(elapsed < Duration::from_secs(5), "waited {elapsed:?}");
    database.close().await;
}

#[actix_web::test]
async fn creating_a_task_fails_with_500_after_the_acquire_timeout_when_the_database_is_down() {
    let (config, _port) = closed_port_config(Some("1"));
    let database = create_pool(&config.database).unwrap();
    let app = test::init_service(create_app(
        config.http,
        container_for(&database),
        #[cfg(feature = "api-doc")]
        config.api_doc,
    ))
    .await;
    let started = Instant::now();
    let response = test::call_service(
        &app,
        test::TestRequest::post()
            .uri("/v1/tasks")
            .set_json(json!({"title": "Write documentation"}))
            .to_request(),
    )
    .await;
    let elapsed = started.elapsed();
    assert_eq!(response.status(), StatusCode::INTERNAL_SERVER_ERROR);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/problem+json"
    );
    assert_eq!(
        test::read_body_json::<Value, _>(response).await,
        json!({"title": "Internal Server Error", "status": 500}),
    );
    assert!(
        elapsed >= Duration::from_secs(1),
        "answered after {elapsed:?}"
    );
    assert!(elapsed < Duration::from_secs(5), "waited {elapsed:?}");
    database.close().await;
}

#[actix_web::test]
async fn health_answers_at_once_when_the_database_port_is_closed() {
    let (config, _port) = closed_port_config(None);
    let database = create_pool(&config.database).unwrap();
    let app = test::init_service(create_app(
        config.http,
        container_for(&database),
        #[cfg(feature = "api-doc")]
        config.api_doc,
    ))
    .await;
    let started = Instant::now();
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    let elapsed = started.elapsed();
    assert_eq!(response.status(), StatusCode::OK);
    // The default acquire timeout is 10 seconds; health must not wait for any of it.
    assert!(elapsed < Duration::from_secs(1), "waited {elapsed:?}");
    assert_eq!(database.size(), 0);
    database.close().await;
}

#[actix_web::test]
async fn the_shared_pool_is_lazy_and_health_never_acquires_a_connection() {
    let config = Config::from_lookup(|name| {
        (name == "DATABASE_URL")
            .then(|| "postgres://localhost/pool_test".to_owned())
            .or_else(|| {
                (name == "API_DOC_PASSWORD").then(|| "documentation-test-password".to_owned())
            })
    })
    .unwrap();
    let database = create_pool(&config.database).unwrap();
    let container = container_for(&database);
    assert_eq!(database.size(), 0);
    let app = test::init_service(create_app(
        config.http,
        container,
        #[cfg(feature = "api-doc")]
        config.api_doc,
    ))
    .await;
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(database.size(), 0);
    assert_eq!(database.num_idle(), 0);
    database.close().await;
}

#[actix_web::test]
async fn invalid_connection_options_do_not_escape_in_startup_errors() {
    let config = Config::from_lookup(|name| {
        (name == "DATABASE_URL")
            .then(|| {
                "postgres://user:secret-password@localhost/db?sslmode=secret-invalid-mode"
                    .to_owned()
            })
            .or_else(|| {
                (name == "API_DOC_PASSWORD").then(|| "documentation-test-password".to_owned())
            })
    })
    .unwrap();
    let error = create_pool(&config.database).unwrap_err();
    for rendered in [error.to_string(), format!("{error:?}")] {
        assert!(rendered.contains("DATABASE_URL"));
        assert!(!rendered.contains("secret"));
        assert!(!rendered.contains("postgres://"));
    }
}
