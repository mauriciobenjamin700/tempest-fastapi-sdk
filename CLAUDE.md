# CLAUDE.md — tempest-fastapi-sdk

Project-specific guidance for Claude Code working in this repository.
The global instructions at `~/.claude/CLAUDE.md` apply too — this file
only documents what is *different* or *load-bearing* for this SDK.

## What this is

`tempest-fastapi-sdk` is a **PyPI-distributed library**, not a
deployable service. It ships the shared FastAPI/SQLAlchemy/Pydantic
building blocks every Tempest service imports.

Two structural consequences:

- **Flat layout.** The package directory `tempest_fastapi_sdk/` lives
  at the repo root, next to `pyproject.toml`. **No `src/` wrapper.**
  Tests live in `tests/` at the root. This contradicts the
  service-layout rule in the global `CLAUDE.md` on purpose — detecting
  a `src/tempest_fastapi_sdk/` directory is a defect, flag it before
  adding features.
- **Every public surface change ships docs in the same commit.**
  README install snippets, `CHANGELOG.md`, the MkDocs site under
  `docs/` (bilingual PT-BR + EN-US), and the API reference must all
  reflect the new shape **before** the `vX.Y.Z` tag is pushed. See
  the "Documentation must follow the code" section in the global
  `CLAUDE.md`.

## Release flow

```bash
# 1. bump version
sed -i 's/version = "X.Y.Z"/version = "X.Y.Z+1"/' pyproject.toml
sed -i 's/__version__: str = "X.Y.Z"/__version__: str = "X.Y.Z+1"/' tempest_fastapi_sdk/__init__.py

# 2. CHANGELOG entry under ## [X.Y.Z+1] — YYYY-MM-DD (Keep a Changelog format)

# 3. update relevant docs/recipes/*.md (and the .en.md mirror)

# 4. gate
UV_PYTHON=3.11 make check                 # ruff + mypy + 661+ tests
UV_PYTHON=3.11 uv run --group docs mkdocs build --strict
UV_PYTHON=3.11 make smoke                 # import-test the wheel

# 5. commit + tag + push
git add -A && git commit -m "feat: vX.Y.Z+1 — <subject>"
git tag vX.Y.Z+1
git push origin main && git push origin vX.Y.Z+1
```

CI on tag push runs `release-pypi.yml` (trusted-publishing — no
token), then `docs.yml` redeploys GitHub Pages. Don't push a tag
without the docs being green.

**Docs-only change skips all of this.** Touched only `docs/`,
`README.md` or `CLAUDE.md` prose (no `tempest_fastapi_sdk/**` delta)?
No version bump, no CHANGELOG entry, no tag — commit `docs: <subject>`
and push straight to `main` (rebase on `origin/main` first if behind).
`docs.yml` triggers on the `main` push and redeploys Pages by itself.
Gate is just `uv run --group docs mkdocs build --strict` +
`pytest tests/test_docs_api_guard.py tests/test_docs_organization.py`;
the full `make check` is unnecessary because no Python changed. See
"Docs-only change" in the global `CLAUDE.md` for the reasoning. A
docstring edit that changes a signature or behavior is **not**
docs-only — that follows the flow above.

## Escopo já entregue — leia antes de planejar

A lista do que o SDK cobre vive em [`SHIPPED.md`](SHIPPED.md), não aqui.
Consulte antes de propor uma feature: boa parte do que parece faltar já
existe, e re-planejar trabalho pronto já aconteceu com os tiers do admin
e com o roadmap de genai.

Ao entregar algo, escreva a entrada nova **no `SHIPPED.md`**, na mesma
PR. Este arquivo só cresce quando uma *regra* muda.


## Regra de organização da documentação

**A documentação fica organizada, ordenada e completa nas duas línguas.**
Isso não é revisão de gosto: é regra do projeto, verificada por
`tests/test_docs_organization.py` (roda dentro do `make check`, logo na
CI). Uma página nova não está pronta enquanto os itens abaixo não valem.

### Ao adicionar (ou renomear) uma página

1. **Duas línguas.** `docs/<página>.md` (PT-BR, default) **e**
   `docs/<página>.en.md` (EN-US). Espelho faltando cai em fallback
   silencioso no site.
