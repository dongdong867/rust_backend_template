//! Gives each request an ID and logs one line when it completes.

use std::time::Instant;

use actix_web::Error;
use actix_web::body::MessageBody;
use actix_web::dev::{ServiceRequest, ServiceResponse};
use actix_web::http::header::{HeaderName, HeaderValue};
use actix_web::middleware::Next;
use tracing::Instrument;
use uuid::Uuid;

const REQUEST_ID_HEADER: &str = "x-request-id";

/// Attaches a request ID and logs each completed request.
///
/// Keeps a valid `x-request-id` supplied by a caller or creates a new one. The ID goes on
/// the request's log span and response header. The logged path excludes the query string,
/// which may carry secrets.
pub async fn request_id(
    req: ServiceRequest,
    next: Next<impl MessageBody>,
) -> Result<ServiceResponse<impl MessageBody>, Error> {
    let request_id = req
        .headers()
        .get(REQUEST_ID_HEADER)
        .and_then(|value| value.to_str().ok())
        .filter(|id| is_valid_request_id(id))
        .map_or_else(|| Uuid::new_v4().to_string(), str::to_owned);
    let method = req.method().to_string();
    let path = req.path().to_owned();
    let span = tracing::info_span!("request", request_id = %request_id);
    let started = Instant::now();

    let result = next.call(req).instrument(span.clone()).await;

    let duration_ms = u64::try_from(started.elapsed().as_millis()).unwrap_or(u64::MAX);
    let status = match &result {
        Ok(response) => response.status(),
        Err(error) => error.as_response_error().status_code(),
    };
    span.in_scope(|| {
        tracing::info!(
            method,
            path,
            status = status.as_u16(),
            duration_ms,
            "request completed"
        );
    });

    let mut response = result?;
    // A validated ID or a UUID is always a valid header value.
    if let Ok(value) = HeaderValue::from_str(&request_id) {
        response
            .headers_mut()
            .insert(HeaderName::from_static(REQUEST_ID_HEADER), value);
    }
    Ok(response)
}

fn is_valid_request_id(id: &str) -> bool {
    (1..=128).contains(&id.len())
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_letters_digits_and_separators() {
        assert!(is_valid_request_id("abc-123_DEF.456"));
    }

    #[test]
    fn accepts_128_characters() {
        assert!(is_valid_request_id(&"a".repeat(128)));
    }

    #[test]
    fn rejects_more_than_128_characters() {
        assert!(!is_valid_request_id(&"a".repeat(129)));
    }

    #[test]
    fn rejects_an_empty_id() {
        assert!(!is_valid_request_id(""));
    }

    #[test]
    fn rejects_spaces_and_control_characters() {
        assert!(!is_valid_request_id("abc 123"));
        assert!(!is_valid_request_id("abc\n123"));
    }
}
