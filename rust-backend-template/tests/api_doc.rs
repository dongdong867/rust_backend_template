//! Public-interface checks of the optional documentation boundary.

use actix_web::{http::StatusCode, test};
use environment::HttpConfig;

mod support;

#[cfg(not(feature = "api-doc"))]
#[actix_web::test]
async fn documentation_routes_are_absent_without_the_feature() {
    let app = test::init_service(support::create_app(HttpConfig::default())).await;
    for path in [
        "/docs",
        "/docs/",
        "/docs/openapi.json",
        "/docs/swagger-ui.css",
    ] {
        let response =
            test::call_service(&app, test::TestRequest::get().uri(path).to_request()).await;
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{path}");
    }
}

#[cfg(feature = "api-doc")]
mod enabled {
    use serde_json::Value;

    use super::*;

    const AUTH: &str = "Basic ZG9jczpkb2N1bWVudGF0aW9uLXRlc3QtcGFzc3dvcmQ=";

    #[actix_web::test]
    async fn authentication_scheme_is_case_insensitive() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        for scheme in ["basic", "BASIC", "bAsIc"] {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri("/docs/openapi.json")
                    .insert_header(("Authorization", AUTH.replacen("Basic", scheme, 1)))
                    .to_request(),
            )
            .await;
            assert_eq!(response.status(), StatusCode::OK, "{scheme}");
        }
    }

    #[actix_web::test]
    async fn repeated_authorization_values_are_rejected_in_either_order() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        for values in [
            [AUTH, "Basic invalid"],
            ["Basic invalid", AUTH],
            [AUTH, AUTH],
        ] {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri("/docs/openapi.json")
                    .append_header(("Authorization", values[0]))
                    .append_header(("Authorization", values[1]))
                    .to_request(),
            )
            .await;
            assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
            assert!(response.headers().contains_key("www-authenticate"));
            let body: Value = test::read_body_json(response).await;
            assert_eq!(body["status"], 401);
        }
        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/docs/openapi.json")
                .append_header(("Authorization", AUTH))
                .append_header((
                    "Authorization",
                    actix_web::http::header::HeaderValue::from_bytes(&[0xff]).unwrap(),
                ))
                .to_request(),
        )
        .await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[actix_web::test]
    async fn overridden_username_is_used_by_the_production_composition() {
        let config = environment::Config::from_lookup(|name| match name {
            "DATABASE_URL" => Some("postgres://localhost/documentation_test".to_owned()),
            "API_DOC_USERNAME" => Some("custom".to_owned()),
            "API_DOC_PASSWORD" => Some("documentation-test-password".to_owned()),
            _ => None,
        })
        .unwrap();
        let container = std::sync::Arc::new(
            rust_backend_template::container::Container::new(&config.database).unwrap(),
        );
        let app = test::init_service(rust_backend_template::create_app::create_app(
            config.http,
            container.clone(),
            config.api_doc,
        ))
        .await;
        for (auth, status) in [
            (AUTH, StatusCode::UNAUTHORIZED),
            (
                "Basic Y3VzdG9tOmRvY3VtZW50YXRpb24tdGVzdC1wYXNzd29yZA==",
                StatusCode::OK,
            ),
        ] {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri("/docs/openapi.json")
                    .insert_header(("Authorization", auth))
                    .to_request(),
            )
            .await;
            assert_eq!(response.status(), status);
        }
        container.shutdown().await;
    }

    #[actix_web::test]
    async fn initializer_uses_local_document_without_credentials_or_remote_validator() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/docs/swagger-initializer.js")
                .insert_header(("Authorization", AUTH))
                .to_request(),
        )
        .await;
        let text = String::from_utf8(test::read_body(response).await.to_vec()).unwrap();
        assert!(text.contains("/docs/openapi.json"));
        assert!(text.contains("\"validatorUrl\": \"none\""));
        assert!(!text.contains("documentation-test-password"));
        assert!(!text.contains(AUTH));
        let health =
            test::call_service(&app, test::TestRequest::get().uri("/health").to_request()).await;
        assert_eq!(health.status(), StatusCode::OK);
    }

    #[actix_web::test]
    async fn non_text_authorization_is_rejected_without_panicking() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/docs/openapi.json")
                .insert_header((
                    "Authorization",
                    actix_web::http::header::HeaderValue::from_bytes(&[0xff]).unwrap(),
                ))
                .to_request(),
        )
        .await;
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
    }

    #[actix_web::test]
    async fn authenticated_documentation_and_local_assets_are_served() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        for (path, content_type) in [
            ("/docs/", "text/html"),
            ("/docs/openapi.json", "application/json"),
            ("/docs/swagger-ui.css", "text/css"),
            ("/docs/swagger-ui-bundle.js", "text/javascript"),
            ("/docs/swagger-initializer.js", "text/javascript"),
        ] {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri(path)
                    .insert_header(("Authorization", AUTH))
                    .to_request(),
            )
            .await;
            assert_eq!(response.status(), StatusCode::OK, "{path}");
            assert!(
                response
                    .headers()
                    .get("content-type")
                    .unwrap()
                    .to_str()
                    .unwrap()
                    .starts_with(content_type),
                "{path}"
            );
        }
    }

    #[actix_web::test]
    async fn every_documentation_resource_rejects_missing_or_invalid_authentication() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        for path in [
            "/docs",
            "/docs/",
            "/docs/openapi.json",
            "/docs/swagger-ui.css",
            "/docs/swagger-initializer.js",
        ] {
            for authorization in [
                None,
                Some("not-basic"),
                Some("Basic invalid-base64"),
                Some("Basic ZG9jczp3cm9uZy1wYXNzd29yZA=="),
            ] {
                let mut request = test::TestRequest::get().uri(path);
                if let Some(value) = authorization {
                    request = request.insert_header(("Authorization", value));
                }
                let response = test::call_service(&app, request.to_request()).await;
                assert_eq!(response.status(), StatusCode::UNAUTHORIZED, "{path}");
                assert!(
                    response
                        .headers()
                        .get("www-authenticate")
                        .unwrap()
                        .to_str()
                        .unwrap()
                        .starts_with("Basic ")
                );
                assert_eq!(
                    response.headers().get("content-type").unwrap(),
                    "application/problem+json"
                );
                let body: Value = test::read_body_json(response).await;
                assert_eq!(
                    body,
                    serde_json::json!({"title": "Unauthorized", "status": 401})
                );
            }
        }
    }

    #[actix_web::test]
    async fn specification_describes_health_and_real_business_routes() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/docs/openapi.json")
                .insert_header(("Authorization", AUTH))
                .to_request(),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let document: Value = test::read_body_json(response).await;
        assert!(document["paths"]["/health"]["get"].is_object());
        assert!(document["paths"]["/v1/tasks"]["post"].is_object());
        assert!(document["paths"]["/v1/tasks/{id}"]["get"].is_object());
        assert!(document["paths"]["/v1/tasks/{id}/complete"]["post"].is_object());
        assert!(document["components"]["schemas"]["TaskResponse"].is_object());
        assert!(document["security"].is_null());
        let request =
            &document["components"]["schemas"]["CreateTaskRequest"]["properties"]["title"];
        assert_eq!(request["minLength"], 1);
        assert_eq!(request["maxLength"], 200);
        let response = &document["components"]["schemas"]["TaskResponse"];
        assert_eq!(response["properties"]["id"]["format"], "uuid");
        assert_eq!(response["properties"]["created_at"]["format"], "date-time");
        assert_eq!(
            response["properties"]["status"]["enum"],
            serde_json::json!(["open", "completed"])
        );
        assert!(
            response["required"]
                .as_array()
                .unwrap()
                .iter()
                .any(|name| name == "completed_at")
        );
        assert_eq!(
            document["paths"]["/v1/tasks/{id}/complete"]["post"]["responses"]["409"]["content"]["application/problem+json"]
                ["schema"]["$ref"],
            "#/components/schemas/ProblemDetails"
        );
    }

    #[actix_web::test]
    async fn documentation_csp_does_not_weaken_the_api_policy() {
        let app = test::init_service(support::create_app(HttpConfig::default())).await;
        let response = test::call_service(
            &app,
            test::TestRequest::get()
                .uri("/docs/")
                .insert_header(("Authorization", AUTH))
                .to_request(),
        )
        .await;
        assert_eq!(response.status(), StatusCode::OK);
        let policy = response
            .headers()
            .get("content-security-policy")
            .unwrap()
            .to_str()
            .unwrap();
        assert!(policy.contains("script-src 'self'"));
        assert!(policy.contains("connect-src 'self'"));
        assert!(!policy.contains("unsafe-eval"));
        for path in ["/health", "/docs/openapi.json", "/missing"] {
            let response = test::call_service(
                &app,
                test::TestRequest::get()
                    .uri(path)
                    .insert_header(("Authorization", AUTH))
                    .to_request(),
            )
            .await;
            assert_eq!(
                response.headers().get("content-security-policy").unwrap(),
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            );
        }
    }
}
