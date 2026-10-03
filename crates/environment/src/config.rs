use std::env;
use std::time::Duration;

use tracing_subscriber::EnvFilter;

use crate::{ConfigError, CorsOrigin, DatabaseConfig, DatabaseUrl, HttpConfig, LogFormat};

/// Validated, immutable settings for one process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub port: u16,
    pub log_filter: String,
    pub log_format: LogFormat,
    pub http: HttpConfig,
    pub database: DatabaseConfig,
}

impl Config {
    pub fn from_env() -> Result<Self, ConfigError> {
        Self::from_try_lookup(|name| match env::var(name) {
            Ok(value) => Ok(Some(value)),
            Err(env::VarError::NotPresent) => Ok(None),
            Err(env::VarError::NotUnicode(_)) => Err(ConfigError::NotUnicode { name }),
        })
    }

    /// Reads each setting through `lookup`; DATABASE_URL is required, others have defaults.
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
                Some(8080),
                "a port number from 0 to 65535",
                |value| value.parse().ok(),
            )?,
            log_filter: setting(
                &lookup,
                "RUST_LOG",
                Some("info".to_owned()),
                "a nonempty tracing filter such as info or debug,actix_server=warn",
                |value| {
                    (!value.trim().is_empty() && EnvFilter::try_new(value).is_ok())
                        .then(|| value.to_owned())
                },
            )?,
            log_format: setting(
                &lookup,
                "LOG_FORMAT",
                Some(LogFormat::Pretty),
                "pretty or json",
                |value| match value {
                    "pretty" => Some(LogFormat::Pretty),
                    "json" => Some(LogFormat::Json),
                    _ => None,
                },
            )?,
            http: read_http_config(&lookup)?,
            database: read_database_config(&lookup)?,
        })
    }
}

fn read_database_config(
    lookup: &impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
) -> Result<DatabaseConfig, ConfigError> {
    Ok(DatabaseConfig {
        url: secret_setting(
            lookup,
            "DATABASE_URL",
            None,
            "a PostgreSQL URI with a host",
            DatabaseUrl::parse,
        )?,
        max_connections: setting(
            lookup,
            "DATABASE_MAX_CONNECTIONS",
            Some(DatabaseConfig::DEFAULT_MAX_CONNECTIONS),
            "a positive 32-bit connection count",
            |value| value.parse().ok().filter(|count| *count > 0),
        )?,
    })
}

fn read_http_config(
    lookup: &impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
) -> Result<HttpConfig, ConfigError> {
    Ok(HttpConfig {
        cors_enabled: setting(
            lookup,
            "HTTP_CORS_ENABLED",
            Some(HttpConfig::DEFAULT_CORS_ENABLED),
            "true or false",
            |value| match value {
                "true" => Some(true),
                "false" => Some(false),
                _ => None,
            },
        )?,
        cors_allowed_origins: setting(
            lookup,
            "HTTP_CORS_ALLOWED_ORIGINS",
            Some(Vec::new()),
            "a comma-separated list of HTTP or HTTPS origins with an optional leftmost *. wildcard, or an empty list",
            parse_cors_allowed_origins,
        )?,
        request_timeout: setting(
            lookup,
            "HTTP_REQUEST_TIMEOUT_SECS",
            Some(HttpConfig::DEFAULT_REQUEST_TIMEOUT),
            "a positive number of seconds",
            |value| {
                value
                    .parse()
                    .ok()
                    .filter(|seconds| *seconds > 0)
                    .map(Duration::from_secs)
            },
        )?,
        request_body_limit: setting(
            lookup,
            "HTTP_REQUEST_BODY_LIMIT_BYTES",
            Some(HttpConfig::DEFAULT_REQUEST_BODY_LIMIT),
            "a positive number of bytes",
            |value| value.parse().ok().filter(|bytes| *bytes > 0),
        )?,
    })
}

fn parse_cors_allowed_origins(value: &str) -> Option<Vec<CorsOrigin>> {
    if value.is_empty() {
        return Some(Vec::new());
    }

    value.split(',').map(CorsOrigin::parse).collect()
}

/// Reads a setting with an optional default and public validation diagnostics.
fn setting<T>(
    lookup: impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
    name: &'static str,
    default: Option<T>,
    expected: &'static str,
    parse: impl Fn(&str) -> Option<T>,
) -> Result<T, ConfigError> {
    read_setting(lookup, name, default, parse, |value| ConfigError::Invalid {
        name,
        expected,
        value,
    })
}

