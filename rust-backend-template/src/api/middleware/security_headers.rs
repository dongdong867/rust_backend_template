use actix_web::http::header::{HeaderMap, HeaderName, HeaderValue};
use actix_web::middleware::DefaultHeaders;

const HEADERS: [(&str, &str); 5] = [
    (
        "content-security-policy",
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    ),
    (
        "permissions-policy",
        "camera=(), geolocation=(), microphone=()",
    ),
    ("referrer-policy", "no-referrer"),
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
];

/// The fixed browser hardening policy applied to every response.
pub(crate) struct SecurityHeaders;

impl SecurityHeaders {
    pub(crate) fn middleware() -> DefaultHeaders {
        HEADERS
            .iter()
            .fold(DefaultHeaders::new(), |headers, &(name, value)| {
                headers.add((name, value))
            })
    }

    /// Adds the defaults to a response created after the normal middleware chain timed out.
    pub(crate) fn apply(headers: &mut HeaderMap) {
        for &(name, value) in &HEADERS {
            let name = HeaderName::from_static(name);
            if !headers.contains_key(&name) {
                headers.insert(name, HeaderValue::from_static(value));
            }
        }
    }
}
