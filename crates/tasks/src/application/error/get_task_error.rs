#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum GetTaskError {
    #[error("task not found")]
    NotFound,
    #[error("task persistence failed")]
    Persistence,
}
