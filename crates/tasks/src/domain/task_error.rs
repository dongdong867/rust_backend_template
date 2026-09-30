#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum TaskError {
    #[error("invalid title")]
    InvalidTitle,
    #[error("task already completed")]
    AlreadyCompleted,
    #[error("invalid task state")]
    InvalidState,
}
