# LESSONS.md — as evidências por trás das regras

`CLAUDE.md` enuncia as regras. Este arquivo guarda **por que** cada uma
existe: o defeito que shippou, o comando que mediu, o número que apareceu.
Consulte quando a regra parecer exagerada — ela quase sempre é a cicatriz
de algo que passou por revisão manual e escapou.

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