/// Reads a setting without retaining its value in validation errors.
fn secret_setting<T>(
    lookup: impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
    name: &'static str,
    default: Option<T>,
    expected: &'static str,
    parse: impl Fn(&str) -> Option<T>,
) -> Result<T, ConfigError> {
    read_setting(lookup, name, default, parse, |_| {
        ConfigError::InvalidSecret { name, expected }
    })
}

/// A setting without a declared default is required, regardless of its name or type.
fn read_setting<T>(
    lookup: impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
    name: &'static str,
    default: Option<T>,
    parse: impl Fn(&str) -> Option<T>,
    invalid: impl Fn(String) -> ConfigError,
) -> Result<T, ConfigError> {
    match lookup(name)? {
        None => default.ok_or(ConfigError::Missing { name }),
        Some(value) => parse(&value).ok_or_else(|| invalid(value)),
    }
}

#[cfg(test)]
mod tests {
    use std::collections::HashMap;
    use std::time::Duration;

    use super::*;
    use crate::{CorsOrigin, HttpConfig};

    const TEST_DATABASE_URL: &str = "postgres://localhost/environment_test";

    fn test_database_config() -> DatabaseConfig {
        DatabaseConfig {
            url: DatabaseUrl::parse(TEST_DATABASE_URL).unwrap(),
            max_connections: DatabaseConfig::DEFAULT_MAX_CONNECTIONS,
        }
    }

