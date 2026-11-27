---
description: "Use para controlar quem vê e faz o quê no admin: `access_policy` por model e ação, `row_scope` na leitura e na escrita e `impersonate`."
---

# Permissões: RBAC, escopo por linha e impersonate

Use para controlar quem vê e faz o quê: RBAC por model e ação, escopo por linha na leitura e na escrita e entrar como outro usuário.

Parte da receita [Painel admin](../admin.md).

## RBAC granular (`access_policy=`)

Por padrão, quem loga no admin (`is_admin`) faz tudo o que os flags
`can_*` de cada `AdminModel` permitem. Para dar a um admin acesso só a
**alguns** modelos/ações — um "suporte" que vê pedidos mas não apaga,
um "editor" que só mexe em conteúdo — passe um `access_policy` no
`make_admin_router`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import AuditLog, UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    if user.role == "superadmin":
        return True
    if user.role == "support":
        return action is AdminPermissionEnum.VIEW      # só leitura
    return admin.model is not AuditLog             # editor: tudo menos AuditLog


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
    ),
)
```

A política é consultada `(principal, admin, action)` em **toda** ação —
`VIEW` / `CREATE` / `EDIT` / `DELETE`, e `IMPERSONATE` no model com
`impersonate=` (veja [Entrar como um usuário](#entrar-como-um-usuario-impersonate)). Negar dá `403`; negar `VIEW`
também **esconde** o modelo do dashboard e da barra lateral, e negar
create/edit/delete some com os botões correspondentes. Vale em list,
detail, create, edit, delete, bulk (delete → `DELETE`, resto → `EDIT`),
export, import e autocomplete de FK.

Vale também para o que a página de um model mostra de **outro** model:

- a tabela de inline no detail do pai só aparece com `VIEW` no filho — um
  filho sem `AdminModel` registrado é perguntado por um
  `AdminModel(Filho)` descartável, então uma policy que libera por
  `admin.model` decide igual;
- no inline editável, a linha de adicionar exige `CREATE` (e o
  `can_create` do filho) e a caixa de apagar exige `DELETE`; um `POST` do
  formset que peça a ação negada responde `403` sem gravar nada;
- o `<select>` de FK, o filtro de FK da listagem e o autocomplete exigem
  `VIEW` no model referenciado. Sem ele, o campo de FK vira input de texto
  e o autocomplete responde `403`.

!!! info "Compõe com os flags `can_*`"
    A policy **soma** aos flags `can_create`/`can_edit`/`can_delete` do
    `AdminModel` — os dois precisam liberar. Sem `access_policy`, nada
    muda (todo admin logado faz tudo), com uma exceção: `IMPERSONATE` só
    vem de uma política que o conceda. Pode ser sync ou `async`.

### Uma ação sem `EDIT` no model (`action_policy=`) *(v0.366.0+)*

Sem mais nada, toda ação customizada pede `EDIT` no model à `access_policy`.
Num painel só de leitura, liberar **uma** ação ("Confirmar e-mail") obrigava
a liberar `EDIT` no model inteiro — e, se alguém ligasse o `can_edit` por
engano, a mesma permissão abria a edição livre de qualquer campo. Também não
dava para dar a um papel uma ação sem dar outra.

Passe um `action_policy`: ele é perguntado `(principal, admin, AdminAction)`
para cada ação customizada, e a ação passa a pedir `VIEW` no model mais a
aprovação dele, não `EDIT`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminAction,
    AdminModel,
    AdminPermissionEnum,
    UserModelAuthBackend,
    make_admin_router,
)

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    """Painel só de leitura: VIEW em todo model, nada mais."""
    return action is AdminPermissionEnum.VIEW


def action_policy(user: UserModel, admin: AdminModel, action: AdminAction) -> bool:
    """Libera só a ação de confirmar e-mail, só para o suporte."""
    return user.role == "support" and action.name == "verify_email"


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
        action_policy=action_policy,
    ),
)
```

Medido em `tests/admin/test_custom_action_flow.py`:

- a ação liberada roda (`303`) e a negada responde `403`, no mesmo model;
- o menu da lista só mostra as ações que a política libera — e, de quebra,
  Activate / Deactivate e Delete só aparecem quando o model os habilita
  (`can_edit` + `can_toggle_active`, `can_delete`) **e** o principal tem
  `EDIT` / `DELETE`; sem nenhuma ação permitida, o formulário de lote some;
