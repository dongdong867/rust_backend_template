use std::time::Duration;

use crate::DatabaseUrl;

// The limit is a macro literal so `concat!` can put the same number in the startup message.
macro_rules! max_acquire_timeout_secs {
    () => {
        300
    };
}

/// Validated database connection settings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DatabaseConfig {
    pub url: DatabaseUrl,
    pub max_connections: u32,
    pub acquire_timeout: Duration,
}

impl DatabaseConfig {
    pub const DEFAULT_MAX_CONNECTIONS: u32 = 10;
    pub const DEFAULT_ACQUIRE_TIMEOUT: Duration = Duration::from_secs(10);
    pub const MAX_ACQUIRE_TIMEOUT_SECS: u64 = max_acquire_timeout_secs!();
    pub(crate) const ACQUIRE_TIMEOUT_EXPECTED: &'static str = concat!(
        "a positive number of seconds up to ",
        max_acquire_timeout_secs!()
    );
}
