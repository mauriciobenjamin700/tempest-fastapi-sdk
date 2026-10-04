# Planilhas (.xlsx)

PDF é o que você envia quando os números estão fechados. Planilha é o que
você envia quando **quem recebe precisa trabalhar com eles**: ordenar,
filtrar, refazer o total, conferir linha a linha. Orçamento, tabela de
preços, conciliação, exportação de relatório — tudo isso chega como
`.xlsx`, e quase sempre é montado com `openpyxl` na mão.

Montar na mão custa três coisas, sempre as mesmas:

* **Aritmética de linha.** Cada escrita é um par `(linha, coluna)` que você
  controla. Insira uma linha no topo e todas as constantes abaixo mudam.
* **Estilo que escorre.** Quatro atribuições (`font`, `fill`, `alignment`,
  `border`) repetidas em cada célula, e a milésima linha não parece com a
  primeira.
* **O formato numérico errado.** `"#,##0.00"` parece certo e é uma
  armadilha: o Excel resolve essa máscara com o locale de **quem abre**. A
  planilha que você gerou em São Paulo mostra `1.234,56` aqui e
  `1,234.56` no notebook en-US do colega. O valor é o mesmo; o documento
  está errado, e ninguém percebe.

`tempest_fastapi_sdk.spreadsheet` resolve os três: um cursor de linha,
colunas declaradas uma vez, e máscaras fixadas em pt-BR.

E no sentido contrário, para ler uma planilha — o `.xlsx` que o usuário
sobe, ou a que alguém mantém no Google —, veja
[Ler um arquivo `.xlsx`](#ler-um-arquivo-xlsx) e
[Ler uma planilha do Google Sheets](#ler-uma-planilha-do-google-sheets).

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[spreadsheet]"
    ```
    Traz `openpyxl`. O motor é importado no primeiro uso, então importar o
    módulo — e definir as colunas e o tema do projeto — funciona sem ele.

## Sua primeira planilha

```python
# scripts/orcamento.py

from decimal import Decimal

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    Column,
    SheetWriter,
    new_workbook,
    workbook_to_bytes,
)


def main() -> None:
    """Write a two-item price table to disk."""
    workbook = new_workbook("Orçamento")
    writer = SheetWriter(
        workbook["Orçamento"],
        columns=[
            Column("Item", width=48, wrap=True),
            Column("Qtd.", width=12, horizontal="center"),
            Column("Valor unitário", width=20, number_format=BR_CURRENCY_FORMAT),
        ],
    )

    writer.title_block(["PREFEITURA MUNICIPAL DE EXEMPLO", "Pregão 1/2026"])
    writer.header_row()
    writer.write_row(["Serviço de instalação", 2, Decimal("2930.00")])
    writer.write_row(["Manutenção mensal", 12, Decimal("450.50")])
    writer.total_row(["Total", None, Decimal("11266.00")])
    writer.apply_widths()

    with open("orcamento.xlsx", "wb") as handle:
        handle.write(workbook_to_bytes(workbook))


if __name__ == "__main__":
    main()
```

```bash
uv run python scripts/orcamento.py
```

Abra o arquivo: título centralizado nas três colunas, cabeçalho azul-marinho
com texto branco, valores alinhados à direita como `R$ 2.930,00`, e a linha
de total destacada em âmbar.

!!! check "O que você não escreveu"
    Nenhum par `(linha, coluna)`. Nenhuma `Font`, `PatternFill` ou `Border`.
    Nenhuma máscara repetida por célula. O cursor é do `SheetWriter`, o
    estilo vem do tema e o formato vem da coluna.

## `new_workbook` e a aba fantasma

O `openpyxl` sempre cria a pasta de trabalho com uma aba chamada `Sheet`.
Esquecer de removê-la entrega um documento com uma aba vazia sobrando — o
tipo de detalhe que denuncia que o arquivo foi gerado por script.

```python
from tempest_fastapi_sdk.spreadsheet import new_workbook

workbook = new_workbook("Análise", "Orçamento", "Exequibilidade")
print(workbook.sheetnames)  # ['Análise', 'Orçamento', 'Exequibilidade']
```

Sem argumento nenhum, a aba padrão é mantida — útil quando você vai nomeá-la
depois.

## Colunas: declare uma vez

`Column` é a especificação da coluna, não de uma célula. Ela vale para
todas as linhas do corpo, e é por isso que o formato não pode divergir entre
a primeira linha e a milésima.

```python
from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    BR_PERCENT_FORMAT,
    Column,
    TEXT_FORMAT,
)

columns = [
    Column("Processo", width=18, number_format=TEXT_FORMAT),
    Column("Descrição", width=52, wrap=True),
    Column("Deságio", width=12, number_format=BR_PERCENT_FORMAT),
    Column("Valor", width=18, number_format=BR_CURRENCY_FORMAT),
]
```

| Campo | Para quê |
| --- | --- |
| `title` | Texto do cabeçalho, usado por `header_row()` |
| `width` | Largura em caracteres; `None` deixa o padrão (que corta texto) |
| `number_format` | Máscara aplicada a toda célula do corpo |
| `horizontal` | `"left"`, `"center"`, `"right"`; `None` deixa o Excel decidir |
| `wrap` | Quebra de linha — ligue na descrição, deixe desligada no resto |

!!! warning "`wrap=True` em coluna curta deixa a linha alta à toa"
    A altura da linha é a da célula mais alta. Uma coluna de duas palavras
    com quebra ligada estica a linha inteira sem ganhar nada.

## Números, não strings

A tentação é formatar em Python e escrever o texto pronto. A célula fica
com `"R$ 2.930,00"`, que é **texto**: quem recebe não consegue somar,
ordenar nem filtrar por ela, e o `SOMA` do Excel devolve zero para a coluna
inteira.

```python
from decimal import Decimal

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    Column,
    SheetWriter,
    new_workbook,
)
from tempest_fastapi_sdk.utils import format_currency_br

workbook = new_workbook("Orçamento")
writer = SheetWriter(
    workbook["Orçamento"],
    columns=[
        Column("Item", width=48),
        Column("Valor", width=18, number_format=BR_CURRENCY_FORMAT),
    ],
)

# ❌ vira texto: nada de soma, ordenação ou filtro
writer.write_row(["Serviço", format_currency_br(Decimal("2930.00"))])

