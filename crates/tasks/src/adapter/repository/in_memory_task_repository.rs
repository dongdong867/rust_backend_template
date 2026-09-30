use std::{collections::HashMap, sync::Mutex};

use async_trait::async_trait;
use uuid::Uuid;

use crate::{
    application::{error::TaskRepositoryError, port::out::TaskRepository},
    domain::{Task, TaskStatus},
};

/// Test-only repository with the same atomic transition contract as PostgreSQL.
#[derive(Default)]
pub struct InMemoryTaskRepository {
    tasks: Mutex<HashMap<Uuid, Task>>,
    persistence_failure: bool,
}

impl InMemoryTaskRepository {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_persistence_failure() -> Self {
        Self {
            persistence_failure: true,
            ..Self::default()
        }
    }

    fn tasks(&self) -> Result<std::sync::MutexGuard<'_, HashMap<Uuid, Task>>, TaskRepositoryError> {
        if self.persistence_failure {
            return Err(TaskRepositoryError::Persistence);
        }
        self.tasks
            .lock()
            .map_err(|_| TaskRepositoryError::Persistence)
    }
}

#[async_trait]
impl TaskRepository for InMemoryTaskRepository {
    async fn create(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        let mut tasks = self.tasks()?;
        if task.status() != TaskStatus::Open || tasks.contains_key(&task.id()) {
            return Err(TaskRepositoryError::Persistence);
        }
        tasks.insert(task.id(), task.clone());
        Ok(task)
    }

    async fn get(&self, id: Uuid) -> Result<Task, TaskRepositoryError> {
        self.tasks()?
            .get(&id)
            .cloned()
            .ok_or(TaskRepositoryError::NotFound)
    }

    async fn complete(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        let mut tasks = self.tasks()?;
        let stored = tasks
            .get_mut(&task.id())
            .ok_or(TaskRepositoryError::NotFound)?;
        if stored.status() == TaskStatus::Completed {
            return Err(TaskRepositoryError::AlreadyCompleted);
        }
        if task.status() != TaskStatus::Completed
            || task.title() != stored.title()
            || task.created_at() != stored.created_at()
        {
            return Err(TaskRepositoryError::Persistence);
        }
        *stored = task.clone();
        Ok(task)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn fake_obeys_creation_and_atomic_completion_contract() {
        let repository = InMemoryTaskRepository::new();
        let task = repository
            .create(Task::new("task".into()).unwrap())
            .await
            .unwrap();
        assert_eq!(
            repository.create(task.clone()).await,
            Err(TaskRepositoryError::Persistence)
        );
        let mut candidate = task;
        candidate.complete().unwrap();
        let (first, second) = tokio::join!(
            repository.complete(candidate.clone()),
            repository.complete(candidate),
        );
        assert!(first.is_ok());
        assert_eq!(second, Err(TaskRepositoryError::AlreadyCompleted));
        let mut completed = Task::new("completed".into()).unwrap();
        completed.complete().unwrap();
        assert_eq!(
            repository.create(completed).await,
            Err(TaskRepositoryError::Persistence)
        );
    }
}
