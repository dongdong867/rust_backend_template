# rust-backend-template

An HTTP service in Rust with Actix Web.
Read this file before changing code. [docs/architecture.md](docs/architecture.md) has the detail and the reasons.

## Commands

- test: `make test` — healthy: `test result: ok.`
- lint: `make lint` — healthy: ``Finished `dev` profile [unoptimized + debuginfo] target(s)``

Run `make test` during normal test-driven development; it never contacts PostgreSQL.
Run `make test-db` after changing SQL, migrations, database types, row mapping or completion concurrency.
Run `make check` on the complete publication candidate; it includes lint, fast tests and real database tests.
Database tests require `TEST_DATABASE_URL` pointing to an already-running dedicated test server with permission to create isolated databases.
Never start PostgreSQL automatically or add database tests to the pre-commit hook.

`make help` lists every target.
Run `make hooks-install` once per clone so each commit checks formatting first.

## Rules

- The repository root holds the workspace `Cargo.toml`, the service package in `rust-backend-template/`, and every other crate in `crates/`.
- The service package owns startup and shutdown, logging, dependency composition, route registration, middleware, and the mapping from errors to HTTP status codes. Its library is imported as `rust_backend_template`.
- `crates/environment` reads configuration once at startup. Only the service package depends on it. `Config` stays at the crate root; subordinate validated setting types live under `src/setting/`. A new setting gets a named environment variable, a validated type and tests, and an invalid value stops startup with a message that names the setting. Do not add a general mode switch such as `APP_ENV`.
- Each business feature is one crate under `crates/` with `domain`, `application`, `adapter` and `framework` layers, and folders by kind inside each layer. [docs/architecture.md](docs/architecture.md) shows the layout.
- One type per file, named after the type in snake case. A parent module file only declares its modules and re-exports their types.
- The controller trait in `adapter/port/in` is the only input port. Use cases are concrete `*UseCase` types in `application/use_case`. Application outbound dependencies are traits in `application/port/out`, implemented in the adapter layer.
- A feature has one shared adapter repository. It depends on a provider trait in `adapter/port/out` and plain storage DTOs, not concrete providers. The feature's `framework/storage` implements that provider with PostgreSQL or memory storage. Add other framework categories only when an integration needs them.
- HTTP request and response types live in the feature's `adapter/dto`. A route handler in the service package calls the controller and maps its error to a status code.
- Feature crates never depend on Actix or read environment variables. Production `domain`, `application` and `adapter` code never uses SQLx or imports `framework`; storage-specific SQLx code belongs in the feature's framework layer. Review imports and dependency direction against these rules; there is no custom lexical architecture checker.
- Keep domain fields private when validation, coordinated transitions or stable identity require controlled mutation. DTO fields can be public. Domain/application errors are plain enums with `Debug` and needed comparison/clone derives; add `Display` or `Error` only for an actual caller requirement.
- `DATABASE_URL` is required and has no runtime fallback. Keep example destinations in `.example.env`, never real secrets. `DATABASE_MAX_CONNECTIONS` defaults to `10`.
- Logs never contain secrets, SQL, connection strings or query strings.
- `GET /health` stays a fast liveness check. It checks no dependencies.
- Write a failing test before new behavior. Unit tests sit next to the code. A package's `tests/` folder holds tests through its public interface: in-memory HTTP tests and process tests for the service package.