# ✅ escreva o número e deixe a máscara apresentar
writer.write_row(["Serviço", Decimal("2930.00")])
```

Na tela as duas linhas dão exatamente o mesmo `R$ 2.930,00`; no arquivo só
a segunda é um número.

!!! tip "Use `format_currency_br` para prosa"
    [`tempest_fastapi_sdk.utils.format_currency_br`](br-helpers.md#dinheiro-em-real)
    existe para o texto que vai para um PDF, um e-mail ou uma página. Célula
    de planilha recebe número.

## Formatos brasileiros

| Constante | Renderiza | Para |
| --- | --- | --- |
| `BR_CURRENCY_FORMAT` | `R$ 1.234,56` | Dinheiro com símbolo |
| `BR_CURRENCY_FORMAT_NO_SYMBOL` | `1.234,56` | Coluna cujo cabeçalho já diz `(R$)` |
| `BR_QUANTITY_FORMAT` | `1.234,56` | Quantidade não monetária |
| `BR_INTEGER_FORMAT` | `1.234` | Contagem, número inteiro |
| `BR_PERCENT_FORMAT` | `30,00%` | Percentual |
| `BR_DATE_FORMAT` | `14/08/2026` | Data |
| `BR_DATETIME_FORMAT` | `14/08/2026 19:30` | Data e hora |
| `TEXT_FORMAT` | o que você escreveu | Identificador que parece número |

O que faz essas máscaras funcionarem é o código de idioma pt-BR, `[$-416]`,
no começo de **todas** elas — moeda, percentual e data inclusive. Ele fixa
o ponto como separador de milhar, a vírgula como decimal e a barra como
separador de data **dentro do arquivo**, então o documento não depende da
máquina de quem abre.

Medido abrindo a mesma planilha no LibreOffice 7.4 headless em três
locales:

| Constante | en-US | de-DE | pt-BR |
| --- | --- | --- | --- |
| `BR_CURRENCY_FORMAT` | `R$ 1.234,56` | `R$ 1.234,56` | `R$ 1.234,56` |
| `BR_PERCENT_FORMAT` | `30,00%` | `30,00%` | `30,00%` |
| `BR_DATE_FORMAT` | `14/08/2026` | `14/08/2026` | `14/08/2026` |
| `0.00%` (sem o código) | `30.00%` | `30,00 %` | `30,00%` |
| `DD/MM/YYYY` (sem o código) | `14/08/2026` | `14.08.2026` | `14/08/2026` |
| `[$R$-416] #,##0.00` | `R$ 1,234.56` | `R$ 1.234,56` | `R$ 1.234,56` |

!!! warning "A barra da data não é literal"
    Numa máscara de data, `/` é o separador de data **do locale**, não o
    caractere barra. Sem o `[$-416]`, quem abre em alemão lê `14.08.2026`.

!!! info "Por que a moeda não usa `[$R$-416]`"
    `[$R$-416]` é a forma "símbolo de moeda com idioma" e parece o jeito
    natural de escrever real. Não fixa os separadores: a última linha da
    tabela mostra o LibreOffice lendo `1,234.56` em en-US. Por isso
    `BR_CURRENCY_FORMAT` é `[$-416]"R$ "#,##0.00` — o código de idioma na
    frente e o símbolo como texto literal. O Excel não foi medido.

!!! danger "Percentual guarda a razão, não o percentual"
    O Excel multiplica por 100 sozinho. Uma célula com
    `BR_PERCENT_FORMAT` tem que receber `Decimal("0.30")`, não `30` —
    escrever 30 mostra `3000,00%`. Parece erro de digitação, mas é erro de
    unidade.

!!! tip "`TEXT_FORMAT` salva zeros à esquerda"
    CPF, número de processo (`0001/2026`), agência bancária. Sem ele o Excel
    normaliza para número e os zeros somem sem volta.

## As linhas que um documento tem

```python
from decimal import Decimal

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    Column,
    SheetWriter,
    new_workbook,
)

workbook = new_workbook("Orçamento")
writer = SheetWriter(
    workbook["Orçamento"],
    columns=[
        Column("Item", width=48),
        Column("Qtd.", width=10, horizontal="center"),
        Column("Valor", width=18, number_format=BR_CURRENCY_FORMAT),
    ],
)

writer.title_block(["ÓRGÃO", "Pregão 1/2026", "Anexo I"])  # mesclado, centralizado
first_item_row = writer.header_row()                       # cabeçalho da tabela
writer.group_row(["GRUPO 1 — ARTESANATO"])                 # subtítulo dentro da tabela
writer.write_row(["Item", 2, Decimal("10.00")])            # corpo
writer.total_row(["Total", None, Decimal("20.00")])        # destaque
writer.blank_rows(2)                                       # respiro
```

Todos devolvem **a próxima linha livre** — foi assim que `first_item_row`
ficou com a posição do primeiro item sem ninguém contar linha.

Uma célula `None` no meio da linha continua estilizada: é assim que a linha
de total pula as colunas do meio sem perder o preenchimento.

## Fórmulas vivas

Uma string começando com `=` vira fórmula de verdade:

```python
from tempest_fastapi_sdk.spreadsheet import Column, SheetWriter, new_workbook

workbook = new_workbook("Orçamento")
writer = SheetWriter(workbook["Orçamento"], [Column("Item"), Column("Valor")])
writer.write_row(["Soma conferida", "=SUM(B5:B24)"])
```

Vale a pena para as linhas de conferência. Um auditor que edita um valor vê
o número reagir, em vez de ler uma constante que era verdade só no instante
em que o arquivo foi gerado.

## Tema

`SheetStyle` é **dado puro** — cores em hexadecimal, tamanhos em inteiros,
nenhum objeto do `openpyxl`. Por isso o tema do seu projeto é definível,
testável e comparável sem o extra instalado.

```python
from tempest_fastapi_sdk.spreadsheet import SheetStyle, SheetWriter

CORPORATE = SheetStyle(
    header_background="0B3D2E",
    header_foreground="FFFFFF",
    group_background="D6E9DF",
    total_background="F3E5AB",
    border_color="C0C0C0",
    font_name="Calibri",
)
```

Passe no construtor: `SheetWriter(sheet, columns, style=CORPORATE)`.

!!! note "Cores seguem a convenção do openpyxl"
    `RRGGBB` ou `AARRGGBB`, **sem** `#` na frente.

## Servindo como download

Nada disso toca o disco: `workbook_to_bytes` devolve os bytes, e o handler
os entrega.

