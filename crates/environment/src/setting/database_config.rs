use crate::DatabaseUrl;

/// Validated database connection settings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DatabaseConfig {
    pub url: DatabaseUrl,
    pub max_connections: u32,
}

impl Default for DatabaseConfig {
    fn default() -> Self {
        Self {
            url: DatabaseUrl::parse("postgres://localhost/rust_backend_template")
                .expect("the default database URI is valid"),
            max_connections: 10,
        }
    }
}
