# CLAUDE.md — tempest-fastapi-sdk

Guia do repositório. O global (`~/.claude/CLAUDE.md`) vale também; aqui
fica só o que é **diferente** ou **load-bearing** neste SDK.

Companheiros deste arquivo — leia antes de planejar ou de discutir uma
regra:

- [`SHIPPED.md`](SHIPPED.md) — o que o SDK já cobre. Boa parte do que
  parece faltar já existe; re-planejar trabalho pronto já aconteceu com os
  tiers do admin e com o roadmap de genai. Entrega nova escreve a entrada
  lá, na mesma PR. Este arquivo só cresce quando uma **regra** muda.
- [`LESSONS.md`](LESSONS.md) — a evidência atrás de cada regra: o defeito
  que shippou, o comando que mediu, o número que apareceu.

Regras de área vivem no `CLAUDE.md` do diretório e são carregadas só quando
você abre arquivo de lá: [`tests/CLAUDE.md`](tests/CLAUDE.md) (suíte, guards,
fixtures) e
[`tempest_fastapi_sdk/integrations/CLAUDE.md`](tempest_fastapi_sdk/integrations/CLAUDE.md)
(código gerado, drift, armadilhas de API de terceiro). Fluxos executáveis são
skill/agente em `.claude/`: `/release` corta a release, o agente
`docs-prose-auditor` audita prosa contra código.

## O que isto é

Biblioteca distribuída no PyPI, não serviço deployável. Ships os blocos
FastAPI/SQLAlchemy/Pydantic que todo serviço Tempest importa.

Duas consequências estruturais:

- **Layout flat.** `tempest_fastapi_sdk/` na raiz, ao lado do
  `pyproject.toml`. **Sem wrapper `src/`.** Testes em `tests/` na raiz.
  Isso contradiz a regra de layout de serviço do global **de propósito** —
  achar um `src/tempest_fastapi_sdk/` é defeito, sinalize antes de
  adicionar feature.
- **Toda mudança de superfície pública ships docs no mesmo commit.**
  Snippets de install do README, `CHANGELOG.md`, o site MkDocs bilíngue em
  `docs/` e a referência de API refletem a forma nova **antes** da tag
  `vX.Y.Z`.

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

CI na tag roda `release-pypi.yml` (trusted publishing, sem token), depois
`docs.yml` redeploya o Pages. Não empurre tag com docs vermelhas.

**Docs-only pula tudo isso.** Tocou só `docs/`, `README.md` ou prosa de
`CLAUDE.md`/`LESSONS.md` (zero delta em `tempest_fastapi_sdk/**`)? Sem bump,
sem CHANGELOG, sem tag — commit `docs: <subject>` direto na `main` (rebase
em `origin/main` primeiro se atrasado). Gate é
`uv run --group docs mkdocs build --strict` +
`pytest tests/test_docs_api_guard.py tests/test_docs_organization.py`; o
`make check` completo é desnecessário porque nenhum Python mudou. Edição de
docstring que muda assinatura ou comportamento **não** é docs-only.

## Toda afirmação sobre comportamento é medida, não deduzida

Se doc, CHANGELOG ou docstring afirma o que o software **faz**, essa frase
saiu de um comando que rodou. Não de leitura de código, não de como a
biblioteca "deve" se comportar. Rodou, viu a saída, escreveu.

Nenhum guard lê prosa — por isso este é o defeito que mais escapa aqui.
Três afirmações falsas shipparam juntas na v0.218.0; ver
[`LESSONS.md`](LESSONS.md).

Na prática:

- **Propriedade que atravessa processo, máquina ou container é testada
  atravessando.** Duas chamadas no mesmo processo não medem nada sobre o
  que sobrevive a ele. `tests/test_vacuous_guard.py` falha quando o nome ou
  a docstring afirma ter cruzado ("across processes", "survives a restart")
  e o corpo não sai do lugar.
- **Afirmação sobre ambiente é feita no ambiente.** Container, imagem slim,
  pacote de sistema, versão de dependência: construa e rode. `docker build`
  custa minutos; a frase errada fica anos.
- **Declare o escopo junto da afirmação.** "Byte a byte idêntico" quase
  nunca é verdade sem qualificação — diga sob quais condições, e o que
  continua variando.
- **Ao afirmar um modo de falha, reproduza-o.** Mensagem de erro, status
  code, o que o usuário vê.
- **Prosa entra na revisão do diff.** Para cada frase que afirma
  comportamento: *qual comando produziu isso?* Sem resposta, ou roda, ou
  reescreve como o que é — uma expectativa.

## Regra vale o que o guard vale

