# tempest-fastapi-sdk — developer & release automation.
#
# Run `make` (or `make help`) to see every target.
# Override defaults: `make release VERSION=0.2.0`.

PACKAGE := tempest_fastapi_sdk
PYTHON_VERSION := 3.11

.DEFAULT_GOAL := help
.PHONY: help install sync clean openpix-regen mercadopago-regen mercadopago-fetch stripe-regen stripe-fetch zap-regen zap-fetch zap-ws-regen zap-ws-fetch test test-cov test-model test-gpu cov lint fix fmt fmt-check type check ci build smoke release tag version docs docs-serve docs-build

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

## ---------- setup ----------

install: ## Sync dependencies with all extras (auth, email, upload)
	uv sync --all-extras

sync: install ## Alias for `install`

## ---------- code quality ----------

# The suite runs across every core (pytest-xdist, from the `[tests]` extra)
# and without coverage. Measured on 2026-10-09, 12 cores: 12 262 tests in
# 4m44s with `-n 12 --no-cov`, against ~36 min serial under coverage. The
# coverage report lives in `test-cov`. Tests run only here: the repo's CI
# publishes and nothing else.
#
# One BLAS/OpenMP thread per worker: each of the N workers otherwise opens a
# pool the size of the machine (numpy, scipy, sklearn, torch, onnxruntime),
# N x N threads on N cores. Measured on 12 cores, two runs each: 2m52s and
# 2m50s with the cap, against 3m15s-3m38s without. `worksteal` hands a slow
# worker's queue to idle ones, so the 20-40 s tests (mypy runs, docs guards)
# stop leaving cores idle at the end. `-n logical`, not `-n auto`: with psutil
# installed `auto` counts physical cores (6 of 12 here) -- 4m11s against
# 2m14s on the same code.
PYTEST_PARALLEL := OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
	uv run pytest -n logical --dist worksteal -p no:cacheprovider

test: ## Run pytest in parallel, without coverage
	$(PYTEST_PARALLEL)

test-cov: ## Run pytest in parallel with the coverage report
	$(PYTEST_PARALLEL) --cov=$(PACKAGE) --cov-report=term-missing

# The Python matrix CI used to run. Each version gets its own venv outside
# the repository (an in-repo `.venv-3.x` would ship in the sdist), synced
# from the committed lock with every extra.
MATRIX_PYTHONS := 3.11 3.12 3.13
MATRIX_VENVS := $(HOME)/.cache/tempest-fastapi-sdk/venvs

.PHONY: test-matrix
test-matrix: ## Run the suite on every supported Python (3.11, 3.12, 3.13)
	@for v in $(MATRIX_PYTHONS); do \
		echo "== Python $$v"; \
		UV_PROJECT_ENVIRONMENT=$(MATRIX_VENVS)/$$v uv sync --locked --all-extras --python $$v --quiet || exit 1; \
		OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
		UV_PROJECT_ENVIRONMENT=$(MATRIX_VENVS)/$$v uv run --no-sync python -m pytest -n logical --dist worksteal -p no:cacheprovider -q || exit 1; \
	done

test-model: ## Run opt-in model smoke tests (downloads tiny weights on first run)
	uv run pytest -m model

test-gpu: ## Run GPU tests (needs CUDA; skipped without a GPU)
	uv run pytest -m gpu

.PHONY: test-docker
test-docker: ## Run tests that start a real container (needs a docker daemon)
	uv run pytest -m docker

.PHONY: test-network
test-network: ## Run tests that reach a real third-party endpoint (needs internet)
	uv run pytest -m network

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

## ---------- supply chain ----------

