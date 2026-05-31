# Contribuindo {#contributing}

## Ambiente de desenvolvimento {#development-environment}

```bash
# Clone + sync every extra and the dev/docs groups
git clone https://github.com/mauriciobenjamin700/tempest-fastapi-sdk.git
cd tempest-fastapi-sdk
uv sync --all-extras --group dev --group docs
```

!!! tip "Verificação rápida"
    `make check` roda o quality gate completo (lint + verificação de formatação + mypy + pytest). A CI roda o mesmo target em cada push, então um `make check` verde localmente significa um PR verde.

## Quality gates {#quality-gates}

| Comando | O que faz |
| --- | --- |
| `make lint` | `ruff check .` (sem auto-fix) |
| `make fix` | `ruff check --fix .` + `ruff format .` (escreve) |
| `make fmt` | `ruff format .` (escreve) |
| `make fmt-check` | `ruff format --check .` (somente leitura) |
| `make type` | `mypy tempest_fastapi_sdk` (strict) |
| `make test` | `pytest` com cobertura |
| `make check` | `lint + fmt-check + type + test` (para na primeira falha) |
| `make ci` | `check + build + smoke` (espelho completo da CI) |

Os mesmos gates estão disponíveis via a CLI incluída: `tempest lint` / `tempest fix` / `tempest check` também funcionam em qualquer projeto consumidor.

## Testes {#tests}

```bash
make test                    # full suite + coverage
uv run pytest tests/admin    # just the admin module
uv run pytest -k cursor      # tests matching "cursor"
uv run pytest -x             # stop at first failure
```

A suíte usa SQLite em memória via `tempest_fastapi_sdk.testing.test_session`. Os testes de repositório compartilham a fixture `session` de `tests/conftest.py`.

## Docs {#docs}

```bash
make docs-serve              # mkdocs serve — live reload at http://127.0.0.1:8000
make docs-build              # build the static site into ./site/
```

As edições chegam ao site do Pages publicado no push para a `main` via [`.github/workflows/docs.yml`](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/blob/main/.github/workflows/docs.yml).

## Estilo das mensagens de commit {#commit-message-style}

Prefixos de Conventional Commits:

- `feat:` — nova capacidade voltada ao usuário
- `fix:` — correção de bug
- `refactor:` — reestruturação interna sem mudança de comportamento
- `docs:` — somente documentação
- `style:` — formatação / espaços em branco
- `tests:` — mudanças somente em testes
- `chore:` — tooling, deps, encanamento de release

Adicione `!` após o prefixo para mudanças breaking (`feat!: drop class-attr config`). Marque na mensagem a versão que entrega a mudança.

## Release {#release}

`make release VERSION=X.Y.Z` atualiza ambos os locais de versão, roda todos os gates, cria o commit + a tag e diz o que você deve dar push:

```bash
make release VERSION=0.20.0
git push origin main
git push origin v0.20.0
```

O workflow de publicação no PyPI dispara no push da tag `vX.Y.Z` (trusted publishing — sem API token no repositório).

## Onde registrar as coisas {#where-to-file-things}

| Necessidade | Canal |
| --- | --- |
| Reporte de bug / pedido de feature | [GitHub Issues](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues) |
| Divulgação de segurança | mauricio.benjamin@reloverelations.com (privado) |
| Erro de digitação na doc | PR direto contra `docs/<page>.md` |
