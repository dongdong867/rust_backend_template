use std::fmt;

use url::{Host, Url};

/// A validated PostgreSQL connection URI. Debug output never exposes credentials.
#[derive(Clone, PartialEq, Eq)]
pub struct DatabaseUrl(String);

impl DatabaseUrl {
    pub fn parse(value: &str) -> Option<Self> {
        let url = Url::parse(value).ok()?;
        if !matches!(url.scheme(), "postgres" | "postgresql")
            || !url.has_host()
            || url.host_str().is_none_or(str::is_empty)
            || url.host_str().is_none_or(|host| Host::parse(host).is_err())
            || !value.contains("://")
            || value
                .chars()
                .any(|character| character.is_whitespace() || character.is_control())
            || url
                .query_pairs()
                .any(|(key, _)| !Self::supports_parameter(&key))
        {
            return None;
        }
        Some(Self(value.to_owned()))
    }

    fn supports_parameter(key: &str) -> bool {
        // Keep aligned with sqlx-postgres 0.9.0 options/parse.rs, our selected
        // connection protocol. Unknown decoded keys intentionally reject startup:
        // SQLx otherwise logs both the unrecognized key and its secret value.
        match key {
            "sslmode"
            | "ssl-mode"
            | "sslrootcert"
            | "ssl-root-cert"
            | "ssl-ca"
            | "sslcert"
            | "ssl-cert"
            | "sslkey"
            | "ssl-key"
            | "statement-cache-capacity"
            | "host"
            | "hostaddr"
            | "port"
            | "dbname"
            | "user"
            | "password"
            | "application_name"
            | "options" => true,
            _ => key
                .strip_prefix("options[")
                .and_then(|name| name.strip_suffix(']'))
                .is_some_and(|name| {
                    // Nonempty ASCII identifiers, optionally dot-separated for
                    // extension settings. Exclude brackets, whitespace, controls,
                    // and option syntax rather than silently accepting malformed keys.
                    name.split('.').all(|part| {
                        let mut bytes = part.bytes();
                        bytes
                            .next()
                            .is_some_and(|first| first.is_ascii_alphabetic() || first == b'_')
                            && bytes.all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
                    })
                }),
        }
    }

    /// Explicit access to the original URI; callers must not log this value.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for DatabaseUrl {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("DatabaseUrl([REDACTED])")
    }
}

#[cfg(test)]
mod tests {
    use super::DatabaseUrl;

    #[test]
    fn rejects_unsupported_decoded_connection_parameter_names() {
        for query in [
            "token=PARAMETER_SECRET",
            "%74oken=PARAMETER_SECRET",
            "=PARAMETER_SECRET",
            "unknown_name=PARAMETER_SECRET",
            "application_name=ok&token=PARAMETER_SECRET",
            "token=PARAMETER_SECRET&token=another",
            "options[]=PARAMETER_SECRET",
            "options[work_mem=PARAMETER_SECRET",
            "options[work_mem]extra=PARAMETER_SECRET",
            "options[work[mem]]=PARAMETER_SECRET",
            "options[work+mem]=PARAMETER_SECRET",
            "options[work%00mem]=PARAMETER_SECRET",
            "options[.custom]=PARAMETER_SECRET",
        ] {
            let uri = format!("postgres://user:pass@localhost/db?{query}");
            assert!(DatabaseUrl::parse(&uri).is_none(), "accepted {query}");
        }
    }

    #[test]
    fn preserves_supported_parameters_and_aliases_without_rewriting_the_uri() {
        for query in [
            "sslmode=require",
            "ssl-mode=verify-full",
            "sslrootcert=%2Ftmp%2Froot.pem",
            "ssl-root-cert=root.pem",
            "ssl-ca=root.pem",
            "sslcert=client.pem",
            "ssl-cert=client.pem",
            "sslkey=client.key",
            "ssl-key=client.key",
            "statement-cache-capacity=100",
            "host=localhost",
            "hostaddr=127.0.0.1",
            "port=5432",
            "dbname=db",
            "user=user",
            "password=PARAMETER_SECRET",
            "application_name=service",
            "%61pplication_name=service",
            "options=-c%20work_mem%3D64MB",
            "options%5Bwork_mem%5D=64MB",
            "options[my_extension.setting_2]=value",
            "application_name=first&application_name=last",
        ] {
            let uri = format!("postgres://user:pass@localhost/db?{query}");
            assert_eq!(DatabaseUrl::parse(&uri).unwrap().as_str(), uri);
        }
    }
}