- com `EDIT` negado, o formulário de edição continua `403`, mesmo com a ação
  liberada.

Sem `action_policy`, nada muda: a ação customizada pede `EDIT`, como antes.

### Console SQL, tasks e logs (`surface_policy=`)

O console SQL, o painel de tasks, a página de logs e o navegador de storage
não são de um model, então a `access_policy` não tem o que perguntar. Quem decide é a
`surface_policy`, consultada `(principal, AdminSurfaceEnum)`:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    AdminSurfaceEnum,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import SqlShellService

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()


def policy(user: UserModel, admin: AdminModel, action: AdminPermissionEnum) -> bool:
    return user.role == "superadmin" or action is AdminPermissionEnum.VIEW


def surfaces(user: UserModel, surface: AdminSurfaceEnum) -> bool:
    return user.role == "superadmin"


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.JWT_SECRET,
        access_policy=policy,
        surface_policy=surfaces,
        sql_shell=SqlShellService(db),
        show_logs=True,
    ),
)
```

Negar responde `403` em todas as rotas da superfície (o `POST` do
console, o cancelamento de task, o export de logs e as escritas e o `raw`
do storage incluídos) e tira a
entrada da barra lateral.

!!! warning "Com `access_policy`, as superfícies fecham por padrão"
    Sem nenhuma das duas policies, todo admin abre todas as superfícies,
    como antes. Com `access_policy` e **sem** `surface_policy`, ninguém
    abre: o console lê qualquer tabela que a `SqlShellPolicy` libera, e um
    operador restrito a alguns models leria o resto por ali. Passe uma
    `surface_policy` para devolver as superfícies a quem deve tê-las.

## Escopo por linha (`row_scope=`)

O `access_policy` responde sim ou não para o **model inteiro**. No
multi-tenant isso não basta: o suporte da empresa A pode ver pedidos,
mas só os da empresa A. Contornar com um painel por empresa ou um
repositório filtrado à mão é onde o dado vaza — basta uma rota esquecer
o filtro. `row_scope=` põe o filtro no painel:

```python
from typing import Any

from sqlalchemy import ColumnElement, false, true

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import TicketModel, UserModel


def own_company(user: UserModel, admin: AdminModel[Any]) -> ColumnElement[bool]:
    if user.is_superuser:
        return true()
    if user.company_id is None:
        return false()
    return TicketModel.company_id == user.company_id


