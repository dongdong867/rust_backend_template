//! `GET /health`: a fast liveness answer. It checks no dependencies and reveals nothing internal.

use actix_web::{HttpResponse, web};
use serde::Serialize;

#[derive(Serialize)]
#[cfg_attr(feature = "api-doc", derive(utoipa::ToSchema))]
struct HealthResponse {
    status: &'static str,
}

#[cfg_attr(feature = "api-doc", utoipa::path(
    get,
    path = "/health",
    tag = "Health",
    responses((status = 200, description = "Process is alive; dependencies are not checked", body = HealthResponse))
))]
async fn health() -> HttpResponse {
    HttpResponse::Ok().json(HealthResponse { status: "ok" })
}

pub fn config_health_routes(cfg: &mut web::ServiceConfig) {
    cfg.service(web::resource("/health").route(web::get().to(health)));
}
