# Campos do formulário

Use para ajustar o formulário: choices, upload de arquivo, FK com autocomplete, inlines, muitos-para-muitos, widgets, senha e colunas escondidas.

Parte da receita [Painel admin](../admin.md).

## Campo de escolha numa coluna `String` (`choices=`)

Uma coluna `Mapped[MeuEnum]` já aparece no form como `<select>`. Uma coluna
`String` que guarda o valor de um enum, ou um vocabulário que o painel deve
restringir, aparece como texto livre, porque o admin não tem como saber dos
valores. É o caso do `kind` da carteira: a coluna é `String(64)` porque a
aplicação grava os próprios tipos ("SALE", "TIP") ao lado dos do SDK.

`choices=` diz ao admin quais valores aceitar, sem mudar a coluna:

```python
from tempest_fastapi_sdk import AdminModel, AdminSite
from tempest_fastapi_sdk.wallet.models import make_wallet_entry_model
from tempest_fastapi_sdk.wallet.schemas import WalletEntryKindEnum

WalletEntryModel = make_wallet_entry_model()

site = AdminSite(title="Carteira")
site.register(
    AdminModel(
        WalletEntryModel,
        choices={
            "kind": WalletEntryKindEnum,
            "reference_type": [("order", "Pedido"), ("payout", "Saque")],
        },
    )
)
```

- **Cada entrada** é uma classe `Enum` (o valor de cada membro, com o nome
  humanizado como rótulo), uma lista de strings, ou de pares
  `(valor, rótulo)`.
- **O form vira `<select>`**: "Kind" mostra Withdraw, Withdraw refund,
  Reversal e Opening balance; "Reference type", sendo opcional, ganha
  "— none —" na frente.
- **O servidor confere.** Um POST com valor fora da lista volta `400` com
  "Choose one of the listed values for Kind." e não grava nada; o select não
  é a única barreira.
- **Nome que não é coluna** (um typo) levanta `ValueError` na construção, e
  lista vazia também.
- **No filtro da lista**: a coluna que também está em `list_filter` ganha o
  mesmo `<select>`, com os mesmos rótulos, em vez de uma caixa de texto livre.

!!! tip "Sem Activate / Deactivate"
    Em tabela onde `is_active` não significa nada (um registro financeiro,
    por exemplo), `AdminModel(can_toggle_active=False)` tira as duas ações em
    massa da lista e recusa com `400` um `action=deactivate` montado à mão.

!!! tip "Vocabulário aberto"
    Se a aplicação grava tipos próprios no `kind`, junte-os aos do SDK numa
    lista: `choices={"kind": [*WalletEntryKindEnum, "SALE", "TIP"]}` vira as
    seis opções, cada uma com o próprio valor como rótulo.

## Campo de upload de arquivo / imagem

Uma coluna `String` que guarda o caminho/chave de um arquivo pode virar um
**input de upload** no formulário. Liste a coluna em `upload_fields` e
passe um `upload_storage` (os backends que o SDK já tem —
`LocalUploadStorage` / `MinIOUploadStorage`). No submit, o arquivo é
salvo no storage e a **chave retornada** é gravada na coluna.

```python
from tempest_fastapi_sdk import AdminModel
from tempest_fastapi_sdk.utils import LocalUploadStorage

from src.admin import site
from src.db.models import DocumentModel


site.register(AdminModel(
    model=DocumentModel,
    upload_fields=[DocumentModel.attachment],   # coluna String que guarda a chave
    upload_storage=LocalUploadStorage("media/"),  # ou MinIOUploadStorage(...)
))
```

!!! info "Instalação"
    Os backends de upload não vêm com o `[admin]`. `LocalUploadStorage`
    depende do extra `[upload]` — `uv add "tempest-fastapi-sdk[upload]"`
    (traz `aiofiles`); `MinIOUploadStorage` depende do `[minio]` —
    `uv add "tempest-fastapi-sdk[minio]"` (traz `minio`).