Regra violável em silêncio ganha teste. Ao adicionar uma regra, decida na
hora:

1. **Tem guard** — escreva o teste na mesma PR, e faça-o provar que
   **dispara** na forma que de fato shippou.
2. **Não tem guard** — escreva por quê ("precisa da assinatura do callee
   resolvida", "é julgamento de redação"), para o próximo leitor não achar
   que a checagem existe.

Guards ativos, todos dentro do `make check`:

| Guard | Cobre | Ponto cego |
| --- | --- | --- |
| `test_docs_api_guard` | bloco `python` de doc parseia; nome de `__all__` resolve | prosa (roadmap/covers driftando) |
| `test_docs_signature_guard` | exemplo casa com assinatura real; import resolve; versão do snippet ≤ `pyproject.toml` | símbolo usado sem import; prosa |
| `test_docs_organization` | espelho `.en.md`, dois navs, ordem alfabética, índice de receitas | — |
| `test_docs_examples_compile` / `_names` | exemplos completos compilam e usam nomes reais | — |
| `test_reference_coverage` | símbolo público tem stub em `docs/reference.md` | — |
| `test_kwargs_guard` | função lê chave do **próprio** `**kwargs` | splat em callable que absorve a chave |
| `test_reexport_guard` | `from x import Y as Y` + `__all__` em `__init__.py` | — |
| `test_vacuous_guard` | teste afirma cruzar processo/réplica e não cruza | — |
| `test_alias_guard` | `Field(alias=...)` voltando | — |

Marcadores de escape: `# docs-guard: skip` (fragmento não-parseável de
propósito), `# kwargs-guard: skip` (caso que genuinamente não é isso, com
docstring dizendo por quê).

## Convenções deste repo

- **Exemplos de doc são tipados.** Todo bloco de código em `README.md`,
  `docs/`, `tempest_fastapi_sdk/cli/_templates/*.tmpl` tem anotação
  completa (parâmetros + retorno). API untyped "estilo Django mágico" foi
  rejeitada explicitamente.
- **Docs bilíngues.** Toda página vive duas vezes: `docs/<page>.md` (PT-BR,
  default) e `docs/<page>.en.md` (EN-US), cada uma no `nav:` da sua língua
  (o de topo e o do locale `en` dentro do plugin `i18n`), em posição
  alfabética pelo rótulo visível. Receita nova entra também na tabela de
  `docs/recipes/index.md` + `.en.md`. Espelho faltando é defeito
  estrutural, não polimento. Autoridade:
  `tests/test_docs_organization.py`.
- **Fora da ordem alfabética, de propósito:** abas de topo (`Início →
  Instalação → Arquitetura → Tutorial → …`), `learning/`, a trilha
  `getting-started/` e o tour da landing de receitas seguem ordem
  **didática**. Mesma disciplina fora do nav: tabela de módulos do README,
  tabela de extras (com `[all]` por último, catch-all) e os grupos de
  `docs/reference.md` (com `## Superfície de topo` fixa no início, e
  agrupamento por submódulo nos blocos `###` — ali o agrupamento é a
  informação).
- **`Field(alias=...)` é defeito** (v0.234.0). Escreva o nome do fio duas
  vezes: `validation_alias` para ler, `serialization_alias` para escrever.
  mypy aceita `alias`, pyright/basedpyright não — ver
  [`LESSONS.md`](LESSONS.md).
- **Re-export explícito em todo `__init__.py`.** Todo símbolo público usa
  **as duas** formas: `from x import Y as Y` (PEP 484) **e** `__all__`.
  Consumidores rodam mypy/pyright/pylance/basedpyright em strictness
  variada e sem `pyrightconfig.json`; sozinha, cada forma é teoricamente
  compliant, mas basedpyright + Pylance strict ainda acusam "private import
  usage" sem o `as`. Import simples dentro de `__init__.py` é defeito
  estrutural.

  ```python
  # tempest_fastapi_sdk/foo/__init__.py
  from tempest_fastapi_sdk.foo.bar import Bar as Bar
  from tempest_fastapi_sdk.foo.baz import Baz as Baz

  __all__: list[str] = ["Bar", "Baz"]
  ```

- **Wildcard não é re-export.** Para superfície gerada (OpenPix, 373 nomes
  lazy), `__all__` é a única forma disponível — e é suficiente. Detalhe em
  [`tempest_fastapi_sdk/integrations/CLAUDE.md`](tempest_fastapi_sdk/integrations/CLAUDE.md).
- **Sem emoji** em código ou docs, salvo pedido explícito.
- **Bind default `127.0.0.1`** nos templates do CLI; `0.0.0.0` só quando um
  frontend de outra origem consome o serviço.
