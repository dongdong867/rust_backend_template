use std::sync::Arc;

use chrono::Utc;
use sqlx::PgPool;
use tasks::{
    adapter::repository::TaskRepositoryImpl,
    application::{
        command::CompleteTaskCommand,
        error::{CompleteTaskError, TaskRepositoryError},
        port::out::TaskRepository,
        use_case::CompleteTaskUseCase,
    },
    domain::{Task, TaskStatus},
    framework::storage::PostgresTaskStorageProvider,
};
use uuid::Uuid;

#[ignore = "requires TEST_DATABASE_URL; run make test-db"]
#[sqlx::test(migrations = "../../migrations")]
async fn postgres_provider_rejects_invalid_input_and_returns_actual_rows(pool: PgPool) {
    use tasks::adapter::{
        dto::storage::TaskStorageRecord, error::TaskStorageError, port::out::TaskStorageProvider,
    };
    let provider = PostgresTaskStorageProvider::new(pool.clone());
    let record = TaskStorageRecord {
        id: Uuid::new_v4(),
        title: "  \u{754c}\n ".into(),
        status: "open".into(),
        created_at: Utc::now(),
        completed_at: None,
    };
    for invalid in [
        TaskStorageRecord {
            title: "".into(),
            ..record.clone()
        },
        TaskStorageRecord {
            title: "\u{754c}".repeat(201),
            ..record.clone()
        },
        TaskStorageRecord {
            title: "a\0b".into(),
            ..record.clone()
        },
        TaskStorageRecord {
            status: "unknown".into(),
            ..record.clone()
        },
        TaskStorageRecord {
            completed_at: Some(record.created_at),
            ..record.clone()
        },
        TaskStorageRecord {
            status: "completed".into(),
            completed_at: Some(record.created_at),
            ..record.clone()
        },
    ] {
        assert_eq!(
            provider.create(invalid).await,
            Err(TaskStorageError::Persistence)
        );
    }
    let actual = provider.create(record.clone()).await.unwrap();
    let raw: (
        Uuid,
        String,
        String,
        chrono::DateTime<Utc>,
        Option<chrono::DateTime<Utc>>,
    ) = sqlx::query_as(
        "SELECT id, title, status, created_at, completed_at FROM tasks WHERE id = $1",
    )
    .bind(record.id)
    .fetch_one(&pool)
    .await
    .unwrap();
    assert_eq!(
        (
            actual.id,
            actual.title.clone(),
            actual.status.clone(),
            actual.created_at,
            actual.completed_at
        ),
        raw
    );
    assert_eq!(provider.get(record.id).await.unwrap(), actual);
    let completed = TaskStorageRecord {
        status: "completed".into(),
        completed_at: Some(actual.created_at),
        ..actual.clone()
    };
    for invalid in [
        TaskStorageRecord {
            completed_at: None,
            ..completed.clone()
        },
        TaskStorageRecord {
            completed_at: Some(actual.created_at - chrono::Duration::seconds(1)),
            ..completed.clone()
        },
        TaskStorageRecord {
            title: "changed".into(),
            ..completed.clone()
        },
        TaskStorageRecord {
            created_at: actual.created_at - chrono::Duration::seconds(1),
            ..completed.clone()
        },
        actual.clone(),
    ] {
        assert_eq!(
            provider.complete(invalid).await,
            Err(TaskStorageError::Persistence)
        );
        assert_eq!(provider.get(actual.id).await.unwrap(), actual);
    }
    assert_eq!(
        provider.complete(completed.clone()).await.unwrap(),
        completed
    );
}

