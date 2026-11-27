# Ações customizadas

Use para adicionar botões que rodam código seu (`@admin_action`): mostrar um segredo uma vez, pedir dados antes de rodar, criar por service.

Parte da receita [Painel admin](../admin.md).

Além das 3 fixas (activate / deactivate / delete), você registra **ações
próprias** — uma função async decorada com `@admin_action` e passada em
`AdminModel(actions=[...])`. Cada uma vira uma opção no dropdown de ações
em massa, operando nas linhas marcadas.

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    EmailUtils,
    admin_action,
)

from src.admin import site
from src.core.settings import settings
from src.db.models import UserModel

mailer = EmailUtils(**settings.email_kwargs())


@admin_action(label="Enviar boas-vindas")
async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
    """Roda nas linhas selecionadas; a mensagem é exibida na list view."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    for user in users:
        await mailer.send(
            user.email,
            subject="Bem-vindo",
            body=f"Olá, {user.email}! Sua conta está pronta.",
        )
    return AdminActionResult(f"{len(users)} e-mails enviados.")


site.register(AdminModel(model=UserModel, actions=[send_welcome]))
```

O handler recebe um `AdminActionContext` com:

| Campo | O que é |
| --- | --- |
| `ids` | Identidades das linhas marcadas. |
| `repository` | `BaseRepository` do modelo, na sessão do request. |
| `db_session` | A sessão DB (pra trabalho além do repositório). |
| `request` | O request inbound. |
| `session` | A sessão do admin autenticado. |
| `principal` | A linha do usuário admin que disparou a ação. |

Retorne um `AdminActionResult(message, category="success"|"error"|"warning")`
pra exibir um banner na list view (ou `None` pra não mostrar nada). A
função fica **diretamente chamável/testável** — o decorator só anexa
metadados. Use `name=` pra fixar o identificador (default: nome da função)
e `dangerous=True` pra marcar ação destrutiva.

O painel **commita** a sessão do request quando o handler retorna, com ou
sem `secrets` no resultado, e faz rollback quando ele levanta. Então a ação
pode mudar a linha direto na sessão, sem commit próprio:

```python
from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


@admin_action(label="Confirmar e-mail")
async def verify_email(ctx: AdminActionContext) -> AdminActionResult:
    """Marca o e-mail como confirmado; o painel commita no fim."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} conta(s) confirmada(s).")
```

!!! warning "Antes da 0.366.0, a ação sem `secrets` não era commitada"
    O commit só acontecia no ramo com `secrets`: uma ação que gravava pela
    sessão mostrava o flash de sucesso e perdia a gravação no fim do request
    (#784).

Cada opção do select de ações em massa leva o próprio texto de confirmação.
Ação comum (e Activate/Deactivate) pergunta "Apply the selected action to the
checked rows?"; ação `dangerous=True` (e o Delete embutido) nomeia a ação e
avisa que não tem volta — `Run "Recusar e devolver" on the checked rows? This
cannot be undone.`. O rótulo vai num atributo `data-confirm` escapado pelo
template, nunca interpolado no JavaScript, então aspas ou `<` no rótulo não
quebram a página.

## Mostrar um segredo uma vez (`secrets=`)

Algumas ações **criam uma credencial**: uma chave de API, um token de
integração, uma senha temporária. O operador precisa ver o valor uma vez, e
o flash não serve para isso, porque ele volta pelo redirect na query string
(`?flash=...`). Ali o valor iria para o histórico do browser, para o access
log e para o `Referer` da próxima página.

Devolva o valor em `AdminActionResult(secrets=[...])`, como `AdminSecret`:

```python
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    AdminSecret,
    admin_action,
    generate_opaque_token,
)

from src.admin import site
from src.db.models import ApiKeyModel, UserModel


@admin_action(label="Gerar chave de API")
async def issue_api_key(ctx: AdminActionContext) -> AdminActionResult:
    """Cria uma chave por usuário marcado e mostra cada uma uma vez."""
    users = await ctx.repository.list(filters={"id": ctx.ids})
    secrets: list[AdminSecret] = []
    for user in users:
        plaintext, digest = generate_opaque_token()
        ctx.db_session.add(ApiKeyModel(user_id=user.id, key_hash=digest))
        secrets.append(AdminSecret(f"Chave de {user.email}", plaintext))
    return AdminActionResult(f"{len(secrets)} chave(s) criada(s).", secrets=secrets)


site.register(AdminModel(model=UserModel, actions=[issue_api_key]))
```

Com `secrets`, o painel **não redireciona**. A resposta do próprio `POST`
é uma página com a mensagem e cada valor num campo só de leitura (o foco já
seleciona o texto para copiar), e com um botão de volta para a lista. O
que muda em relação ao flash:

- **Nada vai para URL nem header.** O valor só existe no corpo dessa
  resposta.
- **`Cache-Control: no-store`** e `Pragma: no-cache`: nem o browser nem um
  proxy guardam cópia da página. Recarregar reenvia o `POST`, mas o form
  da lista leva um token de uso único e o reenvio não roda a ação de novo
  (detalhe em [Recarregar não emite de novo](#recarregar-nao-emite-de-novo)).
- **`Referrer-Policy: no-referrer`**: o link de volta não carrega a URL da
  ação.
- **A sessão do request recebe `commit` antes de a página renderizar.** O
  `ctx.db_session.add(...)` acima não precisa de commit próprio, e um
  segredo na tela nunca é de uma linha que voltou atrás.
- **`repr(AdminSecret)` omite o valor**, então logar o resultado não vaza
  o segredo.

!!! warning "Guarde só o digest"
    O painel mostra o texto puro uma vez, mas guardá-lo é decisão sua.
    Persista o `digest` do `generate_opaque_token()` (SHA-256) e confira
    a chave recebida com `verify_opaque_token`. A coluna do digest vai em
    `__secret_columns__`, para nenhuma tela do painel exibi-la.

Ação sem `secrets` continua igual: flash na list view, por redirect.

## Pedir dados antes de rodar (`schema=`) *(v0.366.0+)*

Algumas ações precisam de um dado do operador: o **motivo** de um acesso
auditado, um valor, uma data. Passe um model Pydantic em `schema=` e o
handler passa a receber `(data, ctx)`:

```python
from pydantic import BaseModel, Field

