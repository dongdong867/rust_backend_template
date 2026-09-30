#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum TaskRepositoryError {
    #[error("task not found")]
    NotFound,
    #[error("task already completed")]
    AlreadyCompleted,
    #[error("task persistence failed")]
    Persistence,
}
