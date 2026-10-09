//! Process configuration, read once from the environment at startup. Only the service package
//! depends on this crate; domain and application code never reads the environment.

pub(crate) mod config;
pub(crate) mod config_error;
pub(crate) mod setting;

pub use config::Config;
pub use config_error::ConfigError;
#[cfg(feature = "api-doc")]
pub use setting::ApiDocConfig;
pub use setting::{CorsOrigin, DatabaseConfig, DatabaseUrl, HttpConfig, LogFormat};
