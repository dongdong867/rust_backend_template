use std::sync::Arc;

use crate::{
    application::{command::GetTaskCommand, error::GetTaskError, port::out::TaskRepository},
    domain::Task,
};

pub struct GetTaskUseCase {
    repository: Arc<dyn TaskRepository>,
}

impl GetTaskUseCase {
    pub fn new(repository: Arc<dyn TaskRepository>) -> Self {
        Self { repository }
    }

    pub async fn execute(&self, command: GetTaskCommand) -> Result<Task, GetTaskError> {
        self.repository.get(command.id).await.map_err(Into::into)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::application::use_case::test_repository::TestRepository;
    use uuid::Uuid;

    #[tokio::test]
    async fn maps_every_outbound_error() {
        use crate::application::error::TaskRepositoryError;
        for (error, expected) in [
            (TaskRepositoryError::NotFound, GetTaskError::NotFound),
            (
                TaskRepositoryError::AlreadyCompleted,
                GetTaskError::Persistence,
            ),
            (TaskRepositoryError::Persistence, GetTaskError::Persistence),
        ] {
            let service = GetTaskUseCase::new(Arc::new(TestRepository::with_error(error)));
            assert_eq!(
                service.execute(GetTaskCommand { id: Uuid::new_v4() }).await,
                Err(expected)
            );
        }
    }

    #[tokio::test]
    async fn get_returns_saved_task_and_not_found() {
        let repository = Arc::new(TestRepository::new());
        let task = repository
            .create(Task::new("task".into()).unwrap())
            .await
            .unwrap();
        let service = GetTaskUseCase::new(repository);
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
        let service = GetTaskUseCase::new(Arc::new(TestRepository::with_persistence_failure()));
        assert_eq!(
            service.execute(GetTaskCommand { id: Uuid::new_v4() }).await,
            Err(GetTaskError::Persistence)
        );
    }
}