- O form vira `multipart/form-data` automaticamente quando há `upload_fields`.
- **Create**: arquivo obrigatório só se a coluna for `NOT NULL` e sem default.
- **Edit**: sem arquivo novo → mantém o valor atual (mostra "Current: …"); com arquivo → substitui.
- A coluna guarda a **chave** do storage (`<slug>/<campo>/<uuid>.<ext>`); a página do registro tem o link **Download** dela.

!!! warning "`upload_fields` exige `upload_storage`"
    Registrar `upload_fields` sem `upload_storage` levanta `ValueError` na
    construção do `AdminModel` — sem storage não há onde gravar o arquivo.

### Só um tipo de arquivo (`upload_accept=`)

`upload_accept` diz que tipos um campo de upload aceita. O input ganha
`accept=` (o seletor de arquivos do browser já filtra) e o servidor confere
antes de gravar: o tipo declarado tem de estar na lista **e**, quando a
lista tem algum tipo que o `sniff_mime` reconhece pelos bytes
(`SNIFFABLE_MIMETYPES`: PDF, PNG, JPEG, GIF, BMP, WebP, HEIC), o começo do
arquivo tem de ser de um tipo da lista. Um PNG renomeado para `.pdf` é
recusado com erro no campo, e nada vai para o storage.

!!! warning "A conferência é da lista, não de cada tipo"
    Misturar um tipo sem assinatura com um que tem — `["text/plain",
    "application/pdf"]` — recusa texto puro: texto não tem bytes mágicos,
    então nunca "sniffa" para um tipo da lista. Medido chamando a checagem do
    router: `text/plain` com `hello world` é recusado nessa lista e aceito
    em `["text/plain"]` sozinho.

```python
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel
from tempest_fastapi_sdk.utils import LocalUploadStorage


class InvoiceModel(BaseModel):
    """An invoice with its PDF."""

    __tablename__ = "invoices"

    pdf_key: Mapped[str | None] = mapped_column(String(255), nullable=True)


invoices: AdminModel[InvoiceModel] = AdminModel(
    InvoiceModel,
    upload_fields=["pdf_key"],
    upload_storage=LocalUploadStorage("media/"),
    upload_accept={"pdf_key": ["application/pdf"]},
)
```

Na página do registro, cada campo de upload preenchido mostra o link
**Download**: o storage que gera URL temporária (`MinIOUploadStorage`)
redireciona para ela; o `LocalUploadStorage` entrega o arquivo como anexo,
com `X-Content-Type-Options: nosniff`.

### Regra que depende da linha (`validate_edit=`)

`validate_edit(row, form)` roda no submit da edição, antes de qualquer
upload ser gravado. Devolver `{"campo": "mensagem"}` recusa a edição com
esses erros — é o lugar de uma regra como "o comprovante de um registro
finalizado não muda". O preset abaixo usa exatamente isso.

### Campo trancado por linha (`readonly_fields_for=`)

`readonly_fields` tranca o mesmo campo em toda linha. Quando o campo só pode
mudar enquanto a linha está num estado — o motivo de uma recusa, editável
até o registro ser finalizado —, passe `readonly_fields_for(row)`: ele
recebe a linha guardada e devolve os campos a trancar **nela**, somados aos
de `readonly_fields`. A trava vale nos dois lados, do mesmo jeito que a
estática: o campo sai do formulário de edição daquela linha, e um valor
postado para ele à mão é ignorado no salvamento (a resposta é o `303` de
sempre, e o valor guardado não muda).

```python
from collections.abc import Sequence

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, BaseModel


class TicketModel(BaseModel):
    __tablename__ = "tickets"

    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)


def note_locked_when_closed(row: TicketModel) -> Sequence[str]:
    return ["note"] if row.status == "closed" else []


tickets: AdminModel[TicketModel] = AdminModel(
    TicketModel,
    readonly_fields=["status"],
    readonly_fields_for=note_locked_when_closed,
)
```

!!! note "O que ele não cobre"
    Campo de upload e de senha é tratado à parte no formulário, então
    tranque-o com `validate_edit`. E o gancho vale para o formulário de
    edição da própria linha, não para o formset inline do pai.

### Preset: saque manual (`manual_payout_admin`)

