use super::TaskRepositoryError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GetTaskError {
    NotFound,
    Persistence,
}

impl From<TaskRepositoryError> for GetTaskError {
    fn from(error: TaskRepositoryError) -> Self {
        match error {
            TaskRepositoryError::NotFound => Self::NotFound,
            TaskRepositoryError::AlreadyCompleted | TaskRepositoryError::Persistence => {
                Self::Persistence
            }
        }
    }
}
