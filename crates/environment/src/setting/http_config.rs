use std::time::Duration;

use crate::CorsOrigin;

/// Validated limits and cross-origin policy applied to every HTTP request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpConfig {
    pub cors_enabled: bool,
    pub cors_allowed_origins: Vec<CorsOrigin>,
    pub request_timeout: Duration,
    pub request_body_limit: usize,
}

impl HttpConfig {
    pub(crate) const DEFAULT_CORS_ENABLED: bool = true;
    pub(crate) const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
    pub(crate) const DEFAULT_REQUEST_BODY_LIMIT: usize = 1_048_576;

    /// Whether CORS is enabled and the concrete origin matches the configured allowlist.
    pub fn allows_origin(&self, origin: &str) -> bool {
        self.cors_enabled
            && self
                .cors_allowed_origins
                .iter()
                .any(|allowed| allowed.matches(origin))
    }
}

impl Default for HttpConfig {
    fn default() -> Self {
        Self {
            cors_enabled: Self::DEFAULT_CORS_ENABLED,
            cors_allowed_origins: Vec::new(),
            request_timeout: Self::DEFAULT_REQUEST_TIMEOUT,
            request_body_limit: Self::DEFAULT_REQUEST_BODY_LIMIT,
        }
    }
}
