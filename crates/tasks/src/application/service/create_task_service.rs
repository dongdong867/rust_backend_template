use std::sync::Arc;

use crate::{
    application::{command::CreateTaskCommand, error::CreateTaskError, port::out::TaskRepository},
    domain::Task,
};

pub struct CreateTaskService {
    repository: Arc<dyn TaskRepository>,
}

impl CreateTaskService {
    pub fn new(repository: Arc<dyn TaskRepository>) -> Self {
        Self { repository }
    }

    pub async fn execute(&self, command: CreateTaskCommand) -> Result<Task, CreateTaskError> {
        let task = Task::new(command.title).map_err(|_| CreateTaskError::InvalidTitle)?;
        self.repository
            .create(task)
            .await
            .map_err(|_| CreateTaskError::Persistence)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapter::repository::InMemoryTaskRepository;

    #[tokio::test]
    async fn create_preserves_title_and_persists_open_task() {
        let repository = Arc::new(InMemoryTaskRepository::new());
        let service = CreateTaskService::new(repository.clone());
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
        let service =
            CreateTaskService::new(Arc::new(InMemoryTaskRepository::with_persistence_failure()));
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
