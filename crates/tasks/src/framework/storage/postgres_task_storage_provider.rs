use super::PostgresTaskRow;
use crate::adapter::{
    dto::storage::TaskStorageRecord, error::TaskStorageError, port::out::TaskStorageProvider,
};
use async_trait::async_trait;
use sqlx::PgPool;
use uuid::Uuid;

pub struct PostgresTaskStorageProvider {
    pool: PgPool,
}

impl PostgresTaskStorageProvider {
    pub fn new(pool: PgPool) -> Self {
        Self { pool }
    }

    fn persistence_error(_: sqlx::Error) -> TaskStorageError {
        // Never format or retain SQLx errors, SQL, or credentials.
        tracing::error!("task persistence failed");
        TaskStorageError::Persistence
    }
}

#[async_trait]
impl TaskStorageProvider for PostgresTaskStorageProvider {
    async fn create(
        &self,
        record: TaskStorageRecord,
    ) -> Result<TaskStorageRecord, TaskStorageError> {
        if record.status != "open" || record.completed_at.is_some() {
            return Err(TaskStorageError::Persistence);
        }
        sqlx::query_as::<_, PostgresTaskRow>(
            "INSERT INTO tasks (id, title, status, created_at, completed_at)
             VALUES ($1, $2, 'open', $3, NULL)
             RETURNING id, title, status, created_at, completed_at",
        )
        .bind(record.id)
        .bind(record.title)
        .bind(record.created_at)
        .fetch_one(&self.pool)
        .await
        .map(Into::into)
        .map_err(Self::persistence_error)
    }

    async fn get(&self, id: Uuid) -> Result<TaskStorageRecord, TaskStorageError> {
        sqlx::query_as::<_, PostgresTaskRow>(
            "SELECT id, title, status, created_at, completed_at FROM tasks WHERE id = $1",
        )
        .bind(id)
        .fetch_optional(&self.pool)
        .await
        .map_err(Self::persistence_error)?
        .map(Into::into)
        .ok_or(TaskStorageError::NotFound)
    }

    async fn complete(
        &self,
        record: TaskStorageRecord,
    ) -> Result<TaskStorageRecord, TaskStorageError> {
        if record.status != "completed"
            || record.completed_at.is_none()
            || record.completed_at.is_some_and(|at| at < record.created_at)
        {
            return Err(TaskStorageError::Persistence);
        }
        // PostgreSQL rechecks this predicate after locking a contended row.
        let row = sqlx::query_as::<_, PostgresTaskRow>(
            "UPDATE tasks SET status = 'completed', completed_at = $2
             WHERE id = $1 AND status = 'open' AND title = $3 AND created_at = $4
             RETURNING id, title, status, created_at, completed_at",
        )
        .bind(record.id)
        .bind(record.completed_at)
        .bind(record.title)
        .bind(record.created_at)
        .fetch_optional(&self.pool)
        .await
        .map_err(Self::persistence_error)?;
        if let Some(row) = row {
            return Ok(row.into());
        }
        match self.get(record.id).await {
            Ok(stored) if stored.status == "completed" => Err(TaskStorageError::AlreadyCompleted),
            Ok(_) => Err(TaskStorageError::Persistence),
            Err(error) => Err(error),
        }
    }
}
