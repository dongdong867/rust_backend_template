use actix_web::body::MessageBody;
use actix_web::dev::{ServiceRequest, ServiceResponse};
use actix_web::http::StatusCode;
use actix_web::http::header::{AUTHORIZATION, HeaderValue, WWW_AUTHENTICATE};
use actix_web::middleware::Next;
use actix_web::{Error, FromRequest, web};
use actix_web_httpauth::extractors::basic::BasicAuth;
use environment::ApiDocConfig;
use subtle::ConstantTimeEq;

use crate::api::error::ProblemDetails;

pub(super) async fn authenticate(
    mut request: ServiceRequest,
    next: Next<impl MessageBody + 'static>,
) -> Result<ServiceResponse<impl MessageBody>, Error> {
    let Some(header) = normalized_authorization(&request) else {
        return Ok(unauthorized(request).map_into_right_body());
    };
    request.headers_mut().insert(AUTHORIZATION, header);

    let Ok(credentials) = BasicAuth::extract(request.request()).await else {
        return Ok(unauthorized(request).map_into_right_body());
    };

    let Some(expected) = request.app_data::<web::Data<ApiDocConfig>>() else {
        return Ok(request
            .into_response(ProblemDetails::http_response(
                StatusCode::INTERNAL_SERVER_ERROR,
            ))
            .map_into_right_body());
    };

    let username_matches = expected
        .username()
        .as_bytes()
        .ct_eq(credentials.user_id().as_bytes());
    let password_matches = expected
        .password()
        .as_bytes()
        .ct_eq(credentials.password().unwrap_or_default().as_bytes());

    if bool::from(username_matches & password_matches) {
        Ok(next.call(request).await?.map_into_left_body())
    } else {
        Ok(unauthorized(request).map_into_right_body())
    }
}

fn normalized_authorization(request: &ServiceRequest) -> Option<HeaderValue> {
    let mut values = request.headers().get_all(AUTHORIZATION);
    let value = values.next()?;
    if values.next().is_some() {
        return None;
    }

    let (scheme, token) = value.to_str().ok()?.split_once(' ')?;
    if !scheme.eq_ignore_ascii_case("Basic") {
        return None;
    }

    // The library parser expects exactly "Basic ". HTTP schemes are case-insensitive
    // and allow multiple spaces; reject ambiguous repeated fields before extracting.
    HeaderValue::from_str(&format!("Basic {}", token.trim_start_matches(' '))).ok()
}

fn unauthorized(request: ServiceRequest) -> ServiceResponse {
    let mut response = ProblemDetails::http_response(StatusCode::UNAUTHORIZED);
    response.headers_mut().insert(
        WWW_AUTHENTICATE,
        HeaderValue::from_static("Basic realm=\"docs\", charset=\"UTF-8\""),
    );

    request.into_response(response)
}
