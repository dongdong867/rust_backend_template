use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::{
    application::error::TaskRepositoryError,
    domain::{Task, TaskStatus},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskStorageRecord {
    pub id: Uuid,
    pub title: String,
    pub status: String,
    pub created_at: DateTime<Utc>,
    pub completed_at: Option<DateTime<Utc>>,
}

impl From<Task> for TaskStorageRecord {
    fn from(task: Task) -> Self {
        Self {
            id: task.id(),
            title: task.title().into(),
            status: match task.status() {
                TaskStatus::Open => "open",
                TaskStatus::Completed => "completed",
            }
            .into(),
            created_at: task.created_at(),
            completed_at: task.completed_at(),
        }
    }
}

impl TryFrom<TaskStorageRecord> for Task {
    type Error = TaskRepositoryError;

    fn try_from(record: TaskStorageRecord) -> Result<Self, Self::Error> {
        let status = match record.status.as_str() {
            "open" => TaskStatus::Open,
            "completed" => TaskStatus::Completed,
            _ => return Err(TaskRepositoryError::Persistence),
        };
        Task::from_parts(
            record.id,
            record.title,
            status,
            record.created_at,
            record.completed_at,
        )
        .map_err(|_| TaskRepositoryError::Persistence)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn converts_every_field_without_losing_domain_invariants() {
        let mut task = Task::new("  界\n ".into()).unwrap();
        for status in [TaskStatus::Open, TaskStatus::Completed] {
            if status == TaskStatus::Completed {
                task.complete().unwrap();
            }
            let record: TaskStorageRecord = task.clone().into();
            assert_eq!(record.id, task.id());
            assert_eq!(record.title, task.title());
            assert_eq!(
                record.status,
                if status == TaskStatus::Open {
                    "open"
                } else {
                    "completed"
                }
            );
            assert_eq!(record.created_at, task.created_at());
            assert_eq!(record.completed_at, task.completed_at());
            assert_eq!(Task::try_from(record).unwrap(), task);
        }
    }
}
