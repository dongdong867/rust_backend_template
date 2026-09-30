#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct CreateTaskRequest {
    pub title: String,
}
