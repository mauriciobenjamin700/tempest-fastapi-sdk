# LESSONS.md — as evidências por trás das regras

`CLAUDE.md` enuncia as regras. Este arquivo guarda **por que** cada uma
existe: o defeito que shippou, o comando que mediu, o número que apareceu.
Consulte quando a regra parecer exagerada — ela quase sempre é a cicatriz
de algo que passou por revisão manual e escapou.

## O teste fixava o rótulo e o runtime morria no primeiro tick (v0.284.0)

`CronOffset.BRASILIA` é a string `"-03:00"`. O TaskIQ lê `cron_offset`
**string** como chave IANA (`taskiq/cli/scheduler/run.py:102` →
`now.astimezone(ZoneInfo(offset))`) e **`timedelta`** como soma. Então o
valor que a receita `recipes/queue-tasks/` ensinava não era um fuso — era
uma chave que não existe:

```pycon
>>> is_cron_task_now("0 2 * * *", _utc(5), offset="-03:00")
ZoneInfoNotFoundError: 'No time zone found with key -03:00'
>>> is_cron_task_now("0 2 * * *", _utc(5), offset=timedelta(hours=-3))
True
```

O loop guarda o tick com `except CronValueError` apenas, então a exceção
escapava de `_is_schedule_ready_to_send` e encerrava o `while True`.
Medido ponta a ponta, duas tasks `* * * * *`, uma com offset e uma sem:

| | `loop_done` | `exc` | `health_check()` | `fired` |
| --- | --- | --- | --- | --- |
| 0.283.1 | `True` | `ZoneInfoNotFoundError` | `False` | `[]` |
| 0.284.0 | `False` | `None` | `True` | as duas |

`fired=[]` inclui a task **sem** offset: uma declaração como a doc ensina
silenciava o agendamento do processo inteiro, 0,363 s depois do startup.

**Por que escapou:** três testes fixavam o rótulo
(`tests/tasks/test_cron.py` chegava a assertar
`type(sched["cron_offset"]) is str`) e nenhum media o efeito. O rótulo
estava exatamente como o autor pretendia; o que ninguém rodou foi o
scheduler com aquele valor. A regra que sai daqui é estreita e barata:
**quando o SDK entrega um valor para uma biblioteca consumir, o teste
chama a função que a biblioteca chama** — aqui `is_cron_task_now`, que é
o predicado do `SchedulerLoop`. Asserção sobre o dicionário que a gente
mesmo montou mede a nossa intenção, não o comportamento dela.

Sem guard mecânico: exigiria resolver como o callee interpreta cada
valor. O que existe é um teste de sobrevivência do loop
(`tests/tasks/test_scheduler.py`), com orçamento cinco vezes o tempo
medido de morte.

## Duas chaves escolhidas a dedo falharam três vezes no mesmo arquivo (v0.284.0)

`TaskPanelService.schedule()` lia `cron` e `interval` das entradas de
schedule do registro. Terceira vez que a projeção perdeu uma declaração:

| Release | O que evaporou | Como aparecia na tela |
| --- | --- | --- |
| v0.268.0 | `interval` | `on demand` |
| v0.284.0 | `time` (disparo único) | `on demand` |
| v0.284.0 | `cron_offset` | expressão nua, três horas errada |
| v0.284.0 | segundo gatilho de uma task com dois crons | só um aparecia |

O `cron_offset` é o pior porque uma expressão cron sem fuso **parece
completa** — nada convida o leitor a conferir, ao contrário de uma coluna
vazia.

A correção que interessa não é ler a terceira chave: é parar de projetar.
`TaskTrigger` carrega a entrada como o registro a declara, com `extra`
para o que o SDK ainda não modela, e
`tests/test_schedule_projection_guard.py` deriva o conjunto autoritativo
do `ScheduledTask.model_fields` do próprio TaskIQ — chave nova upstream
falha o teste em vez de evaporar na tela. Alimentado com a projeção da
0.283.1, o guard reporta `{'cron_offset', 'time'}`.

**Corolário sobre fake:** os testes do painel usavam um `_FakeTask` cujos
dicts de schedule o próprio teste escrevia. Com isso, o painel podia ler
uma chave com nome diferente do que o decorator grava e nenhum teste
notaria. Passaram a atravessar `TaskQueue(InMemoryBroker())` + `@tq.cron`.

## A issue descreve o sintoma; a consequência precisa ser medida (v0.284.0)

Quatro issues de um consumidor, todas com bloco "Medido — 0.283.1". Duas
descreviam a consequência errada, e em ambos os casos a medição mudou o
desenho, não só a prosa:

