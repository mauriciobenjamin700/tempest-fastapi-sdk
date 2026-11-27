---
description: "Use para turbinar a listagem do admin: contagem de relacionados, import CSV/XLSX, duplicar, edição em massa, lenses e contagem em tabela grande."
---

# Listagem: contagens, import e edição em massa

Use para turbinar a listagem: contagem de relacionados, import CSV/XLSX, duplicar, edição em massa, lenses e contagem em tabela grande.

Parte da receita [Painel admin](../admin.md).

## Contagem de relacionados na listagem (`related_counts=`)

Na listagem de clientes, quantos pedidos cada um tem? Declare no admin do
**pai** a coluna FK do **filho**:

```python
from tempest_fastapi_sdk import AdminModel, RelatedCount

from src.admin import site
from src.db.models import CustomerModel, OrderModel


site.register(
    AdminModel(
        model=CustomerModel,
        related_counts=[RelatedCount(OrderModel.customer_id, label="Pedidos")],
    )
)
site.register(AdminModel(model=OrderModel))
```

A listagem ganha a coluna **Pedidos** com a contagem por linha, e cada
número é um link para a listagem de pedidos já filtrada pelo cliente
(`?filter_customer_id=<id>`) — o filtro vale mesmo com `customer_id` fora
do `list_filter` dos pedidos, aparece como um chip **Filtrado por** com
**Limpar filtro**, e o export da listagem filtrada respeita o mesmo
filtro.

A contagem de uma página inteira sai de **uma** consulta,
`SELECT customer_id, COUNT(*) ... WHERE customer_id IN (<ids da página>)
GROUP BY customer_id`, nunca uma por linha — o teste conta os statements
de uma página de 25 linhas e acha um só `GROUP BY`.

- A consulta leva o `row_scope` do admin do filho: o número conta só os
  pedidos que o principal alcança.
- Sem `VIEW` no filho, a coluna some — o número é dado daquele model.
- O link só existe quando o filho tem admin registrado; filho sem admin
  mostra a contagem sem link. Se o admin do filho esconde a coluna FK
  (`exclude_fields`, `__secret_columns__`), também não há link, e o
  `?filter_<fk>=` é ignorado na listagem do filho: filtrar por uma coluna
  que a tela não mostra responderia, pela contagem, o que ela guarda.
- `RelatedCount` recebe o atributo mapeado (para saber o model do filho);
  a coluna precisa ter `ForeignKey` para a tabela do pai, ou o
  `AdminModel` levanta `ValueError` na construção.

## Import CSV (`can_import=True`)

O admin já exporta a listagem (CSV/JSON). A contraparte: subir um CSV
para **criar** registros em massa. Habilite com `can_import=True`:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import Product


