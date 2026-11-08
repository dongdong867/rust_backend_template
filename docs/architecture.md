# Architecture

Where new code goes. [AGENTS.md](../AGENTS.md) has the rules and checks.

## Ownership

| Area | Owns |
|---|---|
| Service package | Startup and shutdown, logging, composing dependencies, routes, middleware, and mapping errors to HTTP responses |
| `crates/environment` | Configuration, read and checked once before startup |
| `crates/<feature>` | One business feature: its domain, use cases, ports and integrations |
| `migrations/` | PostgreSQL schema changes, applied only by `migrate` |

Business rules stay out of the service package; it only picks integrations and connects them to features.

## Dependency placement

Inside a feature, dependencies point inward: `application` uses `domain`, `adapter` uses both, and `framework` implements adapter interfaces.

| New code | Goes in |
|---|---|
| Entity or value type | `domain/` |
| Use case and its input command | `application/use_case/` and `application/command/` |
| Dependency a use case needs | `application/port/out/` |
| Controller trait and implementation | `adapter/port/in/` and `adapter/controller/` |
| HTTP request or response type | `adapter/dto/` |
| Repository and plain storage types | `adapter/repository/` and `adapter/dto/storage/` |
| Storage-provider interface | `adapter/port/out/` |
| PostgreSQL or in-memory storage | `framework/storage/` |

The repository converts between domain and storage types and translates errors; storage providers only persist.
Put an error conversion in the outer of the two layers involved.
Add a framework folder only for a real integration, such as `cache/` for a Redis cache.

## Configuration

Settings are read and checked once before the server starts, so a bad value fails at startup, not on a request.
Feature code receives its dependencies and never reads the environment.
Named settings configure runtime behavior and Cargo features choose compiled code; there is no general environment mode.
A setting that holds a secret is never printed, including in its error.
`DATABASE_URL` checking refuses parameters the driver does not know; keep that list in step when upgrading SQLx.

## Composition and lifecycle

Production and the HTTP tests build the app the same way; tests only swap the providers.
Providers come in one typed bundle with one implementation per port.
Handlers receive their controller, not the whole container.
The service owns one lazy PostgreSQL pool and closes it after the server stops, so startup and `/health` never need the database.
Migrations run only through `migrate`, never at startup.
Queries are checked at runtime, so building needs no database.

## Errors, logs and tests

Every route returns RFC 9457 problem details without internal error text.
Logs never contain SQL, credentials, connection strings or query strings; request logs use only the path.
Buffered request bodies share `HTTP_REQUEST_BODY_LIMIT_BYTES`; a route that streams its body sets its own limit.
An HTML route sets its own Content-Security-Policy instead of weakening the API's.
The `api-doc` feature gates the documentation crates and the schema derives on DTOs.

Unit tests replace ports; in-memory HTTP tests use the real app; database tests cover SQL, mapping and migrations.
Check log output in process tests, because tracing caches log settings across tests that run in parallel.
