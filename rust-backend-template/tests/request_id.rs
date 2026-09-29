//! In-memory HTTP tests of the request ID every response carries.

use actix_web::test;
use rust_backend_template::create_app::create_app;
use uuid::Uuid;

#[actix_web::test]
async fn response_carries_a_new_request_id() {
    let app = test::init_service(create_app()).await;

    let response =
        test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;

    let id = response
        .headers()
        .get("x-request-id")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(Uuid::parse_str(id).is_ok(), "{id} is not a UUID");
}

#[actix_web::test]
async fn a_valid_request_id_from_the_caller_is_kept() {
    let app = test::init_service(create_app()).await;
    let request = test::TestRequest::get()
        .uri("/health")
        .insert_header(("x-request-id", "abc-123"))
        .to_request();

    let response = test::call_service(&app, request).await;

    assert_eq!(response.headers().get("x-request-id").unwrap(), "abc-123");
}

#[actix_web::test]
async fn an_invalid_request_id_from_the_caller_is_replaced() {
    let app = test::init_service(create_app()).await;
    let request = test::TestRequest::get()
        .uri("/health")
        .insert_header(("x-request-id", "not valid"))
        .to_request();

    let response = test::call_service(&app, request).await;

    let id = response
        .headers()
        .get("x-request-id")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(Uuid::parse_str(id).is_ok(), "{id} is not a UUID");
}

#[actix_web::test]
async fn unknown_paths_get_a_request_id_too() {
    let app = test::init_service(create_app()).await;

    let response =
        test::call_service(&app, test::TestRequest::get().uri("/missing").to_request()).await;

    assert!(response.headers().contains_key("x-request-id"));
}
