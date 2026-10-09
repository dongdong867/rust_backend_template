use actix_web::Error;
use actix_web::body::MessageBody;
use actix_web::dev::{ServiceRequest, ServiceResponse};
use actix_web::http::header::{CONTENT_SECURITY_POLICY, CONTENT_TYPE, HeaderValue};
use actix_web::middleware::Next;

const UI_POLICY: &str = "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'";

/// Applies the documentation CSP to successful HTML responses.
///
/// Swagger requires runtime inline styles; scripts and requests stay same-origin.
/// Other responses retain the API-wide policy.
pub(super) async fn documentation_csp(
    request: ServiceRequest,
    next: Next<impl MessageBody>,
) -> Result<ServiceResponse<impl MessageBody>, Error> {
    let mut response = next.call(request).await?;
    let is_html = response
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|value| value.split(';').next() == Some("text/html"));
    if response.status().is_success() && is_html {
        response
            .headers_mut()
            .insert(CONTENT_SECURITY_POLICY, HeaderValue::from_static(UI_POLICY));
    }
    Ok(response)
}