2. **Dois navs.** A entrada vai no `nav:` de topo **e** no `nav:` do
   locale `en` (dentro do plugin `i18n` no `mkdocs.yml`). O
   `mkdocs-static-i18n` traduz rótulo mas **não reordena** nav
   compartilhado, por isso existem dois — mexer em um exige mexer no
   outro.
3. **Na posição alfabética**, em cada língua, pelo rótulo visível
   (comparação case- e acento-insensível). Vale para a seção
   `Receitas`/`Recipes` e a subseção `Exemplos completos`/`Complete
   examples`.
4. **Índice da landing.** Receita nova entra na tabela de
   `docs/recipes/index.md` **e** `.en.md`, também em ordem alfabética.
5. **Referência.** Símbolo público novo ganha stub em
   `docs/reference.md` (a `reference.en.md` é página-ponteiro, não
   duplica).
6. **Build limpo.** `uv run --group docs mkdocs build --strict` com zero
   warning.

### O que o guard cobre

- espelho `.en.md` para toda página, e nenhuma `.en.md` órfã;
- toda página do disco alcançável pelo nav da sua língua;
- os dois navs cobrindo o mesmo conjunto de páginas, sem duplicata;
- seções alfabéticas alfabéticas **nas duas línguas**;
- tabelas da landing de receitas ordenadas **e** cobrindo toda receita
  do nav.

### O que fica fora da ordem alfabética, de propósito

Abas de topo (`Início → Instalação → Arquitetura → Tutorial → …`),
páginas de `learning/`, a trilha de `getting-started/` (aninhada sob a
aba `Instalação`: uv → versões do Python → primeiro projeto →
documentação oficial) e o tour na landing de receitas seguem **ordem
didática**. Ordenar essas seria a regressão. Fora do nav, a mesma
disciplina vale para listas que o leitor usa como índice: tabela de
módulos do README, tabela de extras da instalação (com `[all]` por
último, por ser catch-all) e os grupos temáticos de
`docs/reference.md` (com `## Superfície de topo` fixa no início). Dentro
da referência, os blocos `###` de módulo e suas entradas `:::` mantêm o
agrupamento por submódulo — ali o agrupamento é a informação.

## Toda afirmação sobre comportamento é medida, não deduzida

**A regra:** se a documentação, o CHANGELOG ou uma docstring afirma o que
o software *faz*, essa frase precisa ter saído de um comando que rodou.
Não de leitura de código, não de como a biblioteca "deve" se comportar.
Rodou, viu a saída, escreveu.

Isso não é zelo — é o defeito que mais escapa aqui, porque nenhum guard lê
prosa. Os três da v0.218.0, todos deduzidos e todos falsos:

| Escrito | Medido |
| --- | --- |
| "mesma entrada, mesmos bytes, inclusive entre processos" | 3 execuções do mesmo container → **3 hashes**; o subset de fonte grava timestamp na tabela `head` |
| "sem `fonts-dejavu-core` todo glifo vira retângulo" | o pacote chega **transitivamente** com o Pango; o texto sai legível sem pedir |
| "precisa de Pango — o erro aparece no primeiro render" | o erro aparece no primeiro render, mas nomeia **`libgobject-2.0-0`**, que é o que a pessoa vai pesquisar |

O primeiro tinha teste. O teste comparava dois renders **no mesmo
processo**, onde bater é trivial — provava uma propriedade que ninguém
precisa, com a redação de uma que ninguém tinha.

### O que fazer na prática

