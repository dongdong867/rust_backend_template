use super::TaskRepositoryError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CompleteTaskError {
    NotFound,
    AlreadyCompleted,
    Persistence,
}

impl From<TaskRepositoryError> for CompleteTaskError {
    fn from(error: TaskRepositoryError) -> Self {
        match error {
            TaskRepositoryError::NotFound => Self::NotFound,
            TaskRepositoryError::AlreadyCompleted => Self::AlreadyCompleted,
            TaskRepositoryError::Persistence => Self::Persistence,
        }
    }
}
