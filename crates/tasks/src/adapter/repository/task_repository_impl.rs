use crate::{
    adapter::{dto::storage::TaskStorageRecord, port::out::TaskStorageProvider},
    application::{error::TaskRepositoryError, port::out::TaskRepository},
    domain::{Task, TaskStatus},
};
use async_trait::async_trait;
use std::sync::Arc;
use uuid::Uuid;

pub struct TaskRepositoryImpl {
    storage_service: Arc<dyn TaskStorageProvider>,
}

impl TaskRepositoryImpl {
    pub fn new(storage_service: Arc<dyn TaskStorageProvider>) -> Self {
        Self { storage_service }
    }
}

#[async_trait]
impl TaskRepository for TaskRepositoryImpl {
    async fn create(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        if task.status() != TaskStatus::Open {
            return Err(TaskRepositoryError::Persistence);
        }
        let input: TaskStorageRecord = task.into();
        let record = self
            .storage_service
            .create(input.clone())
            .await
            .map_err(TaskRepositoryError::from)?;
        if record.id != input.id || record.title != input.title || record.status != "open" {
            return Err(TaskRepositoryError::Persistence);
        }
        record.try_into()
    }

    async fn get(&self, id: Uuid) -> Result<Task, TaskRepositoryError> {
        let record = self
            .storage_service
            .get(id)
            .await
            .map_err(TaskRepositoryError::from)?;
        if record.id != id {
            return Err(TaskRepositoryError::Persistence);
        }
        record.try_into()
    }

    async fn complete(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        if task.status() != TaskStatus::Completed {
            return Err(TaskRepositoryError::Persistence);
        }
        let input: TaskStorageRecord = task.into();
        let record = self
            .storage_service
            .complete(input.clone())
            .await
            .map_err(TaskRepositoryError::from)?;
        if record.id != input.id
            || record.title != input.title
            || record.created_at != input.created_at
            || record.status != "completed"
        {
            return Err(TaskRepositoryError::Persistence);
        }
        record.try_into()
    }
}
