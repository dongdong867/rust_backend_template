use std::process::ExitCode;
use std::sync::Arc;

use environment::Config;
use rust_backend_template::{container::Container, server, telemetry};

fn main() -> ExitCode {
    let config = match Config::from_env() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("{error}");
            return ExitCode::FAILURE;
        }
    };
    telemetry::init(&config.log_filter, config.log_format);

    let result = actix_web::rt::System::new().block_on(async {
        let container = Arc::new(Container::new(&config.database)?);
        let result = server::run(config.port, config.http, container.clone()).await;
        container.shutdown().await;
        result
    });
    match result {
        Ok(()) => {
            tracing::info!("stopped");
            ExitCode::SUCCESS
        }
        Err(error) => {
            tracing::error!(%error, "server failed");
            ExitCode::FAILURE
        }
    }
}
