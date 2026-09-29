# Architecture

This document explains the rules in [AGENTS.md](../AGENTS.md) and the reasons for them.

## Repository layout

```text
Cargo.toml                        the workspace: members and shared dependency versions
rust-backend-template/            the service package, named after the service
  src/
    main.rs                       reads configuration, starts logging, runs the server
    lib.rs                        the library that main.rs and the tests import
    api/route/health.rs           GET /health: handler, response and route registration
    api/middleware/request_id.rs  request IDs and one log line per request
    create_app.rs                 the Actix App: middleware and every route
    server.rs                     binds the port, serves the App, stops gracefully
    telemetry.rs                  the log subscriber
  tests/                          in-memory HTTP tests and process tests
crates/
  environment/                    Config::from_env, ConfigError, LogFormat
  <feature>/                      one crate per business feature
```

Later work adds `container.rs` to build each feature's services, controller and repositories once, `database.rs` for the shared PostgreSQL pool, `api/route/v1/<feature>.rs` for a feature's routes, `swagger/` for API documentation, and `migrations/`.

## Module files

Each type gets its own file, named after the type in snake case.
A parent module file only declares its modules and re-exports their types, as in:

```rust
// application/service.rs
pub(crate) mod create_task_service;

pub use create_task_service::CreateTaskService;
```

Callers import the re-export, such as `application::service::CreateTaskService`.

## The service package

The service package ties the service to its runtime:

- process startup and shutdown, and logging;
- composing dependencies, such as creating one shared database pool and each feature's controller;
- registering routes and middleware;
- mapping each controller error to an HTTP status code.

It holds no business rules.

## Configuration

`crates/environment` reads every setting once, before the server starts, and returns an immutable `Config`.
Only the service package depends on it, so domain and application code never reads the environment.
An unset setting takes its default.
A set but invalid setting stops startup with an error that names the setting, the expected form and the value.
A secret setting must not echo its value in that error.
Non-Unicode values are rejected without echoing their contents. An empty or whitespace-only `RUST_LOG` is invalid rather than silently lowering the log level.
`Config` remains the crate's root aggregate. Subordinate validated types, such as `HttpConfig` and `LogFormat`, live under `crates/environment/src/setting/`.

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8080` | TCP port on all IPv4 interfaces. `0` asks the system for a free port; the `listening` log line shows which one. |
| `RUST_LOG` | `info` | Log filter, such as `debug,actix_server=warn`. |
| `LOG_FORMAT` | `pretty` | `pretty` for people, `json` for log collectors. |
| `HTTP_CORS_ENABLED` | `true` | Installs the CORS middleware when `true`; `false` emits no CORS headers. |
| `HTTP_CORS_ALLOWED_ORIGINS` | empty | Comma-separated HTTP or HTTPS origins allowed browser access. Each entry is normalized to an origin; paths, credentials, queries, fragments and `*` are invalid. |
| `HTTP_REQUEST_TIMEOUT_SECS` | `30` | Positive whole-request deadline in seconds. |
| `HTTP_REQUEST_BODY_LIMIT_BYTES` | `1048576` | Positive maximum size for JSON and other buffered request-body extractors. |

The binary reads only its process environment.
`make run` loads a local `.env` file first; `.example.env` shows the settings.
`.env` files are ignored by Git, and `.example.env` holds placeholders only.
Production receives settings and secrets from its deployment environment.
Safe defaults stay in code, while environment variables carry values that differ between deployments.
The project does not add a TOML configuration source for these flat settings because doing so would add file discovery and precedence rules without replacing the need for deployment overrides.

A named environment variable configures runtime behavior.
A Cargo feature switches code in or out at compile time.
There is no general feature-flag system and no `APP_ENV`-style mode.

## Feature crates

A business feature is one crate under `crates/`, with three layers and folders by kind inside each layer:

```text
crates/tasks/src/
  lib.rs                                  pub mod adapter; pub mod application; pub mod domain;
  domain/                                 entities and value types
    task.rs
    task_status.rs
  application/
    command/create_task_command.rs        input to one use case
    error/create_task_error.rs            what one use case can fail with
    port/out/task_repository.rs           an outbound trait the use cases need
    service/create_task_service.rs        one concrete use case
  adapter/
    port/in/task_controller.rs            the input port: a trait the route handlers call
    controller/task_controller_impl.rs    request → command → service → response
    dto/create_task_request.rs            HTTP request and response types
    dto/task_response.rs
    error/task_controller_error.rs        the controller's error
    repository/postgres_task_repository.rs  implements application/port/out
