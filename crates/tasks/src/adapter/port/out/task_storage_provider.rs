use crate::adapter::{dto::storage::TaskStorageRecord, error::TaskStorageError};
use async_trait::async_trait;
use uuid::Uuid;

/// Returns the actual persisted representation, not the submitted record.
/// Timestamp precision may be normalized, but identity and title must be preserved.
#[async_trait]
pub trait TaskStorageProvider: Send + Sync {
    /// Insert a valid open record; duplicates and invalid records fail safely.
    async fn create(
        &self,
        record: TaskStorageRecord,
    ) -> Result<TaskStorageRecord, TaskStorageError>;
    /// Return only the record with the requested identity.
    async fn get(&self, id: Uuid) -> Result<TaskStorageRecord, TaskStorageError>;
    /// Atomically check open state and immutable title/created_at before completion.
    /// Exactly one contender may succeed and return a completed record with unchanged created_at.
    async fn complete(
        &self,
        record: TaskStorageRecord,
    ) -> Result<TaskStorageRecord, TaskStorageError>;
}
