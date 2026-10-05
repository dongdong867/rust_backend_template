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
    use crate::{
        application::{error::TaskRepositoryError, port::out::TaskRepository},
        domain::Task,
        test::{MockTaskRepository, RepositoryExpectation},
    };
    use std::sync::Arc;

    fn controller(repository: Arc<MockTaskRepository>) -> TaskControllerImpl {
        let repository: Arc<dyn TaskRepository> = repository;
        TaskControllerImpl::new(
            CreateTaskUseCase::new(repository.clone()),
            GetTaskUseCase::new(repository.clone()),
            CompleteTaskUseCase::new(repository),
        )
    }

    #[tokio::test]
    async fn controller_wires_use_cases_and_maps_responses_and_errors() {
        let supplied = Task::new(" task ".into()).unwrap();
        let mut completed_task = supplied.clone();
        completed_task.complete().unwrap();
        let create_result = supplied.clone();
        let complete_result = completed_task.clone();
        let missing_id = Uuid::new_v4();
        let repository = Arc::new(MockTaskRepository::new([
            RepositoryExpectation::Create(Box::new(move |task| {
                assert_eq!(task.title(), " task ");
                Ok(create_result)
            })),
            RepositoryExpectation::Get(supplied.id(), Ok(supplied.clone())),
            RepositoryExpectation::Get(supplied.id(), Ok(supplied.clone())),
            RepositoryExpectation::Complete(Box::new(move |task| {
                assert_eq!(task.id(), complete_result.id());
                assert_eq!(task.title(), complete_result.title());
                assert_eq!(task.created_at(), complete_result.created_at());
                assert!(task.completed_at().is_some());
                Ok(complete_result)
            })),
            RepositoryExpectation::Get(supplied.id(), Ok(completed_task.clone())),
            RepositoryExpectation::Get(missing_id, Err(TaskRepositoryError::NotFound)),
        ]));
        let controller: Arc<dyn TaskController> = Arc::new(controller(repository.clone()));
        let task = controller
            .create_task(CreateTaskRequest {
                title: " task ".into(),
            })
            .await
            .unwrap();
        assert_eq!(task.title, " task ");
        assert_eq!(task.status, "open");
        assert_eq!(task.id, supplied.id());
        assert_eq!(task.created_at, supplied.created_at());
        assert!(task.completed_at.is_none());

        assert_eq!(controller.get_task(task.id).await.unwrap(), task);

        let completed = controller.complete_task(task.id).await.unwrap();
        assert_eq!(completed.status, "completed");
        assert_eq!(completed.completed_at, completed_task.completed_at());

        assert_eq!(
            controller.complete_task(task.id).await,
            Err(TaskControllerError::AlreadyCompleted)
        );
        assert_eq!(
            controller.get_task(missing_id).await,
            Err(TaskControllerError::NotFound)
        );
        assert_eq!(
            controller
                .create_task(CreateTaskRequest { title: "".into() })
                .await,
            Err(TaskControllerError::InvalidTitle)
        );
        repository.verify();
    }

    #[tokio::test]
    async fn controller_maps_repository_failures() {
        let id = Uuid::new_v4();
        let repository = Arc::new(MockTaskRepository::new([
            RepositoryExpectation::Create(Box::new(|task| {
                assert_eq!(task.title(), "valid");
                Err(TaskRepositoryError::Persistence)
            })),
            RepositoryExpectation::Get(id, Err(TaskRepositoryError::Persistence)),
            RepositoryExpectation::Get(id, Err(TaskRepositoryError::Persistence)),
        ]));
        let failed = self::controller(repository.clone());

        assert_eq!(
            failed
                .create_task(CreateTaskRequest {
                    title: "valid".into()
                })
                .await,
            Err(TaskControllerError::Persistence)
        );
        assert_eq!(
            failed.get_task(id).await,
            Err(TaskControllerError::Persistence)
        );
        assert_eq!(
            failed.complete_task(id).await,
            Err(TaskControllerError::Persistence)
        );
        repository.verify();
    }
}
