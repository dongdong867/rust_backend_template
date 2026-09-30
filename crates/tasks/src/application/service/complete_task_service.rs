use std::sync::Arc;

use crate::{
    application::{
        command::CompleteTaskCommand,
        error::{CompleteTaskError, TaskRepositoryError},
        port::out::TaskRepository,
    },
    domain::{Task, TaskError},
};

pub struct CompleteTaskService {
    repository: Arc<dyn TaskRepository>,
}

impl CompleteTaskService {
    pub fn new(repository: Arc<dyn TaskRepository>) -> Self {
        Self { repository }
    }

    pub async fn execute(&self, command: CompleteTaskCommand) -> Result<Task, CompleteTaskError> {
        let mut task = self
            .repository
            .get(command.id)
            .await
            .map_err(Self::repository_error)?;
        task.complete().map_err(|error| match error {
            TaskError::AlreadyCompleted => CompleteTaskError::AlreadyCompleted,
            _ => CompleteTaskError::Persistence,
        })?;
        self.repository
            .complete(task)
            .await
            .map_err(Self::repository_error)
    }

    fn repository_error(error: TaskRepositoryError) -> CompleteTaskError {
        match error {
            TaskRepositoryError::NotFound => CompleteTaskError::NotFound,
            TaskRepositoryError::AlreadyCompleted => CompleteTaskError::AlreadyCompleted,
            TaskRepositoryError::Persistence => CompleteTaskError::Persistence,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{adapter::repository::InMemoryTaskRepository, domain::TaskStatus};
    use uuid::Uuid;

    #[tokio::test]
    async fn complete_persists_transition_and_refuses_second_completion() {
        let repository = Arc::new(InMemoryTaskRepository::new());
        let task = repository
            .create(Task::new("task".into()).unwrap())
            .await
            .unwrap();
        let service = CompleteTaskService::new(repository.clone());
        let completed = service
            .execute(CompleteTaskCommand { id: task.id() })
            .await
            .unwrap();
        assert_eq!(completed.status(), TaskStatus::Completed);
        assert!(completed.completed_at().is_some());
        assert_eq!(repository.get(task.id()).await.unwrap(), completed);
        assert_eq!(
            service.execute(CompleteTaskCommand { id: task.id() }).await,
            Err(CompleteTaskError::AlreadyCompleted)
        );
        assert_eq!(
            service
                .execute(CompleteTaskCommand { id: Uuid::new_v4() })
                .await,
            Err(CompleteTaskError::NotFound)
        );
    }

    #[tokio::test]
    async fn complete_hides_repository_failure() {
        let service =
            CompleteTaskService::new(Arc::new(InMemoryTaskRepository::with_persistence_failure()));
        assert_eq!(
            service
                .execute(CompleteTaskCommand { id: Uuid::new_v4() })
                .await,
            Err(CompleteTaskError::Persistence)
        );
    }
}
