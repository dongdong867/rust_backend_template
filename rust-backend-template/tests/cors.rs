//! In-memory HTTP tests of the explicit browser-origin allowlist.

use std::time::Duration;

use actix_web::http::StatusCode;
use actix_web::http::header::{
    ACCESS_CONTROL_ALLOW_METHODS, ACCESS_CONTROL_ALLOW_ORIGIN, ACCESS_CONTROL_REQUEST_METHOD,
    CONTENT_TYPE, ORIGIN, VARY,
};
use actix_web::test;
use environment::HttpConfig;
use rust_backend_template::create_app::create_app;
use serde_json::{Value, json};

fn http_config(cors_enabled: bool, origins: &[&str]) -> HttpConfig {
    HttpConfig {
        cors_enabled,
        cors_allowed_origins: origins.iter().map(ToString::to_string).collect(),
        request_timeout: Duration::from_secs(30),
        request_body_limit: 1_048_576,
    }
}

#[actix_web::test]
async fn an_allowed_origin_receives_cors_permission() {
    let app = test::init_service(create_app(http_config(true, &["https://app.example.com"]))).await;
    let request = test::TestRequest::get()
        .uri("/health")
        .insert_header((ORIGIN, "https://app.example.com"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers().get(ACCESS_CONTROL_ALLOW_ORIGIN).unwrap(),
        "https://app.example.com"
    );
}

#[actix_web::test]
async fn a_refused_origin_receives_no_cors_permission() {
    let app = test::init_service(create_app(http_config(true, &["https://app.example.com"]))).await;
    let request = test::TestRequest::get()
        .uri("/health")
        .insert_header((ORIGIN, "https://other.example.com"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    assert!(!response.headers().contains_key(ACCESS_CONTROL_ALLOW_ORIGIN));
}

#[actix_web::test]
async fn an_empty_allowlist_permits_no_origin() {
    let app = test::init_service(create_app(http_config(true, &[]))).await;
    let request = test::TestRequest::get()
        .uri("/health")
        .insert_header((ORIGIN, "https://app.example.com"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    assert!(!response.headers().contains_key(ACCESS_CONTROL_ALLOW_ORIGIN));
}

#[actix_web::test]
async fn disabling_cors_installs_no_cors_middleware() {
    let app =
        test::init_service(create_app(http_config(false, &["https://app.example.com"]))).await;
    let request = test::TestRequest::get()
        .uri("/health")
        .insert_header((ORIGIN, "https://app.example.com"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    assert!(!response.headers().contains_key(ACCESS_CONTROL_ALLOW_ORIGIN));
    assert!(!response.headers().contains_key(VARY));
}

#[actix_web::test]
async fn an_allowed_preflight_can_use_any_registered_route_method() {
    let app = test::init_service(create_app(http_config(true, &["https://app.example.com"]))).await;
    let request = test::TestRequest::default()
        .method(actix_web::http::Method::OPTIONS)
        .uri("/health")
        .insert_header((ORIGIN, "https://app.example.com"))
        .insert_header((ACCESS_CONTROL_REQUEST_METHOD, "GET"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::OK);
    assert_eq!(
        response.headers().get(ACCESS_CONTROL_ALLOW_ORIGIN).unwrap(),
        "https://app.example.com"
    );
    assert!(
        response
            .headers()
            .get(ACCESS_CONTROL_ALLOW_METHODS)
            .unwrap()
            .to_str()
            .unwrap()
            .split(", ")
            .any(|method| method == "GET")
    );
}

#[actix_web::test]
async fn a_refused_preflight_uses_problem_details() {
    let app = test::init_service(create_app(http_config(true, &["https://app.example.com"]))).await;
    let request = test::TestRequest::default()
        .method(actix_web::http::Method::OPTIONS)
        .uri("/health")
        .insert_header((ORIGIN, "https://other.example.com"))
        .insert_header((ACCESS_CONTROL_REQUEST_METHOD, "GET"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/problem+json"
    );
    assert_eq!(
        test::read_body_json::<Value, _>(response).await,
        json!({
            "type": "about:blank",
            "title": "Bad Request",
            "status": 400,
        })
    );
}