- **Propriedade que atravessa processo, máquina ou container é testada
  atravessando.** Comparar duas chamadas no mesmo processo não mede nada
  sobre o que sobrevive a ele. `tests/test_vacuous_guard.py` falha quando
  o nome ou a docstring de um teste **afirma** ter cruzado ("across
  processes", "across replicas", "survives a restart") e o corpo não sai
  do lugar. Ele não policia a palavra "determinístico" nem
  "idempotente" — a primeira versão fazia isso e sinalizou 22 testes, uns
  20 deles corretos: idempotência é `f(f(x)) == f(x)`, propriedade do
  mesmo processo, e um dos sinalizados afirmava o **oposto**.
- **Afirmação sobre ambiente é feita no ambiente.** Se a frase fala de
  container, imagem slim, pacote de sistema ou versão de dependência,
  construa e rode. `docker build` custa minutos; a frase errada fica anos.
- **Declare o escopo junto da afirmação.** "Byte a byte idêntico" quase
  nunca é verdade sem qualificação — diga *sob quais condições*, e diga o
  que continua variando (versão de fonte, de biblioteca, de imagem).
- **Ao afirmar um modo de falha, reproduza-o.** Mensagem de erro, código
  de status, o que o usuário vê. Errei o 500-vs-422 do router de PDF
  porque deduzi que o FastAPI converteria um `ValidationError` levantado
  dentro do corpo da rota. Ele não converte.
- **Prosa entra na revisão do diff.** Antes de commitar, releia cada frase
  que afirma comportamento e responda: *qual comando produziu isso?* Sem
  resposta, ou roda, ou reescreve como o que é — uma expectativa.

O objetivo não é escrever menos. É que o que está escrito sobreviva a
alguém testando.

## Regra vale o que o guard vale

Quando uma regra deste arquivo for violável em silêncio, ela ganha teste.
O histórico é consistente: o defeito de `**kwargs` shippou **cinco vezes**
e sobreviveu a uma auditoria manual do próprio arquivo antes de virar
`tests/test_kwargs_guard.py`. A regra de re-export com `as` ficou escrita
aqui por meses e estava violada **769 vezes** em 18 arquivos no dia em que
alguém contou.

Ao adicionar uma regra, decida uma das duas na hora:

1. **Tem guard** — escreva o teste na mesma PR, e faça o teste provar que
   ele **dispara** na forma que de fato shippou. Guard que não pode
   falhar é guard em que ninguém deveria confiar.
2. **Não tem guard** — escreva por quê ("precisa da assinatura do callee
   resolvida", "é julgamento de redação"), para o próximo leitor não achar
   que a checagem existe.

Guards ativos, todos dentro do `make check`: `test_docs_api_guard`,
`test_docs_signature_guard`, `test_docs_organization`,
`test_docs_examples_compile`, `test_docs_examples_names`,
`test_reference_coverage`, `test_kwargs_guard`, `test_reexport_guard`,
`test_vacuous_guard`, `test_alias_guard`.

- **`Field(alias=...)` é defeito (v0.234.0).** Runtime não distingue
  `alias` de `validation_alias`+`serialization_alias` quando
  `populate_by_name=True` está setado; o **type-checker distingue**:
  `alias` renomeia o parâmetro do `__init__` sintetizado, e o pyright
  passa a rejeitar `ChargePayload(correlation_id=...)` exigindo
  `correlationID`. Medido com basedpyright contra a wheel 0.233.0
  publicada — e medido de novo com `validate_by_name`, que também não
  resolve. mypy aceita as duas grafias, por isso shippou. Escreva o nome
  do fio duas vezes (`validation_alias` para ler, `serialization_alias`
  para escrever); `tests/test_alias_guard.py` falha se `alias=` voltar.

## Conventions specific to this repo

- **Typed examples in docs.** Every code block in `README.md`,
  `docs/`, `tempest_fastapi_sdk/cli/_templates/*.tmpl` MUST have full
  type annotations (params + return). User explicitly rejected
  "magic Django-style" untyped APIs.
- **Docs/API guard.** `tests/test_docs_api_guard.py` (runs in `make
  check`) asserts every ```python doc block parses and every
  `__all__` name resolves — it catches broken examples and
  renamed/removed exports the docs still reference. It does **not**
  catch *prose* drift (a covers/roadmap line describing something as
  backlog that's actually shipped, or vice-versa). So on every
  feature/release, **re-read the covers list + any roadmap/next-version
  prose in this file against the shipped code** and fix mismatches in
  the same PR — this drifts easily (it happened for both the admin
  tiers and the genai roadmap). Add `# docs-guard: skip` to a doc block
  only for an intentionally non-parseable fragment.
- **Docs signature guard (v0.170.3).**
  `tests/test_docs_signature_guard.py` (also in `make check`) is the
  layer above: it checks every doc example **against the real
  signatures** — keywords exist, positional arity fits (so
  `f(obj, ..., kw=1)` is caught: the literal `Ellipsis` is an
  argument), `from tempest_fastapi_sdk... import X` resolves, and no
  install snippet requires a version above `pyproject.toml`'s. Symbols
  resolve per block from that block's own imports, so the two
  `RetryPolicy` classes (root/HTTP `max_attempts` vs `.tasks`
  `max_retries`) never collide; a symbol used without an import is not
  checked. **Prose is still unguarded**: a sentence promising a
  parameter that does not exist only fails this suite when an example
  passes it, so re-read the prose you write around a signature.
- **`**kwargs` guard (v0.208.0).** `tests/test_kwargs_guard.py` (also in
  `make check`) walks the package with `ast` and fails when a function
  reads a key out of its **own** `**kwargs`/`**options` — `options.pop("x")`
  makes `x` a real parameter the type checker cannot see, the docstring
  stops describing, and an upstream parameter of that name will one day
  collide with. The fix is always to promote it to a named keyword-only
  parameter, which is source compatible. This shipped **five times** in
  `MessageBroker` and survived a manual audit of that exact file, which is
  why it is a test. It does **not** see the subtler form (splatting
  `**options` into a callable whose named parameters absorb keys — how
  `publisher_for` had it), since that needs the callee's signature
  resolved. The suite also asserts the guard **fires** on the shape that
  actually shipped: a guard that cannot fail is one nobody should trust.
  Mark a line `# kwargs-guard: skip` only for a case that is genuinely not
  this, with a docstring saying why. See "`**kwargs` is for passthrough
  only" in the global `CLAUDE.md`.
- **Regra: a documentação fica organizada e em ordem — e isso é
  testado.** Ver a seção "Regra de organização da documentação" acima;
  `tests/test_docs_organization.py` (roda no `make check`) é a
  autoridade.
- **No emojis in code or docs** unless the user explicitly asks.
- **Bilingual docs.** Every page lives twice: `docs/<page>.md`
  (PT-BR, default) and `docs/<page>.en.md` (EN-US). The MkDocs
  `mkdocs-static-i18n` plugin renders both. Forgetting the `.en.md`
  mirror is a structural defect, not a polish item.
- **Bind defaults: `127.0.0.1`** in CLI-generated templates;
  `0.0.0.0` only when a frontend on a different origin consumes
  the service.
- **Logging tests must pass `file_output=False`** to avoid stray
  `logs/` folders in cwd. The default behavior writes to disk
  (since v0.22.0).
- **Explicit re-exports in every `__init__.py`.** Every public
  symbol that an `__init__.py` re-exports MUST use **both**:

  1. The PEP 484 `from x import Y as Y` form (explicit re-export),
     and
  2. A `__all__: list[str]` listing the same symbol.

  Reason: third-party consumers run a mixed bag of type-checkers
  (mypy, pyright, pylance, basedpyright) on different strictness
  settings and without project-aware `pyrightconfig.json`. Either
  form ALONE is theoretically PEP 484 compliant, but in practice
  basedpyright + Pylance strict still flag `from foo import Bar`
  inside an `__init__.py` as "private import usage" unless the
  symbol is aliased with `as Bar`. Always pair the two so any
  IDE — with or without a project config — accepts
  `from tempest_fastapi_sdk.<module> import Symbol` without a
  diagnostic. Example:

  ```python
  # tempest_fastapi_sdk/foo/__init__.py
  from tempest_fastapi_sdk.foo.bar import Bar as Bar
  from tempest_fastapi_sdk.foo.baz import Baz as Baz

  __all__: list[str] = ["Bar", "Baz"]
  ```

  Plain `from tempest_fastapi_sdk.foo.bar import Bar` (without
  `as Bar`) inside an `__init__.py` is a structural defect — flag
  it before adding features. When adding a new public symbol,
  update **both** the import alias and `__all__` in the same
  patch.

  **A wildcard is not a re-export.** The OpenPix package resolves its
  373 generated names lazily and makes them visible to a type-checker
  with `from ...schemas import *` under `TYPE_CHECKING`. Measured with
  basedpyright against the installed wheel (v0.232.0): a consumer's
  `from ...openpix import ChargePayload` got *"ChargePayload" is not
  exported from module*, with advice to import from the private
  submodule. mypy accepted it, which is why it shipped. The fix is
  `__all__` — generated by `scripts/regen_openpix.py`, pinned by
  `tests/integrations/payment/openpix/test_generated_drift.py`. For a
  package whose exports come from a wildcard, `__all__` is the only
  form available, and it is enough.
