use std::process::ExitCode;

use environment::Config;
use rust_backend_template::{server, telemetry};

fn main() -> ExitCode {
    let config = match Config::from_env() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("{error}");
            return ExitCode::FAILURE;
        }
    };
    telemetry::init(&config.log_filter, config.log_format);

    match actix_web::rt::System::new().block_on(server::run(config.port)) {
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
