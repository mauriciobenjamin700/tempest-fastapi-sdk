# PDFs

Todo serviço acaba precisando emitir um documento: recibo, orçamento,
relatório, contrato, comprovante. O caminho comum é montar HTML na mão e
mandar para alguma biblioteca — e o que quebra nunca é a renderização, é o
resto: o total impresso não bate com as linhas, o valor por extenso saiu
errado, o cabeçalho da tabela some na página 2, o logo não carregou e ninguém
percebeu.

`tempest_fastapi_sdk.pdf` resolve o documento inteiro: payload tipado →
template Jinja2 → PDF, com paginação de verdade e formatação brasileira.

O caminho inverso também mora aqui: extrair o texto de um PDF
([Lendo um PDF de volta](#lendo-um-pdf-de-volta)) e receber um PDF pela API
para iterar página por página
([Lendo um PDF recebido pela API](#lendo-um-pdf-recebido-pela-api)).

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

### Comprovante: o QR code sai daqui

Ingresso, cobrança Pix, etiqueta de volume: o voucher quase sempre carrega um
QR code. Passe o **texto** e o SDK codifica:

```python
from tempest_fastapi_sdk.pdf import VoucherDocument

ingresso = VoucherDocument(
    heading="INGRESSO",
    subtitle="Show de sábado · 20h",
    fields={"Setor": "Pista", "Portador": "Ana Souza"},
    qr_content="https://example.com/v/8f3a2c1d",
    note="Apresente este QR code na entrada.",
)

print(ingresso.qr_image is not None)  # True
```

Você não escolhe correção de erro, escala nem margem — quem é dono do quadro
de `26mm` onde a imagem cai é o template do SDK, então a calibração mora ao
lado dele:

- **Correção de erro H** (recupera 30 % do símbolo). Voucher é impresso,
  dobrado e lido de cima de um vinco. Um Pix BR Code de 197 caracteres sai na
  versão 15, com módulo de `0,32mm`, e renderizado pelo WeasyPrint e
  rasterizado a 100 dpi ainda decodifica.
- **Margem de 2 módulos** dentro da imagem. O padrão pede 4; o resto vem do
  branco do próprio voucher, em vez de encolher cada módulo.
- **`image-rendering: pixelated`** no template, que faz o WeasyPrint gravar
  `/Interpolate false`: sem ele o PDF pede ao visualizador para suavizar a
  imagem.

Um conteúdo longo demais para qualquer versão de QR falha na **validação**,
como `ValidationError`, e não no meio da renderização.

Precisa de outra calibração, ou de um encoder seu? `qr_data_uri(...)` é
público, com os mesmos defaults como keyword-only, e o campo
`qr_data_uri` do documento aceita a imagem pronta:

```python
from tempest_fastapi_sdk.pdf import VoucherDocument, qr_data_uri

etiqueta = VoucherDocument(
    heading="VOLUME 3/5",
    qr_data_uri=qr_data_uri("PED-2026-0042/3", error="m"),
)
```

Os dois juntos levantam `ValidationError`: um é texto para o SDK codificar, o
outro é imagem já codificada, e não há resposta certa para qual vence.

!!! note "`segno` vem no extra `[pdf]`"
    Python puro, sem dependência de runtime e sem upper bound — não prende o
    lock de ninguém. É importado na primeira codificação.

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

### Uma `AssetPolicy` para todas as renderizações

A mesma instância — e a `AssetPolicy` que você passou para ela — atende todas
as renderizações em paralelo. Só as **regras** são compartilhadas: cada
renderização coleta as recusas numa cópia própria
(`AssetPolicy.for_render()`), então o `details["refused"]` de um
`AssetRefused` lista só as URLs do documento que **aquela** chamada pediu, e
com `strict_assets=False` cada renderização registra o próprio aviso.

```python
import asyncio

from tempest_fastapi_sdk.pdf import AssetPolicy, AssetRefused, PdfRenderer

policy = AssetPolicy()
renderer = PdfRenderer(assets=policy)


async def render(url: str) -> list[str]:
    try:
        await renderer.render_html(f'<html><body><img src="{url}"></body></html>')
    except AssetRefused as exc:
        return list(exc.details["refused"])
    return []


async def main() -> None:
    first, second = await asyncio.gather(
        render("http://a.test/logo.png?token=a"),
        render("http://b.test/logo.png?token=b"),
    )
    print(first)
    print(second)
    print(policy.refusals)


asyncio.run(main())
```

```text
['http://a.test/logo.png?token=a — remote assets are not allowed by this policy']
['http://b.test/logo.png?token=b — remote assets are not allowed by this policy']
[]
```

A última linha mostra a outra metade do contrato: a lista `refusals` do objeto
que você passou não é lida nem limpa pelo renderizador — as recusas chegam a
você pelo `AssetRefused` (ou pelo log, no modo leniente), nunca por ali.

!!! info "Por que não um lock"
    Até a 0.308.0 todas as renderizações liam e limpavam a mesma lista. Com
    duas em paralelo, o erro de uma podia carregar a URL da outra — e URL de
    asset costuma levar token na query string —, enquanto a recusa da dona
    sumia. Um lock só deixaria a troca síncrona; o que resolve é cada
    renderização ter o próprio coletor.

## Lendo um PDF de volta

Escrever é metade. A outra é ler — o primeiro passo de todo pipeline que
entrega um PDF para um modelo: uma nota para classificar, um contrato para
resumir, um edital para transcrever.

Isso mora num extra **separado**, `[pdf-read]`, que traz só `pypdf`:

```bash
uv add "tempest-fastapi-sdk[pdf-read]"
```

Separado de propósito. Renderizar puxa WeasyPrint mais Pango e fontconfig do
sistema; um serviço que só lê não deve carregar nada disso.

```python
from pathlib import Path

from tempest_fastapi_sdk.pdf import extract_pdf_text

dados: bytes = Path("nota.pdf").read_bytes()
texto: str = extract_pdf_text(dados)
print(texto)
```

Para um PDF de três páginas, a saída sai marcada:

```text
=== PAGE 1 ===
Recibo 001
Valor: R$ 1.234,56

=== PAGE 2 ===
Página dois
Assinatura

=== PAGE 3 ===
Página três
Anexo
```

O marcador não é enfeite: é o que dá ao modelo algo para citar quando você
pergunta de onde veio um valor. Troque pelo idioma do resto do prompt com
`page_marker=`:

```python
from pathlib import Path

from tempest_fastapi_sdk.pdf import extract_pdf_text

dados: bytes = Path("nota.pdf").read_bytes()
texto: str = extract_pdf_text(dados, page_marker="=== PÁGINA {page} ===")
```

### Página por página

Quando você quer decidir por página em vez de receber um texto só,
`extract_pdf_pages` devolve tuplas `(número, texto)` com numeração
**começando em 1**:

```python
from pathlib import Path

from tempest_fastapi_sdk.pdf import PageText, extract_pdf_pages

dados: bytes = Path("nota.pdf").read_bytes()
paginas: list[PageText] = extract_pdf_pages(dados)
for numero, texto in paginas:
    print(numero, texto[:40])
```

```text
1 Recibo 001
Valor: R$ 1.234,56
2 Página dois
Assinatura
3 Página três
Anexo
```

### Cortar sem mentir

`max_chars` limita o texto extraído e **anuncia** o corte:

```python
from pathlib import Path

from tempest_fastapi_sdk.pdf import extract_pdf_text

dados: bytes = Path("nota.pdf").read_bytes()
texto: str = extract_pdf_text(dados, max_chars=60)
```

A última linha passa a ser:

```text
=== DOCUMENT TRUNCATED AFTER PAGE 1 OF 3 ===
```

!!! check "`max_chars` é teto duro, e sempre volta texto (v0.252.0)"
    Três coisas mudaram, porque as três estavam erradas:

    | Antes | Agora |
    | --- | --- |
    | aviso e marcadores entravam **por cima** do limite (`max_chars=40` → 46 chars) | o resultado **nunca** passa de `max_chars` |
    | `max_chars` menor que a página 1 devolvia **só o aviso** | volta texto do documento, cortado dentro da página |
    | aviso dizia `PAGE 0`, página que não existe | a menor página citada é 1 |

    A ordem de preferência é: páginas inteiras enquanto couberem; se nem a
    primeira couber, corte dentro dela com
    `=== PAGE 1 OF 3 TRUNCATED MID-PAGE ===`; e se o orçamento não couber um
    aviso deixando ao menos **um terço** para texto, o orçamento inteiro vai
    para o texto — sem aviso e sem marcador de página, porque anotação que
    engole o conteúdo é o defeito que isto conserta.

    O aviso descreve o corte que **de fato** aconteceu, não o caminho que o
    código tomou: `MID-PAGE` só aparece quando o texto devolvido é prefixo
    estrito da página. Se a página 1 inteira couber, a linha é
    `=== DOCUMENT TRUNCATED AFTER PAGE 1 OF 3 ===` — medido num documento de
    3 páginas, `max_chars=120` devolve 106 caracteres nessa forma. E no
    orçamento que segura a página mas não esse aviso (100, no mesmo
    documento, onde a forma de fronteira precisa de 106), o corte tira um
    caractere para o `MID-PAGE` ficar verdadeiro: 58 dos 59 caracteres da
    página 1 com aviso valem mais que os 59 com as páginas 2 e 3 sumindo em
    silêncio.

    Medido numa página de 239 caracteres: até `max_chars=60` a resposta é
    texto puro do tamanho exato do orçamento; em `max_chars=61` o aviso passa
    a caber e a resposta traz 20 caracteres de documento mais o aviso; daí
    para cima o texto só cresce. O degrau para baixo acontece **uma vez** —
    no orçamento em que o aviso passa a caber — e é o preço de saber que
    falta metade. Um teste fixa isso: em nenhum outro ponto entre 1 e 300 um
    orçamento maior devolve menos documento.

    Se o seu fluxo dependia do comportamento antigo (retorno sempre em número
    inteiro de páginas), use `extract_pdf_pages` e monte você mesmo: ele
    devolve página por página, sem teto.

O corte silencioso é o perigoso: o modelo responde sobre a metade que viu, sem
sinal de que existe outra metade. Sobrescreva a frase com
`truncation_notice=` se o seu prompt está em outro idioma.

!!! danger "Camada de texto apenas — não há OCR aqui"
    Um PDF que saiu de um editor carrega o texto dele. Um PDF que é uma
    digitalização carrega imagem de página e mais nada. Nesse caso
    `extract_pdf_text` devolve **string vazia**, e `extract_pdf_pages` devolve
    uma entrada por página com texto vazio — medido: um PDF de uma página em
    branco dá `[(1, "")]` e `""`.

    Entregar prompt vazio a um modelo é como se inventa uma resposta confiante
    sobre uma página que ninguém leu. Checar é explícito:

    ```python
    from pathlib import Path

    from tempest_fastapi_sdk.pdf import extract_pdf_text

    dados: bytes = Path("nota.pdf").read_bytes()
    texto: str = extract_pdf_text(dados)
    if not texto:
        raise ValueError("PDF sem camada de texto — provavelmente é uma digitalização")
    ```

    Roteie esses arquivos para um caminho de OCR.

!!! info "Sem o extra, o erro diz o que instalar"
    Sem `pypdf`, a chamada levanta `ImportError` com a instrução completa:

    ```text
    pypdf is required to read PDFs. Install the extra:
    pip install "tempest-fastapi-sdk[pdf-read]"
    ```

## Lendo um PDF recebido pela API

`extract_pdf_text` e `extract_pdf_pages` respondem "me dá o texto" para quem
já tem os bytes na mão. Numa API, o PDF chega como upload, e o que você quer
devolver (ou passar adiante no seu código) é um **objeto**: quantas páginas,
o texto de cada uma, as tabelas, com um schema que o OpenAPI descreve.

É isso que `read_pdf_pages` faz. Ele recebe os bytes e devolve um
`PdfPagesResult`, que é um schema Pydantic do SDK:

| Campo | Tipo | O que é |
| --- | --- | --- |
| `extractor` | `PdfExtractorEnum` | qual leitor produziu o resultado (`text` ou `layout`) |
| `page_count` | `int` | número de páginas lidas |
| `pages` | `list[PdfPage]` | uma entrada por página, na ordem do documento |

E cada `PdfPage` traz `page_number` (começa em 1), `text` e `tables`.

### A rota

Instale os dois extras de leitura: `[pdf-read]` para o leitor de texto e
`[pdf-layout]` para o que recupera colunas e tabelas.

```bash
uv add "tempest-fastapi-sdk[pdf-read,pdf-layout,upload]"
```

```python
# main.py
from typing import Annotated

from fastapi import FastAPI, File, Form, UploadFile

from tempest_fastapi_sdk import BodySizeLimitMiddleware, register_exception_handlers
from tempest_fastapi_sdk.pdf import PdfExtractorEnum, PdfPagesResult, read_pdf_pages

app: FastAPI = FastAPI()
app.add_middleware(BodySizeLimitMiddleware, max_bytes=10 * 1024 * 1024)
register_exception_handlers(app)


@app.post("/api/documents/read", response_model=PdfPagesResult)
async def read_document(
    file: Annotated[UploadFile, File()],
    extractor: Annotated[PdfExtractorEnum, Form()] = PdfExtractorEnum.TEXT,
    password: Annotated[str | None, Form()] = None,
) -> PdfPagesResult:
    data: bytes = await file.read()
    return await read_pdf_pages(data, extractor=extractor, password=password)
```

Suba com `uv run uvicorn main:app` e mande um PDF de duas páginas:

```bash
curl -s http://127.0.0.1:8000/api/documents/read -F file=@contrato.pdf
```

```json
{
  "extractor": "text",
  "page_count": 2,
  "pages": [
    {"page_number": 1, "text": "Contrato 042\nValor total: R$ 1.234,56", "tables": []},
    {"page_number": 2, "text": "Anexo\nItem Qtd\nParafuso 10\nPorca 20", "tables": []}
  ]
}
```

Pedaço por pedaço:

- **`File()` e `Form()`.** O upload é `multipart/form-data`, então os campos
  ao lado do arquivo (`extractor`, `password`) vêm como `Form`, não como JSON.
  `[upload]` traz o `python-multipart`, que o FastAPI exige para ler esse
  formato.
- **`extractor` é o enum.** O FastAPI valida o valor: `extractor=ocr` volta
  `422` com `Input should be 'text' or 'layout'` antes de qualquer leitura.
- **`await file.read()`** entrega os bytes; `read_pdf_pages` faz o resto.
- **`read_pdf_pages` é `async`.** Os dois leitores são síncronos e pesados de
  CPU, então ele roda o trabalho numa thread (`asyncio.to_thread`) em vez de
  travar o event loop.
- **`response_model=PdfPagesResult`.** Como o retorno é schema do SDK, o
  OpenAPI da rota já descreve `PdfPagesResult`, `PdfPage` e `PdfExtractorEnum`,
  sem você declarar nada.
- **`BodySizeLimitMiddleware`** recusa o corpo grande antes de qualquer parse
  (detalhes abaixo).

!!! tip "PDF com tabela: `extractor=layout`"
    O mesmo arquivo com `-F extractor=layout` preenche `tables` na página 2:

    ```json
    {"page_number": 2, "text": "Anexo\nItem Qtd\nParafuso 10\nPorca 20", "tables": [[["Item", "Qtd"], ["Parafuso", "10"], ["Porca", "20"]]]}
    ```

    `text` é `pypdf`: rápido, prosa corrida, `tables` sempre vazio. `layout`
    é `pdfplumber`: mais lento, mas recupera colunas e devolve cada tabela
    como lista de linhas, cada linha como lista de células (`str`, nunca
    `None`).

### Iterando as páginas

Na rota acima o objeto vai direto para a resposta. Quando o seu serviço
precisa **fazer** algo com o documento, ele é um schema Pydantic comum:
itere `pages`.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk.pdf import PdfExtractorEnum, PdfPagesResult, read_pdf_pages


async def main() -> None:
    data: bytes = Path("contrato.pdf").read_bytes()
    result: PdfPagesResult = await read_pdf_pages(data, extractor=PdfExtractorEnum.LAYOUT)
    print(f"{result.page_count} páginas, leitor {result.extractor}")
    for page in result.pages:
        print(f"--- página {page.page_number} ---")
        print(page.text)
        for table in page.tables:
            header, *rows = table
            for row in rows:
                print(dict(zip(header, row, strict=True)))


asyncio.run(main())
```

```text
2 páginas, leitor layout
--- página 1 ---
Contrato 042
Valor total: R$ 1.234,56
--- página 2 ---
Anexo
Item Qtd
Parafuso 10
Porca 20
{'Item': 'Parafuso', 'Qtd': '10'}
{'Item': 'Porca', 'Qtd': '20'}
```

`page_number` é o número que um humano lê no rodapé, e a entrada `n` é sempre
a página `n`: página sem texto volta com `text=""` em vez de sumir da lista.

### Quando o PDF não colabora

Com `register_exception_handlers`, cada falha vira o envelope do SDK, com
`code` estável para o cliente decidir o que fazer:

| Situação | HTTP | `code` |
| --- | --- | --- |
| PDF protegido sem senha, ou com senha errada | `400` | `PDF_DECRYPT_FAILED` |
| arquivo que não é PDF | `422` | `PDF_EXTRACT_FAILED` |
| corpo acima de `max_bytes` | `413` | `REQUEST_BODY_TOO_LARGE` |
| `extractor` fora do enum | `422` | erro de validação do FastAPI |
| digitalização (sem camada de texto) | `200` | nenhum: `text` vem `""` |

```bash
curl -s http://127.0.0.1:8000/api/documents/read -F file=@contrato-protegido.pdf
```

```json
{"detail": "PDF is encrypted. Provide the correct password.", "code": "PDF_DECRYPT_FAILED", "details": {}}
```

Com `-F password=...` correto, a resposta é a mesma do PDF aberto. Para um
arquivo que não é PDF:

```json
{"detail": "Failed to extract PDF content.", "code": "PDF_EXTRACT_FAILED", "details": {"reason": "Stream has ended unexpectedly"}}
```

O `reason` vem do leitor e muda com ele: o mesmo arquivo com
`extractor=layout` diz `No /Root object! - Is this really a PDF?`. Decida
pelo `code`, nunca pelo texto do `reason`.

!!! info "Sem `register_exception_handlers`, o `code` some"
    O status continua certo, mas o corpo vira só
    `{"detail": "PDF is encrypted. Provide the correct password."}`. O cliente
    perde o `code` e passa a comparar mensagem.

!!! warning "Digitalização é `200` com texto vazio"
    O caso que **não** vira erro é o mais perigoso: um PDF escaneado é imagem
    sem camada de texto, e a resposta é `200` com `"text": ""` em cada
    página. Antes de mandar o resultado para um modelo, confira:

    ```python
    import asyncio
    from pathlib import Path

    from tempest_fastapi_sdk.pdf import PdfPagesResult, read_pdf_pages


    async def main() -> None:
        for name in ("contrato.pdf", "digitalizado.pdf"):
            result: PdfPagesResult = await read_pdf_pages(Path(name).read_bytes())
            without_text: list[int] = [
                page.page_number for page in result.pages if not page.text
            ]
            print(name, without_text)


    asyncio.run(main())
    ```

    ```text
    contrato.pdf []
    digitalizado.pdf [1]
    ```

    Páginas nessa lista vão para um caminho de OCR, que este módulo não tem.

!!! note "Por que o limite é middleware, e não um `if` na rota"
    Quando a sua função roda, o Starlette **já leu o upload inteiro**: o
    arquivo vai para um `SpooledTemporaryFile` (1 MiB em memória, o resto em
    disco). Um `if len(data) > ...` dentro da rota chega tarde. O
    `BodySizeLimitMiddleware` corta antes, pelo `Content-Length` ou contando
    os bytes em upload `chunked`, e responde `413`.

    O teto vale para o **corpo** da requisição, não para o arquivo: o
    multipart soma cabeçalhos e o campo `extractor`. Medido: um arquivo de
    exatamente 10 MiB é recusado com `max_bytes=10 * 1024 * 1024`. Deixe
    folga no teto. O middleware vale para o app inteiro; rota que aceita
    arquivo maior entra em `exclude_paths=` e aplica o próprio limite (veja a
    [receita de HTTP](http.md)).

## Recap

- `PdfRenderer` renderiza HTML, template ou documento tipado; sempre `async`.
- Cinco documentos prontos, cada um com schema Pydantic — totais calculados,
  campo faltando falha na validação.
- Relatório pagina de verdade: cabeçalho repetido, `página X de Y`, total geral
  só na última página.
- O voucher codifica o próprio QR code com `qr_content=`, calibrado para o
  quadro do template; `qr_data_uri(...)` fica público para outra calibração.
- `make_pdf_router` serve por HTTP; `dependencies=` é onde entram auth e rate
  limit.
- `tempest pdf render --html` é o laço rápido para ajustar template.
- O padrão de assets **nega tudo**; abrir é decisão explícita.
- Container precisa de Pango + fontconfig + uma fonte.
- `extract_pdf_text` / `extract_pdf_pages` leem de volta, no extra separado
  `[pdf-read]`. **Camada de texto apenas**: digitalização devolve `""`, e
  checar isso é por sua conta.
- `read_pdf_pages` devolve um `PdfPagesResult` tipado (`pages`, cada uma com
  `page_number`, `text` e `tables`): é o que uma rota de upload devolve, com
  `BodySizeLimitMiddleware` na frente e `register_exception_handlers` para o
  `code` de PDF protegido ou inválido.

Próximo: [E-mail transacional](email.md) para mandar o documento por anexo, ou
[Artefatos versionados](artifact-registry.md) se você precisa guardar cada
documento emitido com hash.