`manual_payout_admin(ManualPayoutModel, upload_storage=...)` devolve o
`AdminModel` da tabela de saques pagos à mão (`make_manual_payout_model`):
valores só leitura, comprovante só em PDF, link de download e a ação
**Marcar como pago**, que só conclui linha `PENDING` com comprovante.

Com `ledger_factory=` (uma função que recebe a sessão do painel e devolve o
`WalletLedger` sobre ela), entra também **Recusar e devolver**: o
administrador escreve o motivo no campo `rejection_reason` da linha e roda a
ação, que deixa a linha `REJECTED` e devolve o valor à carteira numa linha
`WITHDRAW_REFUND`, no mesmo commit. Linha sem motivo fica `PENDING` e a
mensagem lista o `correlation_id` dela; linha que já saiu de `PENDING` não
muda, e o campo do motivo sai do formulário de edição dela
(`readonly_fields_for`). As duas ações chamam
`ManualPayoutService` e rodam os `hooks=` que você passar — veja
[Carteira » Concluir ou recusar o saque à mão](../wallet.md#concluir-ou-recusar-o-saque-a-mao-manualpayoutservice).

```python
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository, BaseUserModel
from tempest_fastapi_sdk.admin import AdminModel, manual_payout_admin
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import (
    WalletBalanceMixin,
    WalletLedger,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


def ledger(session: AsyncSession) -> WalletLedger:
    return WalletLedger(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


payouts: AdminModel[Any] = manual_payout_admin(
    ManualPayoutModel,
    upload_storage=LocalUploadStorage("receipts"),
    ledger_factory=ledger,
)
```

O fluxo inteiro — o saque que grava a linha e manda o e-mail ao
administrador — está em
[Mercado Pago: testando webhooks » Saque manual](../mercado-pago-webhooks.md#saque-manual).

!!! tip "Navegação por sidebar + burger"
    Toda página autenticada tem uma **sidebar** persistente: Dashboard, um
    link por modelo registrado (agrupados em "Models") e, com
    `show_logs=True`, "Logs" em "System". O item da página atual fica
    destacado. No **desktop** a sidebar fica sempre visível à esquerda; no
    **mobile** (≤768px) ela vira off-canvas, aberta pelo ícone **burger**
    no header e fechada tocando no scrim — tudo CSS puro, sem JS.

!!! info "Página de logs (`show_logs=True`)"
    `GET /admin/logs` lê os arquivos JSON estruturados que o
    `configure_logging(log_dir=…)` grava. Passe o **mesmo** `log_dir` para
    `make_admin_router`. A página oferece filtro por fonte
    (`all`/`debug`/`info`/`warning`/`error`/`critical`/`500`), busca por
    substring na mensagem e paginação, com badges coloridos por nível.
    É **opt-in** (`show_logs=False` por default) porque o payload expõe
    tracebacks e metadados de request — só habilite atrás do login do
    admin. Sem arquivos no `log_dir`, a página mostra um estado vazio.

    O botão **Refresh** recarrega a página com a mesma fonte e a mesma busca,
    voltando à primeira página, que é a dos registros mais recentes. A página
    lê os arquivos a cada request, então recarregar é o que basta; o admin
    não carrega JavaScript, e o botão é um link. Abaixo do título, **Read at**
    mostra quando os arquivos foram lidos (UTC), para ficar visível que a
    leitura rodou mesmo quando nada novo chegou. O botão aparece também no
    estado vazio, que é justamente quando se espera o primeiro registro.

!!! tip "Traceback de erros 500 e export para issue"
    Cada registro que carrega um traceback (os handlers do SDK logam com
    `exc_info=True`) vira um item clicável: **a própria mensagem é o
    gatilho**, então clicar em qualquer ponto da entrada revela o trace,
    com os campos de correlação do request (`path`, `method`,
    `status_code`, `request_id`) ao lado. `<details>`/`<summary>` puro, sem
    JS, recolhido por default para que uma página cheia de 500 continue
    escaneável — registro sem traceback não ganha gatilho nenhum.

    `GET /admin/logs/export?format=md|json` baixa a seleção **filtrada**
    (mesmos `?source=` e `?q=` da página), do mais recente para o mais
    antigo, até 500 registros:

    - **`format=md`** — cada traceback vai num bloco cercado ```` ```pytb ````,
      então sobrevive a um colar em issue/PR com a indentação intacta. O
      cabeçalho declara fonte, contagem, filtro aplicado e — quando o teto
      de 500 corta — quantos registros casavam no total, para um export
      parcial nunca se passar por completo.
    - **`format=json`** — os registros verbatim, com todo campo que a
      aplicação logou via `extra=`, para consumo por ferramenta.

    Em telas abaixo de 600px a tabela vira **cards empilhados** (o header
    é escondido e cada célula se nomeia sozinha), então a mensagem e o
    traceback ficam na viewport sem scroll horizontal. As outras list views
    mantêm o scroll lateral, que serve para lista que se folheia.

    O export herda o gate de sessão do admin: traceback é exatamente o
    payload que não pode ficar público. Para montar seu próprio export,
    `render_entries_markdown` e `render_entries_json` são exportados no
    nível do pacote.

!!! tip "Responsivo por padrão"
    Os templates + CSS embutidos são responsivos: em telas estreitas (≤600px) o header empilha, busca/filtros/ações viram full-width, as tabelas ganham scroll horizontal (nunca quebram o layout) e o grid do detail colapsa para uma coluna. Headers de coluna são clicáveis para alternar a ordenação (▲/▼).

## FK com autocomplete (`autocomplete_fields=`)

Por padrão um campo FK cujo alvo tem admin registrado vira um `<select>`
com **todas** as linhas (capado em 1000). Numa tabela grande isso é
inutilizável. Passe `autocomplete_fields` e o campo vira uma **caixa de
busca** (HTMX) que consulta o alvo sob demanda:

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import Company, Employee


site.register(AdminModel(model=Company, search_fields=[Company.name]))
site.register(
    AdminModel(model=Employee, autocomplete_fields=[Employee.company_id])
)
```

No form de Employee, `company_id` deixa de listar todas as empresas:
você digita, o endpoint `/admin/m/employee/autocomplete/company_id?q=…`
busca nos `search_fields` do admin de `Company` (ILIKE, OR, até 20
resultados) e mostra as opções; clicar fixa o id no campo. No edit, o
rótulo da empresa atual já vem preenchido.

!!! info "Requisitos"
    O alvo do FK precisa ter um `AdminModel` registrado (são os
    `search_fields` dele que guiam a busca). Sem `autocomplete_fields`, o
    FK continua um `<select>` (útil para tabelas pequenas).

## Inlines — filhos 1-N no detail (`inlines=`)

Para ver (e chegar a) os registros que apontam pra este — pedidos de um
cliente, membros de um time — declare `inlines`. O detail do pai passa a
listar cada relação numa tabela, com link pro admin do filho e um botão
**Add** que já pré-preenche o FK do pai.

```python
# src/admin/site.py

from tempest_fastapi_sdk import AdminModel, Inline

from src.admin import site
from src.db.models import Member, Team


site.register(
    AdminModel(
        model=Team,
        inlines=[Inline(Member, Member.team_id, list_display=[Member.name])],
    )
)
site.register(AdminModel(model=Member))   # o filho precisa de admin p/ os links
```

No detail de um Team aparece a tabela **Member** com seus membros; "Add"
abre o create de Member já com `team_id` preenchido (via query param). As
colunas saem do `list_display` do `Inline` (ou, se omitido, do admin do
filho). Até 50 linhas por inline.

O modelo filho precisa ter um `AdminModel` registrado para os links
funcionarem; sem ele, as linhas aparecem só-leitura.

### Edição in-place (`editable=True`)

Para editar os filhos **sem sair do detail do pai**, passe
`editable=True` (e `can_delete=True` pra permitir remoção). A tabela vira
um formset: uma linha de inputs por filho existente + uma linha em branco
pra adicionar mais uma.

```python
from tempest_fastapi_sdk import AdminModel, Inline

from src.admin import site
from src.db.models import Member, Team


site.register(
    AdminModel(
        model=Team,
        inlines=[
            Inline(
                Member,
                Member.team_id,
                editable=True,
                can_delete=True,
            )
        ],
    )
)
site.register(AdminModel(model=Member))   # can_edit / can_delete valem aqui
```

Ao salvar (`POST /admin/m/<pai>/<id>/inlines/<filho>`), tudo acontece numa
transação: linhas existentes são atualizadas, uma linha em branco com
qualquer valor vira um filho novo, e uma caixa de exclusão marcada remove
a linha.

!!! tip "Transparente, sem mágica"
    - O **FK do pai é implícito** — forçado ao pai, nunca é um input; o
      usuário não escolhe (nem consegue reapontar) o pai errado.
    - Cada linha é **escopada ao pai**: um filho cujo FK não bate é
      ignorado, nunca editado por tabela.
    - Colunas de **upload/autocomplete** ficam no form próprio do filho
      (não entram no formset compacto).
    - Erros de validação **re-renderizam o formset in-place**, com
      mensagem por campo e sem inserir nada.
    - Precisa do `AdminModel` do filho + `can_edit` (e `can_delete` pra
      excluir). Até 50 linhas por inline.

## Muitos-para-muitos no form (`m2m_fields=`)

Um `relationship(..., secondary=...)` — tags de um artigo, papéis de um
usuário — não é coluna, então o form não o mostrava. Declare em
`m2m_fields`:

```python
from sqlalchemy import Column, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
from tempest_fastapi_sdk import AdminModel, BaseModel

from src.admin import site

article_tags = Table(
    "article_tags",
    BaseModel.metadata,
    Column("article_id", ForeignKey("articles.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


class Tag(BaseModel):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(64))


class Article(BaseModel):
    __tablename__ = "articles"
    title: Mapped[str] = mapped_column(String(120))
    tags: Mapped[list[Tag]] = relationship(secondary=article_tags)


site.register(AdminModel(model=Tag, search_fields=[Tag.name]))
site.register(AdminModel(model=Article, m2m_fields=[Article.tags]))
```

O create e o edit ganham um `<select multiple>` com as tags (rotuladas
como no `<select>` de FK: o primeiro `search_fields` do admin do alvo),
e o detail lista as tags ligadas, com link quando o alvo tem admin. Salvar
**substitui o conjunto** na mesma transação do registro: o que não veio
selecionado é desligado, e se o save volta (validação, escopo, conflito),
nenhum vínculo muda.

Com escopo, a regra é a da FK na escrita:

- as opções são só as linhas que o principal alcança pelo `row_scope` do
  alvo (e pedem `VIEW` nele — sem `VIEW`, o campo nem aparece, e os
  vínculos gravados ficam como estão);
- um vínculo que o operador **não** alcança não aparece no form e
  **continua** depois do save — ele não apaga o que não vê;
- um id postado fora do escopo, ou que não existe, recusa o campo com
  `Select a valid choice; that record does not exist.`, o mesmo erro, para
  um `POST` forjado não descobrir ids de outro tenant.

!!! info "Conferido na construção"
    Um nome em `m2m_fields` que não é `relationship` com `secondary`
    (uma coluna, um 1-N) levanta `ValueError` quando o `AdminModel` é
    criado. O 1-N tem o [`inlines=`](#inlines-filhos-1-n-no-detail-inlines);
    o N-1 já é o `<select>` de FK. As opções param em `FK_OPTION_CAP`
    (1000) linhas, lidas com `LIMIT` no SQL; um vínculo alcançável que o
    teto deixou de fora entra como mais uma opção, já marcada, como na FK
    de seleção única — salvar sem mexer no campo não o apaga. Essas opções
    extras também param no teto, lidas com o escopo na mesma consulta; um
    vínculo que nem assim aparece continua gravado: o save só desliga o que
    o form ofereceu e voltou desmarcado.

!!! info "Campo trancado"
    Um nome de `m2m_fields` em `readonly_fields`, ou no que
    `readonly_fields_for` devolve para a linha, sai do form e o save não
    mexe nos vínculos dele. O form que o **Restaurar** do histórico abre
    traz os vínculos atuais do jeito do form de edição, então restaurar
    outro campo não desliga nenhum.

## Widgets de campo por tipo de coluna

O form de create/edit escolhe o widget pelo tipo da coluna, sem
configuração: `bool` → checkbox, `Enum` → select, `int`/`float`/`Decimal`
→ number, `datetime`/`date`/**`time`** → inputs nativos, string longa →
textarea, **coluna `JSON`** → editor JSON monoespaçado (pretty-print ao
abrir, `json.loads` + validação no submit — JSON inválido vira erro de
campo, não uma string salva no lugar). FK vira `<select>` (ou
autocomplete, via `autocomplete_fields`), e `upload_fields` vira input de
arquivo.

## Senha no form (`password_fields=`)

Um modelo que herda `BaseUserModel` **não podia ser criado pelo painel**.
O form não mostra `hashed_password` (ninguém digita um digest bcrypt num
`<input type="text">`), a coluna é `NOT NULL`, e o insert respondia um 400
com `Conflict creating <Model>` — mensagem que o operador lê como "e-mail
já cadastrado".

`password_fields=` fecha o caminho: a caixa aceita texto puro e o save
grava o hash.

```python
# src/admin.py
from tempest_fastapi_sdk import AdminModel, AdminSite, PasswordPolicy

from src.db.models import LocalUserModel

site = AdminSite(title="Painel")
site.register(
    AdminModel(
        LocalUserModel,
        password_fields=[LocalUserModel.hashed_password],
        password_policy=PasswordPolicy(min_length=12, require_complexity=True),
        list_display=[LocalUserModel.name, LocalUserModel.email],
    ),
)
```

O que muda, campo a campo:

- **Create:** `<input type="password" autocomplete="new-password">`,
  obrigatório enquanto a coluna for `NOT NULL`. O valor passa por
  `check_password_policy` e, reprovando, vira erro **no campo** — não mais
  um `ConflictException`. O rótulo perde o prefixo `hashed_`
  (`hashed_password` → **Password**): a caixa recebe texto puro, e chamá-la
  de "Hashed Password" descreveria a coluna, não o que se digita.
- **Edit:** a caixa nasce vazia e vazio significa *não mexer*. Preenchida,
  rotaciona o hash. O digest **nunca** pré-popula o input.
- **Hash:** usa o `set_password` do próprio modelo quando existe (é o que
  `BaseUserModel` ships, e onde um projeto troca o algoritmo); senão,
  `PasswordUtils`.
- **Nunca aparece:** fora do `list_display` (mesmo declarado à mão), fora
  do detail e fora do export CSV/JSON.

!!! warning "`password_fields` não combina com `can_import=True`"
    Hashear uma coluna de texto puro vinda de arquivo é outra decisão, e o
    importador postaria linha nenhuma com senha. A combinação levanta
    `ValueError` na construção do `AdminModel` — no boot, não em produção.

!!! tip "A política vem das suas settings"
    `PasswordPolicy.from_settings(settings)` lê `AUTH_PASSWORD_MIN_LENGTH`,
    `AUTH_PASSWORD_MAX_BYTES` e `AUTH_PASSWORD_REQUIRE_COMPLEXITY` do
    `Settings` que você já compõe de `AuthSettings` — assim o painel aplica
    a mesma regra que o `signup`. Sem argumento, valem os defaults do
    `AuthSettings`.

## Coluna que nunca aparece (`exclude_fields=`)

Nem toda credencial é senha. A assinatura de Web Push guarda `endpoint`,
`p256dh` e `auth` — quem tem os três manda push para aquele aparelho —, o
usuário guarda um `push_token` e um `totp_secret`. `readonly_fields` não
resolve: ele tranca o input e **mostra** o valor no detail, para qualquer
admin.

`exclude_fields=` tira a coluna do painel inteiro:

```python
# src/admin.py
from tempest_fastapi_sdk import AdminModel, AdminSite

from src.db.models import LocalUserModel, WebPushSubscriptionModel

site = AdminSite(title="Painel")
site.register(
    AdminModel(
        LocalUserModel,
        exclude_fields=[LocalUserModel.totp_secret, LocalUserModel.push_token],
    ),
)
site.register(
    AdminModel(
        WebPushSubscriptionModel,
        exclude_fields=[
            WebPushSubscriptionModel.endpoint,
            WebPushSubscriptionModel.p256dh,
            WebPushSubscriptionModel.auth,
        ],
        can_create=False,
    ),
)
```

A coluna sai de **toda** superfície de uma vez: listagem, detail, form de
create e edit (um `POST` que mande o campo à mão é ignorado), import CSV,
export CSV/JSON, cabeçalho ordenável (`?sort=endpoint` é ignorado e vale a ordenação
default), a tabela de inline no detail do pai — mesmo que o `Inline`
declare a coluna no próprio `list_display` —, as linhas de antes/depois da
linha do tempo de auditoria, e o rótulo que o `<select>` de FK de outro
model mostra. Todas leem o mesmo conjunto, `hidden_field_names()`, que é
`hashed_password` + `__secret_columns__` do model + `password_fields` +
`exclude_fields`.

!!! info "As credenciais dos models do SDK já saem sozinhas"
    `MFAMixin.totp_secret`, `endpoint`/`p256dh`/`auth` do
    `BaseWebPushSubscriptionModel`, `token`/`p256dh`/`auth` do
    `BaseDeviceTokenModel`, `token_hash` do `BaseUserRefreshTokenModel` e
    `code_hash` do `BaseUserRecoveryCodeModel` são declarados em
    `__secret_columns__`. Um `AdminModel(model=...)` simples — ou o
    `automap` — já os esconde de toda superfície acima, e a auditoria os
    grava como `[redacted]`, sem `exclude_fields` nem `__audit_redact__`.
    O `exclude_fields` continua sendo o caminho para a coluna do **seu**
    model, como o `push_token` acima. A coluna secreta `NOT NULL` sem
    default ainda aparece em branco no form de **create** (nunca no de
    edit), para a assinatura de push continuar podendo ser criada pelo
    painel. Um model seu declara as próprias com
    `__secret_columns__: ClassVar[frozenset[str]] = frozenset({"api_key"})`.

A configuração que se contradiz falha na construção do `AdminModel`, no
boot, em vez de renderizar o segredo:

- nome que não é coluna do model;
- coluna excluída que outra opção também nomeia: `list_display`,
  `list_filter`, `search_fields`, `readonly_fields`, `upload_fields`,
  `autocomplete_fields`, `password_fields`, `identity_field`, `ordering` ou
  um `Lens` (`filters` ou `order_by`);
- coluna `NOT NULL` sem default excluída com `can_create=True` — o form
  nunca a preencheria, e todo create acabaria em `Conflict creating
  <Model>`. É o caso da assinatura de push acima, por isso o
  `can_create=False`.

### E a tabela de auditoria?

Esconder a linha do tempo não basta: com `audit_model=`, o `add_audited` /
`update_audited` gravariam o valor na própria tabela de auditoria, legível
por query ou pelo console SQL do painel. Por isso o `AdminModel` com
`audit_model=` **recusa** uma coluna excluída que o model não declare em
`__audit_redact__`:

```python
# src/db/models.py
from typing import ClassVar

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel


class WebPushSubscriptionModel(BaseModel):
    """Assinatura de Web Push de um aparelho."""

    __audit_redact__: ClassVar[frozenset[str]] = frozenset(
        {"endpoint", "p256dh", "auth"}
    )

    platform: Mapped[str] = mapped_column(String(16))
    endpoint: Mapped[str] = mapped_column(String(512))
    p256dh: Mapped[str] = mapped_column(String(128))
    auth: Mapped[str] = mapped_column(String(64))
```

A entrada de auditoria grava `"[redacted]"` no lugar do valor. No update, o
diff é calculado sobre os valores reais antes da troca, então rotacionar a
chave ainda aparece — `{"before": "[redacted]", "after": "[redacted]"}` —, e
`None` continua `None`, para "não definido" seguir distinguível de
"definido". `hashed_password` é redigido sempre, declarado ou não.

!!! note "`can_import=True` continua permitido"
    Ao contrário de `password_fields`, não há decisão escondida no import: a
    coluna excluída simplesmente não é importável, como não é editável.