site.register(AdminModel(model=Product, can_import=True))
```

Aparece um link **Import CSV** na list view → página com upload. O CSV
precisa de uma linha de cabeçalho com os nomes das colunas editáveis; a
página mostra quais são. O import tem **dois passos**:

1. **Prévia.** O upload valida cada linha com as **mesmas regras do
   create** (tipos, obrigatórios, `choices`) e ensaia os inserts numa
   transação que sempre sofre rollback — então a prévia também mostra o
   que só o banco recusaria (valor duplicado numa `unique`) e o que o
   [`row_scope`](permissions.md#escopo-por-linha-row_scope) não alcança. Nada é
   gravado. A página diz quantas linhas entram, mostra as primeiras 20 e
   lista cada linha recusada com o número (o cabeçalho é a linha 1, como
   na planilha) e o erro de cada campo.
2. **Confirmar.** O botão **Confirm import of N records** reenvia o CSV
   num campo oculto **assinado** com o `secret_key` do router e preso ao
   model e ao operador da prévia: o que entra é exatamente o arquivo que
   foi visto. As linhas rodam de novo, de verdade, numa transação só, e
   o commit é **tudo ou nada** — se a tabela mudou desde a prévia e uma
   linha agora falha, nada entra e a página volta com os erros. Deu
   certo, o painel redireciona para a listagem com `Created N record(s).`

Com erro em **qualquer** linha o botão de confirmar vem desabilitado, e
o `POST` de confirmação recusa do mesmo jeito: nada é importado pela
metade.

!!! info "Limites do passo assinado"
    - O arquivo vai até **512 KiB** (`ADMIN_IMPORT_MAX_BYTES`); maior que
      isso a prévia responde `400` pedindo para dividir o arquivo. Medido
      sobre seis arquivos de 512 KiB (hex aleatório, ASCII imprimível
      aleatório, Latin-1, CJK, emoji, linhas de um caractere), o campo
      assinado ficou em no máximo 571 099 bytes.
    - A prévia vale **1 hora** (`ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS`).
      Depois disso, ou com o campo adulterado, ou assinado para outro
      model ou outro operador, a confirmação responde `400` e não grava.
    - A confirmação gasta um nonce de uso único: recarregar a resposta não
      importa de novo.

!!! warning "Sinais do repositório só na confirmação"
    A prévia insere pela sessão (depois do gancho `_prepare_insert` do
    repositório), sem o `add` do repositório — nenhum handler de
    `PRE_SAVE`/`POST_SAVE` dispara por uma prévia. A confirmação usa
    `repository.add`, com sinais. Como o commit é único no fim, um
    `POST_SAVE` de uma linha já disparou quando uma linha seguinte faz o
    lote voltar; handler com efeito externo (e-mail, fila) deve olhar o
    commit, não o sinal.

!!! info "Opt-in + requer create"
    `can_import` é `False` por padrão e exige `can_create` (importar é
    criar em massa). Colunas de upload de arquivo não entram pelo CSV.

### Exportar e importar `.xlsx`

Com o extra `[spreadsheet]` instalado, a listagem ganha **Export XLSX** ao
lado de CSV e JSON, e o import aceita `.xlsx` além de `.csv`:

```bash
uv add "tempest-fastapi-sdk[admin,spreadsheet]"
```

- **Export.** As mesmas linhas do CSV — busca, filtros, `row_scope` e o
  teto `export_max_rows` —, numa aba, com o cabeçalho congelado. Datetime
  sai como o **mesmo texto ISO 8601 em UTC** do CSV; número e booleano
  continuam número e booleano. Uma célula de texto que começa com `=`
  continua texto (o `SheetWriter` grava como string), então
  `=HYPERLINK(...)` digitado num nome não vira fórmula no arquivo.
  Caractere de controle que o XML não aceita (`\x00`-`\x08`, `\x0b`,
  `\x0c`, `\x0e`-`\x1f`) é removido da célula, em vez de derrubar o
  export com `500`.
- **Import.** O `.xlsx` (pelo nome ou por abrir como ZIP) é lido da
  **primeira aba**, linha 1 como cabeçalho, e vira o texto CSV que o import
  já valida: daí em diante é o mesmo caminho — prévia, `row_scope`, FK,
  confirmação assinada. O que vai assinado é esse CSV, então o teto de
  512 KiB vale para o upload **e** para o CSV que ele vira. Booleano vira
  `true`/`false`, datetime vira ISO 8601 (sem fuso, como a planilha guarda,
  lido no fuso do model como o CSV), e `3.0` vira `3`.
- **Fórmula sem valor calculado.** Planilha gerada por biblioteca
  (openpyxl, pandas) guarda a fórmula mas não o resultado, e a célula seria
  lida vazia. A prévia recusa o arquivo nomeando as células — `A3: formula
  with no calculated value...` (em pt-BR, `fórmula sem valor calculado`);
  abrir e salvar no Excel ou LibreOffice grava o valor. Fórmula que o Excel
  calculou como texto vazio (`=IF(A2="";"";...)`) é válida e entra vazia, e
  só as colunas com cabeçalho na linha 1 são conferidas: fórmula numa coluna
  que o import ignora não recusa o arquivo.
- **Limites da leitura.** A aba é lida com teto de
  `ADMIN_XLSX_IMPORT_MAX_ROWS` linhas (`ADMIN_IMPORT_MAX_BYTES // 16`,
  32 768) e `ADMIN_XLSX_IMPORT_MAX_UNCOMPRESSED_BYTES` descompactados
  (32 × o teto, 16 MiB), proporcionais ao teto do CSV; acima deles a
  prévia responde `400` dizendo os dois limites. Uma planilha de linhas com
  menos de 16 bytes de CSV pode ser recusada mesmo cabendo nos 512 KiB.

