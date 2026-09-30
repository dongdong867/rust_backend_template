//! In-memory HTTP tests of the fixed browser hardening policy.

use std::time::Duration;

use actix_web::http::header::HeaderMap;
use actix_web::{HttpResponse, test, web};
use environment::HttpConfig;
use rust_backend_template::create_app::create_app;

fn assert_security_headers(headers: &HeaderMap) {
    assert_eq!(
        headers.get("content-security-policy").unwrap(),
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    );
    assert_eq!(
        headers.get("permissions-policy").unwrap(),
        "camera=(), geolocation=(), microphone=()"
    );
    assert_eq!(headers.get("referrer-policy").unwrap(), "no-referrer");
    assert_eq!(headers.get("x-content-type-options").unwrap(), "nosniff");
    assert_eq!(headers.get("x-frame-options").unwrap(), "DENY");
    assert!(!headers.contains_key("strict-transport-security"));
}

async fn slow_response() -> HttpResponse {
    actix_web::rt::time::sleep(Duration::from_millis(50)).await;
    HttpResponse::NoContent().finish()
}

#[actix_web::test]
async fn a_successful_response_has_every_security_header() {
    let app = test::init_service(create_app(HttpConfig::default())).await;

    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;

    assert_security_headers(response.headers());
}

#[actix_web::test]
async fn an_error_response_has_every_security_header() {
    let app = test::init_service(create_app(HttpConfig::default())).await;

    let response =
        test::call_service(&app, test::TestRequest::get().uri("/missing").to_request()).await;

    assert_security_headers(response.headers());
}

#[actix_web::test]
async fn a_timeout_response_has_every_security_header() {
    let config = HttpConfig {
        request_timeout: Duration::from_millis(10),
        ..HttpConfig::default()
    };
    let app =
        test::init_service(create_app(config).route("/test/slow", web::get().to(slow_response)))
            .await;
    let result = test::try_call_service(
        &app,
        test::TestRequest::get().uri("/test/slow").to_request(),
    )
    .await;
    let error = match result {
        Ok(_) => panic!("expected the request to time out"),
        Err(error) => error,
    };
    let response = error.as_response_error().error_response();

    assert_security_headers(response.headers());
}
