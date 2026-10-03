use std::io;
use std::sync::Arc;

use environment::DatabaseConfig;
use sqlx::PgPool;
use tasks::adapter::controller::TaskControllerImpl;
use tasks::adapter::port::r#in::TaskController;
use tasks::adapter::port::out::TaskStorageProvider;
use tasks::adapter::repository::TaskRepositoryImpl;
use tasks::application::use_case::{CompleteTaskUseCase, CreateTaskUseCase, GetTaskUseCase};
use tasks::framework::storage::PostgresTaskStorageProvider;

use crate::database::create_pool;

/// Process-wide dependencies, composed once before Actix creates any workers.
pub struct Container {
    pub task_controller: Arc<dyn TaskController>,
    database: Option<PgPool>,
}

impl Container {
    /// Builds production resources and the complete dependency graph from validated settings.
    pub fn from_config(config: &DatabaseConfig) -> io::Result<Self> {
        let database = create_pool(config)?;
        let provider = Arc::new(PostgresTaskStorageProvider::new(database.clone()));
        let mut container = Self::new(provider);
        container.database = Some(database);
        Ok(container)
    }

    /// Composes the dependency graph from an injected provider.
    ///
    /// The caller retains ownership of any external resources used by the provider.
    pub fn new(storage_provider: Arc<dyn TaskStorageProvider>) -> Self {
        let repository = Arc::new(TaskRepositoryImpl::new(storage_provider));
        let create = CreateTaskUseCase::new(repository.clone());
        let get = GetTaskUseCase::new(repository.clone());
        let complete = CompleteTaskUseCase::new(repository);
        let task_controller = Arc::new(TaskControllerImpl::new(create, get, complete));
        Self {
            task_controller,
            database: None,
        }
    }

    /// Closes resources created by the configured production composition.
    pub async fn shutdown(&self) {
        if let Some(database) = &self.database {
            database.close().await;
        }
    }
}

#[cfg(test)]
mod tests {
    use environment::Config;

    use super::*;

    #[actix_web::test]
    async fn configured_composition_owns_a_lazy_pool_and_closes_it_on_shutdown() {
        let config = Config::from_lookup(|name| {
            (name == "DATABASE_URL").then(|| "postgres://localhost/container_test".to_owned())
        })
        .unwrap();
        let container = Container::from_config(&config.database).unwrap();
        let database = container.database.as_ref().unwrap();
        assert_eq!(database.size(), 0);
        container.shutdown().await;
        assert!(database.is_closed());
    }
}
