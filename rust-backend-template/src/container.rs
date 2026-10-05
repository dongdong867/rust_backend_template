use std::io;
use std::sync::Arc;

use environment::DatabaseConfig;
use sqlx::PgPool;
use tasks::adapter::controller::TaskControllerImpl;
use tasks::adapter::port::r#in::TaskController;
use tasks::adapter::repository::TaskRepositoryImpl;
use tasks::application::use_case::{CompleteTaskUseCase, CreateTaskUseCase, GetTaskUseCase};
use tasks::framework::storage::PostgresTaskStorageProvider;

use crate::database::create_pool;
use crate::providers::Providers;

/// Process-wide dependencies, composed once before Actix creates any workers.
pub struct Container {
    pub task_controller: Arc<dyn TaskController>,
    database: Option<PgPool>,
}

impl Container {
    /// Builds production resources and the complete dependency graph from validated settings.
    pub fn new(config: &DatabaseConfig) -> io::Result<Self> {
        let database = create_pool(config)?;
        let providers = Providers {
            task_storage: Arc::new(PostgresTaskStorageProvider::new(database.clone())),
        };
        let mut container = Self::with_providers(providers);
        container.database = Some(database);
        Ok(container)
    }

    /// Composes the dependency graph from selected provider implementations.
    ///
    /// The caller retains ownership of any external resources used by the provider.
    pub fn with_providers(providers: Providers) -> Self {
        let repository = Arc::new(TaskRepositoryImpl::new(providers.task_storage));
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
        let container = Container::new(&config.database).unwrap();
        let database = container.database.as_ref().unwrap();
        assert_eq!(database.size(), 0);
        container.shutdown().await;
        assert!(database.is_closed());
    }

    #[actix_web::test]
    async fn supplied_providers_keep_their_external_resources_caller_owned() {
        let config = Config::from_lookup(|name| {
            (name == "DATABASE_URL").then(|| "postgres://localhost/container_test".to_owned())
        })
        .unwrap();
        let database = create_pool(&config.database).unwrap();
        let providers = crate::providers::Providers {
            task_storage: Arc::new(PostgresTaskStorageProvider::new(database.clone())),
        };
        let container = Container::with_providers(providers);
        assert!(container.database.is_none());
        assert_eq!(database.size(), 0);
        container.shutdown().await;
        assert!(!database.is_closed());
        database.close().await;
    }
}
