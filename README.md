# rust-backend-template

A starting point for an HTTP service in Rust with Actix Web.

## Generate a service

Use `cargo generate`, not GitHub's "Use this template" button:

```sh
cargo install cargo-generate --version 0.25.0 --locked
cargo generate --git https://github.com/dongdong867/rust_backend_template.git --name my-service
```

Use **cargo-generate 0.25.0**; the template relies on that version's rendering and hooks.
Choose whether to include the removable tasks example. Both choices retain PostgreSQL and migration support.
Review the hooks before approving their execution, especially for an untrusted template.
Generation runs Cargo and requires Unix `ln` for the agent-instructions symlink; dependency resolution may need network access.

Template maintainers run `TEST_DATABASE_URL=postgres://localhost/postgres make template-test` to check the Liquid twins and fully verify both generated variants.
This requires the dedicated test server described under [Develop](#develop).

## Run it

You need Rust (the pinned version in `rust-toolchain.toml` installs itself) and the pinned Taplo CLI (`make tools`).
Database operations need an already-running PostgreSQL server; PostgreSQL 18 is the tested version.
Create the application's database once, then apply its migrations:

```sh
cp .example.env .env
# Set DATABASE_URL in .env to your application's PostgreSQL database before running.
createdb rust_backend_template
make migrate
make run
curl localhost:8080/health    # {"status":"ok"}
```

`/health` works without PostgreSQL. Database connections are acquired only when database operations need them.
The binary reads its process environment; `make run` and `make migrate` load `.env` for local use.
`DATABASE_URL` must be explicitly supplied; a missing value stops startup instead of selecting a local database.
`.example.env` supplies a placeholder for local setup, not a runtime default.

### Configure it

[.example.env](.example.env) lists the settings and local example values. Supply production settings through the process environment.
To allow browser access, set `HTTP_CORS_ALLOWED_ORIGINS` to comma-separated HTTP(S) origins.
An origin such as `https://*.example.com` includes the apex and subdomains, but only with the same scheme and port.
The empty allowlist permits no cross-origin browser access; CORS is not authentication or authorization.
`HTTP_REQUEST_TIMEOUT_SECS` sets the request deadline; `HTTP_REQUEST_BODY_LIMIT_BYTES` limits buffered request bodies.

### Database transport and connection options

For deployments requiring authenticated encryption, set `sslmode=verify-full` in `DATABASE_URL` and use the database server's certificate hostname.
If its CA is not in the platform trust store, also supply `sslrootcert` pointing to the trusted public CA certificate.
The default `sslmode=prefer` permits plaintext fallback; it does not require encryption.
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
Titles are preserved as submitted and must contain 1–200 Unicode characters, excluding U+0000; invalid titles return `400`.

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
Database tests create isolated databases and may leave failed-test databases or harness bookkeeping for inspection.
They require `TEST_DATABASE_URL`; no command starts PostgreSQL automatically.
The pre-commit hook remains database-free.

## Changing the service

Read [AGENTS.md](AGENTS.md) for coding rules and required checks.
Use [docs/architecture.md](docs/architecture.md) when deciding where new code belongs or changing dependency composition, configuration, or resource ownership.
