# Documentação oficial de referência

Quando você trava numa dúvida, a resposta quase sempre está na documentação oficial da ferramenta — não num blog de 2019. Esta página é o índice curado dessas fontes, agrupado pelo que o SDK usa de fato.

!!! tip "Como ler documentação oficial sem se perder"
    1. **Comece pelo tutorial**, não pela referência de API. Tutorial ensina o caminho feliz; referência responde perguntas pontuais.
    2. **Confira a versão** no topo da página. SQLAlchemy 1.4 e 2.0 são projetos quase diferentes; Pydantic v1 e v2 também.
    3. **Rode o exemplo antes de adaptar.** Se o exemplo original não roda, o problema é ambiente, não código seu.
    4. **Use a busca do site**, que indexa o conteúdo inteiro — costuma achar melhor que buscador genérico.

## Linguagem e ferramentas

| Recurso | Link |
| --- | --- |
| Python — documentação | <https://docs.python.org/3/> |
| Python — tutorial oficial | <https://docs.python.org/3/tutorial/> |
| Python — biblioteca padrão | <https://docs.python.org/3/library/index.html> |
| Python — `asyncio` | <https://docs.python.org/3/library/asyncio.html> |
| Python — `typing` | <https://docs.python.org/3/library/typing.html> |
| Python — calendário de versões | <https://devguide.python.org/versions/> |
| PEP 8 — estilo de código | <https://peps.python.org/pep-0008/> |
| PEP 484 — type hints | <https://peps.python.org/pep-0484/> |
| PEP 621 — metadados no `pyproject.toml` | <https://peps.python.org/pep-0621/> |
| Guia de empacotamento Python | <https://packaging.python.org/en/latest/> |
| `uv` — gerenciador de projetos | <https://docs.astral.sh/uv/> |
| Git — documentação | <https://git-scm.com/doc> |
| Conventional Commits (PT-BR) | <https://www.conventionalcommits.org/pt-br/v1.0.0/> |

## Web e API

| Recurso | Link |
| --- | --- |
| FastAPI | <https://fastapi.tiangolo.com> |
| FastAPI — tutorial | <https://fastapi.tiangolo.com/tutorial/> |
| Starlette (base do FastAPI) | <https://www.starlette.io/> |
| Uvicorn (servidor ASGI) | <https://www.uvicorn.org/> |
| Pydantic v2 | <https://docs.pydantic.dev/latest/> |
| pydantic-settings | <https://docs.pydantic.dev/latest/concepts/pydantic_settings/> |
| Especificação OpenAPI | <https://spec.openapis.org/oas/latest.html> |
| httpx (cliente HTTP) | <https://www.python-httpx.org/> |
| MDN — HTTP | <https://developer.mozilla.org/en-US/docs/Web/HTTP> |
| MDN — Server-Sent Events | <https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events> |
| MDN — Push API | <https://developer.mozilla.org/en-US/docs/Web/API/Push_API> |
| RFC 7519 — JSON Web Token | <https://datatracker.ietf.org/doc/html/rfc7519> |

## Banco de dados

| Recurso | Link |
| --- | --- |
| SQLAlchemy 2.0 | <https://docs.sqlalchemy.org/en/20/> |
| SQLAlchemy — extensão asyncio | <https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html> |
| Alembic (migrations) | <https://alembic.sqlalchemy.org/en/latest/> |
| PostgreSQL | <https://www.postgresql.org/docs/current/> |
| asyncpg (driver async) | <https://magicstack.github.io/asyncpg/current/> |
| SQLite | <https://www.sqlite.org/docs.html> |
| aiosqlite (driver async) | <https://aiosqlite.omnilib.dev/en/stable/> |
| pgvector (embeddings no Postgres) | <https://github.com/pgvector/pgvector> |

## Infraestrutura

| Recurso | Link |
| --- | --- |
| Redis | <https://redis.io/docs/latest/> |
| redis-py (cliente Python) | <https://redis.readthedocs.io/en/stable/> |
| RabbitMQ | <https://www.rabbitmq.com/docs> |
| FastStream (consumers/publishers) | <https://faststream.ag2.ai/latest/> |
| TaskIQ (tarefas em background) | <https://taskiq-python.github.io/> |
| MinIO (object storage S3) | <https://min.io/docs/minio/linux/index.html> |
| Docker | <https://docs.docker.com/> |
| Docker Compose | <https://docs.docker.com/compose/> |
| Prometheus | <https://prometheus.io/docs/> |
| OpenTelemetry para Python | <https://opentelemetry.io/docs/languages/python/> |

## Qualidade de código

| Recurso | Link |
| --- | --- |
| pytest | <https://docs.pytest.org/en/stable/> |
| pytest-asyncio | <https://pytest-asyncio.readthedocs.io/en/latest/> |
| Ruff (lint + format) | <https://docs.astral.sh/ruff/> |
| mypy (type-checking) | <https://mypy.readthedocs.io/en/stable/> |
| coverage.py | <https://coverage.readthedocs.io/en/latest/> |

## Interface e documentação

| Recurso | Link |
| --- | --- |
| HTMX | <https://htmx.org/docs/> |
| Jinja2 (templates) | <https://jinja.palletsprojects.com/en/stable/> |
| MkDocs | <https://www.mkdocs.org/> |
| Material for MkDocs | <https://squidfunk.github.io/mkdocs-material/> |
| React | <https://react.dev/> |
| Vite | <https://vite.dev/> |

## Este SDK e vizinhos

| Recurso | Link |
| --- | --- |
| `tempest-fastapi-sdk` no PyPI | <https://pypi.org/project/tempest-fastapi-sdk/> |
| Código-fonte no GitHub | <https://github.com/mauriciobenjamin700/tempest-fastapi-sdk> |
| `ort-vision-sdk` (visão computacional) | <https://pypi.org/project/ort-vision-sdk/> |
| Índice desta documentação para LLMs | <https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/llms.txt> |

!!! note "O arquivo `llms.txt`"
    O site publica a documentação inteira em texto puro seguindo a convenção [llmstxt.org](https://llmstxt.org): `llms.txt` traz o índice comentado e `llms-full.txt` traz todo o conteúdo num bloco só. Cole a URL num assistente de IA para ele responder sobre o SDK com a doc atual em mãos.

## Recapitulando

- Documentação oficial primeiro; blog depois, e só para complementar.
- Confira sempre a versão da doc contra a versão que você instalou.
- As fontes acima cobrem todo o stack que o SDK toca.

Trilha concluída. Continue por **[Arquitetura »](../architecture.md)** ou **[Tutorial »](../tutorial.md)**.
