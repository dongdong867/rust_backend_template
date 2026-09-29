use actix_web::body::MessageBody;
use actix_web::dev::{ServiceRequest, ServiceResponse};
use actix_web::http::StatusCode;
use actix_web::http::header::HeaderName;
use actix_web::middleware::Next;
use actix_web::{Error, error, web};
use environment::HttpConfig;

use crate::api::error::ProblemDetails;
use crate::api::middleware::request_id::REQUEST_ID_HEADER;

/// Stops request processing after the configured whole-request deadline.
pub async fn request_timeout(
    http_config: web::Data<HttpConfig>,
    request: ServiceRequest,
    next: Next<impl MessageBody + 'static>,
) -> Result<ServiceResponse<impl MessageBody>, Error> {
    let request_id = request.headers().get(REQUEST_ID_HEADER).cloned();
    match actix_web::rt::time::timeout(http_config.request_timeout, next.call(request)).await {
        Ok(response) => response,
        Err(_) => {
            tracing::warn!(
                timeout_ms = http_config.request_timeout.as_millis(),
                "request timed out"
            );
            let mut response = ProblemDetails::http_response(StatusCode::GATEWAY_TIMEOUT);
            if let Some(request_id) = request_id {
                response
                    .headers_mut()
                    .insert(HeaderName::from_static(REQUEST_ID_HEADER), request_id);
            }
            Err(error::InternalError::from_response("request timed out", response).into())
        }
    }
}
