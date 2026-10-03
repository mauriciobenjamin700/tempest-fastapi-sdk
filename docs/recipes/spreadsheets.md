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

E no sentido contrário, para ler a planilha que alguém mantém no Google,
veja [Ler uma planilha do Google Sheets](#ler-uma-planilha-do-google-sheets).

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

from tempest_fastapi_sdk.spreadsheet import new_workbook, workbook_to_bytes
from tempest_fastapi_sdk.utils import build_content_disposition

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

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
  "detail": "The Google Sheet could not be read as CSV. Check the link and share the sheet as 'Anyone with the link'.",
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

??? note "Por que só CSV"
    O export em CSV traz **uma aba** e nada de formatação — exatamente o que
    uma leitura de dados quer. O mesmo endpoint também responde
    `format=xlsx` (a pasta inteira), e `google_sheet_export_url` monta essa
    URL com `export_format="xlsx"`; ler o `.xlsx` baixado ainda não faz
    parte desta API.

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
  linha. Sem extra.

Para gerar o mesmo conteúdo como documento fechado, veja
[Geração de PDF](pdf.md). Para os utilitários de moeda que formatam a prosa
do documento, veja [Helpers brasileiros](br-helpers.md).
