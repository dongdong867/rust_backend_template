use std::time::Duration;

/// Validated limits and cross-origin policy applied to every HTTP request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpConfig {
    pub cors_enabled: bool,
    pub cors_allowed_origins: Vec<String>,
    pub request_timeout: Duration,
    pub request_body_limit: usize,
}

impl HttpConfig {
    pub(crate) const DEFAULT_CORS_ENABLED: bool = true;
    pub(crate) const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);
    pub(crate) const DEFAULT_REQUEST_BODY_LIMIT: usize = 1_048_576;
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
