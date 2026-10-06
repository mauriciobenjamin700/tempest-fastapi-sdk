# CLAUDE.md — tests/

Regras da suíte. O `CLAUDE.md` da raiz vale também; aqui fica o que só
importa quando você está escrevendo ou consertando teste.

## Ferramentas e fixtures

- `pytest` + `pytest-asyncio`. Banco de teste é SQLite in-memory; serviço
  externo (Redis, RabbitMQ, HTTP) é mockado em teste unitário.
- Teste de logging **passa `file_output=False`**. O default escreve em disco
  desde a v0.22.0 e deixa uma pasta `logs/` no cwd de quem rodou.
- Espelhe a árvore do pacote: `tests/<subpacote>/test_<modulo>.py`. Guard
  vive na raiz de `tests/` com o nome `test_<regra>_guard.py`.

## Os guards são a autoridade das regras do repo

Todos rodam dentro do `make check`.

| Guard | Cobre | Ponto cego |
| --- | --- | --- |
| `test_docs_api_guard` | bloco `python` de doc parseia; nome de `__all__` resolve | prosa (roadmap/covers driftando) |
| `test_docs_signature_guard` | exemplo casa com assinatura real; import resolve; versão do snippet ≤ `pyproject.toml` | símbolo usado sem import; prosa |
| `test_docs_organization` | espelho `.en.md`, dois navs, ordem alfabética, índice de receitas | — |
| `test_docs_examples_compile` / `test_docs_examples_names` | exemplos completos compilam e usam nomes reais; import da família Tempest (`tempest_fastapi_sdk`, `tempest_core`, `tempestweb`) resolve de verdade | tipo de argumento |
| `test_docs_method_guard` | atributo lido de instância construída no exemplo existe na classe | tipo de argumento; nome reatribuído; atributo de atributo |
| `test_docs_type_guard` | mypy (config deste repo) sobre todo bloco parseável, filtrado a `arg-type`, `call-arg`, `name-defined`, `used-before-def` e `attr-defined` de classe que o bloco importa da família | prosa; linha com `...` de elisão; `attr-defined` de classe que a página só excerta ou que o Alembic registra em runtime |
| `test_reference_coverage` | símbolo público tem stub em `docs/reference.md` | — |
| `test_docstring_example_guard` | linha `>>>` de docstring chamando atributo privado do pacote — o ponto cego dos guards de bloco markdown, que não leem docstring | prosa que só **cita** o nome privado; tipo e nome livre do exemplo (`embedder`), que nenhum checker resolve ali |
| `test_kwargs_guard` | função lê chave do **próprio** `**kwargs` | splat em callable que absorve a chave |
| `test_reexport_guard` | `from x import Y as Y` + `__all__` em `__init__.py` | — |
| `test_vacuous_guard` | teste afirma cruzar processo/réplica e não cruza | — |
| `test_alias_guard` | `Field(alias=...)` voltando | — |
| `test_enum_schema_guard` | todo enum anotado num campo de `BaseSchema` do pacote é `str`-based — sob `use_enum_values` o campo guarda o **valor**, então `Enum` comum faz `== Member` responder `False` e o branch nunca roda (com `StrEnum` só o `is` falha) | enum de consumidor; comparação escrita com `is` no código que o guard não lê |
| `test_i18n_coverage_guard` | todo `code` de exceção do SDK tem entrada no catálogo, nas duas línguas, e nenhuma entrada órfã; o namespace `VALIDATION.` fica de fora das duas checagens de propósito (é PT-BR só, e quem o cobre é o `test_pydantic_error_types_guard`) | code definido fora de `tempest_fastapi_sdk/exceptions/`; qualidade da tradução |
| `test_settings_passthrough_guard` | todo knob keyword-only de `TaskQueue.redis`/`rabbitmq` é expressável via `from_settings` sem `TypeError` de colisão; knob novo sem valor de amostra falha em vez de ser pulado | keyword que o callee absorve no `**kwargs` dele — foi assim que `result_ex_time` passou pela construção e só quebrou no `connect()` |
| `test_engine_configuration_guard` | todo `create_async_engine` / `create_engine` / `async_engine_from_config` do pacote (e do `env.py.template`) está numa função que chama `_configure_sqlite_engine`, ou numa allowlist **com motivo** — os engines de migration ficam de fora porque FK ligada no batch do Alembic apaga os filhos (#395); entrada velha na allowlist também falha | engine montado por função auxiliar que recebe a URL de outro lugar e chama o configurador fora do mesmo corpo de função |
| `test_schedule_projection_guard` | a linha do painel de tasks modela toda chave de schedule que o TaskIQ lê, derivada do `ScheduledTask.model_fields` dele — chave nova upstream falha aqui em vez de evaporar na tela; chave que não pertence à linha é isentada **com motivo** | o que o template renderiza |
| `test_pydantic_error_types_guard` | a tabela de mensagens de validação cobre exatamente os tipos de erro do `pydantic_core.ErrorType` instalado, nas duas direções, e todo placeholder usado é um que o `ctx` entrega (`{expected_plural}` não é) | qualidade da tradução |
| `test_protocol_shape_guard` | membro de `Protocol` cujo retorno resolve para `Any` (`Any` pelado, `Awaitable[Any]`, `Coroutine[..., Any]`, iterador de `Any`); e parâmetro obrigatório não-posicional em protocolo de cliente de terceiro, que impõe o **nome** ao implementador | a atribuição no call site, que exigiria `disallow_any_expr`; protocolo de terceiro fora da lista `THIRD_PARTY_CLIENT_PROTOCOLS` |
| `test_constraint_shape_guard` | bound numérico em campo `str`, e bound de tamanho em campo numérico — pydantic levanta `TypeError` na construção, então o campo fica inalcançável | anotação que o guard não resolve (alias do projeto, forward reference) é pulada, não adivinhada |
| `test_pydantic_mypy_guard` | `plugins = ["pydantic.mypy"]` sem `init_typed = true` (aqui e no template do `tempest new`) | serviço já scaffoldado, que nenhum arquivo daqui alcança |
| `test_agent_docs_guard` | roster desta tabela bate com o disco; link e caminho citado em arquivo de agente existem | conteúdo da prosa |
| `test_version_agreement` | `pyproject.toml` e `__version__` concordam | `uv.lock`, que `uv run` conserta em disco antes de qualquer teste ler |
| `test_lock_version_guard` | versão **commitada** em `uv.lock` bate com a do `pyproject.toml`, lida por `git show HEAD:` | commit que ainda não existe (drift aparece na próxima execução) |
| `test_timeout_method_guard` | a configuração do `pytest-timeout` deste repo **encerra** um teardown cujo primeiro abort é engolido (finalizer que come o `Failed`, depois outro que trava), e o `timeout_method = "signal"` que ela substituiu não encerra | hang fora de um item (coleta, `sessionfinish`, join de thread não-daemon no fim do interpretador), que nenhum timer por item cobre |
| `test_testclient_httpx2_guard` | dev group deste repo **e** do template do `tempest new` pinam `httpx2`, sem o qual `fastapi.testclient` importa avisando um `UserWarning` que derruba quem roda `filterwarnings = ["error"]` | outro projeto que copie o template antes deste bump |
| `test_wallet_concurrency_guard` | dois créditos, dois débitos, débito contra saldo retido, crédito retido em voo contra débito e liquidação duplicada via `claim_once`, com a intercalação **forçada** (a primeira requisição segura a transação aberta; a segunda só começa depois do sinal), 50 corridas por cenário, em SQLite (arquivo) e PostgreSQL; prova que dispara rodando o mesmo harness contra o read-modify-write (perde 50/50) e fixa que `FOR UPDATE` só protege no PostgreSQL | PostgreSQL só roda com docker ou `TEST_POSTGRES_URL` (a CI não tem); no PostgreSQL o cenário do crédito retido em voo é recusado pelo snapshot sem exercitar a retenção — quem pega lá é o de dois débitos contra saldo retido |
| `test_wallet_update_shape_guard` | todo método do `WalletService` que move saldo decide com o **primeiro** statement sendo o único `UPDATE` da tabela de saldo, com `WHERE`, seguido de um `INSERT` no extrato; `claim_once` é um `UPDATE ... WHERE` e nada mais; método público novo sem classificação falha | o `withdraw`, que compõe métodos já checados com a chamada ao provedor; escrita em outra classe |
| `test_admin_principal_guard` | toda rota do `APIRouter` de `make_admin_router` que depende de `_require_session` chama `_resolve_principal` (lido nas variáveis livres do endpoint), e as quatro rotas que shipparam sem isso estão montadas no router que ele lê | rota que chama o `_resolve_principal` e ignora a exceção; rota autenticada que não usa `_require_session` |
| `test_sdist_payload` | entrada com ponto na raiz do **sdist** é allowlist com motivo — o sdist leva o repo inteiro menos um `exclude`, então diretório que uma ferramenta deixa na raiz shippa até alguém notar (`.claude/` custou 12 938 bytes, `.playwright-mcp/` custou 2 623 bytes e shippou até a 0.284.0) | arquivo não-dotted na raiz |
| `test_wheel_payload` | payload não-`.py` da wheel é exatamente a allowlist | — |

Marcadores de escape: `# docs-guard: skip` (fragmento não-parseável de
propósito, **ou** bloco que é o erro descrito pela seção — é como
`recipes/typing.md` demonstra chamada recusada sem derrubar o guard de tipo),
`# kwargs-guard: skip` (caso que genuinamente não é isso, com docstring
dizendo por quê).

## Timeout: `thread`, não `signal`

`pyproject.toml` configura `timeout = 300` **e** `timeout_method = "thread"`
(#337). Não volte para `signal` para "salvar o resto da suíte":

- **O `signal` é um tiro só, e o tiro pode ser engolido.** Ele arma um
  `SIGALRM` de disparo único e aborta levantando `Failed` (um
  `BaseException`) na thread principal. Levantado dentro de um finalizer
  (`del sys.modules[x]` derrubando o último ref de um recurso), vira
  `Exception ignored in: ... Failed: Timeout`; dentro de um callback do
  `asyncio`, o `Handle._run` loga e segue; dentro de um `with` cujo
  `__exit__` espera o mesmo recurso preso, o cleanup trava de novo. Nada
  rearma o alarme. Medido com teste artificial (timeout de 2 s, prazo de
  15 s, N=10 por cenário): os três cenários ficaram **pendurados 10/10** sob
  `signal` e **encerraram 10/10** sob `thread`, em ≤2,4 s. É o formato do
  que o gate do #336 relatou — o handler disparou dentro do teardown do
  `scaffolded` e o pytest seguiu preso por mais de 40 min —, embora aquele
  travamento específico não tenha se repetido aqui (0 em 40 execuções de
  `tests/agents` + `tests/cli` sob load ~11–22, 0 na suíte inteira).
- **O `thread` mata o processo inteiro** (`os._exit(1)`): os testes
  seguintes não rodam, não sai relatório de cobertura, e o `make check`
  falha no primeiro hang. É a troca certa para o gate — um hang é defeito,
  e 300 s por item está muito acima do item mais lento medido sob carga
  (18,66 s, `test_generic_bounds`, num `make check` de 33 min com load
  entre ~3 e ~17). Em troca, o dump sai de uma thread de timer e
  **inclui a thread principal** (`Stack of MainThread`); o do `signal` pula
  a thread corrente e só mostra a principal pelo traceback do `Failed` — o
  que se perde justamente quando ele é engolido.
- **Custo medido:** 10 000 testes vazios levaram 4,8–5,2 s com `signal` e
  8,5–8,8 s com `thread` (3 execuções de cada, load ~11) — ~0,36 ms por
  item, alguns segundos numa suíte de ~10 200 testes.
- **O que nenhum dos dois cobre:** hang fora de um item. Thread
  **não-daemon** viva no fim da sessão segura o interpretador no
  `threading._shutdown` depois que todo timer já foi cancelado — medido,
  pendurado 3/3 sob os dois métodos. Hoje nenhuma sobra: as que sobrevivem
  à sessão são daemon (workers do `aiosqlite`, `OtelBatchSpanRecordProcessor`,
  `tqdm_monitor`).
- **`faulthandler_timeout` não substitui:** ele só despeja as stacks, uma
  vez, e deixa o processo seguir — medido, o teardown artificial continuou
  pendurado depois do dump. Serve para diagnóstico, não para abortar.

Guard: `test_timeout_method_guard.py` roda a configuração deste repo contra
um teardown que engole o primeiro abort e assere as duas metades — o método
configurado encerra, o `signal` fica preso.

Fixture que abre recurso com thread própria **fecha no teardown**. O
`scaffolded` conectava o `resources.db` do projeto gerado e nunca
desconectava: o worker do `aiosqlite` sobrevivia ao módulo e à sessão, e
quem finalizava a conexão depois era um passe de `gc` no meio de outro
teardown. Hoje ele faz `disconnect()` antes de esquecer os módulos e falha
se alguma thread iniciada pelo módulo ainda estiver viva.

## Matriz de versão de dependência: `uv run --with`, num subprocess

O lock resolve o **piso** de uma dependência; o consumidor resolve a mais
nova. O `parse_integrity_error` passou verde no lock (SQLAlchemy 2.0.52) e
devolvia `columns=()` para toda unique no 2.1.1 (#367). Não havia mecanismo de
matriz de dependência no repo — a CI só varia o Python, e os testes `docker`
nem rodam lá —, então o primeiro é o de
`tests/db/test_integrity_sqlalchemy_matrix.py`: um teste `docker`,
parametrizado pelas versões, que reroda o módulo live num subprocess com
`uv run --no-sync --with sqlalchemy==<versão> python -m pytest <módulo>`.

- **Por que subprocess, e não parametrizar no processo:** a versão de um
  pacote já importado não troca dentro do interpretador. Um job de CI por
  versão exigiria docker na CI, que ela não tem.
- **`python -m pytest`, nunca `pytest`.** Medido: o shebang do script
  `.venv/bin/pytest` é o Python do `.venv`, então
  `uv run --with sqlalchemy==2.1.1 pytest ...` importa o 2.0.52 e passa — a
  reprodução do #367 escrita desse jeito deu 7/7 verde no código quebrado;
  com `python -m pytest`, 3 falhas.
- **Premissa conferida.** O teste imprime `sqlalchemy.__version__` pelo mesmo
  `uv run` e compara com a versão pedida antes de rodar a suíte, e falha se o
  subprocess reportar `skipped` — docker ausente no filho não vira verde.
- **`--no-sync`** para o filho não re-sincronizar o `.venv` com os grupos
  default e derrubar os extras do `uv sync --all-extras`.
- **Piso lido do `pyproject.toml`**, não copiado: subir o piso move a matriz.
  A outra ponta é uma versão fixa (a primeira medida com o defeito), não
  "a mais nova" — o teste precisa reproduzir, e resolver a mais nova a cada
  execução troca a medição sem ninguém ver.
- **Container e porta por execução** (`TEMPEST_INTEGRITY_CONTAINER`,
  `TEMPEST_INTEGRITY_PORT`), para o filho não derrubar o container do módulo
  live rodando no processo pai.

Copie o molde para a próxima dependência cujo comportamento o SDK parseia.

## Ao adicionar guard novo

1. **Ele precisa provar que dispara** na forma que de fato shippou. Guard que
   não pode falhar é guard em que ninguém deveria confiar. O padrão da casa é
   um teste que alimenta o guard com o código exato do defeito histórico e
   assere a falha.
2. **Escopo estreito, medido.** Guard largo demais sinaliza teste correto e
   perde credibilidade — foi o que aconteceu na primeira versão do
   `test_vacuous_guard`:
   [`LESSONS.md`](../LESSONS.md#prosa-deduzida-shippa-errada-v02180).
3. **Entre nesta tabela.** `test_agent_docs_guard` falha se um
   `test_*_guard.py` novo não aparecer aqui, ou se uma linha daqui apontar um
   arquivo que não existe.

## Escrevendo o teste

- **Teste que afirma travessia precisa atravessar.** `test_vacuous_guard`
  falha quando o nome ou a docstring afirma ter cruzado processo, réplica ou
  restart ("across processes", "survives a restart") e o corpo não sai do
  lugar. Duas chamadas no mesmo processo não medem nada sobre o que sobrevive
  a ele. Afirmação sobre container ou pacote de sistema é testada
  construindo e rodando o ambiente.
- **Assere o modo de falha real**, não o que a biblioteca deveria fazer:
  status code e mensagem que o usuário vê. O FastAPI não converte
  `ValidationError` levantado dentro do corpo da rota
  ([`LESSONS.md`](../LESSONS.md#prosa-deduzida-shippa-errada-v02180)).
- **`app.routes` não é a lista de rotas do router incluído.** No FastAPI
  0.141.1 o `include_router` guarda uma entrada `_IncludedRouter` em vez de
  achatar as rotas na aplicação, então `{r.path for r in app.routes}` não
  contém nenhum `/auth/*` — e todo `assert "/auth/x" not in paths` passa por
  vacuidade, inclusive com a feature ligada. Asere sobre o `APIRouter` que a
  factory devolveu (`router.routes`) ou sobre `app.openapi()["paths"]`. Achado
  ao montar o kill-switch de signup (v0.272.0), quando 4 testes passavam
  provando nada.
- **Estado de task em background se espera, não se dorme.** `sleep` fixo
  antes de contar o que outra task fez mede a velocidade do runner: o
  `test_scheduler_lease` contava loops aos 200 ms e falhava com `got 0` no CI
  (#322). Faça polling com prazo sobre o próprio estado (ou sobre um
  `asyncio.Event`/`threading.Event` que o código sinaliza), leia tudo que a
  mensagem de erro cita **no mesmo instante** da asserção, e dimensione o
  prazo para limitar a falha, não o sucesso. E o `fakeredis` expira chave
  contra `time.time()`: num host WSL2 o relógio de parede deu salto de
  3,585 s duas vezes em 60 s de amostragem, o que vence um lease de 2 s sem
  stall nenhum — o `leases` do `test_scheduler_lease` fixa esse relógio. Sem
  guard: distinguir `sleep` que espera estado de `sleep` que simula trabalho
  exige ler a intenção do teste.
- **Fake não substitui o artefato real.** Suíte de fake esconde gap de
  design: dois defeitos do caminho de modelo só apareceram rodando peso de
  verdade, e quatro do caminho OO de fila só com broker real. Para superfície
  nova que fala com mundo externo, rode uma vez contra o real
  (`make test-model`, broker local) antes de confiar na suíte.
