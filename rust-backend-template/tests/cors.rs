//! HTTP tests of the origin allowlist, including timeout responses sent over TCP.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::time::Duration;

use actix_web::http::StatusCode;
use actix_web::http::header::{
    ACCESS_CONTROL_ALLOW_METHODS, ACCESS_CONTROL_ALLOW_ORIGIN, ACCESS_CONTROL_REQUEST_METHOD,
    CONTENT_TYPE, ORIGIN, VARY,
};
use actix_web::{HttpResponse, test, web};
use environment::{Config, HttpConfig};
use rust_backend_template::server::serve;
use serde_json::{Value, json};

mod support;
use support::create_app;

fn http_config(cors_enabled: bool, origins: &[&str]) -> HttpConfig {
    Config::from_lookup(|name| match name {
        "DATABASE_URL" => Some("postgres://localhost/http_test".to_owned()),
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

async fn never_responds() -> HttpResponse {
    std::future::pending().await
}

#[actix_web::test]
async fn timeout_responses_respect_the_origin_allowlist_and_cors_switch() {
    for (enabled, origins, origin, expected) in [
        (
            true,
            &["https://app.example.com"][..],
            "https://app.example.com",
            Some("https://app.example.com"),
        ),
        (
            true,
            &["https://app.example.com"][..],
            "https://other.example.com",
            None,
        ),
        (
            false,
            &["https://app.example.com"][..],
            "https://app.example.com",
            None,
        ),
        (true, &[][..], "https://app.example.com", None),
    ] {
        let mut config = http_config(enabled, origins);
        config.request_timeout = Duration::from_millis(1);
        let app = test::init_service(
            create_app(config).route("/test/timeout", web::get().to(never_responds)),
        )
        .await;
        let request = test::TestRequest::get()
            .uri("/test/timeout")
            .insert_header((ORIGIN, origin))
            .insert_header(("x-request-id", "cors-timeout-test"))
            .to_request();

        // Match the server's conversion of middleware errors into HTTP responses.
        let response = match test::try_call_service(&app, request).await {
            Ok(response) => response.into_parts().1.map_into_boxed_body(),
            Err(error) => error.error_response(),
        };

        assert_eq!(response.status(), StatusCode::GATEWAY_TIMEOUT);
        assert_eq!(
            response
                .headers()
                .get(ACCESS_CONTROL_ALLOW_ORIGIN)
                .map(|value| value.to_str().unwrap()),
            expected
        );
        assert_eq!(
            response.headers().get(CONTENT_TYPE).unwrap(),
            "application/problem+json"
        );
        assert_eq!(
            response.headers().get("x-request-id").unwrap(),
            "cors-timeout-test"
        );
        assert_eq!(
            response.headers().get("x-content-type-options").unwrap(),
            "nosniff"
        );
        assert_eq!(response.headers().contains_key(VARY), enabled);
        assert_eq!(
            serde_json::from_slice::<Value>(
                &actix_web::body::to_bytes(response.into_body())
                    .await
                    .unwrap()
            )
            .unwrap(),
            json!({"title": "Gateway Timeout", "status": 504})
        );
    }
}

#[actix_web::test]
async fn an_allowed_origin_can_read_the_timeout_response_over_tcp() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let mut config = http_config(true, &["https://app.example.com"]);
    config.request_timeout = Duration::from_millis(1);
    let server = serve(listener, move || {
        create_app(config.clone()).route("/test/timeout", web::get().to(never_responds))
    })
    .unwrap();
    let handle = server.handle();
    let running = actix_web::rt::spawn(server);
    let response = actix_web::rt::task::spawn_blocking(move || {
        let mut stream = TcpStream::connect(address).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(10)))
            .unwrap();
        write!(
            stream,
            "GET /test/timeout HTTP/1.1\r\nHost: localhost\r\nOrigin: https://app.example.com\r\nConnection: close\r\n\r\n"
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        response
    })
    .await
    .unwrap();
    handle.stop(true).await;
    running.await.unwrap().unwrap();

    let (headers, body) = response.split_once("\r\n\r\n").unwrap();
    let headers = headers.to_ascii_lowercase();
    assert!(headers.starts_with("http/1.1 504"), "{response}");
    assert!(
        headers.contains("\r\naccess-control-allow-origin: https://app.example.com\r\n"),
        "{response}"
    );
    assert_eq!(
        serde_json::from_str::<Value>(body).unwrap(),
        json!({"title": "Gateway Timeout", "status": 504})
    );
}