| Issue afirmava | Medido |
| --- | --- |
| "o painel mostra o horário errado" (#264) | o painel mostrava errado **e** a task não disparava; o loop do scheduler morria |
| "o marcador não é aplicado e o `500.log` fica vazio" (#267) | o marcador **é** aplicado nos três caminhos de 5xx; com `LogUtils(scope="root")`, o default, o arquivo recebe a linha (1 e 1). O furo é de roteamento: com `scope="logger"` o registro não chega a **nenhum** arquivo (0 e 0) |
| "reaproveita o `Protocol` que o `async_retry` já usa" (#267) | `RetryLogger` tem dois membros e nenhum `extra=`/`exc_info=`; nenhum tipo único cobre `LogUtils` e `logging.Logger` para estas chamadas |

O sinal mais barato apareceu de novo: o docstring do `LogUtils.error_500`,
**no mesmo repositório**, já dizia que os handlers do SDK setam o marcador.
Como na v0.276.0, a frase certa estava a poucas linhas da que a negava —
`grep` o assunto antes de acreditar na afirmação à sua frente, mesmo
quando ela vem com um bloco "medido".

E o inverso também: duas issues **subestimavam** o problema.

- O 422 (#266) era descrito como fora do envelope. Também **devolve o
  valor submetido**: medido, uma `password` que reprova em `min_length`
  põe a senha no corpo, e `SecretStr` não protege — a validação roda
  antes de o wrapper de segredo existir.
- O result backend sem TTL (#265) era descrito como um toggle que faltava.
  Não havia caminho nenhum para result com TTL: `result_ex_time` pelo
  `**options` é **aceito na construção** (o `RedisStreamBroker` engole a
  chave nos `**connection_kwargs`) e explode depois, no `connect()`.

## Prosa deduzida shippa errada (v0.218.0)

Três afirmações escritas por leitura de código, todas falsas quando
alguém rodou:

| Escrito | Medido |
| --- | --- |
| "mesma entrada, mesmos bytes, inclusive entre processos" | 3 execuções do mesmo container → **3 hashes**; o subset de fonte grava timestamp na tabela `head` |
| "sem `fonts-dejavu-core` todo glifo vira retângulo" | o pacote chega **transitivamente** com o Pango; o texto sai legível sem pedir |
| "precisa de Pango — o erro aparece no primeiro render" | o erro aparece no primeiro render, mas nomeia **`libgobject-2.0-0`**, que é o que a pessoa vai pesquisar |

O primeiro tinha teste. O teste comparava dois renders **no mesmo
processo**, onde bater é trivial — provava uma propriedade que ninguém
precisa, com a redação de uma que ninguém tinha. Daí
`tests/test_vacuous_guard.py`: ele falha quando o nome ou a docstring de
um teste **afirma** ter cruzado processo/réplica/restart e o corpo não sai
do lugar. Ele **não** policia "determinístico" nem "idempotente" — a
primeira versão fazia isso, sinalizou 22 testes, uns 20 corretos
(idempotência é `f(f(x)) == f(x)`, propriedade do mesmo processo) e um
deles afirmava o **oposto**.

Outro caso da mesma família: errei o 500-vs-422 do router de PDF porque
deduzi que o FastAPI converteria um `ValidationError` levantado dentro do
corpo da rota. Ele não converte.

## A anotação que a própria receita contradiz (v0.257.0)

O guard novo (`tests/test_docs_type_guard.py`) roda mypy sobre os exemplos.
Achou 162 defeitos; quatro deles não eram da doc, eram do SDK — a anotação
recusava o argumento que a receita mandava passar:

| Escrito na receita | O que a anotação pedia |
| --- | --- |
| `stream.response(on_disconnect=task.cancel)` | `Callable[[], Awaitable[None] \| None]` — `Task.cancel` devolve `bool` |
| `RedisIdempotencyStore(Redis.from_url(...))` | membro `async def get(self, key: str)` — o redis-py chama de `name` e devolve `Awaitable`, não `Coroutine` |
| `require_authenticated(firebase_identity)` | `TypeVar` preso a `BaseUserModel` |

Nos três, o **código** aceitava: `on_disconnect` só aguarda se o retorno for
awaitable e descarta o resto; o store só chama `get`/`set`; o guard só rejeita
`None`. A anotação era mais estreita que o contrato, e o único lugar onde isso
aparecia era a máquina de quem copiava a receita para um serviço com mypy
ligado — nunca aqui, porque `make type` roda sobre o pacote, e o pacote não
chama a si mesmo desse jeito.

Duas consequências práticas:

- **Protocolo que promete aceitar um cliente de terceiro é medido contra ele.**
  Escrever `async def get(self, key: str)` num `Protocol` exige do
  implementador o nome do parâmetro **e** retorno `Coroutine`. A forma que
  funciona é `def get(self, key: str, /) -> Awaitable[str | bytes | None]`:
  posicional resolve o nome, e `Awaitable` no lugar de `async def` resolve o
  `Coroutine`. Devolver `Awaitable[Any]` também aceita o cliente — e joga fora
  o tipo do valor lido em todo call site do SDK, o que é trocar um defeito de
  tipagem por outro. Dos seis stores de
  Redis exportados, três recusavam o `redis.asyncio.Redis` que a doc manda
  passar — e o `RedisLike` do rate limiter, que já usava a forma certa,
  aceitava. A diferença estava no repo desde sempre; ninguém tinha comparado.
- **Fake que finge um protocolo é checado contra o protocolo.**
  `ScriptedBackend` chamava o parâmetro de `specs`, o protocolo de `tools`:
  `Agent(ScriptedBackend([...]))` — a primeira linha da receita de teste — era
  `arg-type`.

E o achado que nenhum type-checker teria pego sozinho, mas que a leitura
disparada por ele pegou: a paginação por cursor do `README.md` comparava
`(created_at, id) > (valor, id)` como tupla do **Python**. Isso compara o
primeiro elemento e devolve a expressão dele — SQL válido, desempate perdido em
silêncio, linha repetida ou pulada na virada de página. `tuple_()` é o que
emite row-value comparison. O `~cmp` da página descendente tinha o mesmo tipo
de erro: `NOT (a > b)` é `<=`, então a página seguinte repetia a linha do
cursor.

## O teste que olha só para a resposta não vê o corpo (v0.265.0)

`create_pix_charge` shippou quebrado: **400 em toda chamada**, contra a
sandbox da Woovi. A suíte tinha teste de `create_charge` no cliente gerado e
no adapter, e os dois passavam — porque os dois afirmam sobre a **resposta**.
O defeito estava no corpo enviado, e nenhum teste o lia.

O corpo, capturado depois num `httpx.MockTransport`:

```text
{"correlationID": "probe-0001", "value": 1190, "additionalInfo": [], "splits": []}
→ 400 {"error": "O array de split precisa ter ao menos um item"}
```

Duas decisões certas isoladamente produziram a errada: o codegen materializa
array opcional como `default_factory=list` (correto para resposta — "nada
casou" é `[]`), e o `_dump` gerado descarta `None` (correto, e inútil aqui:
`[]` não é `None`). O resultado é um cliente que **afirma** o que o chamador
não afirmou, num campo em que afirmar vazio é erro de configuração.

O que levar:

- **Cliente de terceiro precisa de teste que leia o request, não só a
  resposta.** Stub de cliente e asserção sobre o retorno não alcançam o
  ponto em que a integração de fato falha. `httpx.MockTransport` custa dez
  linhas.
- **Default que "não faz mal" faz, quando atravessa a fronteira.** Dentro do
  processo, `[]` e ausente são quase a mesma coisa; no fio, são duas
  mensagens diferentes, e quem julga é o outro lado.
- **A direção do dado muda a regra.** A convenção "coleção vazia é sucesso"
  é de leitura. Na escrita, a pergunta é outra: o chamador disse isso? O
  gerador já tinha o fechamento request-vs-response (usado desde a v0.260.0
  para `extra="allow"`); a regra nova reusa ele em vez de inventar outro.

## Corrigir onde doeu não é corrigir a regra (v0.263.0)

A v0.257.0 achou que `Awaitable[Any]` e nome de parâmetro imposto recusavam
o `redis.asyncio.Redis` que a doc manda passar, e consertou **o protocolo em
que doeu**. Seis releases depois, uma vistoria de `Any` achou a mesma forma
intocada em outros três: `ThrottleBackend`, `_RedisHashClient` e o `RedisLike`
dos dois middlewares — oito membros `Awaitable[Any]` e dezesseis parâmetros
obrigatórios nomeados.

Medido com basedpyright contra redis-py 8.1.0, antes da correção:

```text
error: Type "Redis" is not assignable to declared type "ThrottleBackend"
error: Type "FakeRedis" is not assignable to declared type "ThrottleBackend"
```

O basedpyright trunca a explicação antes da causa; ela aparece isolando o
membro culpado num protocolo de um membro só:

```text
"expire" is an incompatible type
  Type "(name: KeyT, time: ExpiryT, nx: bool = False, xx: bool = False, gt: bool = False, lt: bool = False) -> Awaitable[bool]" is not assignable to type "(name: str, seconds: int) -> Awaitable[Any]"
"delete" is an incompatible type
  Type "(*names: KeyT) -> Awaitable[int]" is not assignable to type "(name: str) -> Awaitable[Any]"
```

(dois diagnósticos separados, um por protocolo isolado.)

Os **dois** clientes que a receita de segurança nomeia — o real e o
`fakeredis` que ela manda usar em teste — eram recusados, enquanto a frase
"funciona out-of-the-box" seguia publicada.

Três coisas para levar:

- **`make check` não podia ver.** O gate roda mypy, e mypy aceita nome de
  parâmetro divergente em compatibilidade de `Protocol` — medido com um par
  mínimo `expire(name, seconds)` / `expire(name, time)`: mypy `Success`,
  basedpyright `Parameter name mismatch`. Regra que só um checker fora do
  gate enxerga é regra que shippa violada até alguém olhar de novo.
- **A lição vira guard ou vira recorrência.** A v0.257.0 escreveu a forma
  certa na prosa do `CLAUDE.md` e não escreveu o teste. O guard que faltava
  (`tests/test_protocol_shape_guard.py`) leva menos de uma tarde e acusa os
  vinte e quatro achados quando alimentado com o código que shippou.
- **Vistoria de `Any` acha o que a dor não achou.** Nenhum usuário reportou:
  o runtime sempre funcionou, e quem roda só mypy nunca viu erro. O defeito
  vivia inteiro dentro do type-checker do consumidor — o lugar que a nossa
  CI, por construção, não é.

## Taxa medida por amostragem precisa do N (v0.273.0)

Escrevi numa issue que `secrets.token_urlsafe(32)` era reprovado pela política
de senha em **25,57%** das vezes — "5113/20000". Número exato, contagem exata,
comando que de fato rodou. Ao implementar, rodei de novo e deu **26,75%**
(5350/20000). Nem um dos dois é falso, e nenhum dos dois é reproduzível: são
duas amostras da mesma distribuição, e a contagem exata que eu tinha citado
sugeria uma precisão que a medição não tinha.

A 200 000 amostras a taxa para de andar:

```text
token_urlsafe(16)    106012/200000 rejeitados (53.01%)
token_urlsafe(32)     53076/200000 rejeitados (26.54%)
token_hex(32)        200000/200000 rejeitados (100.00%)
```

O terceiro é o único que podia ser citado como contagem: `token_hex` não tem
maiúscula nem caractere especial, então reprova **sempre** — não é uma taxa, é
uma propriedade.

E o modelo analítico errou. Inclusão-exclusão sobre "esta classe está ausente",
alfabeto base64url de 64 símbolos, dá 51,08% e 25,59% — 2 pontos abaixo do
medido nos dois casos, muito além do erro amostral (σ ≈ 0,11pp em N=200000). A
causa é real: `token_urlsafe(16)` produz 22 caracteres para 128 bits, e 22×6 =
132, então **o último caractere não é uniforme** — ele carrega 2 bits reais e
sai de um subconjunto de 4 símbolos, todos alfanuméricos. O modelo assumia
uniformidade que o base64 não tem.

Na prática:

- **Taxa medida por amostragem vai com o N declarado, e num N onde ela é
  estável.** "26,54% em 200 000 amostras" é reproduzível dentro de um décimo;
  "5113/20000" não é reproduzível de jeito nenhum.
- **Contagem exata só para o que é determinístico.** 20000/20000 é uma
  propriedade; 5113/20000 é ruído com aparência de dado.
- **Modelo que discorda da medição perde, e a divergência é uma pergunta.**
  Os 2 pontos não eram erro de medição — eram o padding do base64, que é a
  coisa que eu não sabia sobre a função que estava medindo.

Sem guard: nenhum teste lê prosa, e ninguém sabe olhando um número se ele veio
de N=20000 ou de N=200000. É revisão de diff — para cada taxa, *qual N, e ela
é estável ali?*

## Medir no lock não é medir no piso (v0.243.0 → v0.244.0)

A mesma afirmação saiu errada **duas vezes seguidas**, e a segunda foi medida.

A v0.243.0 shippou `build_web_app(..., theme=...)` dizendo que a paleta passava
a valer para os componentes. A frase veio quase literal da docstring do
`create_app` do tempestweb — prosa de upstream lê como autoridade, não como
suposição. Rodei e ela era falsa: `filled_button` numa sessão com tema vermelho
resolvia `rgb(88,71,133)`, o roxo baseline.

Então "corrigi": documentei que a `view` precisava repassar (`Button(...,
theme=app.theme)`) e que os helpers de `tempestweb.components` não repassavam.
Medido, escrito nas quatro receitas, no CHANGELOG, na docstring, numa lição
aqui, e numa issue aberta no repo do tempestweb. **Também errado.**

O `tempest-core` 0.12.0 já tinha resolvido na raiz, no dia anterior:
`current_theme()` / `use_theme()` num `ContextVar`, com `App._build` instalando
o tema em volta da chamada da view e os 46 campos de componente passando a ter
default `current_theme`. Sem mudança de call site, sem mudança de assinatura. E
a `tempestweb` 0.67.0 já pinava esse piso.

O que eu media era o meu `.venv`, resolvido por um lock que trazia
`tempest-core 0.11.0` — porque o piso que a própria release declarava era
`tempestweb>=0.66.0`, e a 0.66.0 pina `tempest-core>=0.11.0`:

| ambiente | `filled_button` numa sessão com tema | |
| --- | --- | --- |
| lock local (core 0.11.0) | `rgb(88,71,133)` | o que eu medi |
| piso real do ecossistema (core 0.12.0) | `rgb(191,13,13)` | o que o usuário vê |

Então a medição estava certa sobre um ambiente que ninguém deveria ter, e o
piso errado era o defeito de verdade — corrigido na 0.244.0 para
`tempestweb>=0.67.0`.

**A regra:** medição é tão boa quanto o ambiente onde rodou, e o ambiente que
importa é o que as **nossas próprias constraints** produzem, não o `.venv` que
está na mesa. Ao afirmar algo sobre comportamento de dependência: resolva o
piso que a gente declara, meça lá, e meça na versão atual. Se as duas
divergem, ou o piso está errado ou a frase precisa dizer de qual versão fala.

Corolário que já vale duas vezes aqui: **issue aberta não é trabalho
pendente**, e agora também **issue que eu abro não é defeito confirmado**.
Antes de relatar upstream, conferir o CHANGELOG da dependência na versão que o
nosso piso alcança — o `tempest-core` 0.12.0 tinha a correção documentada em
prosa clara, publicada antes de eu abrir a issue. Fechada como inválida em
tempestweb#80.

Sem guard: nenhum teste lê prosa, e nenhum resolve "esta frase vale no piso?".
O que dá para automatizar é o piso em si — um teste que instale o piso
declarado e exercite o caminho seria o guard real, e não existe.

## `404` em nome adivinhado não é evidência de ausência (v0.276.0)

A issue #228 pedia para **achar** a origem de `vendor/mercadopago-openapi.yaml`,
sob a premissa de que ninguém sabia como o arquivo tinha sido montado. A
premissa vinha de uma tabela de sondas:

| Tentativa | Resultado |
| --- | --- |
| `api.mercadopago.com/openapi.json` | `404` |
| `api.mercadopago.com/openapi` | `404` |
| `raw.githubusercontent.com/mercadopago/openapi/main/openapi.yaml` | `404` |
| Repositórios da org `mercadopago` no GitHub | *"nenhum de especificação"* |

As três primeiras linhas são medições. A quarta **não é** — é uma conclusão
escrita no formato de uma medição, e a conclusão está errada:

```text
200  raw.githubusercontent.com/mercadopago/openapi/main/spec3.yaml
```

O repositório existe. É público, Apache-2.0, criado em 2026-05-20, e se
descreve como *"MercadoPago's OpenAPI Specification"*. O `404` da terceira
linha era do **nome do arquivo**, não do repositório — e virou, sem transição
registrada, uma afirmação sobre a org inteira.

### O repositório já sabia

Pior que a sonda: a resposta certa estava escrita no próprio arquivo que
carregava a frase errada. `scripts/regen_mercado_pago.py`, docstring do
módulo:

> The specification comes from Mercado Pago's own repository,
> `github.com/mercadopago/openapi` (Apache-2.0), pinned at commit `73bc0e49`
> of 2026-08-04.

Trinta linhas abaixo, `SPEC_PATH`:

> Unlike the OpenPix document, this one has **no upstream to diff against**

As duas conviveram por três releases. Nenhum guard lê prosa, então nada
comparou uma com a outra — e a segunda se propagou para `vendor/PROVENANCE.md`,
`vendor/mercadopago-evidence.md`, `scripts/mercadopago_diff.py`, as duas
línguas da receita, o `SHIPPED.md` e o docstring do guard de digest.

O custo composto: o `SPEC_SHA256` do Mercado Pago foi deliberadamente
documentado como uma garantia **mais fraca** que a da OpenPix — *"this digest
is a claim about us"* —, e essa fraqueza inteira era ficção. Os bytes são os
do provedor. A garantia sempre foi a mesma.

### O que ficou

- **Sondar um nome e concluir sobre o namespace é o erro.** `404` responde
  *"este caminho não serve isto"*. Para responder *"isto não existe"* é preciso
  enumerar — listar os arquivos do repositório, pesquisar a org — e a diferença
  entre as duas perguntas tem que aparecer na tabela de evidência, não sumir
  nela.
- **Ao escrever que algo não existe, registre o que foi enumerado**, não só o
  que respondeu `404`. Linha de evidência sem método é conclusão disfarçada de
  medição.
- **Contradição dentro do mesmo arquivo é o sinal mais barato que existe** — e
  passou. Ao corrigir prosa, `grep` pelo assunto no repositório inteiro antes
  de acreditar na frase que está na sua frente.
- Guard: `tests/integrations/payment/mercado_pago/test_provenance.py`, que
  afirma o fato positivo (URL registrada, digest batendo, alvo de refresh
  existindo, receita nomeando o upstream) em vez de proibir a redação antiga —
  as notas de correção precisam poder citá-la.

## O aviso de depreciação já era um 500 (v0.275.0)

A issue [#251](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/251)
pedia uma coisa cosmética: todo serviço do `tempest new` nascia imprimindo
`StarletteDeprecationWarning` no primeiro `pytest`. A correção era uma linha —
pinar `httpx2` no grupo `dev`. O guard que veio junto é que pagou:

```toml
[tool.pytest.ini_options]
filterwarnings = ["error::starlette.exceptions.StarletteDeprecationWarning"]
```

Duas execuções depois, dois testes do `modelops` falharam. Não pelo
`testclient` — por uma linha do próprio SDK:

```text
tempest_fastapi_sdk/modelops/router.py:260: status.HTTP_422_UNPROCESSABLE_ENTITY
starlette.exceptions.StarletteDeprecationWarning:
  'HTTP_422_UNPROCESSABLE_ENTITY' is deprecated.
  Use 'HTTP_422_UNPROCESSABLE_CONTENT' instead.
```

O que faz disso um defeito e não ruído é a árvore da classe:

```pycon
>>> from starlette.exceptions import StarletteDeprecationWarning as W
>>> [c.__name__ for c in W.__mro__]
['StarletteDeprecationWarning', 'UserWarning', 'Warning', 'Exception',
 'BaseException', 'object']
```

`UserWarning`, não `DeprecationWarning`. Num consumidor que roda
`filterwarnings = ["error"]` — configuração comum, e recomendada — **ler a
constante levanta**, no meio da construção da resposta. O 422 que a rota
promete vira 500, e o consumidor vê isso como bug do SDK sem ter como
adivinhar de onde vem. Ninguém tinha relatado; o aviso estava lá havia
releases.

### E o nome novo não servia

O reflexo é trocar pelo nome que a mensagem sugere. Medido antes, e ainda bem:

| starlette | `HTTP_422_UNPROCESSABLE_ENTITY` | `HTTP_422_UNPROCESSABLE_CONTENT` |
| --- | --- | --- |
| 1.5.1 (o que o lock resolve) | 422, com aviso | 422 |
| 0.46.0 (o piso que `fastapi>=0.141.1` aceita) | 422, sem aviso | **ausente** |

Nenhuma das duas constantes cobre a faixa suportada: a antiga avisa no topo, a
nova não existe no piso. A rota passou a escrever `422`, com o motivo numa
constante nomeada e docstring — que é o formato desta casa para valor cuja
origem precisa sobreviver ao próximo leitor.

É a mesma lição de **Medir no lock não é medir no piso** (acima), com o
final invertido: lá o piso estava atrás do que a doc afirmava; aqui o piso
é que recusa a correção óbvia.

### O que ficou

- Aviso de depreciação de dependência **não é ruído de log**: quando a classe
  herda de `UserWarning`, ele é um modo de falha do consumidor. O gate deste
  repo o trata como erro, para o defeito aparecer aqui e não lá.
- Guard barato achou defeito que revisão manual não achou, em código que
  ninguém tinha tocado. O custo foi uma linha de `pyproject.toml`.

## O formatter desfaz quebra de docstring (v0.249.0)

O emissor de schemas quebrava resumo longo para caber em 88 colunas e o
arquivo gerado saía com 91. Medido em vez de deduzido:

```text
$ cat probe.py
class OrderTransactionPaymentPaymentMethodTransactionSecurity2:
    """Allowed values for OrderTransactionPaymentPaymentMethodTransactionSecurityStatus.
    """

$ ruff format --line-length 88 probe.py
1 file reformatted

$ awk '{print length}' probe.py | sort -rn | head -1
91
```

O `ruff format` puxa o `"""` de fecho para cima quando o conteúdo da docstring
é **uma** linha, e faz isso sem reconferir o orçamento de coluna. Quebrar para
uma linha de exatamente 88 é, portanto, o mesmo que não quebrar.

O caso apareceu no Mercado Pago porque os nomes de schema chegam a 61
caracteres — `Allowed values for <61 chars>.` fecha em 88 com a indentação e o
`"""` de abertura, e nada mais cabia. Sob nomes de 40 caracteres, como no
OpenPix, o defeito não existe: a mesma travessia só falha na spec que
estressa o nome.

A regra que sai daqui é mais forte que "quebre string longa": **o alvo do
emissor não é a régua, é a régua menos o que o formatter vai grudar depois**.
Aqui, `MAX_LINE - 3`, ou forçar a segunda linha de conteúdo.

Guard: a classe `TestSchemaDocstring` em
`tests/openapi/test_hostile_spec.py` — três
casos, um deles rodando o `ruff format` de verdade sobre o que o emissor
produziu, porque a aritmética do emissor é exatamente a parte que estava
errada. Provado que dispara: com o `budget=` revertido, os dois casos
relevantes falham.

## O double não pode falhar do jeito que a produção falha (v0.270.0)

`OpenPixPixProvider` tinha 12 testes verdes e **nunca tinha visto um byte de
JSON**. O `StubOpenPixClient` devolve `CreateChargeResponse(...)` construído
em Python, então o `_validate(GetChargeResponse, response.json())` do
cliente real nunca rodava: alias, enum, tipo declarado de cada campo — a
camada JSON→modelo inteira ficava de fora. Foi por isso que a #238 (`Charge`
inteiro recusado por `expiresIn`) passou pelos 12.

O tamanho do ponto cego só ficou visível ao cruzar a fronteira uma vez.
Uma suíte sobre `httpx.MockTransport` com corpos capturados do sandbox
achou **cinco defeitos distintos**, cada um com correção própria, todos
vivos há releases:

| Issue | O que o stub não podia ver |
| --- | --- |
| #239 | `value` de webhook como `"1990"` virando `amount_cents=0` |
| #240 | bloco `customer` que nenhuma variante do `oneOf` aceita |
| #241 | status fora do enum: 500 na API, `PENDING` no webhook |
| #242 | `order#42` no path endereçando `/charge/order` num `DELETE` |
| #243 | `raw` em `snake_case` na API e `camelCase` no webhook |

(A sexta da mesma release, a #244, veio por outro caminho — o guard de
conflitos de tipo do documento já a listava como conhecida-não-corrigida.
Guard que registra o que ainda dói é a outra metade disto.)

O padrão: **um double construído com os mesmos tipos que o código produz
concorda com o código por construção.** Ele testa o mapeamento e nada mais.
Nenhum dos cinco é sutil — cada um é óbvio no instante em que um byte real
atravessa —, e nenhum era alcançável de dentro.

Não é "fake é ruim": a suíte com stub continua, é rápida e cobre mapeamento.
É que **toda superfície que fala com o mundo externo precisa de pelo menos
um teste que atravesse a serialização de verdade**, e o repo já tinha o
padrão do lado certo (`tests/integrations/payment/mercado_pago/test_pix.py`)
sem tê-lo do lado da OpenPix.

Sem guard: exigiria decidir o que conta como "fronteira" para uma classe
arbitrária. O que existe é a suíte —
`tests/integrations/payment/adapters/test_openpix_adapter_wire.py` — e esta
entrada.

## Guard que varre um diretório varre o que aparecer nele (v0.270.0)

`tests/test_agent_docs_guard.py` e `tests/test_docs_api_guard.py` liam
`.claude/` com `rglob("*.md")` para alcançar as definições de skill e de
agente. No dia em que três `git worktree` foram criados em
`.claude/worktrees/`, os dois passaram a checar **um checkout inteiro do
repositório cada**, virtualenv incluído: o `CHANGELOG.md` daquela cópia, o
`README.md`, o `docs/` — e, no segundo, o
`.venv/.../typeshed/.../README.md` de terceiro. Medido: **238 falhas**,
nenhuma delas sobre o código sob teste.

O modo de falha é o que engana: as falhas são *plausíveis*. Cada uma nomeia
um caminho que de fato não existe — relativo a esta raiz —, então parecem
regressão de conteúdo e não erro de coleta. Custou uma investigação inteira
antes de alguém olhar o prefixo do caminho.

A regra: **guard que coleta por `rglob` declara o que exclui**, e a
exclusão é escrita quando o guard nasce, não quando alguém põe um
diretório no caminho. Aqui é `worktrees/`; a pergunta que vale para o
próximo é "o que mais pode aparecer sob este prefixo?" — `.venv`, `node_modules`,
build, cache, checkout aninhado.

## Regra sem guard sobrevive violada

- **`**kwargs`**: o defeito shippou **cinco vezes** em `MessageBroker` e
  sobreviveu a uma auditoria manual desse exato arquivo antes de virar
  `tests/test_kwargs_guard.py` (v0.208.0). O guard não vê a forma mais
  sutil — splat de `**options` num callable cujos parâmetros nomeados
  absorvem chaves, que foi como `publisher_for` fez —, porque isso exige a
  assinatura do callee resolvida.
- **Re-export com `as`**: a regra ficou escrita meses no `CLAUDE.md` e
  estava violada **769 vezes em 18 arquivos** no dia em que alguém contou.
  Agora é `tests/test_reexport_guard.py`.

Por isso todo guard novo precisa provar que **dispara** na forma que de
fato shippou. Guard que não pode falhar é guard em que ninguém deveria
confiar.

## `Field(alias=...)` quebra o consumidor, não o runtime (v0.234.0)

Runtime não distingue `alias` de `validation_alias`+`serialization_alias`
com `populate_by_name=True`. O **type-checker distingue**: `alias` renomeia
o parâmetro do `__init__` sintetizado, e o pyright passa a rejeitar
`ChargePayload(correlation_id=...)` exigindo `correlationID`. Medido com
basedpyright contra a wheel 0.233.0 publicada — e de novo com
`validate_by_name`, que também não resolve. **mypy aceita as duas
grafias**, e é por isso que shippou. Guard: `tests/test_alias_guard.py`.

## Wildcard não é re-export (v0.232.0)

O pacote OpenPix resolve seus 373 nomes gerados de forma lazy e os torna
visíveis ao type-checker com `from ...schemas import *` sob
`TYPE_CHECKING`. Medido com basedpyright contra a wheel instalada: o
`from ...openpix import ChargePayload` de um consumidor recebeu
*"ChargePayload" is not exported from module*, com conselho de importar do
submódulo privado. mypy aceitou — daí ter shippado. A correção é `__all__`,
gerado por `scripts/regen_openpix.py` e pinado por
`tests/integrations/payment/openpix/test_generated_drift.py`.

## O rollback expira a sessão inteira, não a linha que falhou (v0.240.0)

Todo POST de escrita do admin cujo save falhava respondia **500**, não o
form com o erro. O que quebrou não foi a linha rejeitada: foi o
`principal`, carregado no começo do request e não tocado pela escrita. O
`rollback` que o repositório faz depois do `IntegrityError` expira **todos**
os estados do identity map, e o `getattr(principal, "email", None)` que o
header renderiza virou IO síncrono dentro de contexto async —
`MissingGreenlet`.

O primeiro palpite (`expire_on_commit=False`) não fecha nada, e vale
registrar por quê: em `sqlalchemy/orm/session.py` (2.0.51) o teste de
`expire_on_commit` está em `_remove_snapshot` (linha 1138), o caminho de
**commit**; o rollback passa por `_restore_snapshot`, que expira tudo sem
condição (linha 1126).

**E o caminho de commit tem o mesmo defeito, quando o consumidor não
desliga o `expire_on_commit` (v0.266.0).** As cinco páginas HTML do fluxo de
auth renderizam depois do commit que consome o token, e o
`async_sessionmaker` **default** é `expire_on_commit=True` — então a página
lia coluna expirada e respondia 500 num fluxo que já tinha dado certo. Nada
acusou porque toda fixture da suíte, e o próprio `AsyncDatabaseManager`
(`db/connection.py:326`), constroem com `expire_on_commit=False`: a
configuração quebrada era justamente a que ninguém testava, e é o default de
quem escreve o próprio provider.

A correção é a mesma forma da de cima, mas condicional:
`inspect(user).expired` responde **sem tocar no banco**, então o
`await session.refresh(user)` só acontece para quem de fato expirou. Guard:
`tests/auth/test_expire_on_commit.py`, que monta a factory do jeito default
e exercita as quatro rotas que commitam antes de renderizar — as quatro
falham com `MissingGreenlet` no código da v0.265.0.

Consequência prática: **view que renderiza depois de uma escrita que pode
falhar recarrega, no `await`, tudo o que a página vai ler** — o principal,
a linha pai do formset inline. Sem guard: saber o que o template toca exige
resolver o template, e o `access_policy` do consumidor é código de fora.
O que existe é reprodução por caminho em
`tests/admin/test_form_error_rollback.py` (create, edit, import CSV,
formset inline) — neutralize os quatro reloads e os quatro falham com
`MissingGreenlet`. O policy de acesso faz parte da reprodução de propósito:
uma policy que lê `principal.is_admin` é a segunda coisa que o objeto
expirado quebra.

## `exc.message` mentia quando o raise site passava mensagem (v0.240.0)

`AppException.__init__` gravava a mensagem recebida só em `detail`. `message`
continuava sendo o atributo **de classe**, então
`ConflictException(message="Conflict creating Widget").message` respondia
`"Resource conflict"`. Quem lê `exc.message` de uma exception capturada —
o banner de erro do admin, a página de ativação/reset do fluxo de auth —
reportava o default genérico: `Invalid token` no lugar de
`token expired` / `token already used`, que é o que o serviço tinha
levantado.

A resposta JSON nunca esteve errada, porque o handler usa `detail`. É o que
manteve isso vivo: o caminho testado era o certo, e o atributo com o nome
mais óbvio era o errado. Guard: nenhum — é leitura de atributo em código de
consumidor. O que tem é o par de testes em
`tests/exceptions/test_exceptions.py` fixando instância **e** classe.

## Prosa é o ponto cego dos guards

Nenhum guard lê prosa. Consequências concretas:

- `test_docs_api_guard` garante que todo bloco `python` de doc parseia e que
  todo nome de `__all__` resolve — e **não** pega uma linha de roadmap dizendo
  que algo está em backlog quando já foi entregue. Isso driftou duas vezes
  (tiers do admin, roadmap de genai).
- `test_docs_signature_guard` checa exemplos contra assinaturas reais, mas
  uma frase prometendo um parâmetro inexistente só falha se um exemplo
  passar esse parâmetro.

Daí a regra do `SHIPPED.md` e a releitura obrigatória da prosa no diff.

## Ordem alfabética não é gosto

`tests/test_docs_organization.py` existe porque espelho `.en.md` faltando
cai em **fallback silencioso** no site (a página aparece, em português, sem
aviso) e porque o `mkdocs-static-i18n` traduz rótulo mas **não reordena**
nav compartilhado — é por isso que existem dois `nav:`, e mexer em um sem o
outro produz um site com seções em ordens diferentes por língua.