from tempest_fastapi_sdk import AdminActionContext, AdminActionResult, admin_action

from src.db.models import UserModel


class VerifyEmailInput(BaseModel):
    """O que o operador preenche antes da ação rodar."""

    reason: str = Field(min_length=10, max_length=500)


@admin_action(label="Confirmar e-mail", schema=VerifyEmailInput)
async def verify_email(
    data: VerifyEmailInput, ctx: AdminActionContext
) -> AdminActionResult:
    """Confirma as contas marcadas e grava o motivo na trilha."""
    for user_id in ctx.ids:
        user = await ctx.db_session.get(UserModel, user_id)
        if user is not None:
            user.is_active = True
    return AdminActionResult(f"{len(ctx.ids)} confirmada(s): {data.reason}")
```

O fluxo, medido em `tests/admin/test_custom_action_flow.py`:

1. Escolher a ação na lista **não roda nada**: o `POST /m/{slug}/bulk`
   responde `200` com o formulário gerado do schema (o mesmo gerador do
   `create_handler=`), o texto `2 row(s) selected.`, os ids em campos ocultos
   e um nonce de uso único.
2. Envio inválido responde `400` com o erro no campo
   (`String should have at least 10 characters`) e o que foi digitado; o
   handler não roda e o nonce não é gasto.
3. Envio válido roda o handler com o dado validado e os ids, commita e
   volta para a lista com o flash (`303`). Reenviar o mesmo formulário não
   roda de novo: o nonce já foi gasto, e o painel avisa
   `This form was already submitted; it did not run again.`.
4. Uma `AppException` do handler faz rollback e re-renderiza o formulário
   com a mensagem e o que foi digitado, com `400`.

Ação sem `schema=` continua igual: roda direto da lista.

## Criar por service (`create_handler=`)

Uma chave de API não nasce das colunas: o service gera o segredo, grava só o
hash e mostra o texto puro uma vez. O form de colunas pediria `key_hash` e
`key_prefix` digitados à mão — errado. Levar a emissão para uma ação em lote
na lista de **usuários** funciona, mas quem abre **Api Keys** não acha botão
nenhum, e a ação não tem campos para nome e validade.

`create_handler` troca o form de colunas por um form gerado de um **schema
Pydantic** e um handler que recebe o schema validado:

```python
from uuid import UUID

from pydantic import BaseModel, Field
from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminCreateHandler,
    AdminModel,
    AdminSecret,
    AdminSite,
    generate_opaque_token,
)

from src.db.models import ApiKeyModel


class ApiKeyAdminCreateSchema(BaseModel):
    name: str = Field(title="Nome da chave", min_length=3)
    expires_in_days: int | None = Field(default=None, title="Validade (dias)")
    user_id: UUID | None = Field(default=None, title="Usuário")


async def issue_api_key(
    data: ApiKeyAdminCreateSchema, ctx: AdminActionContext
) -> AdminActionResult:
    plaintext, digest = generate_opaque_token()
    ctx.db_session.add(
        ApiKeyModel(name=data.name, key_hash=digest, user_id=data.user_id)
    )
    return AdminActionResult("Chave emitida", secrets=[AdminSecret(data.name, plaintext)])


site = AdminSite(title="Admin")
site.register(
    AdminModel(
        ApiKeyModel,
        autocomplete_fields=["user_id"],
        create_handler=AdminCreateHandler(
            schema=ApiKeyAdminCreateSchema,
            handler=issue_api_key,
            label="Nova chave de API",
        ),
    )
)
```

- O botão da lista vira **+ Nova chave de API** e abre o form do schema:
  `title` vira o rótulo, o tipo escolhe o controle (enum vira select, `int`
  vira número, `SecretStr` vira senha), campo sem default é obrigatório. Um
  campo cujo nome está em `autocomplete_fields` usa a mesma busca de FK do
  form de colunas.
- Erro de validação re-renderiza com `400`, a mensagem de cada campo e o que
  foi digitado. Uma `AppException` levantada pelo handler volta como erro do
  form, também com o que foi digitado, e a sessão é desfeita.
- Com `secrets`, a resposta é a do próprio `POST`, como na ação acima. Sem
  eles, o painel redireciona para a lista com a mensagem.
- `create_handler` substitui o form de colunas: `can_create=True` junto
  levanta `ValueError` na construção.

### Recarregar não emite de novo

O form leva um token de uso único, gasto logo antes de o handler rodar. Um
reload da página do segredo reenvia o `POST`, e o painel responde com `303`
para a lista e o aviso *This form was already submitted; it did not run
again.*, sem chamar o handler. Medido no Chromium: o reload reenviou o
`POST`, caiu na lista com o aviso e a tabela ficou com **uma** chave.

O mesmo vale para as ações em lote da lista, que agora levam o token. Um
`POST` sem o token (um script, um template próprio) roda como antes, uma vez
por envio. O token é guardado no mesmo backend das revogações de sessão: com
o `InMemoryThrottleBackend` padrão ele vale por processo; atrás de mais de um
worker, passe o cliente Redis em `AdminSessionRevocations` para o reenvio ser
recusado em qualquer um.
