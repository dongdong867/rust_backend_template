use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::adapter::dto::storage::TaskStorageRecord;

#[derive(sqlx::FromRow)]
pub struct PostgresTaskRow {
    id: Uuid,
    title: String,
    status: String,
    created_at: DateTime<Utc>,
    completed_at: Option<DateTime<Utc>>,
}

impl From<PostgresTaskRow> for TaskStorageRecord {
    fn from(row: PostgresTaskRow) -> Self {
        Self {
            id: row.id,
            title: row.title,
            status: row.status,
            created_at: row.created_at,
            completed_at: row.completed_at,
        }
    }
}
