//! Shared PostgreSQL resources. Neither pool construction nor liveness connects to the database.

use std::io;
use std::str::FromStr;
use std::time::Duration;

use environment::DatabaseConfig;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{ConnectOptions, PgPool};

/// Creates the service's lazy pool. PostgreSQL cancels a statement on its connections that
/// runs longer than the configured statement timeout.
pub fn create_pool(config: &DatabaseConfig) -> io::Result<PgPool> {
    build_pool(config, Some(config.statement_timeout))
}

/// Creates a lazy pool for `migrate`. It sets no statement timeout, so a long migration can finish.
pub fn create_migration_pool(config: &DatabaseConfig) -> io::Result<PgPool> {
    build_pool(config, None)
}

/// Invalid options are reported without echoing the connection string.
fn build_pool(config: &DatabaseConfig, statement_timeout: Option<Duration>) -> io::Result<PgPool> {
    let mut options = PgConnectOptions::from_str(config.url.as_str())
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid DATABASE_URL: expected PostgreSQL connection options",
            )
        })?
        .disable_statement_logging();
    if let Some(timeout) = statement_timeout {
        // PostgreSQL applies startup options in order, so this one wins over the URL's.
        options = options.options([("statement_timeout", timeout.as_millis())]);
    }
    Ok(PgPoolOptions::new()
        .min_connections(0)
        .max_connections(config.max_connections)
        .acquire_timeout(config.acquire_timeout)
        .connect_lazy_with(options))
}
