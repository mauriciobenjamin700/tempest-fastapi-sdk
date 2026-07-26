# Contribuindo

!!! tip "A contribuição mais útil é uma issue — não um PR"
    Este SDK é uma **superfície pública versionada**: cada release publica no PyPI e cada símbolo novo carrega docstring, docs bilíngues e entrada na referência. Por isso o fluxo aqui é **issue primeiro**: você descreve o problema (ou a ideia), a gente combina o escopo e o formato, e só então alguém escreve código. Um PR que chega antes desse alinhamento quase sempre precisa ser refeito.

    **[Abrir uma issue »](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new/choose)**

## Abra uma issue

| Necessidade | Onde |
| --- | --- |
| Bug (algo não funciona como documentado) | [Issue: bug report](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=bug_report.yml) |
| Feature / ideia de API | [Issue: feature request](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=feature_request.yml) |
| Doc confusa, incompleta, exemplo que não roda, typo | [Issue: docs](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=docs.yml) |
| Dúvida de uso ("como faço X com o SDK?") | [Issue: dúvida](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/new?template=question.yml) |
| Divulgação de segurança | mauricio.benjamin@reloverelations.com (privado, **nunca** em issue pública) |

O que faz uma issue ser resolvida rápido:

- **versão do SDK** (`python -c "import tempest_fastapi_sdk as t; print(t.__version__)"`) e do Python;
- **snippet mínimo** que reproduz — completo, com imports, sem `...`;
- **o que você esperava** e **o que aconteceu** (traceback inteiro, não a última linha);
- para feature: **o caso de uso**, não a solução já desenhada. O problema costuma ter uma resposta melhor com as peças que o SDK já tem.

!!! note "Typo na doc também é issue"
    Antes era "manda PR direto". Não mais: cada página existe **duas vezes** (`docs/<página>.md` em PT-BR e `docs/<página>.en.md` em EN-US) e um PR que corrige só um lado deixa o site inconsistente. Abra a issue apontando a página e o trecho — a correção sai nas duas de uma vez.

## Por que issue antes de código

Três restrições deste repositório que não aparecem no diff:

1. **Versionamento e compatibilidade.** Todo símbolo público entra em `__all__`, na referência renderizada e no contrato SemVer. Renomear ou mudar assinatura depois é breaking change com guia de migração.
2. **Docs no mesmo commit.** Mudança de superfície pública sem README, `CHANGELOG.md`, receita bilíngue e stub de referência atualizados não passa — é regra do projeto, não preferência do revisor.
3. **Release por feature.** Cada fatia sai como sua própria versão (bump em `pyproject.toml`, `__version__` e `uv.lock`, gates completos, tag). Quem conduz esse ciclo é o mantenedor.

Nada disso impede sua contribuição — só significa que **combinar o escopo na issue é mais rápido** do que descobrir na revisão do PR.

## Quero implementar

Ótimo — diga isso **na issue** e espere o "vai". Aí:

- trabalhe num branch `feat/<slug>` / `fix/<slug>` a partir de `main`;
- rode `make check` (lint + formato + mypy + testes) — o CI roda o mesmo alvo em 3.11 / 3.12 / 3.13;
- atualize a doc PT **e** EN junto com o código, mais `CHANGELOG.md`;
- **não** faça bump de versão nem crie tag — isso é do release;
- um PR por assunto, com o corpo explicando o problema antes da solução.

PR sem issue aceita costuma ser fechado com um pedido pra abrir a issue — não é rejeição do trabalho, é a ordem que mantém a doc e o release coerentes.

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

Toda página vive em duas línguas (`docs/<página>.md` + `docs/<página>.en.md`) e o build roda com `--strict` — warning é erro. As edições caem no site do Pages no push para `main` via [`.github/workflows/docs.yml`](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/blob/main/.github/workflows/docs.yml).

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

## Release (mantenedor)

`make release VERSION=X.Y.Z` faz o bump nas localizações da versão, roda todos os gates, cria o commit + tag e diz o que você deve dar push:

```bash
make release VERSION=0.20.0
git push origin main
git push origin v0.20.0
```

O workflow de publicação no PyPI dispara no push da tag `vX.Y.Z` (publicação confiável — sem token de API no repositório).
