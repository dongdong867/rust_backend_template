use actix_web::body::MessageBody;
use actix_web::dev::{ServiceFactory, ServiceRequest, ServiceResponse};
use actix_web::middleware::{ErrorHandlers, from_fn};
use actix_web::{App, Error};

use crate::api::error::ProblemDetails;
use crate::api::middleware::request_id::request_id;
use crate::api::route::health::config_health_routes;

/// Builds the application: every route, wrapped in the middleware every request passes through.
pub fn create_app() -> App<
    impl ServiceFactory<
        ServiceRequest,
        Config = (),
        Response = ServiceResponse<impl MessageBody>,
        Error = Error,
        InitError = (),
    >,
> {
    App::new()
        .wrap(ErrorHandlers::new().default_handler(ProblemDetails::error_response))
        .wrap(from_fn(request_id))
        .configure(config_health_routes)
}
