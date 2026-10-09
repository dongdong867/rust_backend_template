#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[cfg_attr(feature = "api-doc", derive(utoipa::ToSchema))]
pub struct CreateTaskRequest {
    #[cfg_attr(
        feature = "api-doc",
        schema(min_length = 1, max_length = 200, pattern = "^[^\\u0000]+$")
    )]
    pub title: String,
}
