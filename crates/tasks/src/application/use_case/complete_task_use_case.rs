use std::sync::Arc;

use crate::{
    application::{
        command::CompleteTaskCommand, error::CompleteTaskError, port::out::TaskRepository,
    },
    domain::{Task, TaskError},
};

pub struct CompleteTaskUseCase {
    repository: Arc<dyn TaskRepository>,
}

impl CompleteTaskUseCase {
    pub fn new(repository: Arc<dyn TaskRepository>) -> Self {
        Self { repository }
    }

    pub async fn execute(&self, command: CompleteTaskCommand) -> Result<Task, CompleteTaskError> {
        let mut task = self
            .repository
            .get(command.id)
            .await
            .map_err(CompleteTaskError::from)?;
        task.complete().map_err(|error| match error {
            TaskError::AlreadyCompleted => CompleteTaskError::AlreadyCompleted,
            _ => CompleteTaskError::Persistence,
        })?;
        self.repository.complete(task).await.map_err(Into::into)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{application::use_case::test_repository::TestRepository, domain::TaskStatus};
    use uuid::Uuid;

    #[tokio::test]
    async fn maps_errors_from_both_read_and_atomic_completion() {
        use crate::application::error::TaskRepositoryError;
        for (error, expected) in [
            (TaskRepositoryError::NotFound, CompleteTaskError::NotFound),
            (
                TaskRepositoryError::AlreadyCompleted,
                CompleteTaskError::AlreadyCompleted,
            ),
            (
                TaskRepositoryError::Persistence,
                CompleteTaskError::Persistence,
            ),
        ] {
            let service = CompleteTaskUseCase::new(Arc::new(TestRepository::with_error(error)));
            assert_eq!(
                service
                    .execute(CompleteTaskCommand { id: Uuid::new_v4() })
                    .await,
                Err(expected)
            );
            let repository = Arc::new(TestRepository::with_complete_error(error));
            let task = repository
                .create(Task::new("task".into()).unwrap())
                .await
                .unwrap();
            let service = CompleteTaskUseCase::new(repository.clone());
            assert_eq!(
                service.execute(CompleteTaskCommand { id: task.id() }).await,
                Err(expected)
            );
            assert_eq!(repository.get(task.id()).await.unwrap(), task);
        }
    }

    #[tokio::test]
    async fn complete_persists_transition_and_refuses_second_completion() {
        let repository = Arc::new(TestRepository::new());
        let task = repository
            .create(Task::new("task".into()).unwrap())
            .await
            .unwrap();
        let service = CompleteTaskUseCase::new(repository.clone());
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
            CompleteTaskUseCase::new(Arc::new(TestRepository::with_persistence_failure()));
        assert_eq!(
            service
                .execute(CompleteTaskCommand { id: Uuid::new_v4() })
                .await,
            Err(CompleteTaskError::Persistence)
        );
    }
}
