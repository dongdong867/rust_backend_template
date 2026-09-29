# Every target runs from the repository root. CI calls the same targets.

TAPLO_VERSION := 0.10.0

.DEFAULT_GOAL := help
.PHONY: help run build test fmt fmt-check fmt-toml-check clippy lint check hooks-install tools taplo-version

help: ## List the targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z-]+:.*## / {printf "  %-15s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

run: ## Run the service, loading .env when it exists
	set -a; [ ! -f .env ] || . ./.env; set +a; cargo run --locked

build: ## Build the release binary
	cargo build --locked --release

test: ## Run every test
	cargo test --locked --workspace

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

check: lint test ## Run every check

hooks-install: ## Use the pre-commit hook in .githooks
	git config core.hooksPath .githooks

tools: ## Install the pinned Taplo CLI
	cargo install --locked taplo-cli --version $(TAPLO_VERSION)

taplo-version:
	@taplo --version 2>/dev/null | grep -qx 'taplo $(TAPLO_VERSION)' || { echo "Taplo $(TAPLO_VERSION) is required. Run: make tools" >&2; exit 1; }
