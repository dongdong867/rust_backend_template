//! Checks rejection logs in a fresh process so tracing's cached callsites cannot hide events.

use std::process::Command;

use actix_web::http::StatusCode;
use actix_web::http::header::CONTENT_TYPE;
use actix_web::test as actix_test;
use actix_web::{HttpResponse, web};
use environment::{HttpConfig, LogFormat};
use rust_backend_template::telemetry;
use serde_json::{Value, json};

mod support;
use support::create_app;

const SENSITIVE_VALUE: &str = "SENSITIVE_JSON_TEST_VALUE";

#[test]
fn json_rejection_logs_a_category_without_submitted_values() {
    let output = Command::new(std::env::current_exe().unwrap())
        .args([
            "--ignored",
            "--exact",
            "json_rejection_log_probe",
            "--nocapture",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let logs = String::from_utf8(output.stdout).unwrap();
    assert!(!logs.contains(SENSITIVE_VALUE), "{logs}");
    let rejection = logs
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .find(|event| event["message"] == "JSON request body rejected")
        .expect("the rejection must still be logged");
    assert_eq!(rejection["level"], "WARN");
    assert_eq!(rejection["reason"], "deserialize");
    assert!(rejection["span"]["request_id"].as_str().is_some());
}

async fn accept_number(body: web::Json<u64>) -> HttpResponse {
    HttpResponse::Ok().json(body.into_inner())
}

#[actix_web::test]
#[ignore = "invoked by the parent test in a fresh process to isolate tracing's subscriber cache"]
async fn json_rejection_log_probe() {
    telemetry::init("info", LogFormat::Json);
    let app = actix_test::init_service(
        create_app(HttpConfig::default()).route("/test/number", web::post().to(accept_number)),
    )
    .await;
    let request = actix_test::TestRequest::post()
        .uri("/test/number")
        .insert_header((CONTENT_TYPE, "application/json"))
        .set_payload(serde_json::to_string(SENSITIVE_VALUE).unwrap())
        .to_request();

    let response = actix_test::call_service(&app, request).await;

    assert_eq!(response.status(), StatusCode::BAD_REQUEST);
    assert_eq!(
        response.headers().get(CONTENT_TYPE).unwrap(),
        "application/problem+json"
    );
    assert_eq!(
        actix_test::read_body_json::<Value, _>(response).await,
        json!({"title": "Bad Request", "status": 400})
    );
}
