use async_trait::async_trait;
use sqlx::PgPool;
use uuid::Uuid;

use crate::{
    application::{error::TaskRepositoryError, port::out::TaskRepository},
    domain::{Task, TaskStatus},
};

use super::task_row::TaskRow;

pub struct PostgresTaskRepository {
    pool: PgPool,
}

impl PostgresTaskRepository {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    fn persistence_error(_error: sqlx::Error) -> TaskRepositoryError {
        // Never retain, format or log SQLx errors: they may contain SQL or credentials.
        tracing::error!("task persistence failed");
        TaskRepositoryError::Persistence
    }
}

#[async_trait]
impl TaskRepository for PostgresTaskRepository {
    async fn create(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        if task.status() != TaskStatus::Open {
            return Err(TaskRepositoryError::Persistence);
        }
        let row = sqlx::query_as::<_, TaskRow>(
            "INSERT INTO tasks (id, title, status, created_at, completed_at)
             VALUES ($1, $2, 'open', $3, NULL)
             RETURNING id, title, status, created_at, completed_at",
        )
        .bind(task.id())
        .bind(task.title())
        .bind(task.created_at())
        .fetch_one(&self.pool)
        .await
        .map_err(Self::persistence_error)?;
        row.try_into()
    }

    async fn get(&self, id: Uuid) -> Result<Task, TaskRepositoryError> {
        let row = sqlx::query_as::<_, TaskRow>(
            "SELECT id, title, status, created_at, completed_at FROM tasks WHERE id = $1",
        )
        .bind(id)
        .fetch_optional(&self.pool)
        .await
        .map_err(Self::persistence_error)?
        .ok_or(TaskRepositoryError::NotFound)?;
        row.try_into()
    }

    async fn complete(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        if task.status() != TaskStatus::Completed {
            return Err(TaskRepositoryError::Persistence);
        }
        // PostgreSQL rechecks this predicate after locking a contended row.
        // No read-then-unconditional-write can allow two successful completions.
        let row = sqlx::query_as::<_, TaskRow>(
            "UPDATE tasks SET status = 'completed', completed_at = $2
             WHERE id = $1 AND status = 'open' AND title = $3 AND created_at = $4
             RETURNING id, title, status, created_at, completed_at",
        )
        .bind(task.id())
        .bind(task.completed_at())
        .bind(task.title())
        .bind(task.created_at())
        .fetch_optional(&self.pool)
        .await
        .map_err(Self::persistence_error)?;
        if let Some(row) = row {
            return row.try_into();
        }
        match self.get(task.id()).await {
            Ok(stored) if stored.status() == TaskStatus::Completed => {
                Err(TaskRepositoryError::AlreadyCompleted)
            }
            Ok(_) => Err(TaskRepositoryError::Persistence),
            Err(error) => Err(error),
        }
    }
}
