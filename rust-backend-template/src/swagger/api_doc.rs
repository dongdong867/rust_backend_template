use utoipa::OpenApi;

#[derive(OpenApi)]
#[openapi(paths(
    crate::api::route::health::health,
    crate::api::route::v1::tasks::create_task,
    crate::api::route::v1::tasks::get_task,
    crate::api::route::v1::tasks::complete_task
))]
pub(super) struct ApiDoc;
