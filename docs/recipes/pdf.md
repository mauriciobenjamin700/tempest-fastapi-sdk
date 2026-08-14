# Geração de PDF

Todo serviço acaba precisando emitir um documento: recibo, orçamento,
relatório, contrato, comprovante. O caminho comum é montar HTML na mão e
mandar para alguma biblioteca — e o que quebra nunca é a renderização, é o
resto: o total impresso não bate com as linhas, o valor por extenso saiu
errado, o cabeçalho da tabela some na página 2, o logo não carregou e ninguém
percebeu.

`tempest_fastapi_sdk.pdf` resolve o documento inteiro: payload tipado →
template Jinja2 → PDF, com paginação de verdade e formatação brasileira.

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[pdf]"
    ```
    Traz `weasyprint` + `jinja2`. **E precisa de bibliotecas de sistema** —
    veja [Deploy](#deploy-as-bibliotecas-de-sistema) antes de subir em
    container.

## Seu primeiro documento

```python
# scripts/recibo.py

import asyncio
from datetime import date

from tempest_fastapi_sdk.pdf import Party, PdfRenderer, ReceiptDocument


async def main() -> None:
    """Render a receipt to disk."""
    renderer = PdfRenderer()
    recibo = ReceiptDocument(
        number="0001/2026",
        issue_date=date(2026, 8, 13),
        issuer=Party(name="Acme Serviços LTDA", document="12345678000195"),
        payer=Party(name="Ana Souza", document="12345678901"),
        amount_cents=125000,
        reference="serviços de consultoria prestados em julho/2026",
        place="Recife",
    )
    pdf: bytes = await renderer.render_document(recibo)
    with open("recibo.pdf", "wb") as handle:
        handle.write(pdf)


if __name__ == "__main__":
    asyncio.run(main())
```

Sai um A4 com o valor em destaque, o texto de quitação, os blocos de emitente
e pagador, a data por extenso e a linha de assinatura — e o valor escrito
**por extenso**, que é o elemento que impede alterar a cifra depois de
assinado:

> Recebi de **Ana Souza**, inscrito(a) no CPF/CNPJ sob o nº 123.456.789-01, a
> importância de **R$ 1.250,00** (mil, duzentos e cinquenta reais), referente a
> serviços de consultoria prestados em julho/2026.

!!! tip "Valores em centavos, sempre"
    `amount_cents=125000` é R$ 1.250,00. É a mesma escolha que o SDK faz em
    pagamentos: `float` não representa `0,1 + 0,2`, e um documento que erra um
    centavo é pior do que um que falha.

## Os cinco documentos prontos

| Documento | Classe | Para quê |
| --- | --- | --- |
| `receipt` | `ReceiptDocument` | Recibo — comprovante de pagamento com valor por extenso e assinatura |
| `quote` | `QuoteDocument` | Orçamento / proposta com itens, subtotal, desconto e total |
| `report` | `ReportDocument` | Relatório tabular paginado, cabeçalho repetido e total geral |
| `contract` | `ContractDocument` | Contrato ou declaração com cláusulas numeradas e assinaturas |
| `voucher` | `VoucherDocument` | Comprovante curto ou etiqueta, meia página, com área de QR |

Cada um tem schema Pydantic próprio. É isso que faz valer a pena embarcar os
templates: um arquivo HTML sozinho não diz quais chaves ele espera, então o
primeiro campo faltando aparece como um espaço em branco num documento
assinado. Aqui um recibo sem pagador **não renderiza** — falha na validação,
com o campo nomeado.

### Orçamento: os totais são calculados, não recebidos

```python
from datetime import date

from tempest_fastapi_sdk.pdf import LineItem, Party, QuoteDocument