# The audit reads the *committed* resolution: `uv export --locked` refuses a
# lock that no longer satisfies pyproject.toml instead of re-resolving, and
# every extra is exported because the SDK ships them all. `--disable-pip
# --no-deps` audits the pinned list as-is (no pip resolve), which is what
# keeps a warm run around one second; a cold run measured ~22 s.
# `--strict` fails when a package cannot be audited at all, so a lookup
# failure never reads as "no advisories".
#
# Every ignored ID below is a decision, not a silence. Revisit each one when
# its blocker moves:
#
# chromadb 1.5.9 (newest on PyPI on 2026-10-09; no fixed version exists).
#   All four target the Chroma *server* -- its HTTP collection endpoints and
#   SimpleRBACAuthorizationProvider. The SDK only opens the embedded
#   PersistentClient / EphemeralClient (genai/rag/chroma.py) and never
#   starts or exposes that server.
#   PYSEC-2026-311, PYSEC-2026-3813, PYSEC-2026-3814, PYSEC-2026-3815
#
# transformers 4.57.6, held below 5 by the `transformers<5` bound of
#   [genai-audio] (coqui-tts 0.27.5 still dies importing transformers 5.x;
#   see the comment on that bound in pyproject.toml). Every fixed version is
#   a 5.x, and two of the IDs list no fixed version at all. The consumer that
#   installs [genai] without [genai-audio] resolves 5.x and is not affected
#   by the lock. Drop these when the coqui-tts bound goes.
#   PYSEC-2025-217, PYSEC-2026-2288, PYSEC-2026-2289, PYSEC-2026-2290,
#   PYSEC-2026-3929, PYSEC-2026-4174
PIP_AUDIT_VERSION := 2.10.1
AUDIT_IGNORE := \
	PYSEC-2026-311 PYSEC-2026-3813 PYSEC-2026-3814 PYSEC-2026-3815 \
	PYSEC-2025-217 PYSEC-2026-2288 PYSEC-2026-2289 PYSEC-2026-2290 \
	PYSEC-2026-3929 PYSEC-2026-4174
AUDIT_ARGS ?=

.PHONY: audit
audit: ## Fail on any known advisory in the locked resolution (all extras, pip-audit)
	@req=$$(mktemp) && \
		uv export --locked --quiet --all-extras --no-dev --no-hashes --no-emit-project \
			--format requirements-txt -o "$$req" && \
		uvx pip-audit@$(PIP_AUDIT_VERSION) -r "$$req" --disable-pip --no-deps --strict \
			--progress-spinner off $(foreach id,$(AUDIT_IGNORE),--ignore-vuln $(id)) $(AUDIT_ARGS); \
		status=$$?; rm -f "$$req"; exit $$status

## ---------- packaging ----------

build: ## Build sdist + wheel into dist/
	rm -rf dist
	uv build

openpix-regen: ## Regenerate the vendored OpenPix schemas + client from vendor/openpix-openapi.json
	uv run python scripts/regen_openpix.py

openpix-fetch: ## Refresh vendor/openpix-openapi.json from Woovi's published spec (network)
	uv run python scripts/regen_openpix.py --fetch

openpix-diff: ## Report the distance between the vendored spec and the published one (network)
	uv run python scripts/openpix_diff.py

zap-regen: ## Regenerate the zap-api schemas + client from vendor/zap-openapi.yaml
	uv run python scripts/regen_zap.py

zap-fetch: ## Refresh vendor/zap-openapi.yaml from a running gateway (ZAP_OPENAPI_URL, default 127.0.0.1:3000)
	uv run python scripts/regen_zap.py --fetch

zap-ws-regen: ## Regenerate the zap-api WebSocket client from vendor/zap-asyncapi.yaml
	uv run python scripts/regen_zap_ws.py

zap-ws-fetch: ## Refresh vendor/zap-asyncapi.yaml from a running gateway (ZAP_ASYNCAPI_URL, default 127.0.0.1:3000)
	uv run python scripts/regen_zap_ws.py --fetch

mercadopago-regen: ## Regenerate the vendored Mercado Pago schemas + client from vendor/mercadopago-openapi.yaml
	uv run python scripts/regen_mercado_pago.py

mercadopago-fetch: ## Refresh vendor/mercadopago-openapi.yaml from the provider's spec repository (network)
	uv run python scripts/regen_mercado_pago.py --fetch

