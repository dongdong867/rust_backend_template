# Every target runs from the repository root. CI calls the same targets.

TAPLO_VERSION := 0.10.0

.DEFAULT_GOAL := help
.PHONY: help run migrate build test test-db fmt fmt-check fmt-toml-check clippy lint check hooks-install tools taplo-version

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

run: ## Run the service, loading .env when it exists
	set -a; [ ! -f .env ] || . ./.env; set +a; cargo run --locked

migrate: ## Apply PostgreSQL migrations deliberately, loading .env when it exists
	set -a; [ ! -f .env ] || . ./.env; set +a; cargo run --locked --bin migrate

build: ## Build the release binary
	cargo build --locked --release

test: ## Run fast tests without PostgreSQL
	cargo test --locked --workspace

test-db: ## Run real SQL tests on an existing server (requires TEST_DATABASE_URL)
	@test -n "$${TEST_DATABASE_URL:-}" || { echo "TEST_DATABASE_URL is required; use a dedicated PostgreSQL test server with CREATEDB permission." >&2; exit 1; }
	@DATABASE_URL="$$TEST_DATABASE_URL" cargo test --locked -p tasks --test postgres_task_repository -- --ignored

fmt: taplo-version ## Format Rust and TOML files
	cargo fmt --all
	RUST_LOG=warn taplo fmt

fmt-check: ## Check Rust formatting
	cargo fmt --all --check

fmt-toml-check: taplo-version ## Check TOML formatting
	RUST_LOG=warn taplo fmt --check

clippy: ## Run Clippy, failing on any warning
	cargo clippy --locked --workspace --all-targets --all-features -- -D warnings -D clippy::too_many_lines

lint: fmt-check fmt-toml-check clippy ## Run every static check

check: lint test test-db ## Run the full pre-publication check (requires TEST_DATABASE_URL)

hooks-install: ## Use the pre-commit hook in .githooks
	git config core.hooksPath .githooks

tools: ## Install the pinned Taplo CLI
	cargo install --locked taplo-cli --version $(TAPLO_VERSION)

taplo-version:
	@taplo --version 2>/dev/null | grep -qx 'taplo $(TAPLO_VERSION)' || { echo "Taplo $(TAPLO_VERSION) is required. Run: make tools" >&2; exit 1; }