orcamento = QuoteDocument(
    number="ORC-2026-014",
    issue_date=date(2026, 8, 13),
    valid_until=date(2026, 9, 13),
    issuer=Party(name="Acme Serviços LTDA"),
    customer=Party(name="Ana Souza"),
    items=[
        LineItem(description="Consultoria técnica", quantity=40, unit="h", unit_price_cents=15000),
        LineItem(description="Licença anual", unit_price_cents=250000),
    ],
    discount_cents=50000,
    payment_terms="50% na assinatura, 50% na entrega.",
)

print(orcamento.subtotal_cents)  # 850000
print(orcamento.total_cents)     # 800000
```

`subtotal_cents` e `total_cents` são campos computados a partir dos itens — não
tem como passar um total que discorda das próprias linhas, que é o defeito que
ninguém pega olhando. Desconto maior que o subtotal é recusado na validação:
um total negativo seria impresso como se fosse preço.

Quantidade fracionária arredonda meio-para-cima no centavo, para a linha fechar
com o total, e imprime com vírgula (`2,5 mês`) — ponto lê como separador de
milhar para quem está segurando o papel.

### Relatório: o que faz uma listagem impressa ser utilizável

```python
from datetime import date

from tempest_fastapi_sdk.pdf import ReportColumn, ReportDocument

relatorio = ReportDocument(
    heading="Vendas por cliente",
    subtitle="Julho de 2026",
    generated_at=date(2026, 8, 13),
    columns=[
        ReportColumn(key="cliente", header="Cliente"),
        ReportColumn(key="pedidos", header="Pedidos", align="right"),
        ReportColumn(key="total_cents", header="Total", align="right", money=True),
    ],
    rows=[{"cliente": "Ana", "pedidos": 3, "total_cents": 125000}],
    totals={"total_cents": 125000},
)
```

Três coisas que o template garante e que só aparecem quando o relatório passa
de uma página:

- **O cabeçalho da tabela repete** em toda página. Uma página de continuação
  com colunas sem rótulo é ilegível.
- **A numeração é `página X de Y`.** Alguém precisa conseguir perceber que
  falta uma folha.
- **O total geral sai uma vez só, na última página.** A primeira versão disso
  usava `<tfoot>`, que é `table-footer-group` e portanto **repete** — o total
  aparecia no pé da página 2, acima de linhas que somavam outra coisa. Está
  fixado por teste que lê o texto das páginas renderizadas.

!!! note "Chave ausente imprime vazio"
    Uma linha sem a chave de uma coluna renderiza a célula em branco em vez de
    falhar. Relatório é montado de dado parcial com frequência, e derrubar o
    documento inteiro por uma célula ausente não ajuda ninguém.

## Servindo por HTTP

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.pdf import PdfRenderer, make_pdf_router

from src.api.dependencies.auth import require_user


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(
        make_pdf_router(
            PdfRenderer(),
            dependencies=[Depends(require_user)],
        ),
    )
    return app
```

Monta duas rotas:

- `GET /pdf/documents` — lista os documentos com o **JSON Schema completo** de
  cada payload, o suficiente para um cliente montar o formulário sem ler o
  código do SDK.
- `POST /pdf/documents/{document}` — valida e devolve os bytes com
  `Content-Disposition`.

!!! warning "O router não põe auth nenhuma sozinho"
    Renderizar é CPU-bound e o chamador segura a conexão. `dependencies=` é
    onde entram a autenticação e o rate limit do serviço — o router não chuta
    quais, porque chutar é como um endpoint de documento acaba público.

Nome de arquivo vindo do cliente passa por `safe_filename`: aspas encerram o
valor do header e quebra de linha divide a resposta em duas.

## O CLI: o laço de ajustar template

```bash
tempest pdf list                                    # o que existe
tempest pdf schema receipt                          # o payload que ele aceita
tempest pdf render receipt dados.json -o recibo.pdf
tempest pdf render receipt dados.json --html -o preview.html
```

`--html` para antes da diagramação e escreve o HTML — abre no navegador e
recarrega na hora. É o jeito rápido de iterar no layout antes de conferir como
ele realmente pagina.