mercadopago-diff: ## Validate the vendored Mercado Pago spec against the provider's official SDK (network)
	uv run python scripts/mercadopago_diff.py

stripe-regen: ## Regenerate Stripe's event enum from vendor/stripe-api-facts.yaml (offline)
	uv run python scripts/regen_stripe.py

stripe-fetch: ## Refresh vendor/stripe-api-facts.yaml from Stripe's published spec (network)
	uv run python scripts/regen_stripe.py --fetch

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
	/tmp/$(PACKAGE)-smoke/bin/python -c "from $(PACKAGE).ssr import Page, html_response, make_htmx_router, make_web_app_router, build_web_app, detect_build_mode, htmx, aria, data; \
		assert htmx(post='/x') == {'hx-post': '/x'}, 'htmx builder broken'; \
		print('SSR extra OK ·', Page.__name__, html_response.__name__, make_htmx_router.__name__, make_web_app_router.__name__, build_web_app.__name__, detect_build_mode.__name__, htmx.__name__, aria.__name__, data.__name__)"
	/tmp/$(PACKAGE)-smoke/bin/python -c "from $(PACKAGE).ui import app_stylesheet; \
		from $(PACKAGE).ui.components import Card, DataTable, NavBar, Pagination; \
		from $(PACKAGE).ui.css import Rule, StyleSheet, ThemeTokens, make_css_router; \
		from $(PACKAGE).ui.forms import form_for, form_stylesheet, parse_form; \
		from $(PACKAGE).ui.layout import Grid, Shell; \
		from $(PACKAGE).ui.pages import Page as UiPage; \
		css = StyleSheet(rules=[Rule('.card', declarations={'padding': '16px'})]).to_css(); \
		assert '.card' in css, 'stylesheet did not render'; \
		print('UI layer OK ·', app_stylesheet.__name__, form_for.__name__, parse_form.__name__, UiPage.__name__)"
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

release: ## Bump versions, run every gate, commit and tag. Usage: make release VERSION=0.2.0 SUBJECT="assunto"
	@test -n "$(VERSION)" || (echo 'Usage: make release VERSION=0.2.0 SUBJECT="assunto"'; exit 1)
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "Working tree dirty. Commit or stash first."; exit 1; \
	fi
	@grep -q '^## \[$(VERSION)\]' CHANGELOG.md || \
		(echo "CHANGELOG.md has no '## [$(VERSION)]' entry. Write it before releasing."; exit 1)
	@echo "Bumping pyproject.toml and $(PACKAGE)/__init__.py to $(VERSION)"
	sed -i -E 's/^version = "[^"]+"/version = "$(VERSION)"/' pyproject.toml
	sed -i -E 's/^__version__: str = "[^"]+"/__version__: str = "$(VERSION)"/' $(PACKAGE)/__init__.py
# The sed above invalidates uv.lock, which records this package's own
# version. `make check` refreshes it as a side effect -- `uv run` re-locks
# before it runs anything, measured -- but it was never staged, so every
# tagged commit shipped a lock one version behind its pyproject: v0.236.0,
# v0.237.0 and v0.238.0 all drifted. Staging it is the whole fix.
#
# No guard covers this, deliberately: any test asserting the two agree runs
# under `uv run`, which repairs the lock on disk before the test can read
# it, so the guard could never fail. Measured by corrupting the lock and
# watching a bare `uv run python -c pass` put it back.
#
# `make check` here is the only place the suite runs before a tag: the
# release workflow publishes and does not test (tests/test_release_flow_guard.py).
	$(MAKE) check
	$(MAKE) audit
	$(MAKE) docs-build
	$(MAKE) smoke
	git add pyproject.toml $(PACKAGE)/__init__.py uv.lock
	@if [ -n "$(SUBJECT)" ]; then \
		git commit -m "feat: v$(VERSION) — $(SUBJECT)"; \
	else \
		echo "No SUBJECT given; using the generic message."; \
		git commit -m "chore: release v$(VERSION)"; \
	fi
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
