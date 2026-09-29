//! In-memory HTTP tests of body limits and the whole-request deadline.

use std::time::Duration;

use actix_web::body::MessageBody;
use actix_web::dev::ServiceResponse;
use actix_web::http::StatusCode;
use actix_web::http::header::{CONTENT_LENGTH, CONTENT_TYPE, HeaderName};
use actix_web::{HttpResponse, test, web};
use environment::HttpConfig;
use rust_backend_template::create_app::create_app;
use serde::Deserialize;
use serde_json::{Value, json};

#[derive(Deserialize)]
struct TestBody {
    value: String,
}

async fn accept_json(body: web::Json<TestBody>) -> HttpResponse {
    HttpResponse::NoContent()
        .insert_header(("x-test-value-length", body.value.len().to_string()))
        .finish()
}

async fn accept_bytes(body: web::Bytes) -> HttpResponse {
    HttpResponse::NoContent()
        .insert_header(("x-test-body-length", body.len().to_string()))
        .finish()
}

async fn slow_response() -> HttpResponse {
    actix_web::rt::time::sleep(Duration::from_millis(50)).await;
    HttpResponse::NoContent().finish()
}

async fn assert_problem<B: MessageBody>(
    response: ServiceResponse<B>,
    status: StatusCode,
    title: &str,
) {
    assert_eq!(response.status(), status);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/problem+json"
    );
    assert_eq!(
        test::read_body_json::<Value, _>(response).await,
        json!({
            "type": "about:blank",
            "title": title,
            "status": status.as_u16(),
        })
    );
}

#[actix_web::test]
async fn malformed_json_uses_problem_details() {
    let app = test::init_service(
        create_app(HttpConfig::default()).route("/test/json", web::post().to(accept_json)),
    )
    .await;
    let request = test::TestRequest::post()
        .uri("/test/json")
        .insert_header((CONTENT_TYPE, "application/json"))
        .set_payload(r#"{"value":"unfinished"#)
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_problem(response, StatusCode::BAD_REQUEST, "Bad Request").await;
}

#[actix_web::test]
async fn an_oversized_json_body_uses_problem_details() {
    let config = HttpConfig {
        request_body_limit: 16,
        ..HttpConfig::default()
    };
    let app =
        test::init_service(create_app(config).route("/test/json", web::post().to(accept_json)))
            .await;
    let payload = r#"{"value":"too long"}"#;
    let request = test::TestRequest::post()
        .uri("/test/json")
        .insert_header((CONTENT_TYPE, "application/json"))
        .insert_header((CONTENT_LENGTH, payload.len().to_string()))
        .set_payload(payload)
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_problem(response, StatusCode::PAYLOAD_TOO_LARGE, "Payload Too Large").await;
}

#[actix_web::test]
async fn an_oversized_buffered_body_uses_problem_details() {
    let config = HttpConfig {
        request_body_limit: 4,
        ..HttpConfig::default()
    };
    let app =
        test::init_service(create_app(config).route("/test/bytes", web::post().to(accept_bytes)))
            .await;
    let request = test::TestRequest::post()
        .uri("/test/bytes")
        .insert_header((CONTENT_LENGTH, "5"))
        .set_payload("12345")
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_problem(response, StatusCode::PAYLOAD_TOO_LARGE, "Payload Too Large").await;
}

#[actix_web::test]
async fn a_request_that_exceeds_the_deadline_uses_problem_details() {
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

    assert_eq!(response.status(), StatusCode::GATEWAY_TIMEOUT);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/problem+json"
    );
    assert!(
        response
            .headers()
            .contains_key(HeaderName::from_static("x-request-id"))
    );
    let body = actix_web::body::to_bytes(response.into_body())
        .await
        .unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&body).unwrap(),
        json!({
            "type": "about:blank",
            "title": "Gateway Timeout",
            "status": 504,
        })
    );
}
