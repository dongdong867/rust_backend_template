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
    use crate::test::{MockTaskRepository, RepositoryExpectation};
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
            let id = Uuid::new_v4();
            let repository = Arc::new(MockTaskRepository::new([RepositoryExpectation::Get(
                id,
                Err(error),
            )]));
            let service = GetTaskUseCase::new(repository.clone());
            assert_eq!(service.execute(GetTaskCommand { id }).await, Err(expected));
            repository.verify();
        }
    }

    #[tokio::test]
    async fn get_returns_configured_task_for_requested_id() {
        let task = Task::new("task".into()).unwrap();
        let repository = Arc::new(MockTaskRepository::new([RepositoryExpectation::Get(
            task.id(),
            Ok(task.clone()),
        )]));
        let service = GetTaskUseCase::new(repository.clone());
        assert_eq!(
            service
                .execute(GetTaskCommand { id: task.id() })
                .await
                .unwrap(),
            task
        );
        repository.verify();
    }
}
