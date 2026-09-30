use crate::application::error::{CompleteTaskError, CreateTaskError, GetTaskError};

#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum TaskControllerError {
    #[error("invalid title")]
    InvalidTitle,
    #[error("task not found")]
    NotFound,
    #[error("task already completed")]
    AlreadyCompleted,
    #[error("task persistence failed")]
    Persistence,
}

impl From<CreateTaskError> for TaskControllerError {
    fn from(error: CreateTaskError) -> Self {
        match error {
            CreateTaskError::InvalidTitle => Self::InvalidTitle,
            CreateTaskError::Persistence => Self::Persistence,
        }
    }
}

impl From<GetTaskError> for TaskControllerError {
    fn from(error: GetTaskError) -> Self {
        match error {
            GetTaskError::NotFound => Self::NotFound,
            GetTaskError::Persistence => Self::Persistence,
        }
    }
}

impl From<CompleteTaskError> for TaskControllerError {
    fn from(error: CompleteTaskError) -> Self {
        match error {
            CompleteTaskError::NotFound => Self::NotFound,
            CompleteTaskError::AlreadyCompleted => Self::AlreadyCompleted,
            CompleteTaskError::Persistence => Self::Persistence,
        }
    }
}
