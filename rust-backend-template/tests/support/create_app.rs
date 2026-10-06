use std::sync::Arc;

use actix_web::body::MessageBody;
use actix_web::dev::{ServiceFactory, ServiceRequest, ServiceResponse};
use actix_web::{App, Error};
use environment::HttpConfig;
use rust_backend_template::container::Container;
use rust_backend_template::providers::Providers;
use tasks::framework::storage::InMemoryTaskStorageProvider;

/// Builds the production application and composition with an in-memory storage provider.
pub fn create_app(
    http_config: HttpConfig,
) -> App<
    impl ServiceFactory<
        ServiceRequest,
        Config = (),
        Response = ServiceResponse<impl MessageBody>,
        Error = Error,
        InitError = (),
    >,
> {
    let provider = Arc::new(InMemoryTaskStorageProvider::new());
    let container = Arc::new(Container::with_providers(Providers {
        task_storage: provider,
    }));
    rust_backend_template::create_app::create_app(http_config, container)
}