site.register(AdminModel(model=TicketModel, row_scope=own_company))
```

A função recebe o principal logado e o próprio `AdminModel`, e devolve
uma condição SQLAlchemy. "Todas as linhas" se diz explicitamente, com
`sqlalchemy.true()`. Pode ser `async` (para buscar o tenant em outra
tabela, por exemplo). A condição entra com `AND` em **toda** rota que lê
ou escreve linha do model:

- listagem, contagem (no cabeçalho e no card do dashboard) e export;
- detail, form de edição e o `POST` dele, delete e o download de upload;
- ações em lote — delete, activate/deactivate — e ações customizadas: os
  ids selecionados fora do escopo são descartados como se não existissem;
- autocomplete, `<select>` de FK e filtro de FK **quando este model é o
  alvo**, e a tabela de inline quando ele é o **filho**;
- o formset do inline editável (o pai e cada filho são lidos no escopo);
- a prévia e a confirmação do import CSV e `.xlsx`, e o export `.xlsx`;
- o duplicar (a origem é lida no escopo), a edição em massa, as opções e
  os vínculos de `m2m_fields` e a contagem de `related_counts` (com o
  escopo do model alvo ou filho), e o feed de atividade recente.

Linha fora do escopo responde `404` — o mesmo de uma linha que não
existe, para a resposta não confirmar que ela está lá. No delete e nas
ações em lote embutidas, a condição está no próprio `DELETE` / `UPDATE`,
não só na leitura que veio antes.

!!! danger "Devolver `None` não libera tudo"
    Uma função de escopo que esquece o `return` devolve `None`. O painel
    trata isso como erro: levanta `AdminRowScopeError`, registra no log o
    nome do model e a requisição responde `500` — fecha, em vez de abrir
    todas as linhas. Para liberar tudo, devolva `true()`.

    E cuidado com o principal sem tenant: `TicketModel.company_id ==
    user.company_id` com `company_id` `None` vira `IS NULL`, e casa com
    toda linha sem empresa. Se esse usuário não deve ver nada, devolva
    `false()` antes, como no exemplo.

### Na escrita

A regra vale para o resultado: uma linha **criada**, **editada** ou
**importada** precisa continuar dentro do escopo depois da escrita. O
painel grava dentro de uma transação, conta a linha contra a condição na
mesma transação e, se ela saiu, faz rollback e devolve o form com
`This record would fall outside the rows you may manage; nothing was
saved.` — o operador da empresa A não cria um ticket da empresa B nem
"move" um dos seus para lá trocando o `company_id`. O mesmo vale para:

- **`create_handler`**: os campos do schema com o nome de uma coluna FK
  passam pela checagem de FK abaixo antes do handler rodar. Depois, toda
  linha do model que o handler grava pela sessão — inserida **ou
  alterada**, como num upsert que edita uma linha existente — é contada
  contra o escopo antes do commit; fora, rollback. Escapam da checagem:
  escrita por SQL Core (`insert()`, `update()`, `bulk_create_values`), um
  service que abre uma sessão própria e linhas de **outros** models que o
  handler grave. Handler que dá `commit` sozinho já tornou a linha
  durável: o painel não consegue desfazer, registra em `ERROR` com o model
  e diz no form que o registro **foi** salvo fora do escopo, em vez de
  afirmar que nada foi salvo.
- **activate/deactivate em lote**: se o `is_active` novo tira alguma
  linha do escopo (um escopo que filtra por ele), nada muda e a listagem
  volta com o aviso.
- **edição em massa** (`bulk_edit_fields=`): a mesma regra — o `UPDATE`
  leva o escopo no `WHERE`, e um valor que tira qualquer linha do escopo
  volta a transação inteira.
- **muitos-para-muitos** (`m2m_fields=`): cada id postado precisa estar no
  escopo do alvo, e o vínculo que o operador não alcança é mantido.
- **chave estrangeira**: num create, edit, inline ou import, o valor de
  uma FK cujo model alvo tem `row_scope` precisa ser uma linha que o
  principal alcança lá. Fora do escopo, o campo recebe `Select a valid
  choice; that record does not exist.` — o mesmo erro de um id que não
  existe, então um `POST` forjado não descobre ids de outro tenant. A
  coluna é conferida contra a sua primeira `ForeignKey`: numa
  `ForeignKeyConstraint` composta, ou numa coluna com várias FKs, só esse
  alvo é verificado.

Um arquivo de upload gravado por um create ou edit que acabou recusado é
apagado (tentativa única; falha vai para o log).

No form de edição, uma FK que aponta para uma linha que o operador **não**
alcança (ou além do `FK_OPTION_CAP`) continua selecionada: o `<select>`
ganha uma opção com o valor atual, rotulada só com o id, e salvar sem
mexer nela mantém a referência — antes o browser mandava a primeira
opção e o save trocava a referência sem aviso.

No painel de auditoria do detail, o autor de uma alteração aparece pelo
nome quando o model de usuário não está registrado no site, ou está sem
`row_scope`. Com `row_scope`, o nome só aparece se o principal tem `VIEW`
nesse model e alcança aquele usuário pelo escopo; senão, só o id.

No delete, quando o `DELETE ... AND <escopo>` não apaga nada (a linha
saiu do escopo depois da leitura), o bloco volta inteiro — o que um
handler de `PRE_DELETE` gravou na sessão é desfeito —, a resposta é `404`
e o `POST_DELETE` não dispara. O `POST_DELETE` recebe a instância fora da
sessão, como no `BaseRepository.delete`, então lê as colunas mesmo com
`expire_on_commit=True`.

!!! warning "O que o escopo não cobre"
    - O `row_scope` vale nas rotas `/m/{slug}/...` e no que elas mostram de
      outro model. Ficam de fora o que roda código seu ou SQL livre: os
      cards do dashboard (`dashboard_cards=`), as áreas de indicadores, os
      mapas (`location_maps=`) e o console SQL.
    - Numa ação customizada, os `ctx.ids` já vêm filtrados, mas o
      `ctx.repository` é o repositório do model sem filtro, e o painel não
      confere o resultado: a ação pode ler além dos ids e pode **mover**
      uma linha para fora do escopo. Isso é responsabilidade dela.
    - O navegador de storage (`/storage`, superfície `STORAGE`) serve
      qualquer chave do bucket. Se os uploads do model escopado moram no
      mesmo bucket, quem tem a superfície contorna o escopo do download de
      upload — negue `STORAGE` a esses papéis no `surface_policy`.
    - Create e import continuam sendo oráculo de `UNIQUE` entre tenants:
      um código que já existe na empresa B dá conflito para a empresa A.
      A saída é a constraint `UNIQUE` composta com a coluna do tenant
      (`UniqueConstraint("company_id", "code")`).

!!! tip "Junto com o `access_policy`"
    Os dois compõem: o `access_policy` decide **se** o principal abre o
    model e qual ação faz; o `row_scope` decide **quais linhas**. Um
    suporte com `VIEW` e um escopo por empresa vê a listagem só da própria
    empresa e recebe `404` no detail de qualquer outra.

## Entrar como um usuário (`impersonate=`)

O suporte precisa ver a tela que o cliente vê. O admin não sabe como o seu
serviço autentica usuários, então a tarefa se divide: você entrega uma
função que transforma `(operador, alvo)` na URL que abre a aplicação como o
alvo, e o painel cuida do resto — a permissão, a confirmação, a recusa de
alvo que também é admin, o registro de quem entrou como quem e o redirect.

Para um serviço cujo login emite access token com `JWTUtils`, o handler já
vem pronto:

```python
from datetime import timedelta
from typing import Any

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    JWTUtils,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import AuditLog, UserModel

