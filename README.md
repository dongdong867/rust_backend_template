# rust-backend-template

A starting point for an HTTP service in Rust with Actix Web and PostgreSQL.

## Generate a service

```sh
cargo install cargo-generate --version 0.25.0 --locked
cargo generate --git https://github.com/dongdong867/rust_backend_template.git --name my-service
```

Use cargo-generate 0.25.0, not GitHub's "Use this template" button.
Generation asks whether to include the tasks example; both choices keep PostgreSQL and migrations.
It runs hooks that call Cargo and Unix `ln`, so review them before approving.

Template maintainers check both generated variants with `TEST_DATABASE_URL=postgres://localhost/postgres make template-test`, or with `make template-test-fast` without database tests.

## Run it

You need the Rust version in `rust-toolchain.toml`, which installs itself, the tools from `make tools`, and a running PostgreSQL server (18 is tested).

```sh
cp .example.env .env       # then set DATABASE_URL in .env
createdb rust_backend_template
make migrate
make run
curl localhost:8080/health    # {"status":"ok"}
```

`/health` answers without PostgreSQL; the service connects only when a request needs the database.
The binary reads its process environment; `make run` and `make migrate` also load `.env`.

### Configure it

[.example.env](.example.env) lists every setting. An invalid value stops startup with a message that names the setting.

| Setting | Default | Meaning |
|---|---|---|
| `PORT` | 8080 | Port the service listens on |
| `RUST_LOG` | `info` | Log filter |
| `LOG_FORMAT` | `pretty` | `pretty` or `json` |
| `DATABASE_URL` | required | PostgreSQL connection URL |
| `DATABASE_MAX_CONNECTIONS` | 10 | Pool size |
| `DATABASE_ACQUIRE_TIMEOUT_SECS` | 10 | How long a request waits for a free connection, 1 to 300 |
| `DATABASE_STATEMENT_TIMEOUT_SECS` | 10 | PostgreSQL cancels a longer query, 1 to 300; keep it below the request timeout |
| `HTTP_REQUEST_TIMEOUT_SECS` | 30 | Request deadline; a later answer is 504 |
| `HTTP_REQUEST_BODY_LIMIT_BYTES` | 1048576 | Largest buffered request body |
| `HTTP_CORS_ENABLED` | `true` | Turns the CORS middleware on or off |
| `HTTP_CORS_ALLOWED_ORIGINS` | empty | Comma-separated browser origins; `https://*.example.com` also allows its subdomains |

An empty origin list allows no cross-origin browser access. CORS is not authentication.
`make migrate` ignores the statement timeout, so long migrations can finish. A connection pooler such as PgBouncer may drop it.
For an encrypted database connection, add `sslmode=verify-full` to `DATABASE_URL`, and `sslrootcert` when the system does not trust the server's CA. Quote the URL in `.env` when it contains `&`.
Keep real credentials out of committed files.

### API documentation

Swagger UI is compiled out by default. To include it, set `API_DOC_PASSWORD`, and optionally `API_DOC_USERNAME` (default `docs`), then:

```sh
make run FEATURES=api-doc
```

`/docs/` serves the UI and `/docs/openapi.json` the schema, both behind HTTP Basic authentication. Serve them only over HTTPS.
This protects the documentation only, not the API routes.

### Run it in a container

```sh
make image             # build the production image
make compose-migrate   # apply migrations from the image
make compose-up        # start the service; fails unless GET /health answers within 30 s
make compose-down      # stop and remove it
```

Compose reads `.env`, or the file named by `SERVICE_ENV_FILE`, and never starts PostgreSQL.
Inside the container the service listens on 8080; `PORT` picks the port on this machine, which only this machine can reach.
Reach a PostgreSQL on this machine as `host.docker.internal`, and keep those settings in a second file, for example `SERVICE_ENV_FILE=.env.container make compose-up`.
The container restarts after a crash or a reboot until `make compose-down`. These targets need Docker with Compose 2.24.0 or later.

## Deploy it

This runs one release on one VM with Docker Compose, behind nginx and Cloudflare. The VM builds the image from a release tag, so no registry is needed.

### Prepare the VM

- Docker Engine with Compose 2.24.0 or later, a deploy user in the `docker` group, `curl` and nginx.
- A PostgreSQL server on another host, or a managed one, with the application's database created.
- A clone of this repository at `/srv/rust-backend-template`, owned by the deploy user.

Keep the settings outside the checkout, readable only by the deploy user:

```sh
mkdir -p ~/.config/rust-backend-template
f=~/.config/rust-backend-template/service.env
touch "$f"
chmod 600 "$f"
# Set PORT and DATABASE_URL in it, and API_DOC_PASSWORD for an api-doc build.
```

### Deploy a release

```sh
cd /srv/rust-backend-template
git fetch --tags
git checkout v1.2.3
export SERVICE_ENV_FILE=~/.config/rust-backend-template/service.env
make compose-migrate
make compose-up
```

