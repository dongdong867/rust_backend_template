# Every target runs from the repository root. CI calls the same targets.

TAPLO_VERSION := 0.10.0
CARGO_GENERATE_VERSION := 0.25.0
ACTIONLINT_VERSION := 1.7.12
FEATURES ?=
CARGO_FEATURES = $(if $(strip $(FEATURES)),--features "$(FEATURES)")

.DEFAULT_GOAL := help
.PHONY: help run migrate build test test-db template-test template-test-fast template-tools ci-tools ci-config-check tool-versions fmt fmt-check fmt-toml-check clippy lint check hooks-install tools taplo-version

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

run: ## Run the service, loading .env when it exists
	set -a; [ ! -f .env ] || . ./.env; set +a; cargo run --locked $(CARGO_FEATURES)

migrate: ## Apply PostgreSQL migrations deliberately, loading .env when it exists
	set -a; [ ! -f .env ] || . ./.env; set +a; cargo run --locked $(CARGO_FEATURES) --bin migrate

build: ## Build the release binary
	cargo build --locked --release $(CARGO_FEATURES)

test: ## Run fast tests without PostgreSQL
	cargo test --locked --workspace $(CARGO_FEATURES)
	python3 -m unittest discover -s scripts/tests -p 'test_*.py'

test-db: ## Run real SQL tests on an existing server (requires TEST_DATABASE_URL)
	@test -n "$${TEST_DATABASE_URL:-}" || { echo "TEST_DATABASE_URL is required; use a dedicated PostgreSQL test server with CREATEDB permission." >&2; exit 1; }
	@cargo test --locked -p rust-backend-template $(CARGO_FEATURES) --test postgres_database -- --ignored
	@DATABASE_URL="$$TEST_DATABASE_URL" cargo test --locked -p tasks $(CARGO_FEATURES) --test postgres_task_repository -- --ignored

template-test: ## Generate both variants and run their full checks (requires TEST_DATABASE_URL)
	python3 scripts/template_test.py

template-test-fast: ## Generate both variants and run lint and fast tests without PostgreSQL
	python3 scripts/template_test.py --fast-only

template-tools: ## Install the pinned generator locally for template verification
	cargo install --locked --version $(CARGO_GENERATE_VERSION) --root .tools cargo-generate

ci-tools: ## Install the pinned workflow checker locally with checksum verification
	python3 scripts/install_actionlint.py --version $(ACTIONLINT_VERSION)

ci-config-check: ci-tools ## Check the actual GitHub Actions workflows
	.tools/bin/actionlint -shellcheck= -pyflakes= .github/workflows/*.yml

tool-versions: ## Print tool pins for CI without duplicating versions in workflows
	@printf 'taplo=%s\ncargo-generate=%s\nactionlint=%s\n' '$(TAPLO_VERSION)' '$(CARGO_GENERATE_VERSION)' '$(ACTIONLINT_VERSION)'

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
