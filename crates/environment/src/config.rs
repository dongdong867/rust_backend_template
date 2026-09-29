use std::env;

use tracing_subscriber::EnvFilter;

use crate::{ConfigError, LogFormat};

/// Validated, immutable settings for one process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub port: u16,
    pub log_filter: String,
    pub log_format: LogFormat,
}

impl Config {
    pub fn from_env() -> Result<Self, ConfigError> {
        Self::from_try_lookup(|name| match env::var(name) {
            Ok(value) => Ok(Some(value)),
            Err(env::VarError::NotPresent) => Ok(None),
            Err(env::VarError::NotUnicode(_)) => Err(ConfigError::NotUnicode { name }),
        })
    }

    /// Reads each setting through `lookup`, applying defaults for unset ones.
    pub fn from_lookup(lookup: impl Fn(&str) -> Option<String>) -> Result<Self, ConfigError> {
        Self::from_try_lookup(|name| Ok(lookup(name)))
    }

    fn from_try_lookup(
        lookup: impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
    ) -> Result<Self, ConfigError> {
        Ok(Self {
            port: setting(
                &lookup,
                "PORT",
                8080,
                "a port number from 0 to 65535",
                |value| value.parse().ok(),
            )?,
            log_filter: setting(
                &lookup,
                "RUST_LOG",
                "info".to_owned(),
                "a nonempty tracing filter such as info or debug,actix_server=warn",
                |value| {
                    (!value.trim().is_empty() && EnvFilter::try_new(value).is_ok())
                        .then(|| value.to_owned())
                },
            )?,
            log_format: setting(
                &lookup,
                "LOG_FORMAT",
                LogFormat::Pretty,
                "pretty or json",
                |value| match value {
                    "pretty" => Some(LogFormat::Pretty),
                    "json" => Some(LogFormat::Json),
                    _ => None,
                },
            )?,
        })
    }
}

/// Returns `default` when `name` is unset, the parsed value when it parses, and an error naming
/// the setting otherwise.
fn setting<T>(
    lookup: impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
    name: &'static str,
    default: T,
    expected: &'static str,
    parse: impl Fn(&str) -> Option<T>,
) -> Result<T, ConfigError> {
    match lookup(name)? {
        None => Ok(default),
        Some(value) => parse(&value).ok_or(ConfigError::Invalid {
            name,
            expected,
            value,
        }),
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;

    use super::*;

    fn load(vars: &[(&str, &str)]) -> Result<Config, ConfigError> {
        let vars: HashMap<String, String> = vars
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        Config::from_lookup(|name| vars.get(name).cloned())
    }

    fn invalid_name(vars: &[(&str, &str)]) -> &'static str {
        match load(vars) {
            Err(ConfigError::Invalid { name, .. }) => name,
            Err(error) => panic!("expected an invalid-value error, got {error}"),
            Ok(config) => panic!("expected an error, got {config:?}"),
        }
    }

    #[test]
    fn defaults_apply_when_nothing_is_set() {
        let config = load(&[]).unwrap();

        assert_eq!(
            config,
            Config {
                port: 8080,
                log_filter: "info".to_owned(),
                log_format: LogFormat::Pretty,
            }
        );
    }

    #[test]
    fn reads_every_setting() {
        let config = load(&[
            ("PORT", "3000"),
            ("RUST_LOG", "debug,actix_server=warn"),
            ("LOG_FORMAT", "json"),
        ])
        .unwrap();

        assert_eq!(
            config,
            Config {
                port: 3000,
                log_filter: "debug,actix_server=warn".to_owned(),
                log_format: LogFormat::Json,
            }
        );
    }

    #[test]
    fn port_zero_asks_the_system_for_a_free_port() {
        assert_eq!(load(&[("PORT", "0")]).unwrap().port, 0);
    }

    #[test]
    fn rejects_a_port_that_is_not_a_number() {
        assert_eq!(invalid_name(&[("PORT", "http")]), "PORT");
    }

    #[test]
    fn rejects_a_port_above_65535() {
        assert_eq!(invalid_name(&[("PORT", "65536")]), "PORT");
    }

    #[test]
    fn rejects_an_empty_port() {
        assert_eq!(invalid_name(&[("PORT", "")]), "PORT");
    }

    #[test]
    fn rejects_an_unknown_log_format() {
        assert_eq!(invalid_name(&[("LOG_FORMAT", "xml")]), "LOG_FORMAT");
    }

    #[test]
    fn rejects_a_log_filter_that_does_not_parse() {
        assert_eq!(invalid_name(&[("RUST_LOG", "info,[")]), "RUST_LOG");
    }

    #[test]
    fn rejects_an_empty_log_filter_instead_of_silencing_info_logs() {
        assert_eq!(invalid_name(&[("RUST_LOG", "")]), "RUST_LOG");
        assert_eq!(invalid_name(&[("RUST_LOG", "  ")]), "RUST_LOG");
    }

    #[test]
    fn error_names_the_setting_and_the_value() {
        let error = load(&[("PORT", "http")]).unwrap_err();

        assert_eq!(
            error.to_string(),
            "invalid PORT: expected a port number from 0 to 65535, got \"http\""
        );
    }
}
