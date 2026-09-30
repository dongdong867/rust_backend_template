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
        {
            return None;
        }
        Some(Self(value.to_owned()))
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
