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
    use crate::{
        domain::TaskStatus,
        test::{MockTaskRepository, RepositoryExpectation},
    };

    #[tokio::test]
    async fn every_outbound_error_is_persistence() {
        use crate::application::error::TaskRepositoryError;
        for error in [
            TaskRepositoryError::NotFound,
            TaskRepositoryError::AlreadyCompleted,
            TaskRepositoryError::Persistence,
        ] {
            let repository = Arc::new(MockTaskRepository::new([RepositoryExpectation::Create(
                Box::new(move |task| {
                    assert_eq!(task.title(), "valid");
                    Err(error)
                }),
            )]));
            let service = CreateTaskUseCase::new(repository.clone());
            assert_eq!(
                service
                    .execute(CreateTaskCommand {
                        title: "valid".into()
                    })
                    .await,
                Err(CreateTaskError::Persistence)
            );
            repository.verify();
        }
    }

    #[tokio::test]
    async fn create_preserves_title_and_submits_open_task() {
        let supplied = Task::new("supplied".into()).unwrap();
        let result = supplied.clone();
        let repository = Arc::new(MockTaskRepository::new([RepositoryExpectation::Create(
            Box::new(move |task| {
                assert_eq!(task.title(), "  界\n ");
                assert_eq!(task.status(), TaskStatus::Open);
                assert!(task.completed_at().is_none());
                Ok(result)
            }),
        )]));
        let service = CreateTaskUseCase::new(repository.clone());
        let task = service
            .execute(CreateTaskCommand {
                title: "  界\n ".into(),
            })
            .await
            .unwrap();
        assert_eq!(task, supplied);
        repository.verify();
    }

    #[tokio::test]
    async fn create_rejects_title_without_repository_call() {
        let repository = Arc::new(MockTaskRepository::new([]));
        let service = CreateTaskUseCase::new(repository.clone());
        assert_eq!(
            service
                .execute(CreateTaskCommand { title: "".into() })
                .await,
            Err(CreateTaskError::InvalidTitle)
        );
        repository.verify();
    }
}
