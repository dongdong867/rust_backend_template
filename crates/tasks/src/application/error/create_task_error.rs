use super::TaskRepositoryError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CreateTaskError {
    InvalidTitle,
    Persistence,
}

impl From<TaskRepositoryError> for CreateTaskError {
    fn from(_: TaskRepositoryError) -> Self {
        Self::Persistence
    }
}
