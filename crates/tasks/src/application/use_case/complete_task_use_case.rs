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
    use crate::{
        domain::TaskStatus,
        test::{MockTaskRepository, RepositoryExpectation},
    };
    use uuid::Uuid;

    #[tokio::test]
    async fn maps_errors_from_read_and_completion() {
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
            let id = Uuid::new_v4();
            let repository = Arc::new(MockTaskRepository::new([RepositoryExpectation::Get(
                id,
                Err(error),
            )]));
            let service = CompleteTaskUseCase::new(repository.clone());
            assert_eq!(
                service.execute(CompleteTaskCommand { id }).await,
                Err(expected)
            );
            repository.verify();

            let task = Task::new("task".into()).unwrap();
            let original = task.clone();
            let repository = Arc::new(MockTaskRepository::new([
                RepositoryExpectation::Get(task.id(), Ok(task.clone())),
                RepositoryExpectation::Complete(Box::new(move |completed| {
                    assert_transition(&original, &completed);
                    Err(error)
                })),
            ]));
            let service = CompleteTaskUseCase::new(repository.clone());
            assert_eq!(
                service.execute(CompleteTaskCommand { id: task.id() }).await,
                Err(expected)
            );
            repository.verify();
        }
    }

    #[tokio::test]
    async fn complete_submits_transition_after_get() {
        let task = Task::new("task".into()).unwrap();
        let original = task.clone();
        let mut supplied = task.clone();
        supplied.complete().unwrap();
        let result = supplied.clone();
        let repository = Arc::new(MockTaskRepository::new([
            RepositoryExpectation::Get(task.id(), Ok(task.clone())),
            RepositoryExpectation::Complete(Box::new(move |completed| {
                assert_transition(&original, &completed);
                Ok(result)
            })),
        ]));
        let service = CompleteTaskUseCase::new(repository.clone());
        let completed = service
            .execute(CompleteTaskCommand { id: task.id() })
            .await
            .unwrap();
        assert_eq!(completed, supplied);
        repository.verify();
    }

    #[tokio::test]
    async fn already_completed_never_calls_complete() {
        let mut task = Task::new("task".into()).unwrap();
        task.complete().unwrap();
        let id = task.id();
        let repository = Arc::new(MockTaskRepository::new([RepositoryExpectation::Get(
            id,
            Ok(task),
        )]));
        let service = CompleteTaskUseCase::new(repository.clone());

        assert_eq!(
            service.execute(CompleteTaskCommand { id }).await,
            Err(CompleteTaskError::AlreadyCompleted)
        );
        repository.verify();
    }

    fn assert_transition(original: &Task, completed: &Task) {
        assert_eq!(completed.id(), original.id());
        assert_eq!(completed.title(), original.title());
        assert_eq!(completed.created_at(), original.created_at());
        assert_eq!(completed.status(), TaskStatus::Completed);
        assert!(completed.completed_at().unwrap() >= original.created_at());
    }
}
