use std::env;
use std::time::Duration;

use tracing_subscriber::EnvFilter;
use url::Url;

use crate::{ConfigError, HttpConfig, LogFormat};

/// Validated, immutable settings for one process.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    pub port: u16,
    pub log_filter: String,
    pub log_format: LogFormat,
    pub http: HttpConfig,
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
            http: read_http_config(&lookup)?,
        })
    }
}

fn read_http_config(
    lookup: &impl Fn(&'static str) -> Result<Option<String>, ConfigError>,
) -> Result<HttpConfig, ConfigError> {
    Ok(HttpConfig {
        cors_enabled: setting(
            lookup,
            "HTTP_CORS_ENABLED",
            HttpConfig::DEFAULT_CORS_ENABLED,
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
            Vec::new(),
            "a comma-separated list of HTTP or HTTPS origins without paths, or an empty list",
            parse_cors_allowed_origins,
        )?,
        request_timeout: setting(
            lookup,
            "HTTP_REQUEST_TIMEOUT_SECS",
            HttpConfig::DEFAULT_REQUEST_TIMEOUT,
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
            HttpConfig::DEFAULT_REQUEST_BODY_LIMIT,
            "a positive number of bytes",
            |value| value.parse().ok().filter(|bytes| *bytes > 0),
        )?,
    })
}

fn parse_cors_allowed_origins(value: &str) -> Option<Vec<String>> {
    if value.is_empty() {
        return Some(Vec::new());
    }

    value.split(',').map(parse_cors_origin).collect()
}

fn parse_cors_origin(value: &str) -> Option<String> {
    let url = Url::parse(value.trim()).ok()?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/"
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return None;
    }

    let origin = url.origin().ascii_serialization();
    (origin != "null").then_some(origin)
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
    use std::time::Duration;

    use super::*;
    use crate::HttpConfig;

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
                http: HttpConfig {
                    cors_enabled: false,
                    cors_allowed_origins: vec![
                        "https://app.example.com".to_owned(),
                        "http://localhost:3000".to_owned(),
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
    fn rejects_a_wildcard_cors_origin() {
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
