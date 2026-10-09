use crate::adapter::{
    dto::storage::TaskStorageRecord, error::TaskStorageError, port::out::TaskStorageProvider,
};
use async_trait::async_trait;
use std::{collections::HashMap, sync::Mutex};
use uuid::Uuid;

#[derive(Default)]
pub struct InMemoryTaskStorageProvider {
    tasks: Mutex<HashMap<Uuid, TaskStorageRecord>>,
    persistence_failure: bool,
}

impl InMemoryTaskStorageProvider {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_persistence_failure() -> Self {
        Self {
            persistence_failure: true,
            ..Self::default()
        }
    }

    fn tasks(
        &self,
    ) -> Result<std::sync::MutexGuard<'_, HashMap<Uuid, TaskStorageRecord>>, TaskStorageError> {
        if self.persistence_failure {
            return Err(TaskStorageError::Persistence);
        }
        self.tasks.lock().map_err(|_| TaskStorageError::Persistence)
    }
}

fn valid(record: &TaskStorageRecord) -> bool {
    !record.title.contains('\0')
        && (1..=200).contains(&record.title.chars().count())
        && match (record.status.as_str(), record.completed_at) {
            ("open", None) => true,
            ("completed", Some(at)) => at >= record.created_at,
            _ => false,
        }
}

#[async_trait]
impl TaskStorageProvider for InMemoryTaskStorageProvider {
    async fn create(
        &self,
        record: TaskStorageRecord,
    ) -> Result<TaskStorageRecord, TaskStorageError> {
        let mut tasks = self.tasks()?;
        if !valid(&record) || record.status != "open" || tasks.contains_key(&record.id) {
            return Err(TaskStorageError::Persistence);
        }
        tasks.insert(record.id, record.clone());
        Ok(record)
    }

    async fn get(&self, id: Uuid) -> Result<TaskStorageRecord, TaskStorageError> {
        self.tasks()?
            .get(&id)
            .cloned()
            .ok_or(TaskStorageError::NotFound)
    }

    async fn complete(
        &self,
        record: TaskStorageRecord,
    ) -> Result<TaskStorageRecord, TaskStorageError> {
        let mut tasks = self.tasks()?;
        let stored = tasks
            .get_mut(&record.id)
            .ok_or(TaskStorageError::NotFound)?;
        if stored.status == "completed" {
            return Err(TaskStorageError::AlreadyCompleted);
        }
        if !valid(&record)
            || record.status != "completed"
            || record.title != stored.title
            || record.created_at != stored.created_at
        {
            return Err(TaskStorageError::Persistence);
        }
        *stored = record.clone();
        Ok(record)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use std::sync::Arc;

    fn open() -> TaskStorageRecord {
        TaskStorageRecord {
            id: Uuid::new_v4(),
            title: "  \u{754c}\n ".into(),
            status: "open".into(),
            created_at: Utc::now(),
            completed_at: None,
        }
    }

    #[tokio::test]
    async fn crud_constraints_and_immutable_fields() {
        let provider = InMemoryTaskStorageProvider::new();
        let row = provider.create(open()).await.unwrap();
        assert_eq!(provider.get(row.id).await.unwrap(), row);
        assert_eq!(
            provider.create(row.clone()).await,
            Err(TaskStorageError::Persistence)
        );
        assert_eq!(
            provider.get(Uuid::new_v4()).await,
            Err(TaskStorageError::NotFound)
        );
        for title in ["".to_owned(), "\u{754c}".repeat(201), "a\0b".into()] {
            let mut invalid = open();
            invalid.title = title;
            assert_eq!(
                provider.create(invalid).await,
                Err(TaskStorageError::Persistence)
            );
        }
        let mut boundary = open();
        boundary.title = "\u{754c}".repeat(200);
        assert_eq!(provider.create(boundary.clone()).await.unwrap(), boundary);
        let mut candidate = row.clone();
        candidate.status = "completed".into();
        candidate.completed_at = Some(row.created_at);
        for invalid in [
            TaskStorageRecord {
                title: "changed".into(),
                ..candidate.clone()
            },
            TaskStorageRecord {
                created_at: row.created_at - chrono::Duration::seconds(1),
                ..candidate.clone()
            },
            TaskStorageRecord {
                completed_at: None,
                ..candidate.clone()
            },
            TaskStorageRecord {
                completed_at: Some(row.created_at - chrono::Duration::seconds(1)),
                ..candidate.clone()
            },
            row.clone(),
        ] {
            assert_eq!(
                provider.complete(invalid).await,
                Err(TaskStorageError::Persistence)
            );
            assert_eq!(provider.get(row.id).await.unwrap(), row);
        }
        assert_eq!(
            provider.create(candidate.clone()).await,
            Err(TaskStorageError::Persistence)
        );
        let missing = TaskStorageRecord {
            id: Uuid::new_v4(),
            ..candidate.clone()
        };
        assert_eq!(
            provider.complete(missing).await,
            Err(TaskStorageError::NotFound)
        );
        assert_eq!(
            provider.complete(candidate.clone()).await.unwrap(),
            candidate
        );
        assert_eq!(provider.get(row.id).await.unwrap(), candidate);
        assert_eq!(
            provider.complete(candidate).await,
            Err(TaskStorageError::AlreadyCompleted)
        );
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn forced_concurrent_completion_has_one_winner() {
        let provider = Arc::new(InMemoryTaskStorageProvider::new());
        let mut candidate = provider.create(open()).await.unwrap();
        candidate.status = "completed".into();
        candidate.completed_at = Some(candidate.created_at);
        let barrier = Arc::new(tokio::sync::Barrier::new(2));
        let mut contenders = Vec::new();
        for _ in 0..2 {
            let provider = provider.clone();
            let candidate = candidate.clone();
            let barrier = barrier.clone();
            contenders.push(tokio::spawn(async move {
                barrier.wait().await;
                provider.complete(candidate).await
            }));
        }
        let mut successes = 0;
        let mut conflicts = 0;
        for contender in contenders {
            match contender.await.unwrap() {
                Ok(_) => successes += 1,
                Err(TaskStorageError::AlreadyCompleted) => conflicts += 1,
                other => panic!("unexpected result: {other:?}"),
            }
        }
        assert_eq!((successes, conflicts), (1, 1));
    }

    #[tokio::test]
    async fn failures_and_poisoned_mutex_are_opaque() {
        let provider = InMemoryTaskStorageProvider::with_persistence_failure();
        assert_eq!(
            provider.create(open()).await,
            Err(TaskStorageError::Persistence)
        );
        assert_eq!(
            provider.get(Uuid::new_v4()).await,
            Err(TaskStorageError::Persistence)
        );
        assert_eq!(
            provider.complete(open()).await,
            Err(TaskStorageError::Persistence)
        );
        let provider = InMemoryTaskStorageProvider::new();
        let _ = std::panic::catch_unwind(|| {
            let _guard = provider.tasks.lock().unwrap();
            panic!("poison");
        });
        assert_eq!(
            provider.get(Uuid::new_v4()).await,
            Err(TaskStorageError::Persistence)
        );
    }
}
