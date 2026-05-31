# Contribuindo

## Ambiente de desenvolvimento

```bash
# Clone + sincronize todos os extras e os grupos dev/docs
git clone https://github.com/mauriciobenjamin700/tempest-fastapi-sdk.git
cd tempest-fastapi-sdk
uv sync --all-extras --group dev --group docs
```

!!! tip "Verificação rápida"
    `make check` roda o gate de qualidade completo (lint + checagem de formato + mypy + pytest). O CI roda o mesmo alvo em cada push, então um `make check` verde localmente significa um PR verde.

## Gates de qualidade

| Comando | O que faz |
| --- | --- |
| `make lint` | `ruff check .` (sem auto-fix) |
| `make fix` | `ruff check --fix .` + `ruff format .` (escreve) |
| `make fmt` | `ruff format .` (escreve) |
| `make fmt-check` | `ruff format --check .` (somente leitura) |
| `make type` | `mypy tempest_fastapi_sdk` (strict) |
| `make test` | `pytest` com cobertura |
| `make check` | `lint + fmt-check + type + test` (para no primeiro erro) |
| `make ci` | `check + build + smoke` (espelho completo do CI) |

Os mesmos gates estão disponíveis pela CLI embutida: `tempest lint` / `tempest fix` / `tempest check` funcionam em qualquer projeto consumidor também.

## Testes

```bash
make test                    # suite completa + cobertura
uv run pytest tests/admin    # só o módulo admin
uv run pytest -k cursor      # testes que casam com "cursor"
uv run pytest -x             # para no primeiro erro
```

A suite usa SQLite em memória via `tempest_fastapi_sdk.testing.test_session`. Os testes de repository compartilham a fixture `session` de `tests/conftest.py`.

## Docs

```bash
make docs-serve              # mkdocs serve — live reload em http://127.0.0.1:8000
make docs-build              # build do site estático em ./site/
```

As edições caem no site do Pages no push para `main` via [`.github/workflows/docs.yml`](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/blob/main/.github/workflows/docs.yml).

## Estilo das mensagens de commit

Prefixos de Conventional Commits:

- `feat:` — nova capacidade voltada ao usuário
- `fix:` — correção de bug
- `refactor:` — reestruturação interna sem mudança de comportamento
- `docs:` — só documentação
- `style:` — formatação / espaços
- `tests:` — só mudanças de teste
- `chore:` — tooling, deps, encanamento de release

Adicione `!` após o prefixo para mudanças que quebram compatibilidade (`feat!: drop class-attr config`). Marque na mensagem a versão que entrega a mudança.

## Release

`make release VERSION=X.Y.Z` faz o bump nas duas localizações da versão, roda todos os gates, cria o commit + tag e diz o que você deve dar push:

```bash
make release VERSION=0.20.0
git push origin main
git push origin v0.20.0
```

O workflow de publicação no PyPI dispara no push da tag `vX.Y.Z` (publicação confiável — sem token de API no repositório).

## Onde reportar cada coisa

| Necessidade | Canal |
| --- | --- |
| Bug / pedido de feature | [GitHub Issues](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues) |
| Divulgação de segurança | mauricio.benjamin@reloverelations.com (privado) |
| Typo na doc | PR direto contra `docs/<página>.md` |
