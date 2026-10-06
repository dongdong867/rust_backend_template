/// A missing or unusable setting, named so the operator can fix it.
#[derive(Debug, PartialEq, Eq, thiserror::Error)]
pub enum ConfigError {
    #[error("{name} is required")]
    Missing { name: &'static str },

    #[error("invalid {name}: expected {expected}, got {value:?}")]
    Invalid {
        name: &'static str,
        expected: &'static str,
        value: String,
    },

    #[error("invalid {name}: expected {expected}")]
    InvalidSecret {
        name: &'static str,
        expected: &'static str,
    },

    #[error("invalid {name}: expected Unicode text")]
    NotUnicode { name: &'static str },
}
