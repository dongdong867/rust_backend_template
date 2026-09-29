//! Process configuration, read once from the environment at startup. Only the service package
//! depends on this crate; domain and application code never reads the environment.

pub(crate) mod config;
pub(crate) mod config_error;
pub(crate) mod log_format;

pub use config::Config;
pub use config_error::ConfigError;
pub use log_format::LogFormat;
