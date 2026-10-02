use std::sync::Arc;

use crate::{
    application::{command::CreateTaskCommand, error::CreateTaskError, port::out::TaskRepository},
    domain::Task,
};

pub struct CreateTaskUseCase {
    repository: Arc<dyn TaskRepository>,
}

impl CreateTaskUseCase {
    pub fn new(repository: Arc<dyn TaskRepository>) -> Self {
        Self { repository }
    }

    pub async fn execute(&self, command: CreateTaskCommand) -> Result<Task, CreateTaskError> {
        let task = Task::new(command.title).map_err(|_| CreateTaskError::InvalidTitle)?;
        self.repository.create(task).await.map_err(Into::into)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::application::use_case::test_repository::TestRepository;

    #[tokio::test]
    async fn every_outbound_error_is_persistence() {
        use crate::application::error::TaskRepositoryError;
        for error in [
            TaskRepositoryError::NotFound,
            TaskRepositoryError::AlreadyCompleted,
            TaskRepositoryError::Persistence,
        ] {
            let service = CreateTaskUseCase::new(Arc::new(TestRepository::with_error(error)));
            assert_eq!(
                service
                    .execute(CreateTaskCommand {
                        title: "valid".into()
                    })
                    .await,
                Err(CreateTaskError::Persistence)
            );
        }
    }

    #[tokio::test]
    async fn create_preserves_title_and_persists_open_task() {
        let repository = Arc::new(TestRepository::new());
        let service = CreateTaskUseCase::new(repository.clone());
        let task = service
            .execute(CreateTaskCommand {
                title: "  界\n ".into(),
            })
            .await
            .unwrap();
        assert_eq!(task.title(), "  界\n ");
        assert_eq!(repository.get(task.id()).await.unwrap(), task);
    }

    #[tokio::test]
    async fn create_rejects_title_and_hides_repository_failure() {
        let service = CreateTaskUseCase::new(Arc::new(TestRepository::with_persistence_failure()));
        assert_eq!(
            service
                .execute(CreateTaskCommand { title: "".into() })
                .await,
            Err(CreateTaskError::InvalidTitle)
        );
        assert_eq!(
            service
                .execute(CreateTaskCommand {
                    title: "valid".into()
                })
                .await,
            Err(CreateTaskError::Persistence)
        );
    }
}
