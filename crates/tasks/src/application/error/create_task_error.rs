#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum CreateTaskError {
    #[error("invalid title")]
    InvalidTitle,
    #[error("task persistence failed")]
    Persistence,
}
