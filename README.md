# rust-backend-template

A starting point for an HTTP service in Rust with Actix Web.

## Run it

You need Rust (the pinned version in `rust-toolchain.toml` installs itself) and the pinned Taplo CLI (`make tools`).

```sh
cp .example.env .env
make run
curl localhost:8080/health    # {"status":"ok"}
```

## Develop

```sh
make hooks-install   # check formatting before each commit
make check           # formatting, Clippy and every test
make help            # every target
```

[AGENTS.md](AGENTS.md) holds the project rules, and [docs/architecture.md](docs/architecture.md) explains them.
