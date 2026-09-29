//! `GET /health`: a fast liveness answer. It checks no dependencies and reveals nothing internal.

use actix_web::{HttpResponse, web};
use serde::Serialize;

#[derive(Serialize)]
struct HealthResponse {
    status: &'static str,
}

async fn health() -> HttpResponse {
    HttpResponse::Ok().json(HealthResponse { status: "ok" })
}

pub fn config_health_routes(cfg: &mut web::ServiceConfig) {
    cfg.service(web::resource("/health").route(web::get().to(health)));
}
