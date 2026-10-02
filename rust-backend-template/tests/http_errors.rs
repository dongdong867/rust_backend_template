//! In-memory HTTP tests of the safe error contract shared by every route.

use actix_web::body::MessageBody;
use actix_web::dev::ServiceResponse;
use actix_web::http::StatusCode;
use actix_web::http::header::CONTENT_TYPE;
use actix_web::test;
use environment::HttpConfig;
use serde_json::{Value, json};

mod support;
use support::create_app;

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
            "title": title,
            "status": status.as_u16(),
        })
    );
}

#[actix_web::test]
async fn an_unknown_path_uses_problem_details() {
    let app = test::init_service(create_app(HttpConfig::default())).await;

    let response =
        test::call_service(&app, test::TestRequest::get().uri("/missing").to_request()).await;

    assert_problem(response, StatusCode::NOT_FOUND, "Not Found").await;
}

#[actix_web::test]
async fn a_wrong_method_uses_problem_details() {
    let app = test::init_service(create_app(HttpConfig::default())).await;

    let response =
        test::call_service(&app, test::TestRequest::post().uri("/health").to_request()).await;

    assert_problem(
        response,
        StatusCode::METHOD_NOT_ALLOWED,
        "Method Not Allowed",
    )
    .await;
}
