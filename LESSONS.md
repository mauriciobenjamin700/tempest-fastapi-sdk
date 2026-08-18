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
