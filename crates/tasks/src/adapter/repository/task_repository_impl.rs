use crate::{
    adapter::{
        dto::storage::TaskStorageRecord, error::TaskStorageError, port::out::TaskStorageProvider,
    },
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
impl From<Task> for TaskStorageRecord {
    fn from(task: Task) -> Self {
        Self {
            id: task.id(),
            title: task.title().into(),
            status: match task.status() {
                TaskStatus::Open => "open",
                TaskStatus::Completed => "completed",
            }
            .into(),
            created_at: task.created_at(),
            completed_at: task.completed_at(),
        }
    }
}
impl TryFrom<TaskStorageRecord> for Task {
    type Error = TaskRepositoryError;
    fn try_from(record: TaskStorageRecord) -> Result<Self, Self::Error> {
        let status = match record.status.as_str() {
            "open" => TaskStatus::Open,
            "completed" => TaskStatus::Completed,
            _ => return Err(TaskRepositoryError::Persistence),
        };
        Task::from_parts(
            record.id,
            record.title,
            status,
            record.created_at,
            record.completed_at,
        )
        .map_err(|_| TaskRepositoryError::Persistence)
    }
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
        // Hydrate the persisted timestamp representation; storage may normalize precision.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_every_field_without_losing_domain_invariants() {
        let mut task = Task::new("  界\n ".into()).unwrap();
        for status in [TaskStatus::Open, TaskStatus::Completed] {
            if status == TaskStatus::Completed {
                task.complete().unwrap();
            }
            let record: TaskStorageRecord = task.clone().into();
            assert_eq!(record.id, task.id());
            assert_eq!(record.title, task.title());
            assert_eq!(
                record.status,
                if status == TaskStatus::Open {
                    "open"
                } else {
                    "completed"
                }
            );
            assert_eq!(record.created_at, task.created_at());
            assert_eq!(record.completed_at, task.completed_at());
            assert_eq!(Task::try_from(record).unwrap(), task);
        }
    }
}