#[ignore = "requires TEST_DATABASE_URL; run make test-db"]
#[sqlx::test(migrations = "../../migrations")]
async fn postgres_task_repository_contract(pool: PgPool) {
    let repository = Arc::new(TaskRepositoryImpl::new(Arc::new(
        PostgresTaskStorageProvider::new(pool.clone()),
    )));
    let task = repository
        .create(Task::new("  \u{754c}\n ".into()).unwrap())
        .await
        .unwrap();
    assert_eq!(task.title(), "  \u{754c}\n ");
    assert_eq!(task.status(), TaskStatus::Open);
    assert!(task.completed_at().is_none());
    assert_eq!(repository.get(task.id()).await.unwrap(), task);
    assert_eq!(
        repository.create(task.clone()).await,
        Err(TaskRepositoryError::Persistence)
    );
    let mut candidate = task.clone();
    candidate.complete().unwrap();
    for mismatched in [
        Task::from_parts(
            candidate.id(),
            "changed".into(),
            candidate.status(),
            candidate.created_at(),
            candidate.completed_at(),
        )
        .unwrap(),
        Task::from_parts(
            candidate.id(),
            candidate.title().into(),
            candidate.status(),
            candidate.created_at() - chrono::Duration::seconds(1),
            candidate.completed_at(),
        )
        .unwrap(),
    ] {
        assert_eq!(
            repository.complete(mismatched).await,
            Err(TaskRepositoryError::Persistence)
        );
        assert_eq!(repository.get(task.id()).await.unwrap(), task);
    }
    assert_eq!(
        repository.complete(task.clone()).await,
        Err(TaskRepositoryError::Persistence)
    );
    assert_eq!(
        repository.get(Uuid::new_v4()).await,
        Err(TaskRepositoryError::NotFound)
    );
    let mut completed = task.clone();
    completed.complete().unwrap();
    let saved = repository.complete(completed.clone()).await.unwrap();
    assert_eq!(saved.status(), TaskStatus::Completed);
    assert!(saved.completed_at().unwrap() >= saved.created_at());
    assert_eq!(repository.get(task.id()).await.unwrap(), saved);
    assert_eq!(
        repository.complete(completed).await,
        Err(TaskRepositoryError::AlreadyCompleted)
    );
    let mut missing = Task::new("missing".into()).unwrap();
    missing.complete().unwrap();
    assert_eq!(
        repository.complete(missing).await,
        Err(TaskRepositoryError::NotFound)
    );

    // Two updates contend for the same open row; exactly one can return success.
    let racing = repository
        .create(Task::new("race".into()).unwrap())
        .await
        .unwrap();
    let service = Arc::new(CompleteTaskUseCase::new(repository.clone()));
    let id = racing.id();
    let first = {
        let service = service.clone();
        tokio::spawn(async move { service.execute(CompleteTaskCommand { id }).await })
    };
    let second = tokio::spawn(async move { service.execute(CompleteTaskCommand { id }).await });
    let results = [first.await.unwrap(), second.await.unwrap()];
    assert_eq!(results.iter().filter(|result| result.is_ok()).count(), 1);
    assert_eq!(
        results
            .iter()
            .filter(|result| matches!(result, Err(CompleteTaskError::AlreadyCompleted)))
            .count(),
        1
    );

    // Force both contenders to have read the open state before either update.
    let mut candidate = repository
        .create(Task::new("optimistic race".into()).unwrap())
        .await
        .unwrap();
    candidate.complete().unwrap();
    let barrier = Arc::new(tokio::sync::Barrier::new(2));
    let mut contenders = Vec::new();
    for _ in 0..2 {
        let repository = repository.clone();
        let candidate = candidate.clone();
        let barrier = barrier.clone();
        contenders.push(tokio::spawn(async move {
            barrier.wait().await;
            repository.complete(candidate).await
        }));
    }
    let mut successes = 0;
    let mut conflicts = 0;
    for contender in contenders {
        match contender.await.unwrap() {
            Ok(_) => successes += 1,
            Err(TaskRepositoryError::AlreadyCompleted) => conflicts += 1,
            other => panic!("unexpected completion result: {other:?}"),
        }
    }
    assert_eq!((successes, conflicts), (1, 1));

    // Database constraints protect the same invariants even outside the application.
    for (title, status, completed_at) in [
        ("".to_owned(), "open", None),
        ("\u{754c}".repeat(201), "open", None),
        ("valid".to_owned(), "unknown", None),
        ("valid".to_owned(), "completed", None),
        ("valid".to_owned(), "open", Some(Utc::now())),
        (
            "valid".to_owned(),
            "completed",
            Some(Utc::now() - chrono::Duration::days(1)),
        ),
    ] {
        let error = sqlx::query(
            "INSERT INTO tasks (id, title, status, created_at, completed_at) VALUES ($1, $2, $3, $4, $5)"
        )
        .bind(Uuid::new_v4()).bind(title).bind(status).bind(Utc::now()).bind(completed_at)
        .execute(&pool).await.unwrap_err();
        assert!(matches!(
            error
                .as_database_error()
                .and_then(|error| error.code())
                .as_deref(),
            Some("23514" | "22001")
        ));
    }
    let boundary = repository
        .create(Task::new("\u{754c}".repeat(200)).unwrap())
        .await
        .unwrap();
    assert_eq!(boundary.title().chars().count(), 200);
}