app = FastAPI()
tokens = JWTUtils(settings.JWT_SECRET)

site.register(
    AdminModel(
        model=UserModel,
        audit_model=AuditLog,
        impersonate=make_jwt_impersonation_handler(
            tokens,
            redirect_url="https://app.example.com/impersonate#token={token}",
            ttl=timedelta(minutes=10),
        ),
    )
)


def policy(
    user: UserModel, admin: AdminModel[Any], action: AdminPermissionEnum
) -> bool:
    if action is AdminPermissionEnum.IMPERSONATE:
        return user.role == "support-lead"
    return True


app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        access_policy=policy,
    ),
)
```

O token assinado leva `sub` (o `id` da conta), `typ` = `"access"` — então
`make_bearer_token_dependency` e `make_jwt_user_dependency` com o mesmo
segredo o aceitam como qualquer access token —, o `email` do alvo quando
existe e `act = {"sub": "<id do operador>"}`, o claim de ator da RFC 8693:
a aplicação sabe que a sessão é de impersonação e quem está por trás. O
prazo padrão é `ADMIN_IMPERSONATION_TTL` (15 minutos). `redirect_url`
precisa ter `{token}` (senão `ValueError` na construção); prefira pôr o
token no fragmento (`#`), que o browser não manda ao servidor da aplicação
nem no `Referer`.

Fora o `act`, é um access token comum: **toda** rota que aceita o token do
próprio usuário aceita este. Para uma rota que não deve rodar numa sessão
de impersonação — trocar a senha, mover dinheiro —, `token_actor(claims)`
devolve o operador por trás (`None` na sessão do próprio usuário; um `act`
malformado vem como `"unknown"`, nunca como `None`):

```python
from typing import Any

from fastapi import Depends

from tempest_fastapi_sdk import ForbiddenException, token_actor

from src.api.dependencies.auth import bearer


async def own_session_only(
    claims: dict[str, Any] = Depends(bearer),
) -> dict[str, Any]:
    if token_actor(claims) is not None:
        raise ForbiddenException(message="Indisponível durante impersonação.")
    return claims
```

Se o `impersonate=` está num model que não é a conta — um perfil, uma
matrícula —, diga qual conta ele representa com `impersonate_account=`
(sync ou `async`). O handler recebe essa conta, e é sobre ela que a recusa
de admin roda. A conta precisa ter `id` (é o que vai na credencial) e, se a
classe dela tem admin registrado com `row_scope`, estar dentro desse escopo
para o operador — senão uma matrícula da empresa A entregaria um usuário da
empresa B. `None`, conta sem `id` ou fora do escopo respondem `404`, antes
de qualquer auditoria. O resolver sync roda dentro de
`AsyncSession.run_sync`, então pode ler uma relação lazy
(`membership.user`); um resolver `async` precisa carregar o que lê
(`selectinload`, `AsyncAttrs.awaitable_attrs`):

```python
from tempest_fastapi_sdk import AdminModel, JWTUtils
from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

from src.admin import site
from src.core.settings import settings
from src.db.models import MembershipModel

site.register(
    AdminModel(
        model=MembershipModel,
        impersonate=make_jwt_impersonation_handler(
            JWTUtils(settings.JWT_SECRET),
            redirect_url="https://app.example.com/impersonate#token={token}",
        ),
        impersonate_account=lambda membership: membership.user,
    )
)
```

