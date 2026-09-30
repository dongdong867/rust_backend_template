//! Pool composition is tested without connecting to a PostgreSQL server.

use actix_web::{http::StatusCode, test, web};
use environment::{Config, DatabaseConfig};
use rust_backend_template::{container::Container, create_app::create_app, database::create_pool};

#[actix_web::test]
async fn the_shared_pool_is_lazy_and_health_never_acquires_a_connection() {
    let container = Container::new(&DatabaseConfig::default()).unwrap();
    assert_eq!(container.database.size(), 0);
    let app = test::init_service(
        create_app(Default::default()).app_data(web::Data::from(container.task_controller)),
    )
    .await;
    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(container.database.size(), 0);
    assert_eq!(container.database.num_idle(), 0);
    container.database.close().await;
}

#[actix_web::test]
async fn invalid_connection_options_do_not_escape_in_startup_errors() {
    let config = Config::from_lookup(|name| {
        (name == "DATABASE_URL").then(|| {
            "postgres://user:secret-password@localhost/db?sslmode=secret-invalid-mode".to_owned()
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
