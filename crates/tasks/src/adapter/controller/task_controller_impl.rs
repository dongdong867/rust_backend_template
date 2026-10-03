use async_trait::async_trait;
use uuid::Uuid;

use crate::{
    adapter::{
        dto::{CreateTaskRequest, TaskResponse},
        error::TaskControllerError,
        port::r#in::TaskController,
    },
    application::{
        command::{CompleteTaskCommand, CreateTaskCommand, GetTaskCommand},
        use_case::{CompleteTaskUseCase, CreateTaskUseCase, GetTaskUseCase},
    },
};

pub struct TaskControllerImpl {
    create: CreateTaskUseCase,
    get: GetTaskUseCase,
    complete: CompleteTaskUseCase,
}

impl TaskControllerImpl {
    pub fn new(
        create: CreateTaskUseCase,
        get: GetTaskUseCase,
        complete: CompleteTaskUseCase,
    ) -> Self {
        Self {
            create,
            get,
            complete,
        }
    }
}

#[async_trait]
impl TaskController for TaskControllerImpl {
    async fn create_task(
        &self,
        request: CreateTaskRequest,
    ) -> Result<TaskResponse, TaskControllerError> {
        self.create
            .execute(CreateTaskCommand {
                title: request.title,
            })
            .await
            .map(Into::into)
            .map_err(Into::into)
    }

    async fn get_task(&self, id: Uuid) -> Result<TaskResponse, TaskControllerError> {
        self.get
            .execute(GetTaskCommand { id })
            .await
            .map(Into::into)
            .map_err(Into::into)
    }

    async fn complete_task(&self, id: Uuid) -> Result<TaskResponse, TaskControllerError> {
        self.complete
            .execute(CompleteTaskCommand { id })
            .await
            .map(Into::into)
            .map_err(Into::into)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{application::port::out::TaskRepository, test::TestRepository};
    use std::sync::Arc;

    fn controller(repository: TestRepository) -> TaskControllerImpl {
        let repository: Arc<dyn TaskRepository> = Arc::new(repository);
        TaskControllerImpl::new(
            CreateTaskUseCase::new(repository.clone()),
            GetTaskUseCase::new(repository.clone()),
            CompleteTaskUseCase::new(repository),
        )
    }

    #[tokio::test]
    async fn controller_wires_use_cases_and_maps_responses_and_errors() {
        let controller: Arc<dyn TaskController> = Arc::new(controller(TestRepository::new()));
        let task = controller
            .create_task(CreateTaskRequest {
                title: " task ".into(),
            })
            .await
            .unwrap();
        assert_eq!(task.title, " task ");
        assert_eq!(task.status, "open");
        assert!(task.completed_at.is_none());
        assert_eq!(controller.get_task(task.id).await.unwrap(), task);

        let completed = controller.complete_task(task.id).await.unwrap();
        assert_eq!(completed.status, "completed");
        assert!(completed.completed_at.is_some());
        assert_eq!(
            controller.complete_task(task.id).await,
            Err(TaskControllerError::AlreadyCompleted)
        );
        assert_eq!(
            controller.get_task(Uuid::new_v4()).await,
            Err(TaskControllerError::NotFound)
        );
        assert_eq!(
            controller
                .create_task(CreateTaskRequest { title: "".into() })
                .await,
            Err(TaskControllerError::InvalidTitle)
        );

        let failed = self::controller(TestRepository::with_persistence_failure());
        assert_eq!(
            failed
                .create_task(CreateTaskRequest {
                    title: "valid".into()
                })
                .await,
            Err(TaskControllerError::Persistence)
        );
        assert_eq!(
            failed.get_task(task.id).await,
            Err(TaskControllerError::Persistence)
        );
        assert_eq!(
            failed.complete_task(task.id).await,
            Err(TaskControllerError::Persistence)
        );
    }
}
