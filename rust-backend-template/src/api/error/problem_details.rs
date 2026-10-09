use actix_web::body::MessageBody;
use actix_web::dev::ServiceResponse;
use actix_web::http::StatusCode;
use actix_web::http::header::{CONTENT_LENGTH, CONTENT_TYPE, HeaderValue};
use actix_web::middleware::ErrorHandlerResponse;
use actix_web::{HttpResponse, Result, error, web};
use serde::Serialize;

const PROBLEM_DETAILS_MEDIA_TYPE: &str = "application/problem+json";

/// The safe RFC 9457 response returned for every HTTP error.
#[derive(Serialize)]
#[cfg_attr(feature = "api-doc", derive(utoipa::ToSchema))]
pub(crate) struct ProblemDetails {
    title: &'static str,
    status: u16,
}

impl ProblemDetails {
    fn new(status: StatusCode) -> Self {
        Self {
            title: status.canonical_reason().unwrap_or("HTTP Error"),
            status: status.as_u16(),
        }
    }

    /// Builds a problem response for middleware errors that have no inner response to replace.
    pub(crate) fn http_response(status: StatusCode) -> HttpResponse {
        HttpResponse::build(status)
            .content_type(PROBLEM_DETAILS_MEDIA_TYPE)
            .json(Self::new(status))
    }

    /// Replaces an error response's body while preserving headers such as `Allow`.
    pub(crate) fn error_response<B: MessageBody + 'static>(
        response: ServiceResponse<B>,
    ) -> Result<ErrorHandlerResponse<B>> {
        let status = response.status();
        let problem = Self::new(status);
        let body = serde_json::to_vec(&problem).map_err(error::ErrorInternalServerError)?;
        let (request, mut response) = response.into_parts();
        response.headers_mut().insert(
            CONTENT_TYPE,
            HeaderValue::from_static(PROBLEM_DETAILS_MEDIA_TYPE),
        );
        response.headers_mut().remove(CONTENT_LENGTH);
        let response = response.set_body(web::Bytes::from(body));
        let response = ServiceResponse::new(request, response)
            .map_into_boxed_body()
            .map_into_right_body();

        Ok(ErrorHandlerResponse::Response(response))
    }
}
