use actix_web::http::StatusCode;
use actix_web::{HttpResponse, web};
use tasks::adapter::dto::CreateTaskRequest;
use tasks::adapter::error::TaskControllerError;
use tasks::adapter::port::r#in::TaskController;
use uuid::Uuid;

use crate::api::error::ProblemDetails;

pub fn config_task_routes(config: &mut web::ServiceConfig) {
    config.service(
        web::scope("/v1/tasks")
            .app_data(
                web::PathConfig::default().error_handler(|_, _| {
                    actix_web::error::ErrorBadRequest("invalid task identifier")
                }),
            )
            .route("", web::post().to(create_task))
            .route("/{id}", web::get().to(get_task))
            .route("/{id}/complete", web::post().to(complete_task)),
    );
}

async fn create_task(
    controller: web::Data<dyn TaskController>,
    body: web::Json<CreateTaskRequest>,
) -> HttpResponse {
    match controller.create_task(body.into_inner()).await {
        Ok(task) => HttpResponse::Created().json(task),
        Err(error) => error_response(error),
    }
}

async fn get_task(controller: web::Data<dyn TaskController>, id: web::Path<Uuid>) -> HttpResponse {
    match controller.get_task(id.into_inner()).await {
        Ok(task) => HttpResponse::Ok().json(task),
        Err(error) => error_response(error),
    }
}

async fn complete_task(
    controller: web::Data<dyn TaskController>,
    id: web::Path<Uuid>,
) -> HttpResponse {
    match controller.complete_task(id.into_inner()).await {
        Ok(task) => HttpResponse::Ok().json(task),
        Err(error) => error_response(error),
    }
}

fn error_response(error: TaskControllerError) -> HttpResponse {
    let status = match error {
        TaskControllerError::InvalidTitle => StatusCode::BAD_REQUEST,
        TaskControllerError::NotFound => StatusCode::NOT_FOUND,
        TaskControllerError::AlreadyCompleted => StatusCode::CONFLICT,
        TaskControllerError::Persistence => {
            // SQLx errors can carry queries, connection information or submitted values.
            tracing::error!(reason = "persistence", "task operation failed");
            StatusCode::INTERNAL_SERVER_ERROR
        }
    };
    ProblemDetails::http_response(status)
}
