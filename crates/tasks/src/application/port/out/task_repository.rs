use async_trait::async_trait;
use uuid::Uuid;

use crate::{application::error::TaskRepositoryError, domain::Task};

#[async_trait]
pub trait TaskRepository: Send + Sync {
    async fn create(&self, task: Task) -> Result<Task, TaskRepositoryError>;
    async fn get(&self, id: Uuid) -> Result<Task, TaskRepositoryError>;
    /// Persist an open -> completed transition atomically. Only one caller may succeed.
    async fn complete(&self, task: Task) -> Result<Task, TaskRepositoryError>;
}
