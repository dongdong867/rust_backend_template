# Architecture

Read this when adding a feature or changing an integration, configuration source, or resource lifecycle.
[AGENTS.md](../AGENTS.md) defines the coding rules; [README.md](../README.md) covers setup and operation.

## Ownership

| Area | Owns |
|---|---|
| Service package | Startup and shutdown, logging, dependency composition, Actix routes and middleware, and HTTP error mapping |
| `crates/environment` | Validated configuration, read once before startup |
| `crates/<feature>` | A business feature's domain, use cases, adapter interfaces, and framework integrations |
| `migrations/` | Versioned PostgreSQL schema changes, applied explicitly |

Keep business rules out of the service package. It selects integrations and connects them to the feature's interfaces.
Keeping configuration at this boundary lets the same business code run with production dependencies or test doubles.

## Dependency placement

Within a feature, dependencies point inward: application uses domain, adapter uses application and domain, and framework implements adapter interfaces.
Inner layers do not import framework implementations, including in unit tests.

| New code | Place it in |
|---|---|
| Entity or value type | `domain/` |
| Use case and its input command | `application/use_case/` and `application/command/` |
| Dependency required by a use case | `application/port/out/` |
| Controller input port and implementation | `adapter/port/in/` and `adapter/controller/` |
| HTTP request/response DTO | `adapter/dto/` |
| Shared repository and plain storage DTO | `adapter/repository/` and `adapter/dto/storage/` |
| Storage-provider interface | `adapter/port/out/` |
| PostgreSQL or in-memory storage implementation | `framework/storage/` |

Use cases are concrete because tests exercise the actual business operation and replace its outbound dependencies.
The controller is the input port; adding a second trait for each use case would add indirection without a needed substitution.

The shared adapter repository owns domain/storage conversion, validation of reconstructed domain objects, and error translation.
Storage providers own persistence mechanics. Plain storage DTOs keep SQLx and other storage-specific types outside the adapter.
Provider selection belongs in the service package so choosing a backend does not change business operations or duplicate repository rules.

Keep cross-layer error conversions in the outer involved layer to preserve dependency direction.
Implement context-free conversions with `From` on the destination type; domain/application errors do not need presentation behavior unless a caller requires it.

Add framework categories only for real integrations, according to responsibility: Redis persistence belongs in `storage/`, while Redis read acceleration belongs in `cache/`.
Do not pre-create empty integration scaffolding.

## Configuration

Read and validate settings once before starting the server. A bad setting should fail startup, not cause a request-dependent failure.
Feature code receives dependencies rather than consulting environment variables.
Named variables configure runtime behavior; Cargo features control compiled code. A general environment mode would hide which settings actually differ.

Use the process environment for deployment settings and secrets; `.env` is a local convenience loaded by Make.
Another configuration file format would add discovery and precedence rules without replacing deployment overrides.
See [README configuration guidance](../README.md#configure-it) and [.example.env](../.example.env) for operator-facing settings.

Secret-setting errors must name the setting without echoing its value.
Database URL validation must reject unsupported parameters before driver diagnostics can expose their keys or values.
When upgrading the PostgreSQL driver, keep its recognized parameter list aligned with this validation.

## Composition and lifecycle

Compose dependencies once in the service package. Production and HTTP tests share the assembly path; tests inject different providers rather than maintaining a second graph.
Use a typed provider bundle with one selected implementation per required port, not a registry of every available backend.
Handlers receive their controller, not unrestricted access to the whole container.

The service owns one shared, lazy PostgreSQL pool and closes it after the HTTP server stops.
Injected external resources remain caller-owned. Make ownership explicit when adding integrations so shutdown does not leak resources or close resources belonging to another caller.

Lazy connections let startup and `/health` remain independent of database availability.
Keep `/health` as liveness; add a separate readiness endpoint if a deployment needs dependency checks.
Apply migrations explicitly, not during server startup or once per Actix worker.
Use runtime-checked queries so building the service does not require a database or saved query metadata.

## Safety and test boundaries

Keep HTTP error mapping in the service package, including framework failures, so all routes return safe RFC 9457 problem details.
Do not expose internal error text; logs must also avoid SQL, credentials, connection strings, and query strings.
A URL query can contain secrets, so request logging uses only the path.

Buffered extractors share the configured body limit. A route streaming raw payloads must enforce its own explicit limit.
An HTML route must supply an appropriate route-specific CSP rather than weakening the API-wide policy.
The HTTPS ingress or reverse proxy owns HSTS because it knows whether the connection is secure.

The optional `api-doc` Cargo feature keeps documentation dependencies out of default builds.
Schema derives on adapter DTOs are feature-gated; they do not move HTTP composition into feature crates.
The service owns schema composition and one Basic-authenticated `/docs/` scope for UI, JSON and assets.
Its vendored UI has a scoped CSP; API routes keep their strict policy and existing CORS rules.
Documentation authentication is not API authentication, and credentials remain redacted configuration.

Unit tests replace ports without pulling framework implementations into inner layers.
Public-interface integration tests may compose real providers; in-memory HTTP tests exercise the actual application assembly.
Real-database tests cover SQL, row mapping, migrations, and concurrency that port doubles cannot prove.

Use process tests for logging checks: tracing caches callsite enablement globally, so test-scoped subscribers can miss events when tests run in parallel.
For required commands and when to run them, see [AGENTS.md](../AGENTS.md#commands).
