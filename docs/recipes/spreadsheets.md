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

O que faz essas máscaras funcionarem é o código de idioma embutido —
`[$R$-416]` para moeda, `[$-416]` para o resto. Ele fixa o ponto como
separador de milhar e a vírgula como decimal **dentro do arquivo**, então o
documento lê igual em qualquer máquina.

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

## Recapitulando

* `new_workbook("Aba")` cria a pasta **sem** a aba fantasma do `openpyxl`.
* `Column` declara título, largura, máscara e alinhamento **uma vez**.
* `SheetWriter` segura o cursor: `title_block`, `header_row`, `group_row`,
  `write_row`, `total_row`, `blank_rows` — todos devolvem a próxima linha
  livre.
* Escreva **números**; a máscara apresenta. Texto pronto mata soma e filtro.
* As máscaras `BR_*` embutem o código `416`, então o arquivo lê igual em
  qualquer locale.
* `SheetStyle` é dado puro, então o tema não precisa do extra para existir.
* `workbook_to_bytes` entrega bytes — resposta HTTP, storage, e-mail.

Para gerar o mesmo conteúdo como documento fechado, veja
[Geração de PDF](pdf.md). Para os utilitários de moeda que formatam a prosa
do documento, veja [Helpers brasileiros](br-helpers.md).