Requests fail for a few seconds while the container is replaced. If `make compose-up` fails, read the printed log, fix the settings and run it again, or roll back.
For an api-doc build, add `FEATURES=api-doc` to every `make` command here and in the rollback.

### Put nginx in front

Set `proxy_pass` to your `PORT`.

```nginx
# /etc/nginx/conf.d/rust-backend-template.conf
server {
    # Proxied DNS: Cloudflare connects here over HTTPS with the origin certificate.
    listen 443 ssl;
    listen [::]:443 ssl;
    ssl_certificate     /etc/ssl/cloudflare/origin.pem;
    ssl_certificate_key /etc/ssl/cloudflare/origin.key;
    # Cloudflare Tunnel: replace the four lines above with `listen 127.0.0.1:80;`.

    server_name api.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Set up Cloudflare in one of two ways:

- **Proxied DNS:** point a proxied record at the VM, set SSL/TLS to Full (strict), save a Cloudflare Origin Certificate and its key as the two files above (key at mode 600), and let the firewall accept port 443 only from [Cloudflare's ranges](https://www.cloudflare.com/ips/).
- **Cloudflare Tunnel:** use `listen 127.0.0.1:80;`, open no port, and forward the hostname in the `cloudflared` configuration:

  ```yaml
  ingress:
    - hostname: api.example.com
      service: http://127.0.0.1:80
    - service: http_status:404
  ```

Then check and reload nginx:

```sh
sudo nginx -t && sudo systemctl reload nginx
```

### Roll back

Start the earlier tag without migrating:

```sh
cd /srv/rust-backend-template
git checkout v1.2.2
export SERVICE_ENV_FILE=~/.config/rust-backend-template/service.env
make compose-up
```

Migrations are never undone, and an older `migrate` refuses a newer schema. Roll back only to a release that works with the newer schema; that is why a migration adds columns and tables before a later release removes old ones.

## Tasks example

```sh
curl -X POST localhost:8080/v1/tasks -H 'Content-Type: application/json' -d '{"title":"Write documentation"}'
curl 'localhost:8080/v1/tasks/<id>'
curl -X POST 'localhost:8080/v1/tasks/<id>/complete'
```

Create returns 201, get and complete return 200, and completing a completed task returns 409. A title has 1 to 200 characters; any other title returns 400.

## Develop

```sh
make hooks-install                  # check formatting before each commit
make test                           # unit and in-memory HTTP tests; no PostgreSQL
make test-crate CRATE=environment   # one package's tests
make lint                           # formatting and Clippy
make audit                          # advisories, licenses and sources in Cargo.lock; needs network
make help                           # every target
```

After changing SQL, migrations or database code, run `TEST_DATABASE_URL=postgres://localhost/postgres make test-db`.
Before publishing a change, run `TEST_DATABASE_URL=postgres://localhost/postgres make check`, and again with `FEATURES=api-doc`.
Use a dedicated test server whose role can create databases. No `make` target starts PostgreSQL.
After changing the Dockerfile, the Compose file or startup, run the container targets above by hand.

## GitHub automation

- `.github/workflows/format.yml` checks formatting on every pull request, drafts included.
- `.github/workflows/template-ci.yml`, in the template only, runs Clippy, the tests and the audit for the source and both generated variants on ready pull requests and pushes to `main`.
- `.github/workflows/audit.yml` runs `make audit` every Monday and on demand from **Actions > Dependency audit**. GitHub pauses it in a public repository after 60 days without activity; **Enable workflow** restarts it.
- Dependabot opens weekly grouped updates for Cargo and GitHub Actions.

Pull request workflows have read-only permissions and no secrets.
For public repositories, set **Settings > Actions > General** to require approval for all external contributors. For private repositories, the approval setting covers fork contributors without write permission. Generation does not copy these settings.

### Maintainer-requested checks

On an open pull request, a maintainer with write access comments exactly one command:

| Command | Runs |
|---|---|
| `/lint` | `make lint` |
| `/test` | `make test` with default features and `api-doc` |
| `/test-{crate}` | one workspace package's tests, such as `/test-environment` |

The result appears as a commit status on the pull request's current head, such as `ci-command /test`, with a link to the run. A new commit needs a new command; to run it again, post the command again instead of using **Re-run jobs**.
The command runs the pull request's code, and that code can write default-branch cache entries, so read the PR code before posting a command.

### Release a version

1. Set the service package `version` in `rust-backend-template/Cargo.toml` and merge it to `main`.
2. Run `gh workflow run release.yml -f version=1.2.3`. This is a dry run that checks everything and creates nothing.
3. Run it again with `-f dry_run=false` to create the `v1.2.3` tag and a GitHub Release.

The release always takes the current `main` head. It stops before creating a tag when the version does not match, the tag exists, `main` moved, or a check on the commit failed or is still running. A run from a fork pull request does not count, nor do its checks, because its author can leave it waiting for approval.
It then runs the audit, lint, the tests including `make test-db`, and builds the image.

## Changing the service

Read [AGENTS.md](AGENTS.md) for the coding rules and checks, and [docs/architecture.md](docs/architecture.md) for where new code belongs.
