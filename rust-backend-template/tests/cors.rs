//! In-memory HTTP tests of the explicit browser-origin allowlist.

use actix_web::http::StatusCode;
use actix_web::http::header::{
    ACCESS_CONTROL_ALLOW_METHODS, ACCESS_CONTROL_ALLOW_ORIGIN, ACCESS_CONTROL_REQUEST_METHOD,
    CONTENT_TYPE, ORIGIN, VARY,
};
use actix_web::test;
use environment::{Config, HttpConfig};
use rust_backend_template::create_app::create_app;
use serde_json::{Value, json};

fn http_config(cors_enabled: bool, origins: &[&str]) -> HttpConfig {
    Config::from_lookup(|name| match name {
        "HTTP_CORS_ENABLED" => Some(cors_enabled.to_string()),
        "HTTP_CORS_ALLOWED_ORIGINS" => Some(origins.join(",")),
        _ => None,
    })
    .unwrap()
    .http
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
            "title": "Bad Request",
            "status": 400,
        })
    );
}

#[actix_web::test]
async fn a_wildcard_origin_allows_the_apex_and_any_subdomain_depth() {
    let app = test::init_service(create_app(http_config(
        true,
        &["https://admin.other.example", "https://*.example.com"],
    )))
    .await;

    for origin in [
        "https://admin.other.example",
        "https://example.com",
        "https://tenant.example.com",
        "https://a.b.example.com",
    ] {
        let request = test::TestRequest::get()
            .uri("/health")
            .insert_header((ORIGIN, origin))
            .to_request();

        let response = test::call_service(&app, request).await;

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response.headers().get(ACCESS_CONTROL_ALLOW_ORIGIN).unwrap(),
            origin
        );
    }
}

#[actix_web::test]
async fn a_wildcard_origin_keeps_scheme_port_and_domain_boundaries() {
    let app = test::init_service(create_app(http_config(
        true,
        &["https://*.example.com:8443"],
    )))
    .await;

    for origin in ["https://example.com:8443", "https://a.b.example.com:8443"] {
        let request = test::TestRequest::get()
            .uri("/health")
            .insert_header((ORIGIN, origin))
            .to_request();

        let response = test::call_service(&app, request).await;

        assert_eq!(
            response.headers().get(ACCESS_CONTROL_ALLOW_ORIGIN).unwrap(),
            origin
        );
    }

    for origin in [
        "http://tenant.example.com:8443",
        "https://tenant.example.com",
        "https://evil-example.com:8443",
        "https://example.com.evil.test:8443",
    ] {
        let request = test::TestRequest::get()
            .uri("/health")
            .insert_header((ORIGIN, origin))
            .to_request();

        let response = test::call_service(&app, request).await;

        assert_eq!(response.status(), StatusCode::OK);
        assert!(!response.headers().contains_key(ACCESS_CONTROL_ALLOW_ORIGIN));
    }
}
