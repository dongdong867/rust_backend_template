use async_trait::async_trait;
use chrono::{SubsecRound, Utc};
use std::sync::Arc;
use tasks::{
    adapter::{
        dto::storage::TaskStorageRecord, error::TaskStorageError, port::out::TaskStorageProvider,
        repository::TaskRepositoryImpl,
    },
    application::{error::TaskRepositoryError, port::out::TaskRepository},
    domain::Task,
};
use uuid::Uuid;

struct Provider(Result<TaskStorageRecord, TaskStorageError>);
#[async_trait]
impl TaskStorageProvider for Provider {
    async fn create(&self, _: TaskStorageRecord) -> Result<TaskStorageRecord, TaskStorageError> {
        self.0.clone()
    }
    async fn get(&self, _: Uuid) -> Result<TaskStorageRecord, TaskStorageError> {
        self.0.clone()
    }
    async fn complete(&self, _: TaskStorageRecord) -> Result<TaskStorageRecord, TaskStorageError> {
        self.0.clone()
    }
}
fn record() -> TaskStorageRecord {
    TaskStorageRecord {
        id: Uuid::new_v4(),
        title: " actual persisted title ".into(),
        status: "open".into(),
        created_at: Utc::now(),
        completed_at: None,
    }
}
#[tokio::test]
async fn hydrates_actual_output() {
    let candidate = Task::new(" actual persisted title ".into()).unwrap();
    let mut row: TaskStorageRecord = candidate.clone().into();
    row.created_at = row.created_at.trunc_subsecs(6);
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(row.clone()))));
    for task in [
        repository.create(candidate.clone()).await.unwrap(),
        repository.get(candidate.id()).await.unwrap(),
    ] {
        assert_eq!(task.id(), row.id);
        assert_eq!(task.title(), row.title);
        assert_eq!(task.created_at(), row.created_at);
    }

    let mut completed = Task::try_from(row).unwrap();
    completed.complete().unwrap();
    let mut saved: TaskStorageRecord = completed.clone().into();
    saved.completed_at = saved.completed_at.map(|at| at.trunc_subsecs(6));
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(saved.clone()))));
    let actual = repository.complete(completed).await.unwrap();
    assert_eq!(actual.id(), saved.id);
    assert_eq!(actual.created_at(), saved.created_at);
    assert_eq!(actual.completed_at(), saved.completed_at);
}

#[tokio::test]
async fn rejects_wrong_identity_for_every_operation() {
    let mut task = Task::new("valid".into()).unwrap();
    let mut row: TaskStorageRecord = task.clone().into();
    row.id = Uuid::new_v4();
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(row))));
    assert_eq!(
        repository.create(task.clone()).await,
        Err(TaskRepositoryError::Persistence)
    );
    assert_eq!(
        repository.get(task.id()).await,
        Err(TaskRepositoryError::Persistence)
    );
    task.complete().unwrap();
    let mut row: TaskStorageRecord = task.clone().into();
    row.id = Uuid::new_v4();
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(row))));
    assert_eq!(
        repository.complete(task).await,
        Err(TaskRepositoryError::Persistence)
    );
}

#[tokio::test]
async fn rejects_valid_records_with_the_wrong_operation_state() {
    let task = Task::new("valid".into()).unwrap();
    let open: TaskStorageRecord = task.clone().into();
    let mut completed = task.clone();
    completed.complete().unwrap();
    let completed_row: TaskStorageRecord = completed.clone().into();
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(completed_row))));
    assert_eq!(
        repository.create(task).await,
        Err(TaskRepositoryError::Persistence)
    );
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(open))));
    assert_eq!(
        repository.complete(completed).await,
        Err(TaskRepositoryError::Persistence)
    );
}

#[tokio::test]
async fn rejects_input_states_that_do_not_match_the_operation() {
    let open = Task::new("valid".into()).unwrap();
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(open.clone().into()))));
    assert_eq!(
        repository.complete(open.clone()).await,
        Err(TaskRepositoryError::Persistence)
    );
    let mut completed = open;
    completed.complete().unwrap();
    assert_eq!(
        repository.create(completed).await,
        Err(TaskRepositoryError::Persistence)
    );
}

#[tokio::test]
async fn rejects_changes_to_immutable_fields_in_provider_output() {
    let mut task = Task::new("valid".into()).unwrap();
    let mut changed: TaskStorageRecord = task.clone().into();
    changed.title = "changed".into();
    let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(changed))));
    assert_eq!(
        repository.create(task.clone()).await,
        Err(TaskRepositoryError::Persistence)
    );
    task.complete().unwrap();
    for change_title in [true, false] {
        let mut changed: TaskStorageRecord = task.clone().into();
        if change_title {
            changed.title = "changed".into();
        } else {
            changed.created_at -= chrono::Duration::seconds(1);
        }
        let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(changed))));
        assert_eq!(
            repository.complete(task.clone()).await,
            Err(TaskRepositoryError::Persistence)
        );
    }
}

#[tokio::test]
async fn malformed_records_are_opaque() {
    let mut invalid = Vec::new();
    for title in ["".to_owned(), "界".repeat(201), "a\0b".into()] {
        let mut row = record();
        row.title = title;
        invalid.push(row);
    }
    for (status, completed_at) in [
        ("unknown", None),
        ("completed", None),
        ("open", Some(Utc::now())),
    ] {
        let mut row = record();
        row.status = status.into();
        row.completed_at = completed_at;
        invalid.push(row);
    }
    let mut row = record();
    row.status = "completed".into();
    row.completed_at = Some(row.created_at - chrono::Duration::seconds(1));
    invalid.push(row);
    for mut row in invalid {
        let mut task = Task::new("valid".into()).unwrap();
        row.id = task.id();
        let repository = TaskRepositoryImpl::new(Arc::new(Provider(Ok(row))));
        assert_eq!(
            repository.create(task.clone()).await,
            Err(TaskRepositoryError::Persistence)
        );
        assert_eq!(
            repository.get(task.id()).await,
            Err(TaskRepositoryError::Persistence)
        );
        task.complete().unwrap();
        assert_eq!(
            repository.complete(task).await,
            Err(TaskRepositoryError::Persistence)
        );
    }
}
#[tokio::test]
async fn maps_all_errors() {
    for (storage, expected) in [
        (TaskStorageError::NotFound, TaskRepositoryError::NotFound),
        (
            TaskStorageError::AlreadyCompleted,
            TaskRepositoryError::AlreadyCompleted,
        ),
        (
            TaskStorageError::Persistence,
            TaskRepositoryError::Persistence,
        ),
    ] {
        let repository = TaskRepositoryImpl::new(Arc::new(Provider(Err(storage))));
        let mut task = Task::new("valid".into()).unwrap();
        assert_eq!(repository.create(task.clone()).await, Err(expected));
        assert_eq!(repository.get(task.id()).await, Err(expected));
        task.complete().unwrap();
        assert_eq!(repository.complete(task).await, Err(expected));
    }
}
