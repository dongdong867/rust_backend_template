use crate::application::error::TaskRepositoryError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TaskStorageError {
    NotFound,
    AlreadyCompleted,
    Persistence,
}

impl From<TaskStorageError> for TaskRepositoryError {
    fn from(error: TaskStorageError) -> Self {
        match error {
            TaskStorageError::NotFound => Self::NotFound,
            TaskStorageError::AlreadyCompleted => Self::AlreadyCompleted,
            TaskStorageError::Persistence => Self::Persistence,
        }
    }
}