Outro mecanismo de login (link mágico, sessão por cookie) é só outra
função com a mesma forma — `handler(operador, conta) -> str`, sync ou
`async`:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import UserModel
from src.services.magic_links import issue_magic_link


async def enter_as(operator: UserModel, target: UserModel) -> str:
    link = await issue_magic_link(target.id, actor_id=operator.id)
    return f"https://app.example.com/magic#link={link}"


site.register(AdminModel(model=UserModel, impersonate=enter_as))
```

Como no JWT, a credencial vai no fragmento: no caminho ela entraria no
log de acesso do servidor da aplicação.

!!! danger "O handler entra como a conta que recebeu"
    A recusa de alvo admin roda sobre a conta que o handler recebe. Um
    handler que entra como **outra** coisa — `target.owner`, um id lido de
    outra tabela — passa ao largo dela e precisa recusar conta admin
    sozinho. Ponha o `impersonate=` no model da conta, ou use
    `impersonate_account=`.

O fluxo no painel:

1. O detail do registro ganha o botão **Log in as** / **Entrar como**.
2. Ele abre uma página de confirmação; o botão de confirmar faz `POST` com
   o token CSRF e um nonce de uso único — recarregar a resposta não entra
   de novo, volta para a listagem com o aviso de envio repetido.
3. Com `audit_model`, a entrada de auditoria é gravada (commit) **antes**
   de o handler rodar; se a gravação falha, o handler não roda — nenhuma
   credencial é emitida — e a requisição termina em erro, sem `303`.
4. O handler roda e o browser recebe `303` para a URL devolvida, com
   `Cache-Control: no-store` e `Referrer-Policy: no-referrer`. Um
   `AppException` do handler volta para a confirmação com a mensagem e vai
   para o log em `WARNING`. Quando o handler falha (qualquer exceção), uma
   segunda entrada, `admin.impersonate.failed` (o motivo em
   `context["reason"]`), diz que nada foi entregue; a timeline a mostra
   como **log in as failed** / **falha ao entrar como**.

!!! danger "`IMPERSONATE` nunca vem de graça"
    Sem `access_policy`, o admin libera tudo — **menos** impersonar: o
    botão não aparece e a rota responde `403`. Agir como outra conta vai
    além das telas do admin (e além do `row_scope` e da auditoria dele),
    então só uma política que diz sim para `AdminPermissionEnum.IMPERSONATE`
    concede. Cuidado com política escrita como "tudo menos `DELETE`": ela
    libera `IMPERSONATE` também. Ter `EDIT` não basta.

Mais regras do caminho:

- **Escopo.** O alvo precisa estar no `row_scope` do model; fora dele é
  `404`, como em toda rota de linha.
- **Admin não é alvo.** Uma conta que entra no próprio admin é recusada
  (`403`, sem formulário). São três perguntas ao backend, e qualquer uma
  recusa: `is_admin_account` sobre a linha e sobre a conta resolvida (por
  padrão, um `is_admin` verdadeiro; no `UserModelAuthBackend`, também o que
  o seu `is_authorized` responder — quem autoriza por papel recusa aquele
  papel), e `is_admin_identity(session, id_da_conta)` sobre o `id` que vai
  na credencial. Esta última pega o perfil 1:1 que divide a chave primária
  com um usuário admin: ela pergunta ao `load_principal` e, se ele não
  achar, ao `load_account` — que no `UserModelAuthBackend` lê a linha do
  usuário mesmo inativa ou sem MFA. Num backend próprio cujo
  `load_principal` filtra (inativo, MFA), **sobrescreva `load_account`**: o
  padrão devolve `None`, e um admin que o `load_principal` descarta passaria.
- **Registro.** Toda impersonação gera um log `INFO` com o operador, o
  model e o id do alvo — nunca a URL, que carrega a credencial. Com
  `audit_model`, grava também uma entrada `event` no alvo, autor = o
  operador, **antes** do handler (veja o passo 3). Com `AuditRequestMixin` na tabela, o evento
  (`admin.impersonate`), o IP e o user agent vão para as colunas próprias;
  sem ele, o nome fica em `context["event"]`. A timeline do detail mostra a
  entrada como **logged in as** / **entrou como**.
