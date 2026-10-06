use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::domain::{Task, TaskStatus};

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TaskResponse {
    pub id: Uuid,
    pub title: String,
    pub status: String,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

impl From<Task> for TaskResponse {
    fn from(task: Task) -> Self {
        Self {
            id: task.id(),
            title: task.title().to_owned(),
            status: match task.status() {
                TaskStatus::Open => "open",
                TaskStatus::Completed => "completed",
            }
            .to_owned(),
            created_at: task.created_at(),
            completed_at: task.completed_at(),
        }
    }
}
