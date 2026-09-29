use actix_cors::Cors;
use actix_web::body::MessageBody;
use actix_web::dev::{ServiceFactory, ServiceRequest, ServiceResponse};
use actix_web::middleware::{Condition, ErrorHandlers, from_fn};
use actix_web::{App, Error, web};
use environment::HttpConfig;

use crate::api::error::ProblemDetails;
use crate::api::middleware::request_id::request_id;
use crate::api::middleware::request_timeout::request_timeout;
use crate::api::middleware::security_headers::SecurityHeaders;
use crate::api::route::health::config_health_routes;

/// Builds the application: every route, wrapped in the middleware every request passes through.
pub fn create_app(
    http_config: HttpConfig,
) -> App<
    impl ServiceFactory<
        ServiceRequest,
        Config = (),
        Response = ServiceResponse<impl MessageBody>,
        Error = Error,
        InitError = (),
    >,
> {
    let cors = http_config
        .cors_allowed_origins
        .iter()
        .fold(Cors::default(), |cors, origin| cors.allowed_origin(origin))
        .allow_any_method()
        .allow_any_header();
    let json_config = web::JsonConfig::default()
        .limit(http_config.request_body_limit)
        .error_handler(|error, _request| {
            tracing::warn!(reason = %error, "JSON request body rejected");
            error.into()
        });

    App::new()
        // Shared extractor and middleware state.
        .app_data(web::Data::new(http_config.clone()))
        .app_data(json_config)
        .app_data(web::PayloadConfig::new(http_config.request_body_limit))
        // Registered inner-to-outer; request_id handles incoming requests first.
        .wrap(Condition::new(http_config.cors_enabled, cors))
        .wrap(from_fn(request_timeout))
        .wrap(ErrorHandlers::new().default_handler(ProblemDetails::error_response))
        .wrap(SecurityHeaders::middleware())
        .wrap(from_fn(request_id))
        .configure(config_health_routes)
}
