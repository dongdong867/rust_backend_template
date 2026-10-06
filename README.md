# rust-backend-template

A starting point for an HTTP service in Rust with Actix Web.

## Run it

You need Rust (the pinned version in `rust-toolchain.toml` installs itself) and the pinned Taplo CLI (`make tools`).
The tasks example also needs an already-running PostgreSQL server; PostgreSQL 18 is the tested version.
Create the application's database once, then apply its migrations:

```sh
cp .example.env .env
createdb rust_backend_template
make migrate
make run
curl localhost:8080/health    # {"status":"ok"}
```

`/health` works without PostgreSQL. Database connections are acquired only when task requests need them.
The binary reads its process environment; `make run` and `make migrate` load `.env` for local use.
`DATABASE_URL` must be explicitly supplied; a missing value stops startup instead of selecting a local database.
`.example.env` supplies a placeholder for local setup, not a runtime default.

### Database transport and connection options

The PostgreSQL client includes rustls with platform trust roots.
For deployments requiring authenticated encryption, set `sslmode=verify-full` in `DATABASE_URL` and use the database server's certificate hostname.
If its CA is not in the platform trust store, also supply `sslrootcert` pointing to the trusted public CA certificate.
SQLx's default `sslmode=prefer` still permits plaintext fallback, which keeps local PostgreSQL setups usable; enabling TLS capability does not make that mode require encryption.
Unknown or malformed connection parameter names fail startup with a redacted `DATABASE_URL` error before SQLx parses them.
When putting a URL with `&` query separators in a shell-loaded `.env`, quote the complete value.
Supply real credentials through the deployment environment or an ignored local file, never committed examples.

## Tasks example

```sh
curl -X POST localhost:8080/v1/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title":"Write documentation"}'
curl 'localhost:8080/v1/tasks/<id>'
curl -X POST 'localhost:8080/v1/tasks/<id>/complete'
```

Create returns `201`; get and complete return `200` with the task.
Completing an already-completed task returns `409`.
Titles are preserved as submitted and must contain 1–200 Unicode characters.
The null character U+0000 is rejected with `400` because PostgreSQL text cannot store it.
Errors use safe RFC 9457 problem details, with no internal SQL or connection information.

## Develop

```sh
make hooks-install   # check formatting before each commit
make test            # fast unit, service and in-memory HTTP tests; no PostgreSQL
make lint            # Rust/TOML formatting and Clippy; no PostgreSQL
make help            # every target
```

After changing repository SQL, migrations, database types or concurrency behavior, run:

```sh
TEST_DATABASE_URL=postgres://localhost/postgres make test-db
```

Before proposing the complete change for publication, run:

```sh
TEST_DATABASE_URL=postgres://localhost/postgres make check
```

Use a **dedicated test server**, not production. Its role needs `CREATEDB` permission.
SQLx creates an isolated temporary database and applies the real migrations; tests never migrate the URL's base database.
Successful tests attempt to drop their isolated database; failed tests may retain it for inspection.
SQLx keeps `_sqlx_test` bookkeeping in the base database, and cleanup failures are warnings rather than guaranteed test failures.
No command starts PostgreSQL automatically, and `make test-db` refuses to run without `TEST_DATABASE_URL`.
The pre-commit hook remains database-free.
Fast tests include bounded TLS wire peers with generated test certificates; they exercise the production SQLx TLS path and certificate rejection without running PostgreSQL.

## Feature boundaries

Each feature owns its domain, application, adapter and framework layers.
The shared adapter repository maps domain objects to plain storage DTOs and depends on a storage-provider interface.
Concrete PostgreSQL and in-memory providers live in that feature's `framework/storage/`; the service crate chooses and injects the provider.
Application operations are concrete `*UseCase` types.
`AGENTS.md` and architecture documentation define dependency rules; code review checks them instead of a custom source parser.

[AGENTS.md](AGENTS.md) holds the project rules, and [docs/architecture.md](docs/architecture.md) explains them.