!!! info "Sem o extra"
    O openpyxl só é carregado quando um `.xlsx` é pedido. Sem o extra, o
    botão **Export XLSX** não aparece, o `accept` do upload volta a ser só
    CSV, e as rotas respondem com a mensagem que nomeia o pacote: o export
    redireciona para a listagem com
    `Exporting .xlsx needs the spreadsheet extra: pip install "tempest-fastapi-sdk[spreadsheet]"`
    e a prévia do import responde `400` com o equivalente.

## Duplicar registro (`can_duplicate=True`)

Para criar um registro parecido com um que já existe, `can_duplicate=True`
põe **Duplicar** no detail:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import ProductModel


site.register(AdminModel(model=ProductModel, can_duplicate=True))
```

O botão abre o form de **criação** preenchido com os valores do registro,
menos o que uma cópia não pode carregar: a identidade, as colunas únicas
(`unique=True`, `UniqueConstraint` e índice único — composto inclusive),
senha, upload, colunas secretas e as de auditoria (`created_at`,
`updated_at`, `created_by`, `updated_by`). Os vínculos de
[`m2m_fields`](forms.md#muitos-para-muitos-no-form-m2m_fields) que o principal
alcança vêm marcados.

O form posta para o create comum, então a cópia passa por **toda**
checagem de um registro novo — validação, FK no escopo, `row_scope`
depois da escrita. Exige `CREATE` (e `VIEW`, porque lê a origem), e a
origem precisa estar no escopo: fora dele, `404`. Precisa do form de
colunas: com `can_create=False` ou com `create_handler`, a construção
levanta `ValueError`.

## Edição em massa de um campo (`bulk_edit_fields=`)

Arquivar 40 pedidos de uma vez sem abrir um por um:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import OrderModel


site.register(
    AdminModel(
        model=OrderModel,
        bulk_edit_fields=[OrderModel.status, OrderModel.assignee_id],
        choices={OrderModel.status: ["open", "shipped", "archived"]},
    )
)
```

O menu de ações da seleção ganha **Editar campo**. Marque as linhas,
escolha a ação, escolha o campo (passo pulado quando só há um) e digite o
valor no **widget do próprio campo** — o `<select>` de `choices`, o de
FK. O valor é validado como no form de edição: tipo,
obrigatório, `choices`, a FK no escopo do alvo, o `validate_edit` de cada
linha, e um campo que o `readonly_fields_for` tranca em qualquer linha
selecionada recusa a edição inteira.

A escrita é **um** `UPDATE ... WHERE id IN (...) AND <escopo>` numa
transação:

- só os ids selecionados que o principal alcança entram;
- se o valor novo tira alguma linha do escopo, a transação volta inteira e
  a listagem mostra o aviso de escopo;
- com `audit_model`, cada linha ganha a sua entrada `update` com o diff,
  na mesma transação;
