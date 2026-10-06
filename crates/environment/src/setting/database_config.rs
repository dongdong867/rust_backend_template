use crate::DatabaseUrl;

/// Validated database connection settings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DatabaseConfig {
    pub url: DatabaseUrl,
    pub max_connections: u32,
}

impl DatabaseConfig {
    pub const DEFAULT_MAX_CONNECTIONS: u32 = 10;
}
