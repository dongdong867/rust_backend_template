use crate::{
    application::{error::TaskRepositoryError, port::out::TaskRepository},
    domain::Task,
};
use async_trait::async_trait;
use std::{collections::VecDeque, sync::Mutex};
use uuid::Uuid;

use super::RepositoryExpectation;

/// Verifies ordered port interactions without simulating storage.
///
/// Call `verify` after exercising the subject to detect missing calls.
pub(crate) struct MockTaskRepository {
    expectations: Mutex<VecDeque<RepositoryExpectation>>,
}

impl MockTaskRepository {
    pub fn new(expectations: impl IntoIterator<Item = RepositoryExpectation>) -> Self {
        Self {
            expectations: Mutex::new(expectations.into_iter().collect()),
        }
    }

    pub fn verify(&self) {
        assert!(
            self.expectations.lock().unwrap().is_empty(),
            "unconsumed repository expectations"
        );
    }

    fn next(&self) -> RepositoryExpectation {
        self.expectations
            .lock()
            .unwrap()
            .pop_front()
            .expect("unexpected repository call")
    }
}

#[async_trait]
impl TaskRepository for MockTaskRepository {
    async fn get(&self, id: Uuid) -> Result<Task, TaskRepositoryError> {
        match self.next() {
            RepositoryExpectation::Get(expected, result) => {
                assert_eq!(id, expected, "unexpected get id");
                result
            }
            _ => panic!("unexpected repository operation: get"),
        }
    }

    async fn create(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        match self.next() {
            RepositoryExpectation::Create(callback) => callback(task),
            _ => panic!("unexpected repository operation: create"),
        }
    }

    async fn complete(&self, task: Task) -> Result<Task, TaskRepositoryError> {
        match self.next() {
            RepositoryExpectation::Complete(callback) => callback(task),
            _ => panic!("unexpected repository operation: complete"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[should_panic(expected = "unconsumed repository expectations")]
    fn rejects_missing_calls() {
        let repository = MockTaskRepository::new([RepositoryExpectation::Get(
            Uuid::nil(),
            Err(TaskRepositoryError::NotFound),
        )]);

        repository.verify();
    }

    #[tokio::test]
    #[should_panic(expected = "unexpected repository call")]
    async fn rejects_extra_calls() {
        MockTaskRepository::new([]).get(Uuid::nil()).await.unwrap();
    }

    #[tokio::test]
    #[should_panic(expected = "unexpected repository operation")]
    async fn rejects_wrong_order() {
        let repository = MockTaskRepository::new([RepositoryExpectation::Create(Box::new(Ok))]);

        repository.get(Uuid::nil()).await.unwrap();
    }

    #[tokio::test]
    #[should_panic(expected = "unexpected get id")]
    async fn rejects_wrong_id() {
        let repository = MockTaskRepository::new([RepositoryExpectation::Get(
            Uuid::nil(),
            Err(TaskRepositoryError::NotFound),
        )]);

        repository.get(Uuid::new_v4()).await.unwrap();
    }

    #[tokio::test]
    #[should_panic(expected = "unexpected create title")]
    async fn callback_rejects_wrong_task_argument() {
        let repository =
            MockTaskRepository::new([RepositoryExpectation::Create(Box::new(|task| {
                assert_eq!(task.title(), "expected", "unexpected create title");
                Ok(task)
            }))]);

        repository
            .create(Task::new("wrong".into()).unwrap())
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn returns_configured_results_and_consumes_all_operations() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<MockTaskRepository>();

        let task = Task::new("supplied".into()).unwrap();
        let created = task.clone();
        let completed = task.clone();
        let repository = MockTaskRepository::new([
            RepositoryExpectation::Create(Box::new(move |_| Ok(created))),
            RepositoryExpectation::Get(task.id(), Err(TaskRepositoryError::NotFound)),
            RepositoryExpectation::Complete(Box::new(move |_| Ok(completed))),
        ]);

        assert_eq!(repository.create(task.clone()).await, Ok(task.clone()));
        assert_eq!(
            repository.get(task.id()).await,
            Err(TaskRepositoryError::NotFound)
        );
        assert_eq!(repository.complete(task.clone()).await, Ok(task));
        repository.verify();
    }
}