```

Dependencies point inward.
`domain` depends on nothing else in the crate.
`application` depends on `domain`.
`adapter` depends on both.

The controller trait is the only input port.
Use cases are concrete services, because the tests run them for real and replace only their outbound ports.
Outbound dependencies, such as a repository, are traits in `application/port/out` so tests can replace them.

The controller turns a request type into a command, calls the service, and turns the result into a response type.
The service package's route handler calls the controller through `web::Data<dyn TaskController>` and maps the controller's error to a status code:

```rust
async fn create_task(
    controller: web::Data<dyn TaskController>,
    body: web::Json<CreateTaskRequest>,
) -> HttpResponse {
    match controller.create_task(body.into_inner()).await {
        Ok(task) => HttpResponse::Created().json(task),
        Err(error) => error_response(error),
    }
}
```

A feature crate never depends on Actix; its request and response types need only serde.
Cargo cannot stop `application` from importing SQLx, because the repository in the same crate needs it.
Reviews check that `domain` and `application` never import SQLx or read environment variables.
There is no shared crate holding every feature's SQL: a feature receives the shared pool from the service package and owns its own queries.

## HTTP safety

Every client error and server error uses an RFC 9457 Problem Details body with `application/problem+json`:

```json
{"type":"about:blank","title":"Not Found","status":404}
```

The service package owns one replaceable mapping for both application and Actix failures.
Unknown routes, wrong methods, malformed JSON, oversized buffered bodies and timeouts therefore use the same shape.
The response deliberately has no `detail` field and never exposes SQL, connection strings, secrets or internal error text.
Conversion points log safe diagnostic context instead.
A request that exceeds its whole-request deadline returns `504 Gateway Timeout`.

CORS is enabled by default but starts with an empty origin allowlist, so no cross-origin browser access is allowed until an operator names exact origins.
An allowed origin may use any route method and request header; credentials remain disabled.
A simple request from another origin is processed without an `Access-Control-Allow-Origin` response header, while a refused preflight receives the normal safe error response.
Turning CORS off installs no CORS middleware.
CORS is a browser permission mechanism, not authentication or authorization.

`HTTP_REQUEST_BODY_LIMIT_BYTES` configures Actix's JSON, bytes and string extractors.
A route that intentionally streams a raw `web::Payload` must enforce its own explicit limit rather than buffering the stream.

Every normal and error response receives these fixed headers:

| Header | Value | Purpose |
|---|---|---|
| `Content-Security-Policy` | `default-src 'none'; base-uri 'none'; frame-ancestors 'none'` | Loads no browser resources by default, forbids injected base URLs and prevents framing. A future HTML route such as Swagger UI must set its own narrower route-specific policy. |
| `Permissions-Policy` | `camera=(), geolocation=(), microphone=()` | Disables browser capabilities the API does not use. |
| `Referrer-Policy` | `no-referrer` | Prevents browser URL data from leaking through the `Referer` header. |
| `X-Content-Type-Options` | `nosniff` | Makes browsers honor the declared content type. |
| `X-Frame-Options` | `DENY` | Prevents framing in clients that do not enforce CSP's `frame-ancestors`. |

These headers are fixed application policy, not runtime configuration.
The application does not emit `Strict-Transport-Security`: the HTTPS ingress or reverse proxy that knows the connection is secure owns HSTS, while local development can continue to use HTTP.

## Logging

Every request gets an ID.
A valid `x-request-id` from the caller, such as one set by a proxy, is kept: 1 to 128 letters, digits, `-`, `_` or `.`.
Any other value is replaced by a new UUID.
The ID is returned in the `x-request-id` response header.

Each request logs one `request completed` line with `method`, `path`, `status` and `duration_ms`, inside a span that carries `request_id`.
The path excludes the query string, because a query can carry secrets.
In JSON format, event fields sit at the top level and the span sits under `span`:

```json
{"timestamp":"…","level":"INFO","message":"request completed","method":"GET","path":"/health","status":200,"duration_ms":0,"target":"rust_backend_template::api::middleware::request_id","span":{"request_id":"abc-123","name":"request"}}
```

Logs never contain secrets, SQL, connection strings or query strings.

## Health

`GET /health` answers `200` with `{"status":"ok"}` as soon as the process serves HTTP.
It checks no dependencies and reveals no internal details, so it stays fast and safe to expose.
Do not turn it into a database readiness check; add a separate endpoint if a deployment needs one.

## Shutdown

SIGTERM starts a graceful stop: the server stops accepting connections and waits up to 30 seconds (`SHUTDOWN_TIMEOUT_SECS`) for requests in progress.
SIGINT and SIGQUIT stop at once.
The process logs `stopped` and exits with status 0 after a clean stop.

## Tests

1. Unit tests sit next to the code they test.
2. In-memory HTTP tests in `rust-backend-template/tests/` build the real App with `create_app()` and call it through Actix's test service, with outbound ports replaced where needed.
3. Process tests in the same folder start the built binary with real settings and talk to it over TCP.

Log output is checked in process tests.
Tracing caches each log call's enabled state for the whole process, so a test-scoped subscriber misses events when other tests run in parallel.
