use crate::{
    application::{error::TaskRepositoryError, port::out::TaskRepository},
    domain::{Task, TaskStatus},
};
use async_trait::async_trait;
use std::{collections::HashMap, sync::Mutex};
use uuid::Uuid;

/// Replaces the application repository port in unit tests.
#[derive(Default)]
pub(crate) struct TestRepository {
    tasks: Mutex<HashMap<Uuid, Task>>,
    fail: bool,
    error: Option<TaskRepositoryError>,
    complete_error: Option<TaskRepositoryError>,
}

impl TestRepository {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_persistence_failure() -> Self {
        Self {
            fail: true,
            ..Self::default()
        }
    }

    pub fn with_error(error: TaskRepositoryError) -> Self {
        Self {
            error: Some(error),
            ..Self::default()
        }
    }

    pub fn with_complete_error(error: TaskRepositoryError) -> Self {
        Self {
            complete_error: Some(error),
            ..Self::default()
        }
    }
}

#[async_trait]
impl TaskRepository for TestRepository {
    async fn create(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        if let Some(error) = self.error {
            return Err(error);
        }
        if self.fail {
            return Err(TaskRepositoryError::Persistence);
        }
        self.tasks.lock().unwrap().insert(task.id(), task.clone());
        Ok(task)
    }

    async fn get(&self, id: Uuid) -> Result<Task, TaskRepositoryError> {
        if let Some(error) = self.error {
            return Err(error);
        }
        if self.fail {
            return Err(TaskRepositoryError::Persistence);
        }
        self.tasks
            .lock()
            .unwrap()
            .get(&id)
            .cloned()
            .ok_or(TaskRepositoryError::NotFound)
    }

    async fn complete(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        if let Some(error) = self.complete_error.or(self.error) {
            return Err(error);
        }
        if self.fail {
            return Err(TaskRepositoryError::Persistence);
        }
        let mut tasks = self.tasks.lock().unwrap();
        let stored = tasks
            .get_mut(&task.id())
            .ok_or(TaskRepositoryError::NotFound)?;
        if stored.status() == TaskStatus::Completed {
            return Err(TaskRepositoryError::AlreadyCompleted);
        }
        *stored = task.clone();
        Ok(task)
    }
}
