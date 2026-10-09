use chrono::{DateTime, Utc};
use uuid::Uuid;

use crate::domain::{Task, TaskStatus};

#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[cfg_attr(feature = "api-doc", derive(utoipa::ToSchema))]
pub struct TaskResponse {
    pub id: Uuid,
    pub title: String,
    #[cfg_attr(feature = "api-doc", schema(schema_with = status_schema))]
    pub status: String,
    pub created_at: DateTime<Utc>,
    #[cfg_attr(feature = "api-doc", schema(required = true))]
    pub completed_at: Option<DateTime<Utc>>,
}

#[cfg(feature = "api-doc")]
fn status_schema() -> utoipa::openapi::schema::Object {
    utoipa::openapi::schema::ObjectBuilder::new()
        .schema_type(utoipa::openapi::schema::Type::String)
        .enum_values(Some(["open", "completed"]))
        .build()
}

impl From<Task> for TaskResponse {
    fn from(task: Task) -> Self {
        Self {
            id: task.id(),
            title: task.title().to_owned(),
            status: match task.status() {
                TaskStatus::Open => "open",
                TaskStatus::Completed => "completed",
            }
            .to_owned(),
            created_at: task.created_at(),
            completed_at: task.completed_at(),
        }
    }
}
