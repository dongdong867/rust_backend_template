use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::{
    application::error::TaskRepositoryError,
    domain::{Task, TaskStatus},
};

#[derive(sqlx::FromRow)]
pub(super) struct TaskRow {
    id: Uuid,
    title: String,
    status: String,
    created_at: DateTime<Utc>,
    completed_at: Option<DateTime<Utc>>,
}

impl TryFrom<TaskRow> for Task {
    type Error = TaskRepositoryError;

    fn try_from(row: TaskRow) -> Result<Self, Self::Error> {
        let status = match row.status.as_str() {
            "open" => TaskStatus::Open,
            "completed" => TaskStatus::Completed,
            _ => return Err(TaskRepositoryError::Persistence),
        };
        Task::from_parts(row.id, row.title, status, row.created_at, row.completed_at)
            .map_err(|_| TaskRepositoryError::Persistence)
    }
}
