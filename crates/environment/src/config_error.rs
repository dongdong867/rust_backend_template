/// A setting that is set but cannot be used. It names the setting so the operator can fix it.
#[derive(Debug, PartialEq, Eq, thiserror::Error)]
pub enum ConfigError {
    #[error("invalid {name}: expected {expected}, got {value:?}")]
    Invalid {
        name: &'static str,
        expected: &'static str,
        value: String,
    },

    #[error("invalid {name}: expected Unicode text")]
    NotUnicode { name: &'static str },
}
