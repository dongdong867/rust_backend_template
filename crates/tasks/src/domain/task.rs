use chrono::{DateTime, Utc};
use uuid::Uuid;

use super::{TaskError, TaskStatus};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Task {
    id: Uuid,
    title: String,
    status: TaskStatus,
    created_at: DateTime<Utc>,
    completed_at: Option<DateTime<Utc>>,
}

impl Task {
    pub fn new(title: String) -> Result<Self, TaskError> {
        Self::from_parts(Uuid::new_v4(), title, TaskStatus::Open, Utc::now(), None)
    }

    pub fn complete(&mut self) -> Result<(), TaskError> {
        if self.status == TaskStatus::Completed {
            return Err(TaskError::AlreadyCompleted);
        }
        self.status = TaskStatus::Completed;
        self.completed_at = Some(Utc::now().max(self.created_at));
        Ok(())
    }

    /// Validated construction for persistence hydration; fields cannot be mutated directly.
    pub fn from_parts(
        id: Uuid,
        title: String,
        status: TaskStatus,
        created_at: DateTime<Utc>,
        completed_at: Option<DateTime<Utc>>,
    ) -> Result<Self, TaskError> {
        // PostgreSQL text cannot store U+0000; reject it at the domain boundary.
        if title.contains('\0') || !(1..=200).contains(&title.chars().count()) {
            return Err(TaskError::InvalidTitle);
        }
        match (status, completed_at) {
            (TaskStatus::Open, None) => {}
            (TaskStatus::Completed, Some(at)) if at >= created_at => {}
            _ => return Err(TaskError::InvalidState),
        }
        Ok(Self {
            id,
            title,
            status,
            created_at,
            completed_at,
        })
    }

    pub fn id(&self) -> Uuid {
        self.id
    }

    pub fn title(&self) -> &str {
        &self.title
    }

    pub fn status(&self) -> TaskStatus {
        self.status
    }

    pub fn created_at(&self) -> DateTime<Utc> {
        self.created_at
    }

    pub fn completed_at(&self) -> Option<DateTime<Utc>> {
        self.completed_at
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn title_counts_unicode_characters_and_preserves_whitespace() {
        for title in [" ", "  hello\n ", &"\u{754c}".repeat(200)] {
            let task = Task::new(title.to_owned()).unwrap();
            assert_eq!(task.title, title);
            assert_eq!(task.status, TaskStatus::Open);
            assert!(task.completed_at.is_none());
        }
        for title in ["".to_owned(), "\u{754c}".repeat(201)] {
            assert_eq!(Task::new(title), Err(TaskError::InvalidTitle));
        }
    }

    #[test]
    fn completion_is_a_one_way_transition() {
        let mut task = Task::new("task".into()).unwrap();
        task.complete().unwrap();
        assert_eq!(task.status, TaskStatus::Completed);
        assert!(task.completed_at.unwrap() >= task.created_at);
        assert_eq!(task.complete(), Err(TaskError::AlreadyCompleted));
    }

    #[test]
    fn rejects_null_characters_in_new_and_hydrated_titles() {
        for title in ["\0", "before\0after"] {
            assert_eq!(Task::new(title.into()), Err(TaskError::InvalidTitle));
            assert_eq!(
                Task::from_parts(
                    Uuid::new_v4(),
                    title.into(),
                    TaskStatus::Open,
                    Utc::now(),
                    None
                ),
                Err(TaskError::InvalidTitle),
            );
        }
    }

    #[test]
    fn hydration_rejects_inconsistent_status_and_timestamps() {
        let at = Utc::now();
        for (status, completed_at) in [
            (TaskStatus::Open, Some(at)),
            (TaskStatus::Completed, None),
            (
                TaskStatus::Completed,
                Some(at - chrono::Duration::seconds(1)),
            ),
        ] {
            assert_eq!(
                Task::from_parts(Uuid::new_v4(), "valid".into(), status, at, completed_at),
                Err(TaskError::InvalidState),
            );
        }
        assert!(
            Task::from_parts(
                Uuid::new_v4(),
                "valid".into(),
                TaskStatus::Completed,
                at,
                Some(at)
            )
            .is_ok()
        );
        assert_eq!(
            Task::from_parts(Uuid::new_v4(), "".into(), TaskStatus::Open, at, None),
            Err(TaskError::InvalidTitle),
        );
    }
}
