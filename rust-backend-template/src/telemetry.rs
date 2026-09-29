//! Log output: one subscriber for the whole process, chosen by `LOG_FORMAT`.

use std::io::{self, IsTerminal};

use tracing_subscriber::EnvFilter;
use tracing_subscriber::util::SubscriberInitExt;

use environment::LogFormat;

/// Installs the process-wide subscriber, writing to stdout. JSON output puts event fields at the
/// top level and the current span, which carries the request ID, under `span`. Records from the
/// `log` crate, such as Actix's own startup and shutdown messages, go through it too.
pub fn init(log_filter: &str, log_format: LogFormat) {
    let builder = tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::new(log_filter))
        .with_ansi(io::stdout().is_terminal());
    match log_format {
        LogFormat::Pretty => builder.finish().init(),
        LogFormat::Json => builder
            .json()
            .flatten_event(true)
            .with_current_span(true)
            .with_span_list(false)
            .finish()
            .init(),
    }
}
