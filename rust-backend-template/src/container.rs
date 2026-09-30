use std::io;
use std::sync::Arc;

use environment::DatabaseConfig;
use sqlx::PgPool;
use tasks::adapter::controller::TaskControllerImpl;
use tasks::adapter::port::r#in::TaskController;
use tasks::adapter::repository::PostgresTaskRepository;

use crate::database::create_pool;

/// Process-wide dependencies, composed once before Actix creates any workers.
pub struct Container {
    pub database: PgPool,
    pub task_controller: Arc<dyn TaskController>,
}

impl Container {
    pub fn new(config: &DatabaseConfig) -> io::Result<Self> {
        let database = create_pool(config)?;
        let repository = Arc::new(PostgresTaskRepository::new(database.clone()));
        let task_controller = Arc::new(TaskControllerImpl::new(repository));
        Ok(Self {
            database,
            task_controller,
        })
    }
}
