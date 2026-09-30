use std::sync::Arc;

use crate::{
    application::{
        command::GetTaskCommand,
        error::{GetTaskError, TaskRepositoryError},
        port::out::TaskRepository,
    },
    domain::Task,
};

pub struct GetTaskService {
    repository: Arc<dyn TaskRepository>,
}

impl GetTaskService {
    pub fn new(repository: Arc<dyn TaskRepository>) -> Self {
        Self { repository }
    }

    pub async fn execute(&self, command: GetTaskCommand) -> Result<Task, GetTaskError> {
        self.repository
            .get(command.id)
            .await
            .map_err(|error| match error {
                TaskRepositoryError::NotFound => GetTaskError::NotFound,
                _ => GetTaskError::Persistence,
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapter::repository::InMemoryTaskRepository;
    use uuid::Uuid;

    #[tokio::test]
    async fn get_returns_saved_task_and_not_found() {
        let repository = Arc::new(InMemoryTaskRepository::new());
        let task = repository
            .create(Task::new("task".into()).unwrap())
            .await
            .unwrap();
        let service = GetTaskService::new(repository);
        assert_eq!(
            service
                .execute(GetTaskCommand { id: task.id() })
                .await
                .unwrap(),
            task
        );
        assert_eq!(
            service.execute(GetTaskCommand { id: Uuid::new_v4() }).await,
            Err(GetTaskError::NotFound)
        );
    }

    #[tokio::test]
    async fn get_hides_repository_failure() {
        let service =
            GetTaskService::new(Arc::new(InMemoryTaskRepository::with_persistence_failure()));
        assert_eq!(
            service.execute(GetTaskCommand { id: Uuid::new_v4() }).await,
            Err(GetTaskError::Persistence)
        );
    }
}