    fn load(vars: &[(&str, &str)]) -> Result<Config, ConfigError> {
        let mut vars: HashMap<String, String> = vars
            .iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect();
        vars.entry("DATABASE_URL".to_owned())
            .or_insert_with(|| TEST_DATABASE_URL.to_owned());
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
    fn settings_without_defaults_are_required_for_any_key() {
        let result = setting(
            |_| Ok(None),
            "REQUIRED_SETTING",
            None::<u16>,
            "a number",
            |value| value.parse::<u16>().ok(),
        );
        assert!(matches!(
            result,
            Err(ConfigError::Missing {
                name: "REQUIRED_SETTING"
            })
        ));
    }

    #[test]
    fn declared_defaults_are_used_only_when_the_setting_is_absent() {
        for (value, expected) in [(None, Ok(7)), (Some("11"), Ok(11))] {
            assert_eq!(
                setting(
                    |_| Ok(value.map(str::to_owned)),
                    "NUMBER_SETTING",
                    Some(7_u16),
                    "a number",
                    |value| value.parse().ok(),
                ),
                expected,
            );
        }
        assert_eq!(
            setting(
                |_| Ok(Some("bad".to_owned())),
                "NUMBER_SETTING",
                Some(7_u16),
                "a number",
                |value| value.parse().ok(),
            ),
            Err(ConfigError::Invalid {
                name: "NUMBER_SETTING",
                expected: "a number",
                value: "bad".to_owned(),
            }),
        );
    }

    #[test]
    fn required_settings_parse_provided_values_and_propagate_lookup_errors() {
        assert_eq!(
            setting(
                |_| Ok(Some("11".to_owned())),
                "REQUIRED_SETTING",
                None::<u16>,
                "a number",
                |value| value.parse().ok(),
            ),
            Ok(11),
        );
        assert_eq!(
            setting(
                |_| Err(ConfigError::NotUnicode {
                    name: "REQUIRED_SETTING"
                }),
                "REQUIRED_SETTING",
                None::<u16>,
                "a number",
                |value| value.parse().ok(),
            ),
            Err(ConfigError::NotUnicode {
                name: "REQUIRED_SETTING"
            }),
        );
    }

    #[test]
    fn secret_settings_share_the_required_rule_without_disclosing_invalid_values() {
        assert_eq!(
            secret_setting(
                |_| Ok(None),
                "REQUIRED_SECRET",
                None::<u16>,
                "a number",
                |value| value.parse().ok(),
            ),
            Err(ConfigError::Missing {
                name: "REQUIRED_SECRET"
            }),
        );
        let error = secret_setting(
            |_| Ok(Some("SENSITIVE_TEST_VALUE".to_owned())),
            "REQUIRED_SECRET",
            None::<u16>,
            "a number",
            |value| value.parse().ok(),
        )
        .unwrap_err();
        assert!(matches!(
            error,
            ConfigError::InvalidSecret {
                name: "REQUIRED_SECRET",
                ..
            }
        ));
        assert!(!error.to_string().contains("SENSITIVE_TEST_VALUE"));
        assert!(!format!("{error:?}").contains("SENSITIVE_TEST_VALUE"));
    }

    #[test]
    fn database_url_is_required() {
        let result = Config::from_lookup(|_| None);
        assert!(result.is_err(), "missing DATABASE_URL must reject startup");
        let error = result.unwrap_err();
        assert_eq!(
            error,
            ConfigError::Missing {
                name: "DATABASE_URL"
            }
        );
        assert_eq!(error.to_string(), "DATABASE_URL is required");
    }

    #[test]
    fn defaults_apply_with_only_required_database_url() {
        let config = load(&[]).unwrap();

        assert_eq!(
            config,
            Config {
                port: 8080,
                log_filter: "info".to_owned(),
                log_format: LogFormat::Pretty,
                database: test_database_config(),
                http: HttpConfig {
                    cors_enabled: true,
                    cors_allowed_origins: Vec::new(),
                    request_timeout: Duration::from_secs(30),
                    request_body_limit: 1_048_576,
                },
            }
        );
    }

    #[test]
    fn reads_every_setting() {
        let config = load(&[
            ("PORT", "3000"),
            ("RUST_LOG", "debug,actix_server=warn"),
            ("LOG_FORMAT", "json"),
            ("HTTP_CORS_ENABLED", "false"),
            (
                "HTTP_CORS_ALLOWED_ORIGINS",
                "https://app.example.com, http://localhost:3000/",
            ),
            ("HTTP_REQUEST_TIMEOUT_SECS", "15"),
            ("HTTP_REQUEST_BODY_LIMIT_BYTES", "2048"),
        ])
        .unwrap();

        assert_eq!(
            config,
            Config {
                port: 3000,
                log_filter: "debug,actix_server=warn".to_owned(),
                log_format: LogFormat::Json,
                database: test_database_config(),
                http: HttpConfig {
                    cors_enabled: false,
                    cors_allowed_origins: vec![
                        CorsOrigin::parse("https://app.example.com").unwrap(),
                        CorsOrigin::parse("http://localhost:3000").unwrap(),
                    ],
                    request_timeout: Duration::from_secs(15),
                    request_body_limit: 2048,
                },
            }
        );
    }

    #[test]
    fn port_zero_asks_the_system_for_a_free_port() {
        assert_eq!(load(&[("PORT", "0")]).unwrap().port, 0);
    }

    #[test]
    fn database_connection_default_and_explicit_values() {
        let default = load(&[]).unwrap();
        assert_eq!(default.database.url.as_str(), TEST_DATABASE_URL);
        assert_eq!(default.database.max_connections, 10);
        for uri in [
            "postgres://user:password@localhost:5432/db?token=secret",
            "postgresql://[::1]/db",
            "postgresql://user:p%40ss@db.example/db?application_name=my%20app",
        ] {
            let config =
                load(&[("DATABASE_URL", uri), ("DATABASE_MAX_CONNECTIONS", "42")]).unwrap();
            assert_eq!(config.database.url.as_str(), uri);
            assert_eq!(config.database.max_connections, 42);
            for debug in [
                format!("{config:?}"),
                format!("{:?}", config.database),
                format!("{:?}", config.database.url),
            ] {
                assert!(!debug.contains(uri));
                assert!(!debug.contains("password"));
                assert!(!debug.contains("secret"));
            }
        }
    }

    #[test]
    fn invalid_database_urls_are_secret_errors() {
        for uri in [
            "",
            "bad-secret",
            "http://user:secret@localhost/db",
            "postgres:///db",
            "postgres:db",
            "postgres://",
            "postgres://user:secret@/db",
            "postgres://localhost:bad/db",
            "postgres://bad%20host/db",
            "postgres://localhost:65536/db",
            " postgres://localhost/db",
            "\0postgres://localhost/db",
        ] {
            let error = load(&[("DATABASE_URL", uri)]).unwrap_err();
            assert!(matches!(
                error,
                ConfigError::InvalidSecret {
                    name: "DATABASE_URL",
                    ..
                }
            ));
            for rendered in [error.to_string(), format!("{error:?}")] {
                assert!(rendered.contains("DATABASE_URL"));
                assert!(!rendered.contains("secret"));
                if !uri.is_empty() {
                    assert!(!rendered.contains(uri));
                }
            }
        }
    }

    #[test]
    fn database_connection_limits_are_positive_u32() {
        for value in ["0", "", "-1", "many", "4294967296"] {
            assert_eq!(
                invalid_name(&[("DATABASE_MAX_CONNECTIONS", value)]),
                "DATABASE_MAX_CONNECTIONS"
            );
        }
        assert_eq!(
            load(&[("DATABASE_MAX_CONNECTIONS", "4294967295")])
                .unwrap()
                .database
                .max_connections,
            u32::MAX
        );
    }

    #[test]
    fn nonunicode_database_settings_fail_without_the_value() {
        for setting in ["DATABASE_URL", "DATABASE_MAX_CONNECTIONS"] {
            let error = Config::from_try_lookup(|name| {
                if name == setting {
                    Err(ConfigError::NotUnicode { name })
                } else if name == "DATABASE_URL" {
                    Ok(Some(TEST_DATABASE_URL.to_owned()))
                } else {
                    Ok(None)
                }
            })
            .unwrap_err();
            assert_eq!(error, ConfigError::NotUnicode { name: setting });
            assert!(error.to_string().contains(setting));
        }
    }

    #[test]
    fn reads_each_database_setting_once() {
        use std::cell::RefCell;

        let calls = RefCell::new(HashMap::new());
        Config::from_try_lookup(|name| {
            *calls.borrow_mut().entry(name.to_owned()).or_insert(0) += 1;
            Ok((name == "DATABASE_URL").then(|| TEST_DATABASE_URL.to_owned()))
        })
        .unwrap();
        assert_eq!(calls.borrow()["DATABASE_URL"], 1);
        assert_eq!(calls.borrow()["DATABASE_MAX_CONNECTIONS"], 1);
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
    fn an_empty_cors_origin_list_allows_no_origins() {
        assert!(
            load(&[("HTTP_CORS_ALLOWED_ORIGINS", "")])
                .unwrap()
                .http
                .cors_allowed_origins
                .is_empty()
        );
    }

    #[test]
    fn rejects_an_unknown_cors_enabled_value() {
        assert_eq!(
            invalid_name(&[("HTTP_CORS_ENABLED", "yes")]),
            "HTTP_CORS_ENABLED"
        );
    }

    #[test]
    fn rejects_the_allow_any_origin_wildcard() {
        assert_eq!(
            invalid_name(&[("HTTP_CORS_ALLOWED_ORIGINS", "*")]),
            "HTTP_CORS_ALLOWED_ORIGINS"
        );
    }

    #[test]
    fn rejects_a_cors_origin_with_a_path() {
        assert_eq!(
            invalid_name(&[("HTTP_CORS_ALLOWED_ORIGINS", "https://example.com/private")]),
            "HTTP_CORS_ALLOWED_ORIGINS"
        );
    }

    #[test]
    fn rejects_a_non_http_cors_origin() {
        assert_eq!(
            invalid_name(&[("HTTP_CORS_ALLOWED_ORIGINS", "file:///tmp/example")]),
            "HTTP_CORS_ALLOWED_ORIGINS"
        );
    }

    #[test]
    fn rejects_an_empty_origin_between_commas() {
        assert_eq!(
            invalid_name(&[(
                "HTTP_CORS_ALLOWED_ORIGINS",
                "https://one.example,,https://two.example"
            )]),
            "HTTP_CORS_ALLOWED_ORIGINS"
        );
    }

    #[test]
    fn rejects_a_zero_request_timeout() {
        assert_eq!(
            invalid_name(&[("HTTP_REQUEST_TIMEOUT_SECS", "0")]),
            "HTTP_REQUEST_TIMEOUT_SECS"
        );
    }

    #[test]
    fn rejects_a_request_timeout_that_is_not_a_number() {
        assert_eq!(
            invalid_name(&[("HTTP_REQUEST_TIMEOUT_SECS", "soon")]),
            "HTTP_REQUEST_TIMEOUT_SECS"
        );
    }

    #[test]
    fn rejects_a_zero_request_body_limit() {
        assert_eq!(
            invalid_name(&[("HTTP_REQUEST_BODY_LIMIT_BYTES", "0")]),
            "HTTP_REQUEST_BODY_LIMIT_BYTES"
        );
    }

    #[test]
    fn rejects_a_request_body_limit_that_is_not_a_number() {
        assert_eq!(
            invalid_name(&[("HTTP_REQUEST_BODY_LIMIT_BYTES", "large")]),
            "HTTP_REQUEST_BODY_LIMIT_BYTES"
        );
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
