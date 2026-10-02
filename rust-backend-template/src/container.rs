use std::sync::Arc;

use tasks::adapter::controller::TaskControllerImpl;
use tasks::adapter::port::r#in::TaskController;
use tasks::adapter::port::out::TaskStorageProvider;
use tasks::adapter::repository::TaskRepositoryImpl;
use tasks::application::use_case::{CompleteTaskUseCase, CreateTaskUseCase, GetTaskUseCase};

/// Process-wide dependencies, composed once before Actix creates any workers.
pub struct Container {
    pub task_controller: Arc<dyn TaskController>,
}

impl Container {
    pub fn new(storage_provider: Arc<dyn TaskStorageProvider>) -> Self {
        let repository = Arc::new(TaskRepositoryImpl::new(storage_provider));
        let create = CreateTaskUseCase::new(repository.clone());
        let get = GetTaskUseCase::new(repository.clone());
        let complete = CompleteTaskUseCase::new(repository);
        let task_controller = Arc::new(TaskControllerImpl::new(create, get, complete));
        Self { task_controller }
    }
}