`--template-dir` aponta para os templates do projeto, então dá para revisar as
suas próprias sobrescritas sem subir o serviço.

## Sobrescrevendo um template

O renderer procura primeiro no `template_dir` do projeto e cai nos embarcados —
mesma regra de sombreamento do `EmailUtils`. Para trocar só o recibo, crie um
`receipt.html` no seu diretório:

```html
{% extends "_base.html" %}

{% block heading %}RECIBO — {{ doc.issuer.name }}{% endblock %}

{% block content %}
<p>Recebi {{ doc.amount_cents | brl }} ({{ doc.amount_cents | extenso }})
   de {{ doc.payer.name }}.</p>
{% endblock %}
```

```python
from tempest_fastapi_sdk.pdf import PdfRenderer

renderer = PdfRenderer(template_dir="src/templates/pdf")
```

Os filtros disponíveis em qualquer template:

| Filtro | Entrada | Saída |
| --- | --- | --- |
| `brl` | `125000` | `R$ 1.250,00` |
| `extenso` | `125000` | `mil, duzentos e cinquenta reais` |
| `data` | `date(2026, 8, 13)` | `13/08/2026` |
| `data_extenso` | `date(2026, 8, 13)` | `13 de agosto de 2026` |
| `doc` | `"12345678901"` | `123.456.789-01` |
| `qtd` | `2.5` | `2,5` |

`_base.html` expõe os blocos `lang`, `doc_title`, `extra_head`, `header`,
`heading`, `subheading`, `header_meta` e `content`.

## Segurança: o que um template pode carregar

Este é o ponto que merece leitura atenta.

Um renderizador de HTML resolve URLs em nome da página: `<img src>`, `@import`,
`url()` no CSS. Aponte isso para um documento cujo conteúdo veio de um usuário
e viram dois bugs de uma vez — `file:///etc/passwd` lê o host, e
`http://169.254.169.254/` alcança o endpoint de metadados da nuvem de dentro da
sua rede.

**O padrão nega tudo.** `data:` passa sempre, porque carrega os próprios bytes
e não busca nada. Qualquer outra coisa precisa ser nomeada:

```python
from pathlib import Path

from tempest_fastapi_sdk.pdf import AssetPolicy, PdfRenderer

renderer = PdfRenderer(
    assets=AssetPolicy(allow_dirs=(Path("src/assets"),)),
)
```

A checagem é no caminho **resolvido**: nem `../` nem symlink apontando para
fora passam. Diretório inexistente falha na construção, porque um erro de
digitação leria como "nada é permitido" e só apareceria depois, como documento
sem imagem.

E a recusa é **ruidosa**: o padrão do WeasyPrint é registrar a falha e seguir,
o que transformaria um logo bloqueado num buraco silencioso na nota. O SDK
aborta a renderização na primeira recusa. `strict_assets=False` volta ao
comportamento leniente — e ainda assim registra em log o que foi descartado.

!!! danger "`allow_remote=True` é superfície de SSRF"
    Ligar isso significa que qualquer coisa que chegue no template controla uma
    requisição feita de dentro da sua rede. Prefira embutir a imagem como
    `data:` URI.

Por isso `Branding.logo_data_uri` só aceita `data:` — uma URL seria recusada na
renderização e produziria, em silêncio, um documento sem logo. E
`accent_color` / `page_size` / `margin` têm formato restrito: esses valores são
escritos **dentro da folha de estilo**, e um valor carregando `;` ou `}`
fecharia a regra e acrescentaria declarações próprias.

## Deploy: as bibliotecas de sistema

O WeasyPrint desenha texto via **Pango** e resolve fontes via **fontconfig**.
Uma imagem `python:slim` não tem nenhuma das duas, e o erro não aparece no
build — aparece na primeira renderização, como `OSError` vindo do cffi.

```dockerfile
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        fontconfig \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
```

