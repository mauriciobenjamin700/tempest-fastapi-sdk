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
skill/agente em `.claude/`: `/release` corta a release; os agentes são
`root-cause-planner` (plano que ataca a raiz — checa `SHIPPED.md` antes de
planejar), `code-quality-reviewer` (o que os guards não leem),
`architecture-guardian` (layout flat, re-export, espelho bilíngue, extra
lazy), `ui-design-reviewer` (componente, tokens, responsividade),
`browser-validator` (pixel em browser real, via MCP) e
`docs-prose-auditor` (prosa contra código). Todos são read-only: relatam,
não editam.

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
- **Taxa medida por amostragem vai com o N, e num N onde ela é estável.**
  Contagem exata (`5113/20000`) parece mais precisa do que é e não
  reproduz; a mesma medição a 200 000 deu 26,54% e para de andar. Contagem
  exata só para o que é determinístico (`20000/20000` é propriedade, não
  taxa). E modelo analítico que discorda da medição **perde** — a
  divergência é uma pergunta sobre a função medida, não um arredondamento:
  [`LESSONS.md`](LESSONS.md#taxa-medida-por-amostragem-precisa-do-n-v02730).
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
- **Medir no lock não é medir no piso** (v0.243.0 → v0.244.0). A mesma
  afirmação sobre `build_web_app(theme=...)` saiu errada duas vezes: a
  primeira deduzida da docstring do upstream, a segunda **medida** — num
  `.venv` que o lock resolvia uma minor atrás do piso que a release
  declarava. Ao afirmar comportamento de dependência, resolva o piso que a
  gente declara, meça lá **e** na versão atual; divergirem significa que o
  piso está errado (era o caso) ou que a frase precisa nomear a versão.
  Antes de abrir issue upstream, ler o CHANGELOG da dependência na versão
  que o nosso piso alcança. Sem guard —
  [`LESSONS.md`](LESSONS.md#medir-no-lock-não-é-medir-no-piso-v02430-v02440).
- **Render depois de escrita que pode falhar recarrega o que lê** (v0.240.0).
  O `rollback` expira **todo** o identity map, não a linha rejeitada, e ler
  coluna expirada em contexto async é `MissingGreenlet`. `expire_on_commit`
  não cobre — ele mora no caminho de commit. Sem guard (exige resolver o
  template); a reprodução por caminho está em
  `tests/admin/test_form_error_rollback.py` —
  [`LESSONS.md`](LESSONS.md#o-rollback-expira-a-sessão-inteira-não-a-linha-que-falhou-v02400).
- **A anotação que a receita contradiz é defeito da anotação** (v0.257.0).
  `make type` roda sobre o pacote, e o pacote não se chama do jeito que o
  consumidor chama — então anotação mais estreita que o contrato só aparece
  na máquina de quem copia o exemplo. Duas formas recorrentes: membro de
  `Protocol` escrito `async def get(self, key: str)` exige do implementador
  o **nome** do parâmetro e retorno `Coroutine` (o redis-py chama de `name` e
  devolve `Awaitable`, e três dos seis stores de Redis recusavam o cliente
  que a doc manda passar) — a forma que aceita é
  `def get(self, key: str, /) -> Awaitable[str | bytes | None]`, e **não**
  `Awaitable[Any]`, que aceita igual e apaga o tipo em todo call site; e
  callback cujo retorno o corpo descarta anotado como `-> None`. Guard: `tests/test_docs_type_guard.py` roda mypy sobre os
  exemplos —
  [`LESSONS.md`](LESSONS.md#a-anotação-que-a-própria-receita-contradiz-v02570).
- **O `make check` roda um checker só, e o consumidor roda outro** (v0.263.0).
  mypy aceita nome de parâmetro divergente em compatibilidade de `Protocol`;
  basedpyright recusa com `Parameter name mismatch`. Por isso protocolo que
  descreve um cliente que não é nosso escreve todo parâmetro **obrigatório**
  como posicional (`/`) — parâmetro opcional continua nomeado, porque só dá
  para passá-lo por keyword. A v0.257.0 consertou o protocolo em que doeu e
  deixou outros três; os dois clientes que a receita nomeia
  (`redis.asyncio.Redis` e `fakeredis`) eram recusados. Guard:
  `tests/test_protocol_shape_guard.py`, que cobre também retorno de membro
  resolvendo para `Any` —
  [`LESSONS.md`](LESSONS.md#corrigir-onde-doeu-não-é-corrigir-a-regra-v02630).
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
- **Regra de segurança que a doc manda o consumidor implementar é regra que
  o SDK deveria implementar** (v0.273.0). A receita de OAuth carregava três
  `!!! danger` — conferir o `state`, exigir `email_verified is True` antes de
  ligar conta por e-mail, chavear em `(provider, subject)`. Nenhuma tinha
  guard, e não dava para ter: o SDK não era dono de nenhuma linha daquele
  caminho. `!!! danger` num passo que o leitor escreve à mão é o sinal de que
  falta superfície, não de que falta aviso — trazido para dentro do
  `make_auth_router`, cada uma virou uma classe de teste.
- **Sem emoji** em código ou docs, salvo pedido explícito.
- **Bind default `127.0.0.1`** nos templates do CLI; `0.0.0.0` só quando um
  frontend de outra origem consome o serviço.
