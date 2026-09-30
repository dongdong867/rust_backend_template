//! In-memory HTTP tests of `GET /health`: real routing, middleware and handlers, with no network.

use actix_web::http::StatusCode;
use actix_web::http::header::CONTENT_TYPE;
use actix_web::test;
use environment::HttpConfig;
use rust_backend_template::create_app::create_app;

#[actix_web::test]
async fn health_answers_ok_as_json() {
    let app = test::init_service(create_app(HttpConfig::default())).await;

    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/json"
    );
    assert_eq!(test::read_body(response).await, r#"{"status":"ok"}"#);
}
