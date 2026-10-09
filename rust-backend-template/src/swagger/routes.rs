use actix_web::dev::HttpServiceFactory;
use actix_web::middleware::from_fn;
use actix_web::{HttpResponse, web};
use environment::ApiDocConfig;
use utoipa::OpenApi;
use utoipa_swagger_ui::{Config, SwaggerUi};

use super::api_doc::ApiDoc;
use super::authentication::authenticate;
use super::security_headers::documentation_csp;

pub(crate) fn documentation_scope(credentials: ApiDocConfig) -> impl HttpServiceFactory {
    // Utoipa's built-in UI authentication does not protect its separate JSON resource.
    // Keep every documentation resource inside this one scope-wide boundary instead.
    web::scope("/docs")
        .app_data(web::Data::new(credentials))
        .wrap(from_fn(authenticate))
        .wrap(from_fn(documentation_csp))
        .route(
            "",
            web::get().to(|| async {
                HttpResponse::TemporaryRedirect()
                    .insert_header(("Location", "/docs/"))
                    .finish()
            }),
        )
        .service(
            SwaggerUi::new("/{_:.*}")
                .url("/openapi.json", ApiDoc::openapi())
                // Registration paths are scope-relative, but the browser needs the full URL.
                .config(Config::from("/docs/openapi.json").validator_url("none")),
        )
}
