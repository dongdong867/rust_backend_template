//! Explicit migration entry point; the HTTP server never invokes it.

use std::process::ExitCode;

use environment::Config;
use rust_backend_template::database::create_pool;

#[actix_web::main]
async fn main() -> ExitCode {
    let config = match Config::from_env() {
        Ok(config) => config,
        Err(error) => {
            eprintln!("{error}");
            return ExitCode::FAILURE;
        }
    };
    let pool = match create_pool(&config.database) {
        Ok(pool) => pool,
        Err(error) => {
            eprintln!("{error}");
            return ExitCode::FAILURE;
        }
    };
    let result = sqlx::migrate!("../migrations").run(&pool).await;
    pool.close().await;
    match result {
        Ok(()) => {
            println!("migrations applied");
            ExitCode::SUCCESS
        }
        Err(_) => {
            // Migration errors can contain SQL or connection strings. Never print the source.
            eprintln!("migration failed: verify DATABASE_URL, connectivity and migration history");
            ExitCode::FAILURE
        }
    }
}
