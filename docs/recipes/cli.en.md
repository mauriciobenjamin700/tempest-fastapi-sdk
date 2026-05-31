# CLI


Installing `tempest-fastapi-sdk` exposes a `tempest` console script. It does two jobs: bootstrap a new layered service from the SDK's preferred skeleton, and run the four quality gates (`ruff check`, `ruff format`, `mypy`, `pytest`) without copy-pasting the same commands into every project.

```bash
tempest --help                                  # list every command
tempest --version                               # show the SDK version
```

#### Scaffold a new service

```bash
tempest new my_service                          # scaffold under ./my_service
tempest new my_service --path ~/projects        # custom parent dir
tempest new my_service \
    --bind-host 0.0.0.0 \                       # default HOST in .env.example
    --bind-port 9090 \                          # default PORT in .env.example
    --extras auth,upload                        # pinned SDK extras
tempest new my_service --force                  # overwrite existing dir
```

The skeleton matches the layered architecture documented in this README:

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # pins tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # HOST/PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
├── .gitignore
├── README.md
├── src/
│   ├── server.py            # uvicorn.run() + module-level FastAPI app
│   ├── api/
│   │   ├── app.py           # create_app() wires SDK middleware + handlers
│   │   ├── routers/         # placeholder business router
│   │   └── dependencies/    # auth.py (require_token) + factories
│   ├── controllers/         # orchestration between services
│   ├── services/            # business logic
│   ├── schemas/             # Pydantic DTOs
│   ├── core/                # settings.py + exceptions.py
│   ├── db/
│   │   ├── models/
│   │   └── repositories/
│   └── utils/
└── tests/
    └── test_smoke.py        # asserts /api/ and /health/liveness boot
```

The generated `pyproject.toml` pins the current SDK version (`tempest-fastapi-sdk[auth]>=<version>` by default — change with `--extras`). The scaffolded `.env.example` uses the v0.8.0 settings naming (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), and `src/server.py` delegates to `tempest_fastapi_sdk.run_server` so uvicorn is imported lazily and tests can import the app without it. Validation rules: the project name must match `^[a-z][a-z0-9_]*$` and cannot collide with a Python keyword, so `tempest new Bad-Name` and `tempest new class` exit with code 2 before any file is written.

After scaffolding:

```bash
cd my_service
uv sync                                         # installs SDK + dev tools
cp .env.example .env
uv run python main.py                           # serves on the configured HOST:PORT
uv run pytest                                   # the bundled smoke test
```

#### Quality gates

The lint commands shell out to the project's tooling. They look for the executable on `PATH` first, and otherwise fall back to `uv run <tool>` so a project-local virtualenv works without manual activation.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix . + ruff format .   (writes)
tempest fix --unsafe                            # also apply ruff's --unsafe-fixes
tempest format                                  # ruff format .          (writes)
tempest fmt-check                               # ruff format --check .   (read-only)
tempest type                                    # mypy .
tempest test                                    # pytest
tempest test tests/api/                         # pytest with a path filter
tempest check                                   # lint + fmt-check + type + test, stops at first failure
```

`tempest fix` is the one-shot "organize the project" pass — sorts and dedupes imports, drops unused imports, normalizes string quotes, removes trailing whitespace, then runs `ruff format` to align indentation, line length, blank lines and trailing newlines. Run it before pushing when CI keeps catching style nits.

Every command returns the underlying tool's exit code, so `tempest check` is safe to wire into CI (`tempest check || exit 1`) or pre-commit hooks. When neither the executable nor `uv` is on `PATH`, the wrapper prints `error: '<tool>' is not on PATH and 'uv' is unavailable` and exits with `127` instead of failing silently.