`tempest generate --dockerfile` emite esse bloco automaticamente quando o
projeto fixa o extra `[pdf]` no `pyproject.toml`.

!!! note "Sobre a fonte"
    Medido em `python:3.13-slim`: `fonts-dejavu-core` chega **junto** com os
    pacotes do Pango, e o documento sai legível mesmo sem pedir a fonte
    explicitamente. Ela continua na lista de propósito — é a garantia de que a
    família nomeada na folha de estilo existe, e o custo é desprezível. Numa
    base que não puxe fonte nenhuma (distroless, por exemplo), o layout sai
    certo e cada glifo vira retângulo, que é a falha mais difícil de
    diagnosticar da lista.

!!! info "O erro não fala em Pango"
    Sem as bibliotecas, o import passa e a **primeira renderização** falha com
    `OSError: cannot load library 'libgobject-2.0-0'` — o nome que aparece é
    esse, não `pango`. Verificado em `python:3.13-slim`.

## Saída reproduzível (precisa de uma variável de ambiente)

O WeasyPrint não escreve data de criação nem identificador de documento, então
o PDF em si não carrega relógio. Mas a **fonte embutida** carrega: o subconjunto
de fonte gerado pelo `fontTools` grava um timestamp na tabela `head`, e o
checksum dela entra no arquivo. Duas renderizações do mesmo payload em segundos
diferentes produzem bytes diferentes.

Medido: três execuções do mesmo container, três hashes distintos; a diferença
está no checksum da `head` da fonte, dentro do stream comprimido.

O `fontTools` respeita a convenção `SOURCE_DATE_EPOCH` de builds reproduzíveis.
Com ela fixa, a saída passa a ser byte a byte idêntica entre processos:

```bash
SOURCE_DATE_EPOCH=1700000000 python -m src.server
```

```python
import asyncio
import hashlib

from tempest_fastapi_sdk.pdf import PdfRenderer, ReceiptDocument


async def digest(recibo: ReceiptDocument) -> str:
    """Hash a rendered receipt.

    Só é estável entre processos com ``SOURCE_DATE_EPOCH`` fixa no ambiente.
    """
    pdf: bytes = await PdfRenderer().render_document(recibo)
    return hashlib.sha256(pdf).hexdigest()
```

!!! warning "Não compare hash entre máquinas"
    Mesmo com `SOURCE_DATE_EPOCH`, o byte depende da **versão da fonte** e da
    versão do WeasyPrint. Um hash calculado na CI não bate com o de produção se
    as imagens diferirem. Para "esse documento é o mesmo que emiti antes",
    guarde o hash junto do artefato — não recalcule em outro ambiente
    esperando bater.

Passar `metadata=` (por exemplo `{"pdf_identifier": True}`) abre mão da
reprodutibilidade de propósito.

## Concorrência

Diagramar é CPU-bound. Toda renderização vai para uma thread de trabalho atrás
de um semáforo, então o loop de eventos nunca trava:

```python
from tempest_fastapi_sdk.pdf import PdfRenderer

renderer = PdfRenderer(max_concurrent=8)
```

O padrão é 4. Mais workers que núcleos vira fila, não vazão.

## Recap

- `PdfRenderer` renderiza HTML, template ou documento tipado; sempre `async`.
- Cinco documentos prontos, cada um com schema Pydantic — totais calculados,
  campo faltando falha na validação.
- Relatório pagina de verdade: cabeçalho repetido, `página X de Y`, total geral
  só na última página.
- `make_pdf_router` serve por HTTP; `dependencies=` é onde entram auth e rate
  limit.
- `tempest pdf render --html` é o laço rápido para ajustar template.
- O padrão de assets **nega tudo**; abrir é decisão explícita.
- Container precisa de Pango + fontconfig + uma fonte.

Próximo: [E-mail transacional](email.md) para mandar o documento por anexo, ou
[Artefatos versionados](artifact-registry.md) se você precisa guardar cada
documento emitido com hash.
