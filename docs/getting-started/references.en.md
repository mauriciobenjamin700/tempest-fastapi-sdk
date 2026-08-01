# Official reference docs

When you get stuck, the answer is almost always in the tool's official documentation — not in a blog post from 2019. This page is the curated index of those sources, grouped by what the SDK actually uses.

!!! tip "How to read official docs without getting lost"
    1. **Start with the tutorial**, not the API reference. A tutorial teaches the happy path; the reference answers pinpoint questions.
    2. **Check the version** at the top of the page. SQLAlchemy 1.4 and 2.0 are nearly different projects; so are Pydantic v1 and v2.
    3. **Run the example before adapting it.** If the original example does not run, the problem is the environment, not your code.
    4. **Use the site's own search**, which indexes the whole content — it usually beats a general-purpose search engine.

## Language and tooling

| Resource | Link |
| --- | --- |
| Python — documentation | <https://docs.python.org/3/> |
| Python — official tutorial | <https://docs.python.org/3/tutorial/> |
| Python — standard library | <https://docs.python.org/3/library/index.html> |
| Python — `asyncio` | <https://docs.python.org/3/library/asyncio.html> |
| Python — `typing` | <https://docs.python.org/3/library/typing.html> |
| Python — release cycle | <https://devguide.python.org/versions/> |
| PEP 8 — code style | <https://peps.python.org/pep-0008/> |
| PEP 484 — type hints | <https://peps.python.org/pep-0484/> |
| PEP 621 — `pyproject.toml` metadata | <https://peps.python.org/pep-0621/> |
| Python Packaging User Guide | <https://packaging.python.org/en/latest/> |
| `uv` — project manager | <https://docs.astral.sh/uv/> |
| Git — documentation | <https://git-scm.com/doc> |
| Conventional Commits | <https://www.conventionalcommits.org/en/v1.0.0/> |

## Web and API

| Resource | Link |
| --- | --- |
| FastAPI | <https://fastapi.tiangolo.com> |
| FastAPI — tutorial | <https://fastapi.tiangolo.com/tutorial/> |
| Starlette (FastAPI's foundation) | <https://www.starlette.io/> |
| Uvicorn (ASGI server) | <https://www.uvicorn.org/> |
| Pydantic v2 | <https://docs.pydantic.dev/latest/> |
| pydantic-settings | <https://docs.pydantic.dev/latest/concepts/pydantic_settings/> |
| OpenAPI specification | <https://spec.openapis.org/oas/latest.html> |
| httpx (HTTP client) | <https://www.python-httpx.org/> |
| MDN — HTTP | <https://developer.mozilla.org/en-US/docs/Web/HTTP> |
| MDN — Server-Sent Events | <https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events> |
| MDN — Push API | <https://developer.mozilla.org/en-US/docs/Web/API/Push_API> |
| RFC 7519 — JSON Web Token | <https://datatracker.ietf.org/doc/html/rfc7519> |

## Databases

| Resource | Link |
| --- | --- |
| SQLAlchemy 2.0 | <https://docs.sqlalchemy.org/en/20/> |
| SQLAlchemy — asyncio extension | <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html> |
| Alembic (migrations) | <https://alembic.sqlalchemy.org/en/latest/> |
| PostgreSQL | <https://www.postgresql.org/docs/current/> |
| asyncpg (async driver) | <https://magicstack.github.io/asyncpg/current/> |
| SQLite | <https://www.sqlite.org/docs.html> |
| aiosqlite (async driver) | <https://aiosqlite.omnilib.dev/en/stable/> |
| pgvector (embeddings in Postgres) | <https://github.com/pgvector/pgvector> |

## Infrastructure

| Resource | Link |
| --- | --- |
| Redis | <https://redis.io/docs/latest/> |
| redis-py (Python client) | <https://redis.readthedocs.io/en/stable/> |
| RabbitMQ | <https://www.rabbitmq.com/docs> |
| FastStream (consumers/publishers) | <https://faststream.ag2.ai/latest/> |
| TaskIQ (background tasks) | <https://taskiq-python.github.io/> |
| MinIO (S3 object storage) | <https://min.io/docs/minio/linux/index.html> |
| Docker | <https://docs.docker.com/> |
| Docker Compose | <https://docs.docker.com/compose/> |
| Prometheus | <https://prometheus.io/docs/> |
| OpenTelemetry for Python | <https://opentelemetry.io/docs/languages/python/> |

## Code quality

| Resource | Link |
| --- | --- |
| pytest | <https://docs.pytest.org/en/stable/> |
| pytest-asyncio | <https://pytest-asyncio.readthedocs.io/en/latest/> |
| Ruff (lint + format) | <https://docs.astral.sh/ruff/> |
| mypy (type-checking) | <https://mypy.readthedocs.io/en/stable/> |
| coverage.py | <https://coverage.readthedocs.io/en/latest/> |

## Interface and documentation

| Resource | Link |
| --- | --- |
| HTMX | <https://htmx.org/docs/> |
| Jinja2 (templates) | <https://jinja.palletsprojects.com/en/stable/> |
| MkDocs | <https://www.mkdocs.org/> |
| Material for MkDocs | <https://squidfunk.github.io/mkdocs-material/> |
| React | <https://react.dev/> |
| Vite | <https://vite.dev/> |

## This SDK and its neighbours

| Resource | Link |
| --- | --- |
| `tempest-fastapi-sdk` on PyPI | <https://pypi.org/project/tempest-fastapi-sdk/> |
| Source code on GitHub | <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk> |
| `ort-vision-sdk` (computer vision) | <https://pypi.org/project/ort-vision-sdk/> |
| This documentation, indexed for LLMs | <https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/llms.txt> |

!!! note "The `llms.txt` file"
    The site publishes the whole documentation as plain text following the [llmstxt.org](https://llmstxt.org) convention: `llms.txt` carries the annotated index and `llms-full.txt` carries every page in a single block. Paste the URL into an AI assistant so it answers about the SDK with the current docs in hand.

## Recap

- Official docs first; blog posts later, and only as a complement.
- Always check the doc's version against the version you installed.
- The sources above cover the entire stack the SDK touches.

Track complete. Continue with **[Architecture »](../architecture.md)** or **[Tutorial »](../tutorial.md)**.
