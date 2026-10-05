use std::sync::Arc;

use tasks::adapter::port::out::TaskStorageProvider;

/// Selected implementations of the provider ports needed by the application.
pub struct Providers {
    pub task_storage: Arc<dyn TaskStorageProvider>,
}