```python
from fastapi import APIRouter
from fastapi.responses import Response

from tempest_fastapi_sdk.spreadsheet import (
    XLSX_MEDIA_TYPE,
    new_workbook,
    workbook_to_bytes,
)
from tempest_fastapi_sdk.utils import build_content_disposition

router = APIRouter()


@router.get("/orcamentos/{budget_id}/planilha")
async def download_budget(budget_id: int) -> Response:
    """Stream the budget as an .xlsx download."""
    workbook = new_workbook("Orçamento")
    return Response(
        content=workbook_to_bytes(workbook),
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": build_content_disposition(
                f"orcamento-{budget_id}.xlsx",
            ),
        },
    )
```

`XLSX_MEDIA_TYPE` vem pronto do SDK. Não confie no palpite pela extensão:
a tabela embutida do Python não conhece `.xlsx`, e o `mimetypes` só acerta
quando o host tem `/etc/mime.types` — a `python:3.13-slim` não tem (detalhe
em [Downloads](downloads.md#content-type-sem-depender-da-imagem)).

!!! tip "Sem arquivo temporário, sem corrida"
    Duas requisições simultâneas escreveriam o mesmo caminho temporário. Em
    memória o problema não existe — e não sobra nada para limpar.

## Ler uma planilha do Google Sheets

Até aqui o caminho foi do código para a planilha. O inverso aparece cedo em
todo projeto: alguém mantém a tabela de preços ou o estoque **numa planilha
do Google**, e o serviço precisa ler essa tabela.

A versão feita à mão costuma ser assim: cortar o link em `/edit`, pescar o
`gid` do fragmento, montar `/export?format=csv&gid=...` e mandar para o
`pandas`. Funciona — até o dia em que não funciona, e por motivos que não
aparecem lendo o código:

* **O `#gid=` é fragmento.** O navegador nunca o envia ao servidor; se você
  repassar o link inteiro, a aba escolhida some no caminho.
* **O export responde com redirect.** O `docs.google.com` devolve `307`
  para um host `*.googleusercontent.com`. Um `httpx.AsyncClient` criado sem
  `follow_redirects` entrega esse `307` como se fosse a resposta.
* **Erro não vem com cara de erro.** Um ID que não existe responde `404` com
  uma página HTML; um `gid` que não é aba nenhuma responde `400`, também em
  HTML. Quem não confere o `Content-Type` acaba fazendo parse de HTML como
  se fosse dado.

`read_google_sheet_as` cuida das três coisas e ainda valida cada linha num
modelo Pydantic.

!!! info "Sem extra"
    A leitura usa só `httpx` (dependência base) e o módulo `csv` da
    biblioteca padrão. Não precisa do `[spreadsheet]` nem do `openpyxl`.

### O código

A planilha de exemplo é pública e tem três colunas: `item`, `valor` e
`tamanho`.

```python
# scripts/estoque.py

import asyncio

from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import read_google_sheet_as

SHEET_URL: str = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8/edit?usp=sharing"
)


class Produto(BaseModel):
    """Uma linha da planilha de estoque."""

    item: str
    valor: int | None = None
    tamanho: str


async def main() -> None:
    """Lê a planilha e imprime cada produto."""
    produtos: list[Produto] = await read_google_sheet_as(SHEET_URL, Produto)
    for produto in produtos:
        print(produto.item, produto.valor, produto.tamanho)


if __name__ == "__main__":
    asyncio.run(main())
```

Rodando (saída real, 2026-10-03):

```text
Bota forza 500 41
Bota new forza 1000 41
Macacao forza 1700 X
Protetor de coluna 300 uni
macacao dainese 1000 XL
Jaqueta x11 Masc 200 consultar
Jaqueta x11 Fem 200 consultar
Bota Forma 400 vendida
Luva x11 Fem L 250 M
Luva alpinestar Gp Pro L 250 M
Capacete Ls2 62 arrow*** None consultar
```

### Pedaço por pedaço

**O link.** Passe o link que o Google mostra no botão *Compartilhar*, do
jeito que ele vem. `google_sheet_export_url` aceita `/edit?usp=sharing`,
`?gid=` ou `#gid=`, o link sem `/edit`, o prefixo `/u/<n>/` de quem tem
várias contas, o link sem `https://` ou só o ID da planilha. Link que não é
de planilha levanta `ValueError` antes de qualquer requisição.

```python
from tempest_fastapi_sdk.spreadsheet import google_sheet_export_url

url: str = google_sheet_export_url(
    "https://docs.google.com/spreadsheets/d/abc123/edit?usp=sharing#gid=42"
)
assert url == (
    "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=42"
)
```

**A aba.** Cada chamada lê **uma** aba: a que o `gid` do link indica. Para
ler outra aba, abra-a no navegador e copie o link — o `gid` muda. Sem `gid`,
a URL não escolhe aba e a escolha fica com o Google (na planilha de exemplo,
que tem uma aba só, a resposta foi a mesma de `gid=0`).

**O schema.** O cabeçalho (linha 1) vira o nome dos campos: a coluna `item`
alimenta o campo `item`. Para um cabeçalho que não é identificador válido
(`Preço unitário`), use `Field(validation_alias="Preço unitário")`.

Repare nos dois tipos que fogem do óbvio:

* `tamanho: str`, e não `int`. A coluna mistura número e texto — `41`, `X`,
  `uni`, `consultar`. A planilha é digitada por gente, e o schema descreve o
  que ela **tem**, não o que você gostaria que ela tivesse.
* `valor: int | None = None`. A última linha tem a célula de valor vazia.
  Por padrão (`omit_blank=True`) célula vazia significa **ausente**: o campo
  cai no default, e um campo obrigatório reporta `missing`. Passe
  `omit_blank=False` para receber a string vazia no validador.

**O erro de linha.** Tipar `tamanho` como `int` falha — e o erro diz onde:

```python
import asyncio

from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import GoogleSheetRowError, read_google_sheet_as

SHEET_URL: str = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8/edit?usp=sharing"
)


class ProdutoComTamanhoNumerico(BaseModel):
    """Tipa o tamanho como número — e a planilha discorda."""

    item: str
    valor: int | None = None
    tamanho: int


async def main() -> None:
    """Mostra a linha que falhou."""
    try:
        await read_google_sheet_as(SHEET_URL, ProdutoComTamanhoNumerico)
    except GoogleSheetRowError as exc:
        print(exc.details["row"], exc.details["errors"][0]["input"])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
4 X
```

`details["row"]` é o número que a planilha mostra na lateral (cabeçalho = 1,
`Macacao forza` = 4), contando também as linhas em branco, que são puladas.
`details["errors"]` é a lista de erros do Pydantic daquela linha.

**Dicionários, sem schema.** `read_google_sheet` devolve
`list[dict[str, str]]`, com cada célula exatamente como o CSV traz (célula
vazia é `""`). Planilha só com cabeçalho, ou vazia, devolve `[]` — coleção
vazia é sucesso.

### Quando a planilha não abre

Qualquer resposta que não seja um `text/csv` de sucesso levanta
`GoogleSheetAccessError` (código `GOOGLE_SHEET_UNAVAILABLE`, status `502`).
Os `details` guardam o que veio de fato:

```json
{
  "detail": "The Google Sheet could not be downloaded. Check the link and share the sheet as 'Anyone with the link'.",
  "code": "GOOGLE_SHEET_UNAVAILABLE",
  "details": {
    "export_url": "https://docs.google.com/spreadsheets/d/<id-inexistente>/export?format=csv",
    "status_code": 404,
    "content_type": "text/html; charset=utf-8"
  }
}
```

Esse é o corpo que uma rota devolve com `register_exception_handlers` para
um ID que não existe (medido). Um `gid` que não corresponde a aba nenhuma
chega com `status_code: 400`. As duas exceções são `AppException`, então
dentro de uma rota viram o envelope de erro do SDK sem `try` nenhum; com um
`MessageCatalog` registrado, o `detail` sai traduzido.

!!! warning "Planilha privada: não medido"
    O comportamento de uma planilha **não compartilhada** não foi medido —
    só o `404` do ID inexistente e o `400` do `gid` inválido. Por isso o
    leitor não tenta adivinhar a causa: tudo que não é CSV vira o mesmo
    erro. Se for o seu caso, compartilhe como *Qualquer pessoa com o link*
    (leitor).

!!! tip "Reuse o cliente HTTP"
    Passe `client=` para reaproveitar um `httpx.AsyncClient` do serviço. O
    leitor segue o redirect por requisição (`follow_redirects=True` na
    chamada), então o seu cliente não precisa estar configurado para isso,
    e **nunca** o fecha. Sem `client=`, ele cria um com `timeout=30.0`
    (ajustável por `timeout=`) e fecha ao terminar. Falha de rede (timeout,
    DNS) sobe como `httpx.HTTPError`.

### Aba grande demais

Uma aba enorme, ou um link que aponta para algo maior do que você esperava,
não pode virar uma lista de milhões de `dict` na memória do serviço. Por
isso o caminho CSV tem dois limites, **ligados por padrão**:

| Parâmetro | Default | O que mede | Quando confere |
| --- | --- | --- | --- |
| `max_bytes` | `10 MiB` | bytes do corpo do export | durante o download, em streaming |
| `max_rows` | `100 000` | linhas de dados da aba (linha em branco não conta) | durante o parse, na primeira linha além do limite |

```python
# scripts/estoque_limite.py

import asyncio

from tempest_fastapi_sdk.spreadsheet import SpreadsheetTooLargeError, read_google_sheet

SHEET_URL: str = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8/edit?usp=sharing"
)


async def main() -> None:
    """Lê a mesma aba com um limite de linhas abaixo e outro acima do tamanho dela."""
    try:
        await read_google_sheet(SHEET_URL, max_rows=5)
    except SpreadsheetTooLargeError as erro:
        print(erro.status_code, erro.code, erro.details)
    linhas: list[dict[str, str]] = await read_google_sheet(SHEET_URL, max_rows=20)
    print(len(linhas), "linhas lidas")


if __name__ == "__main__":
    asyncio.run(main())
```

Rodando (saída real, 2026-10-04):

```text
413 SPREADSHEET_TOO_LARGE {'limit': 'rows', 'max': 5, 'actual': 6, 'sheet': None, 'row': 7}
11 linhas lidas
```

**Recusar, nunca truncar.** Passou de um limite, sai
`SpreadsheetTooLargeError` (`413`, código `SPREADSHEET_TOO_LARGE`) — o
mesmo erro dos leitores de `.xlsx`, descrito em
[Limites: zip bomb e planilha enorme](#limites-zip-bomb-e-planilha-enorme).
Nenhum leitor devolve "as primeiras linhas" em silêncio. No CSV,
`details["sheet"]` é `None`: o export não traz o nome da aba.

**O download.** O corpo é lido em streaming e contado. Um `Content-Length`
acima de `max_bytes` é recusado sem ler o corpo; sem o cabeçalho, a
transferência é fechada no primeiro pedaço que passa do limite.
`details["limit"]` sai `"download_bytes"`.

**As linhas.** O CSV é decodificado e parseado aos poucos, direto dos bytes
baixados: na linha `max_rows + 1` a leitura para, e o resto do corpo nunca
vira `str`, `list` nem `dict`. `details["row"]` é a linha da planilha em
que a leitura parou, contando as linhas em branco. Em
`read_google_sheet_as`, o limite é conferido antes de qualquer validação:
uma aba grande demais responde `413`, não o `422` da primeira linha
inválida.

**De onde vêm os defaults.** Medido no CPython 3.11, pico de RSS acima do
que o interpretador já ocupa, só o parse (corpo incluído), três execuções
por arquivo:

* Na aba de 8 colunas usada para medir o `.xlsx` (id, dois textos, três
  números, uma data, um status), cada linha custa **~94 bytes de CSV e
  ~1 014 bytes de memória**: uns **11 bytes de memória por byte de CSV**.
  100 000 linhas são 9 212 287 bytes e chegaram a 96,7 MB; 110 000 linhas
  (10 166 854 bytes, ainda abaixo de 10 MiB) pararam na linha 100 001.
  Nessa forma, quem dispara primeiro é o limite de linhas — a mesma divisão
  do `.xlsx`.
* O pior caso é uma aba de muitas células de dois caracteres: **~31,5 bytes
  de memória por byte de CSV**. 100 000 linhas de 34 células assim
  (10 300 127 bytes) chegaram a 309,4 MB. É por isso que o default do CSV é
  menor que os 32 MiB do `.xlsx`: o CSV não é comprimido, e cada célula vira
  um objeto `str` dentro de um `dict`.
* A planilha pública de 16 abas usada nesta página tem, na maior aba, 18 577
  bytes e 1 004 linhas de dados: lida aba por aba com os defaults (2 580
  linhas no total), nenhuma foi recusada.

**Subir ou desligar.** Os dois limites são por chamada, e `None` desliga
(`max_bytes=None`, `max_rows=None`). Os defaults são as constantes públicas
`DEFAULT_GOOGLE_CSV_MAX_DOWNLOAD_BYTES` e `DEFAULT_XLSX_MAX_ROWS` — a mesma
dos leitores de `.xlsx`. Valor zero ou negativo levanta `ValueError` antes
da requisição.

??? note "CSV ou .xlsx?"
    O CSV traz **uma aba** por requisição, escolhida pelo `gid`, e cada
    célula como **texto formatado** pelo locale da planilha (`"1.234,56"`,
    `"04/10/2026"`). Roda sem extra nenhum. O `.xlsx` traz **a pasta
    inteira** numa requisição, com as abas pelo nome, e cada célula com o
    **tipo** que a planilha guarda (número, data, booleano). Precisa do
    `[spreadsheet]`. Para uma aba de texto simples, o CSV basta; para várias
    abas, ou para colunas de valor e data, veja
    [A pasta inteira do Google numa requisição](#a-pasta-inteira-do-google-numa-requisicao).

## Ler um arquivo `.xlsx`

O caminho mais comum de planilha chegando ao serviço não é o Google: é o
usuário subindo um `.xlsx` num endpoint de importação. E a versão feita à
mão com `openpyxl` tropeça sempre nos mesmos lugares:

* **A linha em branco.** O usuário deixa uma linha vazia no meio. Pular é
  fácil; o difícil é continuar reportando o erro com o número que a
  planilha mostra na lateral, e não o índice da lista.
* **O arquivo que não é planilha.** Um CSV renomeado para `.xlsx`, um
  upload vazio. O `openpyxl` levanta `BadZipFile` ou `KeyError`, e o
  endpoint responde `500`.
* **A aba que não existe.** Pedir `workbook["Vendas"]` numa pasta cuja aba
  se chama `Planilha1` é outro `KeyError`, outro `500`.

`read_xlsx_as` lê uma aba, valida cada linha num modelo Pydantic e
transforma esses três casos em erros tipados.

!!! info "Extra necessário"
    A leitura de `.xlsx` usa o `openpyxl` do extra `[spreadsheet]`. Sem ele,
    o módulo importa normalmente, e a chamada levanta `ImportError` dizendo
    qual extra instalar.

### O código

O exemplo gera a planilha com o próprio `SheetWriter` (para rodar sem
arquivo nenhum) e lê de volta:

```python
# scripts/vendas.py

from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import (
    BR_CURRENCY_FORMAT,
    BR_DATE_FORMAT,
    Column,
    SheetWriter,
    new_workbook,
    read_xlsx_as,
    workbook_to_bytes,
)


class Venda(BaseModel):
    """Uma linha da aba Vendas."""

    data: date
    produto: str
    quantidade: int
    total: Decimal
    pago: bool


def gerar_planilha() -> bytes:
    """Monta a planilha que o usuário mandaria no upload."""
    workbook = new_workbook("Vendas")
    writer = SheetWriter(
        workbook["Vendas"],
        columns=[
            Column("data", number_format=BR_DATE_FORMAT),
            Column("produto"),
            Column("quantidade"),
            Column("total", number_format=BR_CURRENCY_FORMAT),
            Column("pago"),
        ],
    )
    writer.header_row()
    writer.write_row([date(2026, 10, 1), "Café", 2, Decimal("10.50"), True])
    writer.blank_rows()
    writer.write_row([date(2026, 10, 2), "Bolo", 1, Decimal("7.00"), False])
    return workbook_to_bytes(workbook)


def main() -> None:
    """Lê a aba Vendas e imprime cada venda."""
    vendas: list[Venda] = read_xlsx_as(gerar_planilha(), Venda, sheet="Vendas")
    for venda in vendas:
        print(venda.data, venda.produto, venda.quantidade, venda.total, venda.pago)


if __name__ == "__main__":
    main()
```

Rodando (saída real):

```text
2026-10-01 Café 2 10.5 True
2026-10-02 Bolo 1 7 False
```

E o endpoint de importação é o mesmo `read_xlsx_as`, com os bytes do
upload:

```python
# app/main.py

from datetime import date
from decimal import Decimal

from fastapi import FastAPI, UploadFile
from pydantic import BaseModel

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.spreadsheet import read_xlsx_as


class Venda(BaseModel):
    """Uma linha da aba Vendas."""

    data: date
    produto: str
    quantidade: int
    total: Decimal
    pago: bool


app: FastAPI = FastAPI()
register_exception_handlers(app)


@app.post("/vendas/importar")
async def importar_vendas(arquivo: UploadFile) -> list[Venda]:
    """Valida a planilha enviada e devolve as vendas lidas."""
    conteudo: bytes = await arquivo.read()
    return read_xlsx_as(conteudo, Venda, sheet="Vendas")
```

### Pedaço por pedaço

**A origem.** `read_xlsx_as` aceita `bytes` (o `await arquivo.read()` do
upload, o corpo de uma resposta HTTP), um caminho (`str` ou `Path`) ou um
arquivo binário aberto — `arquivo.file` do `UploadFile` também serve.

**A aba.** `sheet=` recebe o **nome** da aba ou a **posição** (a partir de
`0`, negativa vale). Sem `sheet=`, lê a primeira aba. O nome é comparado
como está: uma aba que aparece como `Setembro` pode se chamar `"Setembro "`,
com espaço no fim — o `SheetNotFoundError` lista os nomes reais em
`details["available"]`.

**O tipo da célula.** Esta é a diferença para o CSV: a célula chega com o
**valor** que a planilha guarda, não com o texto que ela mostra. Por isso
`total: Decimal` e `data: date` validam direto, sem desfazer `R$` nem
`dd/mm/aaaa`. O que chega, medido na exportação `.xlsx` de uma planilha
pública do Google com 16 abas:

| Na planilha | Chega como |
| --- | --- |
| número (inclusive moeda) | `int` ou `float` — `30.0` e `50` na mesma coluna |
| porcentagem `40%` | `float` com a **razão**: `0.4` |
| data `02/04/2025` | `datetime(2025, 4, 2, 0, 0)` |
| hora | `datetime.time` |
| booleano | `bool` |
| texto | `str`, com os espaços que tiver |
| célula vazia | `None` |

Duas consequências para o schema. Declare número como `float` ou
`Decimal`, não como `int`: o export escreve `30.0` e `50` na mesma coluna
(um campo `int` aceita `30.0`, mas recusa `30.5`). E coluna que mistura
número e texto, como um tamanho `41` ou `X`, vira `str | int`: o `.xlsx`
entrega `41.0` (float), que um campo `str` recusa — o schema do CSV não é
portável sem esse ajuste.

**Fórmula.** Vale o resultado **guardado** no arquivo. O export do Google
grava o resultado de toda fórmula: das 1 429 fórmulas da planilha medida,
as 975 que chegaram como `None` eram todas `IF(...; ""; ...)` com resultado
vazio. Um arquivo gerado pelo `openpyxl` (inclusive pelo `SheetWriter`)
**não** guarda resultado, então uma fórmula dele chega como `None` até
alguém abrir e salvar no Excel ou no LibreOffice.

**Linhas e cabeçalho.** As regras são as mesmas do CSV, porque o código é
o mesmo:

* a linha 1 é o cabeçalho, e o nome vale como está (`" NOME"` com o espaço);
* célula de cabeçalho vazia vira a chave `""`, e nome repetido fica com o
  valor da **última** coluna;
* linha em branco é pulada, mas conta na numeração;
* linha mais curta que o cabeçalho é completada com `None`; célula além da
  última coluna do cabeçalho é ignorada;
* célula mesclada guarda o valor só no canto superior esquerdo — as outras
  células do intervalo chegam `None`.

!!! warning "Espaço não é linha em branco"
    Só `None` e `""` contam como vazio; `0` e `False` são valor. Uma
    fórmula que responde `" "` (um espaço) mantém a linha: na planilha
    medida, 884 linhas de uma aba eram só isso. Se a sua planilha tem esse
    padrão, filtre as linhas sem o campo obrigatório antes de usar — ou
    deixe o `missing` do schema apontar a primeira.

**O erro.** Os três casos do começo viram `AppException` com status `422`,
e dentro de uma rota viram o envelope de erro do SDK. A resposta real do
endpoint acima, para cada um:

Um CSV enviado como `.xlsx`:

```json
{
  "detail": "The file is not a valid .xlsx spreadsheet.",
  "code": "SPREADSHEET_INVALID",
  "details": {"reason": "File is not a zip file"}
}
```

Uma pasta sem a aba `Vendas`:

```json
{
  "detail": "The spreadsheet has no sheet 'Vendas'.",
  "code": "SPREADSHEET_SHEET_NOT_FOUND",
  "details": {"sheet": "Vendas", "available": ["Planilha1"]}
}
```

Uma data digitada como texto (`"01/10/2026"`) na linha 2:

```json
{
  "detail": "Row 2 of sheet 'Vendas' failed validation.",
  "code": "SPREADSHEET_ROW_INVALID",
  "details": {
    "row": 2,
    "sheet": "Vendas",
    "errors": [
      {
        "type": "date_from_datetime_parsing",
        "loc": ["data"],
        "msg": "Input should be a valid date or datetime, invalid character in year",
        "input": "01/10/2026"
      }
    ]
  }
}
```

`SpreadsheetRowError` tem o mesmo contrato do `GoogleSheetRowError` do CSV
(`details["row"]` com cabeçalho = 1, `details["errors"]` do Pydantic) e
ainda nomeia a aba em `details["sheet"]`. `GoogleSheetRowError` é subclasse
dele, então um `except SpreadsheetRowError` cobre os dois leitores.

**Sem schema.** `read_xlsx` devolve `list[dict[str, XlsxCellValue]]` de uma
aba, e `read_xlsx_sheets` devolve todas, num `dict` pelo nome da aba, na
ordem das abas (aba de gráfico fica de fora). Aba vazia, ou só com
cabeçalho, devolve `[]`.

## Limites: zip bomb e planilha enorme

Um `.xlsx` é um ZIP. O tamanho do upload não diz nada sobre o tamanho que o
`openpyxl` vai descompactar e parsear — e é exatamente isso que um atacante
explora. Medido aqui: um arquivo de **1,7 MB** com uma `sheet1.xml` de
**505 MB** (5 milhões de linhas iguais) levou o leitor sem limites a
**2 278 MB** de RSS e 151 s de CPU; com a memória do processo limitada a
2 GB, terminou em `MemoryError` depois de 113 s. E uma planilha legítima com
milhões de linhas faz o mesmo, só que devagar.

Por isso os leitores de `.xlsx` têm três limites, todos **ligados por
padrão**:

| Parâmetro | Default | O que mede | Quando confere |
| --- | --- | --- | --- |
| `max_uncompressed_bytes` | `100 MiB` | soma do tamanho descompactado das partes do ZIP | antes de abrir, pelo diretório central |
| `max_compression_ratio` | `100` | descompactado ÷ compactado, por parte de pelo menos 1 MiB | antes de abrir, pelo diretório central |
| `max_rows` | `100 000` | linhas de dados **por aba** (linha em branco não conta) | durante a leitura, na primeira linha além do limite |

O mesmo arquivo de 1,7 MB agora responde em 0,1 s, com o pico de RSS do
processo em 126 MB (o import do `openpyxl` incluído), sem descompactar nada.

### O código

```python
# scripts/limites.py

from tempest_fastapi_sdk.spreadsheet import (
    SpreadsheetTooLargeError,
    new_workbook,
    read_xlsx,
    workbook_to_bytes,
)


def gerar_planilha(linhas: int) -> bytes:
    """Monta uma aba Vendas com o número de linhas pedido."""
    workbook = new_workbook("Vendas")
    aba = workbook["Vendas"]
    aba.append(["produto", "quantidade"])
    for numero in range(linhas):
        aba.append([f"Produto {numero}", numero])
    return workbook_to_bytes(workbook)


def main() -> None:
    """Lê a mesma planilha com um limite abaixo e outro acima do tamanho dela."""
    planilha: bytes = gerar_planilha(150)
    try:
        read_xlsx(planilha, max_rows=100)
    except SpreadsheetTooLargeError as erro:
        print(erro.status_code, erro.code, erro.details)
    linhas = read_xlsx(planilha, max_rows=200)
    print(len(linhas), "linhas lidas")


if __name__ == "__main__":
    main()
```

Rodando (saída real):

```text
413 SPREADSHEET_TOO_LARGE {'limit': 'rows', 'max': 100, 'actual': 101, 'sheet': 'Vendas', 'row': 102}
150 linhas lidas
```

### Pedaço por pedaço

**Recusar, nunca truncar.** Passar do limite levanta
`SpreadsheetTooLargeError`; nenhum leitor devolve "as primeiras 100 000
linhas" em silêncio. A leitura para na linha 100 001, e nenhuma linha depois
dela chega a virar `dict`.

**O erro.** `SpreadsheetTooLargeError` é subclasse de
`FileTooLargeException`, então responde `413` como o limite de upload do SDK
— e um `except FileTooLargeException` pega os dois. `details["limit"]` diz
qual limite foi:

* `"uncompressed_bytes"` e `"compression_ratio"`: `details["actual"]` é o
  que o ZIP declara; no da razão, `details["member"]` nomeia a parte;
* `"rows"`: `details["sheet"]` é a aba (`None` no caminho CSV) e `details["row"]` a linha da
  planilha em que a leitura parou;
* `"download_bytes"`: o download do Google (veja a próxima seção).

O endpoint de importação da seção anterior, sem mudar uma linha, responde
assim a uma zip bomb de 208 786 bytes:

```json
{
  "detail": "The spreadsheet decompresses to 209731887 bytes; the limit is 104857600.",
  "code": "SPREADSHEET_TOO_LARGE",
  "details": {
    "limit": "uncompressed_bytes",
    "max": 104857600,
    "actual": 209731887
  }
}
```

**De onde vêm os defaults.** Medido com `openpyxl` 3.1.5 no CPython 3.11,
uma execução por tamanho, numa aba gerada de 8 colunas (id, dois textos,
três números, uma data, um status):

* 200 000 linhas: 291,5 MB de pico de RSS, 15,6 s; 1 000 000 de linhas:
  959,1 MB, 78,1 s. Daí **~834 bytes de RSS e ~78 µs por linha**, sobre
  uns 125 MB que o interpretador e o `openpyxl` já ocupam. Com o default, a
  aba de 200 000 linhas parou na linha 100 001 em 10,8 s, com pico de
  214 MB.
* Cada linha ocupa ~404 bytes de XML, então a memória cresce **~2 bytes por
  byte de XML**: no teto de 100 MiB, algo perto de 215 MB de linhas
  (estimado pela conta, não medido). Numa aba estreita o
  limite de linhas dispara antes (100 000 linhas dão ~40 MB de XML); o de
  bytes é o que segura aba larga, muitas abas e tabela de textos grande.
* A razão de compressão de arquivo legítimo ficou entre 7,2 e 14,7 (a aba de
  200 000 linhas **idênticas**, o caso mais repetitivo); a da zip bomb foi
  294,5. `100` deixa mais de seis vezes de folga.
* A planilha pública de 16 abas usada nesta página tem 2 580 linhas e
  6,1 MB descompactada: passa com folga.

!!! info "Pior caso dentro dos defaults"
    Um arquivo montado para ficar logo abaixo dos dois limites de bytes
    (99 MiB descompactados, razão sob controle) e sem a tag `<dimension>`
    ainda custa: o `openpyxl` varre a aba inteira ao abrir quando a tag
    falta (o export do Google não a escreve), e a leitura para na linha
    100 001. Medido: **11,4 s e 248 MB de pico**. Se o seu endpoint não
    pode pagar isso, baixe os limites.

**O diretório central pode mentir.** Os tamanhos vêm do diretório central
do ZIP, que quem monta o arquivo escreve como quiser. A mentira não
passa porque o `zipfile` para de descompactar uma parte no tamanho
declarado e confere o CRC ali (medido no CPython 3.11 a 3.14): uma parte
que declara 4 KiB e guarda 50 MB lê 4 KiB e falha — CRC errado, ou XML
cortado no meio — e as duas viram `InvalidSpreadsheetError` (`422`). Uma
parte que declara **mais** do que tem é recusada pela soma.

**Subir o limite para planilha confiável.** Passe o valor que serve ao seu
caso, por chamada:

```python
# app/importacao.py

from tempest_fastapi_sdk.spreadsheet import (
    DEFAULT_XLSX_MAX_ROWS,
    XlsxCellValue,
    read_xlsx,
)


def ler_relatorio_interno(conteudo: bytes) -> list[dict[str, XlsxCellValue]]:
    """Lê o relatório que o próprio sistema gera, maior que um upload comum."""
    return read_xlsx(
        conteudo,
        max_rows=DEFAULT_XLSX_MAX_ROWS * 5,
        max_uncompressed_bytes=500 * 1024 * 1024,
    )
```

`None` desliga um limite (`max_rows=None`). Faça isso só para arquivo de
fonte que você controla: os três existem porque o upload é o caso comum. Os defaults são constantes públicas — `DEFAULT_XLSX_MAX_ROWS`,
`DEFAULT_XLSX_MAX_UNCOMPRESSED_BYTES`, `DEFAULT_XLSX_MAX_COMPRESSION_RATIO`
— para você escrever o limite como múltiplo deles. Valor zero ou negativo
levanta `ValueError`.

!!! tip "O limite do corpo da requisição continua valendo"
    Os limites do leitor medem o que o ZIP **vira**; o tamanho do upload em
    si é o limite de corpo da requisição, que fica antes, no
    [`BodySizeLimitMiddleware`](http.md). Os dois se completam.

## A pasta inteira do Google numa requisição

O CSV lê uma aba por requisição, e para isso você precisa do `gid` de cada
aba. Uma planilha de vendas com uma aba por mês são doze links, doze
requisições — e o valor chega como texto formatado.

`download_google_sheet_xlsx` baixa a pasta inteira **uma vez**, como
`.xlsx`; os leitores da seção anterior leem quantas abas quiser dos mesmos
bytes.

### O código

A mesma planilha pública de estoque do exemplo de CSV, agora pelo `.xlsx`:

```python
# scripts/estoque_xlsx.py

import asyncio

from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import download_google_sheet_xlsx, read_xlsx_as

SHEET_URL: str = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8/edit?usp=sharing"
)


class Produto(BaseModel):
    """Uma linha da planilha de estoque, lida do .xlsx."""

    item: str
    valor: int | None = None
    tamanho: str | int


async def main() -> None:
    """Baixa a pasta uma vez e lê a aba de estoque."""
    pasta: bytes = await download_google_sheet_xlsx(SHEET_URL)
    produtos: list[Produto] = read_xlsx_as(pasta, Produto, sheet="Página1")
    for produto in produtos:
        print(produto.item, produto.valor, repr(produto.tamanho))


if __name__ == "__main__":
    asyncio.run(main())
```

Rodando (saída real, 2026-10-04):

```text
Bota forza 500 41
Bota new forza 1000 41
Macacao forza 1700 'X'
Protetor de coluna 300 'uni'
macacao dainese 1000 'XL'
Jaqueta x11 Masc 200 'consultar'
Jaqueta x11 Fem 200 'consultar'
Bota Forma 400 'vendida'
Luva x11 Fem L 250 'M'
Luva alpinestar Gp Pro L 250 'M'
Capacete Ls2 62 arrow*** None 'consultar'
```

Para ver todas as abas sem schema, `read_google_sheet_xlsx` baixa e lê
tudo de uma vez:

```python
# scripts/abas.py

import asyncio

from tempest_fastapi_sdk.spreadsheet import XlsxCellValue, read_google_sheet_xlsx

SHEET_URL: str = (
    "https://docs.google.com/spreadsheets/d/"
    "1d6VsFORSnFrn3GY2MADbeQ2uuifv8alenn9LECDao8A/edit"
)


async def main() -> None:
    """Lê todas as abas numa requisição e conta as linhas de cada uma."""
    abas: dict[str, list[dict[str, XlsxCellValue]]] = await read_google_sheet_xlsx(
        SHEET_URL
    )
    for nome, linhas in abas.items():
        print(f"{nome!r}: {len(linhas)} linhas")
    print(abas["Abril"][0])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
'Itens': 1 linhas
'Eventos': 5 linhas
'Trocas': 14 linhas
'Produtos': 3 linhas
'Mar': 66 linhas
'Abril': 166 linhas
'Maio': 107 linhas
'Junho': 35 linhas
'Julho': 0 linhas
'Agosto': 99 linhas
'Setembro ': 1004 linhas
'Outubro': 999 linhas
'Compras': 14 linhas
'Novembro': 19 linhas
'Dezembro': 3 linhas
'Caixa': 45 linhas
{'Data': datetime.datetime(2025, 4, 1, 0, 0), 'Produto': 'Café ', 'Valor unitário': 1.0, 'Quantidade': 1.0, 'Total': 1, 'Forma de pagamento': 'Pix', 'Vendedor': 'Betania', 'Observação': None, '': None, 'Valor total': 233, 'Total de vendas': 230}
```

### Pedaço por pedaço

**Uma requisição.** As 16 abas vieram num `.xlsx` de 785 152 bytes; o
`read_google_sheet_xlsx` acima levou 2,8 s nesta máquina, download
incluído (uma medição). `Setembro ` e `Outubro` têm ~1 000 linhas porque
uma fórmula da coluna `Total` responde `" "` até a linha 1 000 — veja o
aviso de espaço na seção anterior.

**O `gid` é descartado.** Com `gid` na URL, o export `.xlsx` devolve
**só aquela aba** (medido: a mesma planilha com `&gid=0` veio com uma aba
só, 71 270 bytes). Por isso `download_google_sheet_xlsx` ignora o `gid` do
link e pede sempre a pasta inteira. Para escolher a aba, use `sheet=` na
leitura.

**As mesmas garantias do CSV.** O redirect `307` é seguido por requisição
(também no `client=` injetado, que nunca é fechado); `timeout=` vale para o
cliente criado. Qualquer resposta que não seja um `.xlsx` de sucesso
(`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) vira
`GoogleSheetAccessError`, status `502` — um ID inexistente respondeu `404`
`text/html` também neste formato (medido). E se o corpo vier com o media
type certo mas não abrir como planilha, `read_google_sheet_xlsx` também
responde `GoogleSheetAccessError`, não `422`: o defeito é do upstream, não
de quem chamou.

**O limite do download.** O corpo do export é lido em streaming e
contado: passou de `max_bytes` (default
`DEFAULT_GOOGLE_SHEET_MAX_DOWNLOAD_BYTES`, 32 MiB), a transferência é
fechada e sai `SpreadsheetTooLargeError` com `details["limit"] ==
"download_bytes"` — um `Content-Length` acima do limite é recusado sem ler
o corpo. A conta: a planilha pública de 16 abas comprime 7,7 vezes no
arquivo inteiro (785 152 bytes baixados, 6 072 058 descompactados — a
faixa de 7,2 a 14,7 de cima é por parte, não por arquivo); um `.xlsx` no
teto de 100 MiB descompactados com essa razão baixa uns 13 MiB, e 32 MiB
ainda o aceita comprimindo só 3,2 vezes. A planilha de 16 abas baixa 785 152 bytes.
`read_google_sheet_xlsx` aceita também `max_rows`,
`max_uncompressed_bytes` e `max_compression_ratio`, repassados ao leitor —
e o erro de tamanho sai como `SpreadsheetTooLargeError` (`413`), não como
`GoogleSheetAccessError`. O caminho CSV tem os mesmos `max_bytes` e
`max_rows`, com outro default de download — veja
[Aba grande demais](#aba-grande-demais).

**O extra.** `download_google_sheet_xlsx` só baixa bytes e roda sem extra;
`read_google_sheet_xlsx` precisa do `[spreadsheet]` e confere isso
**antes** do download, para não gastar a requisição.

## Recapitulando

* `new_workbook("Aba")` cria a pasta **sem** a aba fantasma do `openpyxl`.
* `Column` declara título, largura, máscara e alinhamento **uma vez**.
* `SheetWriter` segura o cursor: `title_block`, `header_row`, `group_row`,
  `write_row`, `total_row`, `blank_rows` — todos devolvem a próxima linha
  livre.
* Escreva **números**; a máscara apresenta. Texto pronto mata soma e filtro.
* As máscaras `BR_*` começam com o código `[$-416]`, então o arquivo lê
  igual em en-US, de-DE e pt-BR (medido no LibreOffice 7.4).
* `SheetStyle` é dado puro, então o tema não precisa do extra para existir.
* `workbook_to_bytes` entrega bytes — resposta HTTP, storage, e-mail.
* `read_google_sheet_as(link, Schema)` lê uma aba de uma planilha do Google
  compartilhada por link e valida cada linha; o erro aponta o número da
  linha. Sem extra. O download para em `max_bytes` (10 MiB) e o parse em
  `max_rows` (100 000), com o mesmo `SpreadsheetTooLargeError` (`413`).
* `read_xlsx_as(bytes, Schema, sheet="Aba")` lê uma aba de um `.xlsx`
  (upload, arquivo, export) com a célula **tipada**: número, `datetime`,
  `bool`. Arquivo que não é planilha, aba que não existe e linha inválida
  viram erro `422` tipado.
* Os leitores de `.xlsx` recusam zip bomb e planilha enorme **antes** de
  gastar a memória: `max_uncompressed_bytes` (100 MiB),
  `max_compression_ratio` (100) e `max_rows` (100 000 por aba) viram
  `SpreadsheetTooLargeError` (`413`). Nunca truncam; para arquivo confiável,
  suba o limite ou passe `None`.
* `download_google_sheet_xlsx(link)` baixa a pasta inteira do Google numa
  requisição (o `gid` é descartado), parando o download em `max_bytes`
  (32 MiB); `read_google_sheet_xlsx` já devolve todas as abas pelo nome.

Para gerar o mesmo conteúdo como documento fechado, veja
[Geração de PDF](pdf.md). Para os utilitários de moeda que formatam a prosa
do documento, veja [Helpers brasileiros](br-helpers.md).
