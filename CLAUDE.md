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
você abre arquivo de lá: [`tests/CLAUDE.md`](tests/CLAUDE.md) (suíte, roster de
guards, fixtures), [`docs/CLAUDE.md`](docs/CLAUDE.md) (bilíngue, dois navs,
ordem, estilo tiangolo) e
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

## Release

`make release VERSION=X.Y.Z SUBJECT="<assunto>"` é a autoridade: recusa árvore
suja e CHANGELOG sem entrada, bumpa os dois arquivos de versão, roda o gate
inteiro (`check` + `docs-build` + `smoke`), commita e cria a tag. O push fica
manual. A ordem em volta — CHANGELOG, docs, auditoria de prosa, confirmação
antes do push — está na skill `/release`.

**Docs-only pula tudo isso.** Tocou só `docs/`, `README.md`, `SHIPPED.md` ou
prosa de `CLAUDE.md`/`LESSONS.md` (zero delta em `tempest_fastapi_sdk/**`)? Sem
bump, sem CHANGELOG, sem tag — commit `docs: <subject>` direto na `main`
(rebase em `origin/main` primeiro se atrasado). Gate é `make docs-build` +
`pytest tests/test_docs_api_guard.py tests/test_docs_organization.py`; o
`make check` completo é desnecessário porque nenhum Python mudou. Edição de
docstring que muda assinatura ou comportamento **não** é docs-only.

## Toda afirmação sobre comportamento é medida, não deduzida

Se doc, CHANGELOG ou docstring afirma o que o software **faz**, essa frase
saiu de um comando que rodou. Não de leitura de código, não de como a
biblioteca "deve" se comportar. Rodou, viu a saída, escreveu.

Nenhum guard lê prosa — por isso este é o defeito que mais escapa aqui.
Três afirmações falsas shipparam juntas na v0.218.0:
[`LESSONS.md`](LESSONS.md#prosa-deduzida-shippa-errada-v02180).

Na prática:

- **Propriedade que atravessa processo, máquina ou container é testada
  atravessando** — e afirmação sobre ambiente (container, imagem slim, pacote
  de sistema) é feita construindo e rodando o ambiente. `docker build` custa
  minutos; a frase errada fica anos.
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

O roster dos guards, o que cada um cobre e o ponto cego de cada um estão em
[`tests/CLAUDE.md`](tests/CLAUDE.md) — junto do código que você edita ao
mexer neles. Todos rodam dentro do `make check`.

## Convenções deste repo

- **Exemplos de doc são tipados.** Todo bloco de código em `README.md`,
  `docs/`, `tempest_fastapi_sdk/cli/_templates/*.tmpl` tem anotação
  completa (parâmetros + retorno). API untyped "estilo Django mágico" foi
  rejeitada explicitamente.
- **Docs bilíngues e ordenadas.** Toda página vive duas vezes
  (`docs/<page>.md` + `docs/<page>.en.md`), nos dois `nav:`, em ordem
  alfabética — espelho faltando é defeito estrutural. As regras completas, o
  que fica fora da ordem de propósito e a tabela do README ficam em
  [`docs/CLAUDE.md`](docs/CLAUDE.md); a autoridade é
  `tests/test_docs_organization.py`.
- **Render depois de escrita que pode falhar recarrega o que lê** (v0.240.0).
  O `rollback` expira **todo** o identity map, não a linha rejeitada, e ler
  coluna expirada em contexto async é `MissingGreenlet`. `expire_on_commit`
  não cobre — ele mora no caminho de commit. Sem guard (exige resolver o
  template); a reprodução por caminho está em
  `tests/admin/test_form_error_rollback.py` —
  [`LESSONS.md`](LESSONS.md#o-rollback-expira-a-sessão-inteira-não-a-linha-que-falhou-v02400).
- **`Field(alias=...)` é defeito** (v0.234.0). Escreva o nome do fio duas
  vezes: `validation_alias` para ler, `serialization_alias` para escrever.
  mypy aceita `alias`, pyright/basedpyright não —
  [`LESSONS.md`](LESSONS.md#fieldalias-quebra-o-consumidor-não-o-runtime-v02340).
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
