use async_trait::async_trait;
use uuid::Uuid;

use crate::adapter::{
    dto::{CreateTaskRequest, TaskResponse},
    error::TaskControllerError,
};

#[async_trait]
pub trait TaskController: Send + Sync {
    async fn create_task(
        &self,
        request: CreateTaskRequest,
    ) -> Result<TaskResponse, TaskControllerError>;
    async fn get_task(&self, id: Uuid) -> Result<TaskResponse, TaskControllerError>;
    async fn complete_task(&self, id: Uuid) -> Result<TaskResponse, TaskControllerError>;
}
