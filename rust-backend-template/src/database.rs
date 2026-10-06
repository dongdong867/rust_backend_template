//! Shared PostgreSQL resources. Neither pool construction nor liveness connects to the database.

use std::io;
use std::str::FromStr;

use environment::DatabaseConfig;
use sqlx::postgres::{PgConnectOptions, PgPoolOptions};
use sqlx::{ConnectOptions, PgPool};

/// Creates one lazy pool. Invalid options are reported without echoing the connection string.
pub fn create_pool(config: &DatabaseConfig) -> io::Result<PgPool> {
    let options = PgConnectOptions::from_str(config.url.as_str())
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid DATABASE_URL: expected PostgreSQL connection options",
            )
        })?
        .disable_statement_logging();
    Ok(PgPoolOptions::new()
        .min_connections(0)
        .max_connections(config.max_connections)
        .connect_lazy_with(options))
}
