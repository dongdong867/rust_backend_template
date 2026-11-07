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

    fn persistence_error(error: sqlx::Error) -> TaskStorageError {
        // Never format or retain SQLx errors, SQL, or credentials; only fixed reasons are logged.
        // Tracing records an `Option` field only when it is `Some`.
        let reason = Self::failure_reason(&error);
        tracing::error!(reason, "task persistence failed");
        TaskStorageError::Persistence
    }

    /// The fixed log reason for a database failure that operators need to tell apart.
    fn failure_reason(error: &sqlx::Error) -> Option<&'static str> {
        match error {
            sqlx::Error::PoolTimedOut => Some("pool_timeout"),
            // SQLSTATE 57014 is query_canceled, which PostgreSQL reports when the statement
            // timeout ends a query. A query an administrator cancels has the same code.
            sqlx::Error::Database(error) if error.code().as_deref() == Some("57014") => {
                Some("statement_timeout")
            }
            _ => None,
        }
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

#[cfg(test)]
mod tests {
    use std::borrow::Cow;
    use std::error::Error;
    use std::{fmt, io};

    use sqlx::error::{DatabaseError, ErrorKind};

    use super::PostgresTaskStorageProvider;

    /// A database error that carries only a SQLSTATE code, standing in for PostgreSQL's.
    #[derive(Debug)]
    struct CodedDatabaseError(Option<&'static str>);

    impl fmt::Display for CodedDatabaseError {
        fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            formatter.write_str("synthetic database error")
        }
    }

    impl Error for CodedDatabaseError {}

    impl DatabaseError for CodedDatabaseError {
        fn message(&self) -> &str {
            "synthetic database error"
        }

        fn code(&self) -> Option<Cow<'_, str>> {
            self.0.map(Cow::Borrowed)
        }

        fn as_error(&self) -> &(dyn Error + Send + Sync + 'static) {
            self
        }

        fn as_error_mut(&mut self) -> &mut (dyn Error + Send + Sync + 'static) {
            self
        }

        fn into_error(self: Box<Self>) -> Box<dyn Error + Send + Sync + 'static> {
            self
        }

        fn kind(&self) -> ErrorKind {
            ErrorKind::Other
        }
    }

    fn database_error(code: Option<&'static str>) -> sqlx::Error {
        sqlx::Error::Database(Box::new(CodedDatabaseError(code)))
    }

    #[test]
    fn a_pool_timeout_has_the_pool_timeout_reason() {
        assert_eq!(
            PostgresTaskStorageProvider::failure_reason(&sqlx::Error::PoolTimedOut),
            Some("pool_timeout")
        );
    }

    #[test]
    fn a_cancelled_statement_has_the_statement_timeout_reason() {
        assert_eq!(
            PostgresTaskStorageProvider::failure_reason(&database_error(Some("57014"))),
            Some("statement_timeout")
        );
    }

    #[test]
    fn other_database_failures_have_no_reason() {
        for error in [
            sqlx::Error::PoolClosed,
            sqlx::Error::RowNotFound,
            sqlx::Error::Io(io::Error::from(io::ErrorKind::ConnectionReset)),
            database_error(Some("23514")),
            database_error(Some("57P01")),
            database_error(None),
        ] {
            assert_eq!(
                PostgresTaskStorageProvider::failure_reason(&error),
                None,
                "{error:?}"
            );
        }
    }
}
