# tempest-fastapi-sdk — developer & release automation.
#
# Run `make` (or `make help`) to see every target.
# Override defaults: `make release VERSION=0.2.0`.

PACKAGE := tempest_fastapi_sdk
PYTHON_VERSION := 3.11

.DEFAULT_GOAL := help
.PHONY: help install sync clean test cov lint fix fmt fmt-check type check ci build smoke release tag version docs docs-serve docs-build

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ---------- setup ----------

install: ## Sync dependencies with all extras (auth, email, upload)
	uv sync --all-extras

sync: install ## Alias for `install`

## ---------- code quality ----------

test: ## Run pytest with coverage
	uv run pytest

cov: ## Open the last coverage HTML report (run `pytest --cov-report=html` first)
	@command -v xdg-open >/dev/null && xdg-open htmlcov/index.html || open htmlcov/index.html

lint: ## Run ruff lint
	uv run ruff check .

fix: ## Apply every ruff autofix + format (imports, quotes, whitespace, unused)
	uv run ruff check --fix .
	uv run ruff format .

fmt: ## Auto-format with ruff
	uv run ruff format .

fmt-check: ## Verify formatting without modifying files
	uv run ruff format --check .

type: ## Run mypy in strict mode
	uv run mypy $(PACKAGE)

check: lint fmt-check type test ## Run every gate (lint + format check + mypy + tests)

ci: check build smoke ## Full local mirror of the GitHub Actions pipeline

## ---------- packaging ----------

build: ## Build sdist + wheel into dist/
	rm -rf dist
	uv build

smoke: build ## Install the freshly built wheel in a clean venv and import the top-level surface
	@rm -rf /tmp/$(PACKAGE)-smoke
	uv venv --python $(PYTHON_VERSION) /tmp/$(PACKAGE)-smoke
	uv pip install --python /tmp/$(PACKAGE)-smoke/bin/python --quiet "$$(ls dist/*.whl)[all]"
	/tmp/$(PACKAGE)-smoke/bin/python -c "import $(PACKAGE) as m; \
		assert m.__version__, 'no __version__'; \
		assert m.BaseModel and m.BaseRepository and m.AsyncDatabaseManager, 'core primitives missing'; \
		assert m.AlembicHelper and m.NAMING_CONVENTION, 'alembic helpers missing'; \
		assert m.PasswordUtils and m.JWTUtils and m.EmailUtils and m.UploadUtils, 'utils missing'; \
		assert m.is_valid_cpf and m.is_valid_cnpj and m.is_valid_phone_br, 'BR regex helpers missing'; \
		print('Smoke OK · version =', m.__version__)"
	/tmp/$(PACKAGE)-smoke/bin/python -c "from $(PACKAGE).ssr import Page, html_response, make_htmx_router, make_web_app_router, build_web_app, detect_build_mode; \
		print('SSR extra OK ·', Page.__name__, html_response.__name__, make_htmx_router.__name__, make_web_app_router.__name__, build_web_app.__name__, detect_build_mode.__name__)"
	@rm -rf /tmp/$(PACKAGE)-smoke

version: ## Print the version recorded in pyproject.toml and __init__.py
	@printf "pyproject.toml: "
	@grep -E '^version =' pyproject.toml | head -1
	@printf "__init__.py:    "
	@grep -E "^__version__" $(PACKAGE)/__init__.py | head -1

## ---------- release ----------

tag: ## Tag the current commit with the project version (no push)
	@VER=$$(grep -E '^version =' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/'); \
		git tag "v$$VER" && echo "Tagged v$$VER (run \`git push origin v$$VER\` when ready)"

release: ## Bump versions, commit, tag and push. Usage: make release VERSION=0.2.0
	@test -n "$(VERSION)" || (echo "Usage: make release VERSION=0.2.0"; exit 1)
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree dirty. Commit or stash first."; exit 1; \
	fi
	@echo "Bumping pyproject.toml and $(PACKAGE)/__init__.py to $(VERSION)"
	sed -i -E 's/^version = "[^"]+"/version = "$(VERSION)"/' pyproject.toml
	sed -i -E 's/^__version__: str = "[^"]+"/__version__: str = "$(VERSION)"/' $(PACKAGE)/__init__.py
	$(MAKE) check
	git add pyproject.toml $(PACKAGE)/__init__.py
	git commit -m "chore: release v$(VERSION)"
	git tag "v$(VERSION)"
	@echo
	@echo "Ready to push. Review with \`git show v$(VERSION)\` then:"
	@echo "    git push origin main"
	@echo "    git push origin v$(VERSION)"

## ---------- docs ----------

docs-serve: ## Serve mkdocs with live reload at http://127.0.0.1:8000
	uv run --group docs mkdocs serve

docs-build: ## Build the static docs site into ./site/ (strict — fails on warnings)
	uv run --group docs mkdocs build --strict

docs: docs-build ## Alias for docs-build

## ---------- housekeeping ----------

clean: ## Remove caches, build artifacts and coverage data
	rm -rf dist build *.egg-info site
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov
	rm -f .coverage .coverage.*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
