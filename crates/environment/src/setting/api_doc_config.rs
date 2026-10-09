use std::fmt;

/// Validated credentials for access to API documentation.
#[derive(Clone, PartialEq, Eq)]
pub struct ApiDocConfig {
    username: String,
    password: String,
}

impl ApiDocConfig {
    pub fn new(username: &str, password: &str) -> Option<Self> {
        (Self::valid_username(username) && !password.is_empty()).then(|| Self {
            username: username.to_owned(),
            password: password.to_owned(),
        })
    }

    pub fn username(&self) -> &str {
        &self.username
    }

    pub fn password(&self) -> &str {
        &self.password
    }

    pub(crate) fn valid_username(username: &str) -> bool {
        !username.is_empty() && !username.chars().any(|c| c == ':' || c.is_control())
    }
}

impl fmt::Debug for ApiDocConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ApiDocConfig([REDACTED])")
    }
}

#[cfg(test)]
mod tests {
    use super::ApiDocConfig;

    #[test]
    fn rejects_invalid_credentials() {
        for username in [
            "",
            "private:user",
            "private\nuser",
            "private\u{85}user",
            "private\0user",
        ] {
            assert!(ApiDocConfig::new(username, "synthetic-pass").is_none());
        }
        assert!(ApiDocConfig::new("synthetic-user", "").is_none());
    }

    #[test]
    fn preserves_valid_credentials_but_redacts_debug() {
        let config = ApiDocConfig::new(
            " synthetic-user-\u{00e9} ",
            " synthetic-password:\n\0\u{1f642} ",
        )
        .unwrap();

        assert_eq!(config.username(), " synthetic-user-\u{00e9} ");
        assert_eq!(config.password(), " synthetic-password:\n\0\u{1f642} ");
        assert_eq!(config.clone(), config);

        let debug = format!("{config:?}");
        assert!(!debug.contains(config.username()));
        assert!(!debug.contains(config.password()));
        assert!(!debug.contains("synthetic-user"));
        assert!(!debug.contains("synthetic-password"));
    }
}
