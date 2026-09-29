# rust-backend-template

An HTTP service in Rust with Actix Web.
Read this file before changing code. [docs/architecture.md](docs/architecture.md) has the detail and the reasons.

## Commands

- test: `make test` — healthy: `test result: ok.`
- lint: `make lint` — healthy: ``Finished `dev` profile [unoptimized + debuginfo] target(s)``

`make help` lists every target.
Run `make hooks-install` once per clone so each commit checks formatting first.

## Rules

- The repository root holds the workspace `Cargo.toml`, the service package in `rust-backend-template/`, and every other crate in `crates/`.
- The service package owns startup and shutdown, logging, dependency composition, route registration, middleware, and the mapping from errors to HTTP status codes. Its library is imported as `rust_backend_template`.
- `crates/environment` reads configuration once at startup. Only the service package depends on it. A new setting gets a named environment variable, a validated type and tests, and an invalid value stops startup with a message that names the setting. Do not add a general mode switch such as `APP_ENV`.
- Each business feature is one crate under `crates/` with `domain`, `application` and `adapter` layers, and folders by kind inside each layer. [docs/architecture.md](docs/architecture.md) shows the layout.
- One type per file, named after the type in snake case. A parent module file only declares its modules and re-exports their types.
- The controller trait in `adapter/port/in` is the only input port. Use cases are concrete services in `application/service`. Outbound dependencies are traits in `application/port/out`, implemented in the adapter layer.
- HTTP request and response types live in the feature's `adapter/dto`. A route handler in the service package calls the controller and maps its error to a status code.
- Feature crates never depend on Actix. Domain and application code never uses SQLx or reads environment variables.
- Logs never contain secrets, SQL, connection strings or query strings.
- `GET /health` stays a fast liveness check. It checks no dependencies.
- Write a failing test before new behavior. Unit tests sit next to the code. A package's `tests/` folder holds tests through its public interface: in-memory HTTP tests and process tests for the service package.
