use std::time::Duration;

use crate::DatabaseUrl;

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
    pub const MAX_ACQUIRE_TIMEOUT_SECS: u64 = 300;
}