- `updated_by` recebe o operador e `updated_at` anda — um form de edição
  aberto antes acusa o [conflito](operator.md#edicao-simultanea-trava-otimista).

Exige `can_edit` e `EDIT`. O passo do valor leva um nonce de uso único:
recarregar a resposta não aplica de novo.

!!! warning "Sem sinais do repositório"
    Como o activate/deactivate em lote, a edição em massa é SQL Core:
    handlers de `PRE_SAVE`/`POST_SAVE` não disparam. Cada nome em
    `bulk_edit_fields` precisa ser coluna editável do form (nem upload, nem
    senha, nem `readonly_fields`), conferido na construção.

## Lenses — visões salvas (`lenses=`)

Uma lens é um preset nomeado de filtros + ordenação, mostrado como aba
acima da listagem. Em vez de o operador reentrar "status=aberto,
prioridade>=3, mais antigos primeiro" toda vez, ele clica na aba:

```python
from tempest_fastapi_sdk import AdminModel, Lens

from src.admin import site
from src.db.models import Ticket


site.register(
    AdminModel(
        model=Ticket,
        lenses=[
            Lens("Abertos", filters={"status": "open"}),
            Lens(
                "Urgentes",
                filters={"status": "open", "priority__gte": 3},
                order_by="-created_at",
            ),
        ],
    )
)
```

A lista ganha as abas **All / Abertos / Urgentes**. Clicar aplica os
filtros da lens (ANDeados com a busca/filtros que o usuário já usar) e a
ordenação (`order_by`, `-col` = desc, a menos que o usuário clique num
cabeçalho). A lens ativa é preservada em paginação, sort e export; "All"
volta ao padrão.

!!! info "Mesmas convenções de filtro"
    `filters` usa o mesmo dict do repository (`campo__gte`, `name` ILIKE,
    iterável → `IN`, …). O slug da aba (`?lens=`) sai do nome
    (minúsculo, hífens).

## Contagem em tabela grande (`count_strategy=`)

A listagem roda `COUNT(*)` a cada página para mostrar "N registros" e
"Página X de Y". Numa tabela de dezenas de milhões de linhas, essa
contagem é a parte cara da página. `count_strategy=` troca:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import EventModel, LedgerEntryModel


site.register(AdminModel(model=EventModel, count_strategy="estimate"))
site.register(AdminModel(model=LedgerEntryModel, count_strategy="none"))
```

- `"exact"` (padrão): o `COUNT` de sempre.
- `"estimate"`: no PostgreSQL, lê `pg_class.reltuples` — a estatística que
  o `ANALYZE` (e o autovacuum) mantém, uma consulta ao catálogo seja qual
  for o tamanho da tabela — e a tela mostra `~40 registros` e `Página 1 de
  ~4`, com uma dica no `title`. A página busca uma linha a mais para saber
  se existe próxima.
- `"none"`: não conta. A tela mostra só **Anterior** / **Próxima** e
  `Página N`; a próxima aparece quando a busca de `page_size + 1` linhas
  trouxe a linha extra.

O card do model no dashboard segue a mesma estratégia (`~40`, ou sem
número com `"none"`). `AdminCountStrategyEnum` tem os três valores, se
preferir o enum à string.

!!! info "Quando o `estimate` conta de verdade"
    A estatística descreve a **tabela inteira**. Com busca, filtro, lens ou
    um `row_scope` que não seja `true()`, o que o operador vê é outro
    conjunto, e o número estimado seria de linhas que ele não alcança — no
    caso do escopo, o tamanho da tabela de outros tenants. Nesses casos a
    contagem volta a ser exata (`COUNT` com o mesmo `WHERE`). Também conta
    exato fora do PostgreSQL (SQLite inclusive), numa tabela que o
    PostgreSQL nunca analisou (`reltuples = -1`) e numa subclasse de herança
    de tabela única (a estatística da tabela conta as classes irmãs).

!!! warning "Com `repository_class=`, vale `exact`"
    `estimate` e `none` montam o próprio `SELECT` (e o `estimate` lê o
    catálogo), o que passaria ao largo de um `repository_class` que filtra
    — um isolamento de tenant num `paginate` sobrescrito, por exemplo. Com
    `repository_class` configurado, a listagem e o card usam o `paginate` /
    `count` do seu repositório, como no `exact`.

Medido num `postgres:16-alpine` capturando os statements de uma página:
com `"estimate"` sobre 40 linhas analisadas, a página rodou o `SELECT` da
página e `SELECT reltuples FROM pg_class WHERE oid = to_regclass($1)`,
nenhum `count(`; com `?q=` o `count(*)` voltou; numa tabela recém-criada
(`reltuples = -1`) o `count(*)` rodou depois da leitura do catálogo e o
cabeçalho saiu sem `~`.
