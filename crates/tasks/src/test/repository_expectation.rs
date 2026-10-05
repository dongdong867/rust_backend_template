use crate::{application::error::TaskRepositoryError, domain::Task};
use uuid::Uuid;

/// One ordered interaction with the application repository port.
pub(crate) enum RepositoryExpectation {
    Get(Uuid, Result<Task, TaskRepositoryError>),
    Create(Box<dyn FnOnce(Task) -> Result<Task, TaskRepositoryError> + Send + Sync>),
    Complete(Box<dyn FnOnce(Task) -> Result<Task, TaskRepositoryError> + Send + Sync>),
}
