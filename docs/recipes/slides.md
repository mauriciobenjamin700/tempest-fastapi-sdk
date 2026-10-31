# Slides, gabaritos e Markdown em PDF

Um serviço que ensina — ou que pede a um modelo de linguagem para montar uma
aula — acaba com um texto assim na mão:

```text
Lesson 1: Logic gates
What a gate is
A logic gate maps binary inputs to a single binary output.
```

E precisa entregar isso como apresentação, como PDF para imprimir, como roteiro
do apresentador. O caminho comum é um parser por formato, cada um com a sua
ideia do que é um tópico, e o PowerPoint acaba dizendo uma coisa e o PDF outra.

`tempest_fastapi_sdk.slides` lê o texto **uma vez**, num objeto tipado, e todo
formato sai desse mesmo objeto: HTML, PDF, PPTX, DOCX e Markdown. O mesmo vale
para gabarito (perguntas numeradas e respostas) e para Markdown livre virando
PDF, com a folha de estilo que você escolher — ABNT inclusa.

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[slides]"
    ```
    Traz `weasyprint`, `jinja2`, `python-pptx`, `python-docx` e `markdown`.
    O PDF usa o mesmo motor da [Geração de PDF](pdf.md), então **precisa das
    mesmas bibliotecas de sistema** — veja
    [Deploy](pdf.md#deploy-as-bibliotecas-de-sistema). O parser e os schemas
    importam sem o extra: cada motor é carregado no primeiro uso.

## Seu primeiro deck

```python
# scripts/aula.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import SlideDeck, SlideRenderer, parse_slides

SOURCE: str = """Lesson 1: Logic gates
What a gate is
A logic gate maps binary inputs to a single binary output.
Notes: ask the class for a real-world example first.
The gates you will meet
- NOT gate: inverts its only input, so 0 becomes 1.
- AND gate: outputs 1 only when every input is 1.
- OR gate: outputs 1 when at least one input is 1.
Conclusion
Every digital circuit is built from a handful of gates.
"""


async def main() -> None:
    """Parse the lesson and write it as HTML and PowerPoint."""
    deck: SlideDeck = parse_slides(SOURCE)
    renderer = SlideRenderer()
    Path("aula.html").write_text(renderer.html(deck), encoding="utf-8")
    Path("aula.pptx").write_bytes(await renderer.pptx(deck))
    for slide in deck.slides:
        print(slide.slide_number, slide.kind, slide.title)


if __name__ == "__main__":
    asyncio.run(main())
```

Rodando, sai isto:

```text
1 lesson_title Logic gates
2 content What a gate is
3 bullets The gates you will meet
4 conclusion Conclusion
```

Quatro slides, e cada linha do texto caiu num lugar:

- **`Lesson 1: Logic gates`** abre uma aula e vira a capa dela
  (`lesson_title`). O título do deck, quando você não passa `deck_title=`, é o
  da primeira aula.
- **Linha curta, com inicial maiúscula e sem pontuação no fim**
  (`What a gate is`) abre uma seção. O título da seção é o título do slide.
- **Parágrafo** abaixo de uma seção vira slide de conteúdo (`content`).
- **`Termo: descrição`** vira tópico, e os tópicos de uma seção dividem o
  mesmo slide (`bullets`). O traço na frente é opcional.
- **`Notes:`** é nota do apresentador, e se prende ao parágrafo ou tópico
  logo acima.
- **Seção cujo título contém `conclusion`** vira slide de conclusão.

`parse_slides` é síncrona: é CPU puro sobre uma string. Os renderizadores que
geram binário (`pdf`, `pptx`, `docx`) são `async` e rodam numa thread, para
um deck longo não travar o event loop.

!!! tip "O que não vira tópico"
    Uma frase com dois-pontos no meio passaria por tópico se a regra fosse só
    "tem `:`". Por isso o termo precisa começar com maiúscula, ter no máximo
    8 palavras e 80 caracteres, não ter `.,!?;` nem parêntese desbalanceado, e
    não conter verbo de frase (` is `, ` are `, ` means `, …); a descrição
    precisa de pelo menos 20 caracteres. `The result is: 42 after the second
    pass` continua parágrafo.

### Slide que não estoura

Uma seção com muito conteúdo vira mais de um slide, em vez de um slide com
fonte minúscula:

- **Mais de 6 tópicos** abrem outro slide com o mesmo título. Medido com 8
  tópicos numa seção: um slide com 6 e outro com 2.
- **Parágrafos** se agrupam até 700 caracteres por slide. Um parágrafo nunca é
  cortado ao meio: um parágrafo maior que o limite fica sozinho no slide dele.

## O vocabulário: o texto na língua do seu serviço

Os marcadores que o parser lê e os rótulos que os renderizadores imprimem não
são fixos. Ficam num `SlideVocabulary`, e o padrão é inglês. Um serviço que
escreve em português passa o próprio:

```python
from tempest_fastapi_sdk.slides import SlideDeck, SlideVocabulary, parse_slides

PT_BR: SlideVocabulary = SlideVocabulary(
    lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
    notes_pattern=r"^(?:Notas|Nota)\s*:\s*(.+)$",
    conclusion_marker="conclus",
    sentence_verbs=(" é ", " são ", " foi ", " foram ", " será ", " tem ", " significa "),
    default_lesson_title="Conteúdo",
    lesson_label="Aula {index}",
    conclusion_label="Conclusão",
    handout_subtitle="Roteiro do apresentador",
    handout_cover_note="Slide de capa.",
    notes_heading="Notas do apresentador",
    language="pt-BR",
)

TEXTO: str = """Aula 1: Portas lógicas
O que é uma porta
Uma porta lógica recebe entradas binárias e devolve uma única saída binária.
Notas: peça um exemplo do dia a dia antes de seguir.
As portas básicas
- Porta NOT: inverte a única entrada, então 0 vira 1.
- Porta AND: devolve 1 só quando todas as entradas valem 1.
Conclusão
Todo circuito digital é montado a partir de poucas portas.
"""

deck: SlideDeck = parse_slides(TEXTO, vocabulary=PT_BR)
for slide in deck.slides:
    print(slide.slide_number, slide.kind, slide.title, slide.subtitle)
```

```text
1 lesson_title Portas lógicas Aula 1
2 content O que é uma porta None
3 bullets As portas básicas None
4 conclusion Conclusão None
```

Duas partes, com papéis diferentes:

- **Marcadores** (`lesson_pattern`, `notes_pattern`, `conclusion_marker`,
  `sentence_verbs`, `ignored_lines`) decidem como o texto é **lido**. Os dois
  padrões são regex, comparados sem diferenciar maiúscula; `lesson_pattern`
  precisa de dois grupos (número e título), `notes_pattern` de um (o texto da
  nota). `conclusion_marker="conclus"` casa com `Conclusão` e `Conclusões`.
- **Rótulos** (`lesson_label`, `conclusion_label`, `handout_subtitle`,
  `notes_heading`, `slide_label`, `language`, …) decidem o que é
  **impresso**. `{index}` em `lesson_label` vira o número da aula; `language`
  vai para o `<html lang>`.

Sem o vocabulário, o mesmo texto em português sai torto — e sem erro nenhum,
porque nada ali é inválido, só não é o que você quis dizer. Medido com o
padrão inglês sobre o `TEXTO` acima:

```text
[('lesson_title', 'Content'), ('content', 'O que é uma porta'),
 ('bullets', 'O que é uma porta'), ('bullets', 'As portas básicas'),
 ('content', 'Conclusão')]
```

`Aula 1:` não abriu aula (a capa é a implícita, `Content`), a linha `Notas:`
virou tópico e a conclusão virou slide comum.

!!! warning "Passe o mesmo vocabulário para o renderizador"
    ```python
    from tempest_fastapi_sdk.slides import SlideRenderer, SlideVocabulary

    PT_BR: SlideVocabulary = SlideVocabulary(
        lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
        lesson_label="Aula {index}",
        conclusion_label="Conclusão",
    )
    renderer: SlideRenderer = SlideRenderer(vocabulary=PT_BR)
    ```
    O parser usa os marcadores; o renderizador, os rótulos. Um
    `SlideRenderer()` sem vocabulário imprime `Lesson 1` e `Conclusion` num
    deck lido em português. Crie o vocabulário uma vez, como constante do
    serviço, e passe aos dois.

Um padrão inválido falha na **construção**, longe do primeiro texto de
usuário, e a mensagem nomeia o campo:

```text
ValueError: lesson_pattern needs two groups: number and title
ValueError: lesson_pattern is not a valid regular expression: missing ), unterminated subpattern at position 0
```

## Todos os formatos

Um `SlideDeck`, cinco saídas:

| Formato | Chamada | Notas do apresentador | Tema |
| --- | --- | --- | --- |
| HTML | `renderer.html(deck, theme=...)` — síncrona, `str` | não | sim |
| PDF | `await renderer.pdf(deck, theme=...)` — `bytes` | não | sim |
| PPTX | `await renderer.pptx(deck, theme=...)` — `bytes` | no painel de notas | sim |
| DOCX | `await renderer.docx(deck)` — `bytes` | bloco por página | não |
| Markdown | `renderer.markdown(deck)` — síncrona, `str` | bloco `Note:` | não |

```python
# scripts/formatos.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import SlideDeck, SlideRenderer, SlideTheme, parse_slides


async def main() -> None:
    """Write one deck in every format."""
    deck: SlideDeck = parse_slides(Path("aula.txt").read_text(encoding="utf-8"))
    renderer = SlideRenderer(max_concurrent=2)
    Path("aula.html").write_text(renderer.html(deck), encoding="utf-8")
    Path("aula.md").write_text(renderer.markdown(deck), encoding="utf-8")
    Path("aula.pdf").write_bytes(await renderer.pdf(deck, theme=SlideTheme.DARK))
    Path("aula.pptx").write_bytes(await renderer.pptx(deck, theme=SlideTheme.DARK))
    Path("roteiro.docx").write_bytes(await renderer.docx(deck))


if __name__ == "__main__":
    asyncio.run(main())
```

O que cada um entrega, medido no deck de quatro slides do começo:

- **HTML** é um documento único, com o CSS embutido e nada buscado de fora —
  abre offline e cabe num `<iframe srcdoc>` de pré-visualização. Todo valor é
  escapado.
- **PDF** é esse mesmo HTML diagramado: uma página por slide, 960 × 540 pt
  (1280 × 720 px). Os quatro slides deram quatro páginas.
- **PPTX** sai em 16:9 (13,33 × 7,5 polegadas), um slide por slide, com a nota
  do apresentador no painel de notas — o slide 2 carregou
  `ask the class for a real-world example first.`, os outros nenhuma.
- **DOCX** é o roteiro do apresentador: capa com o título e
  `Presenter handout` (o `handout_subtitle`), depois uma página por slide com
  o rótulo da aula, o título, o conteúdo e um bloco `Speaker notes` — com a
  nota, ou com linhas em branco para escrever à mão. O rodapé é
  `Logic gates  ·  Slide 1 / 4`.
- **Markdown** é no formato do reveal.js, slides separados por `---`:

```text
<!-- Deck: Logic gates -->
<!-- Total slides: 4 -->

<small>Lesson 1</small>

# Logic gates

---

<small>Lesson 1</small>

## What a gate is

A logic gate maps binary inputs to a single binary output.

Note:
ask the class for a real-world example first.
```

`SlideTheme.DEFAULT` é texto escuro sobre branco com destaque azul;
`SlideTheme.DARK`, texto claro sobre fundo ardósia. `max_concurrent` limita
quantos PDF, PPTX e DOCX renderizam ao mesmo tempo nesse renderizador — cada
um é CPU numa thread, e sem teto uma rajada de pedidos ocupa todos os núcleos.

## Gabaritos

Gabarito é outro formato de texto — título, partes, perguntas numeradas e
respostas — com o seu próprio parser e renderizador:

```python
# scripts/gabarito.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import (
    AnswerKey,
    AnswerKeyRenderer,
    AnswerKeyVocabulary,
    parse_answer_key,
)

PT_BR: AnswerKeyVocabulary = AnswerKeyVocabulary(
    title_prefixes=("título:",),
    subtitle_prefixes=("subtítulo:",),
    answer_pattern=r"^(?:R|Resposta)\s*[:.]\s*(.*)$",
    default_title="Gabarito",
    answer_label="Resposta:",
    footer="Gabarito de uso do professor",
    language="pt-BR",
)

TEXTO: str = """Título: Portas lógicas — gabarito
Subtítulo: Turma 3B, 2026

Parte 1: Conceitos
1. O que faz uma porta NOT?
R: Inverte a entrada: 0 vira 1 e 1 vira 0.
2. Quando a porta AND devolve 1?
Resposta:
Só quando <b>todas</b> as entradas valem 1.

Parte 2: Aplicação
1) Qual porta implementa "pelo menos um"?
R: A porta OR.
"""


async def main() -> None:
    """Parse the answer key and write it as Markdown and PDF."""
    gabarito: AnswerKey = parse_answer_key(TEXTO, vocabulary=PT_BR)
    renderer = AnswerKeyRenderer(vocabulary=PT_BR)
    print(gabarito.title, "|", gabarito.subtitle, "|", gabarito.total_items)
    Path("gabarito.md").write_text(renderer.markdown(gabarito), encoding="utf-8")
    Path("gabarito.pdf").write_bytes(await renderer.pdf(gabarito))
    Path("gabarito.docx").write_bytes(await renderer.docx(gabarito))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Portas lógicas — gabarito | Turma 3B, 2026 | 3
```

E o `gabarito.md`:

```text
# Portas lógicas — gabarito

_Turma 3B, 2026_

## Parte 1: Conceitos

**1. O que faz uma porta NOT?**

> Resposta: Inverte a entrada: 0 vira 1 e 1 vira 0.

**2. Quando a porta AND devolve 1?**

> Resposta: Só quando todas as entradas valem 1.

## Parte 2: Aplicação

**1) Qual porta implementa "pelo menos um"?**

> Resposta: A porta OR.
```

As regras de leitura:

- **`1.` ou `1)`** abre uma pergunta, e a numeração fica no texto dela.
- **O marcador de resposta** (`answer_pattern`) abre a resposta, que segue até
  a próxima pergunta ou parte. A resposta pode começar na mesma linha
  (`R: A porta OR.`) ou na seguinte (`Resposta:` sozinho) — linhas várias são
  unidas com espaço.
- **Linha com inicial maiúscula que não termina como frase** abre uma parte —
  mas só fora de pergunta ou depois de linha em branco, para a segunda linha
  de uma pergunta quebrada continuar na pergunta.
- `title=` e `subtitle=` na chamada sobrepõem as linhas do texto.

`AnswerKeyRenderer` tem `html`, `markdown` (síncronas), `pdf` (A4) e `docx`
(`async`). `footer` imprime uma linha no fim do HTML e do PDF; `None`, o
padrão, não imprime nada.

!!! note "Tag HTML no texto é removida, em todo formato"
    Gabarito escrito por modelo às vezes chega com `<b>`. O schema guarda o
    texto como veio (`'Só quando <b>todas</b> as entradas valem 1.'`), e todo
    renderizador remove a tag antes de escrever — repare no Markdown acima.
    Depois disso o HTML ainda escapa o que sobrou. Uma resposta
    `<script>alert(1)</script>yes` sai `alert(1)yes`, como texto, nos quatro
    formatos.

## Markdown em PDF

O terceiro trabalho não tem parser: é Markdown livre — um relatório, um TCC,
um documento que um modelo escreveu — diagramado em PDF. A folha de estilo é
parâmetro:

```python
# scripts/tcc.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import ABNT_MARKDOWN_CSS, MarkdownPdf, MarkdownPdfRenderer

TEXTO: str = """# Introdução

Este trabalho descreve as portas lógicas.

| Porta | Saída |
| --- | --- |
| NOT | inverte |
"""


async def main() -> None:
    """Render a Markdown document with the ABNT stylesheet."""
    renderer = MarkdownPdfRenderer(stylesheet=ABNT_MARKDOWN_CSS)
    documento: MarkdownPdf = await renderer.render(TEXTO, title="TCC")
    Path(f"{documento.title}.pdf").write_bytes(documento.content)


if __name__ == "__main__":
    asyncio.run(main())
```

`render` devolve um `MarkdownPdf` com `content` (os bytes) e `title` — o seu,
ou `default_title` (`"Document"`) quando você não passa. Três folhas de
estilo possíveis:

- **`DEFAULT_MARKDOWN_CSS`** (o padrão): A4, sem serifa, número de página.
- **`ABNT_MARKDOWN_CSS`**: o leiaute da ABNT NBR 14724 para trabalho
  acadêmico — A4, margens de 3 cm em cima e à esquerda e 2 cm embaixo e à
  direita, Times New Roman 12 pt, entrelinha 1,5, texto justificado, recuo de
  1,25 cm na primeira linha, título de primeiro nível em maiúsculas. Medido:
  `# Introdução` sai `INTRODUÇÃO` no texto do PDF com essa folha, e
  `Introdução` com a padrão.
- **A sua**: qualquer CSS que o serviço mantém, passado em `stylesheet=`.

O Markdown liga tabelas, blocos de código cercados, notas de rodapé, sumário
(`toc`) e `attr_list`. Código ganha cor quando o Pygments está instalado e
fica sem cor quando não está.

!!! danger "Texto cru não busca nada"
    Markdown vindo de usuário é URL vinda de usuário: `![x](file:///etc/passwd)`
    leria o disco do servidor, e `![x](http://169.254.169.254/...)` faria uma
    requisição de dentro da sua rede. O `render` passa pela mesma
    [política de assets](pdf.md#seguranca-o-que-um-template-pode-carregar) da
    geração de PDF, sem nada liberado: as duas levantam `AssetRefused` (422,
    `PDF_ASSET_REFUSED`) e nenhum PDF sai. `data:` continua passando, porque
    carrega os próprios bytes — medido com uma imagem SVG em
    `data:image/svg+xml;base64,...`.

### Markdown com imagens: o arquivo `.zip`

Documento com imagem precisa levar a imagem junto. `render_archive` recebe um
`.zip` com o Markdown e os arquivos que ele referencia:

```python
# scripts/relatorio_zip.py

import asyncio
import io
import zipfile
from pathlib import Path

from tempest_fastapi_sdk.slides import MarkdownPdf, MarkdownPdfRenderer

SQUARE: str = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    '<rect width="10" height="10"/></svg>'
)


def build_archive() -> bytes:
    """Zip one Markdown file and the image it references."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("relatorio/relatorio.md", "# Com imagem\n\n![quadrado](img/q.svg)\n")
        archive.writestr("relatorio/img/q.svg", SQUARE)
    return buffer.getvalue()


async def main() -> None:
    """Render a zipped Markdown document with its image."""
    documento: MarkdownPdf = await MarkdownPdfRenderer().render_archive(build_archive())
    print(documento.title)
    Path(f"{documento.title}.pdf").write_bytes(documento.content)


if __name__ == "__main__":
    asyncio.run(main())
```

Imprime `relatorio`: sem `title=`, o título é o nome do arquivo Markdown sem
extensão. Como o arquivo é escolhido e o que ele pode ler:

- **O primeiro `.md` na ordem dos caminhos** é o documento. Com `a.md` e
  `b.md` no mesmo zip, vale o `a.md`.
- **Caminho relativo resolve a partir da pasta do Markdown** — `img/q.svg`
  acima é `relatorio/img/q.svg`.
- **Nada fora do zip extraído pode ser lido.** `![x](../../../../etc/passwd)`
  levanta `AssetRefused`: a política de assets desse render libera só o
  diretório de extração.

E o arquivo é conferido **antes** de qualquer byte ir para o disco, pelo
diretório central do zip. Cada recusa é um `MarkdownArchiveError` (422,
`MARKDOWN_ARCHIVE_INVALID`) com `details["reason"]`:

| `reason` | Quando |
| --- | --- |
| `not_a_zip` | os bytes não são um zip |
| `unsafe_member` | uma entrada cairia fora do diretório de extração (`../fora.md`, `/abs.md`) |
| `too_many_members` | mais entradas que `max_archive_members` (padrão `DEFAULT_MAX_ARCHIVE_MEMBERS`, 500) |
| `too_large` | o tamanho descompactado declarado passa de `max_archive_bytes` (padrão `DEFAULT_MAX_ARCHIVE_BYTES`, 50 MiB) |
| `no_markdown` | nenhum `.md` no zip |
| `not_utf8` | o Markdown não é UTF-8 |

`too_large` é o que segura a bomba de zip: medido, um zip de 221 bytes que
declara 100 000 descompactados é recusado com `max_archive_bytes=50_000`, sem
extrair. O nome da entrada recusada nunca vai no erro — é texto escolhido por
quem mandou o arquivo.

### Diagramas Mermaid

O WeasyPrint não roda JavaScript, então um bloco `` ```mermaid `` chegaria ao PDF
como código. Com `render_mermaid=True`, cada bloco é desenhado num Chromium
headless antes da diagramação:

```bash
uv add "tempest-fastapi-sdk[slides,slides-mermaid]"
uv run playwright install chromium
```

```python
# scripts/fluxo.py

import asyncio
from pathlib import Path

from tempest_fastapi_sdk.slides import MarkdownPdf, MarkdownPdfRenderer

FENCE: str = "`" * 3
TEXTO: str = f"# Fluxo\n\n{FENCE}mermaid\ngraph TD\n  Pedido-->Pagamento\n{FENCE}\n"


async def main() -> None:
    """Render a document whose Mermaid block becomes a drawing."""
    renderer = MarkdownPdfRenderer(
        mermaid_timeout=15.0,
        mermaid_error_message="Não foi possível desenhar este diagrama.",
    )
    documento: MarkdownPdf = await renderer.render(TEXTO, render_mermaid=True)
    Path("fluxo.pdf").write_bytes(documento.content)


if __name__ == "__main__":
    asyncio.run(main())
```

Medido com o Chromium instalado: o diagrama válido vira SVG embutido (`Pedido`
e `Pagamento` aparecem no texto do PDF), e um bloco inválido no mesmo
documento vira uma caixa com a mensagem e o código-fonte escapado. Um navegador
desenha todos os blocos do documento.

O diagrama que falha nunca derruba o documento. Sem Playwright, sem Chromium
(medido com o Playwright instalado e o navegador ausente), com sintaxe
inválida ou estourando o prazo (medido com `mermaid_timeout=0.001`), o bloco
vira a caixa `.mermaid-error` e o PDF sai do mesmo jeito. As duas folhas que o SDK ships
estilizam essa caixa e o `.mermaid-diagram`; uma folha sua que parta delas
mantém o diagrama dentro da página.

!!! warning "O script do Mermaid vem da internet"
    A página do Chromium carrega `mermaid@10.9.8` do jsDelivr a cada
    renderização. Essa requisição é do navegador, **fora** da política de
    assets do PDF — o servidor precisa de saída para
    `cdn.jsdelivr.net`. Medido apontando o script para um endereço que não
    responde: o diagrama vira caixa de erro. A versão é fixa,
    para um release novo do Mermaid não mudar o seu PDF sem aviso.

!!! info "Por que é um extra separado, e fora do `[all]`"
    O Playwright pede o download de um navegador (`playwright install
    chromium`) que um serviço que só gera deck não deveria carregar. Por isso
    `[slides-mermaid]` não vem no `[slides]` nem no `[all]`.

## Erros

Todos são `AppException`: deixe propagar da rota e o cliente recebe o envelope
do SDK, com `code`. Com `register_exception_handlers(app,
catalog=default_message_catalog())`, a frase sai na língua do
`Accept-Language` — as entradas existem em pt-BR e en-US.

| Exceção | Status | `code` | Quando |
| --- | --- | --- | --- |
| `SlideSourceEmptyError` | 422 | `SLIDES_SOURCE_EMPTY` | texto vazio ou só espaço (parser, `render`, Markdown vazio no zip) |
| `SlideSourceTooLargeError` | 413 | `SLIDES_SOURCE_TOO_LARGE` | texto maior que `max_chars` (padrão `DEFAULT_MAX_SOURCE_CHARS`, 200 000); `details={"limit": n}` |
| `MarkdownArchiveError` | 422 | `MARKDOWN_ARCHIVE_INVALID` | zip recusado; `details={"reason": ...}`, tabela acima |
| `AssetRefused` | 422 | `PDF_ASSET_REFUSED` | o Markdown referenciou algo fora do permitido; `details={"refused_count": n}` |

`max_chars=None` em `parse_slides`, `parse_answer_key` ou no
`MarkdownPdfRenderer` desliga o limite. Nenhum desses erros devolve o texto
recusado. No resto do SDK, o `AssetRefused` lista as URLs recusadas em
`details["refused"]`; vindo do Markdown, ele leva só `details={"refused_count":
n}` — a URL é o próprio texto do usuário ecoado de volta e, dentro de um zip,
revelaria o caminho do diretório temporário do servidor. O WeasyPrint para na
primeira recusa, então `n` é 1 na prática: medido com duas referências
recusadas no mesmo documento, `{"refused_count": 1}`.

## Servindo por HTTP

```python
# src/api/app.py

from fastapi import FastAPI, Response
from pydantic import Field

from tempest_fastapi_sdk.api import register_exception_handlers
from tempest_fastapi_sdk.exceptions import default_message_catalog
from tempest_fastapi_sdk.pdf import PDF_MEDIA_TYPE
from tempest_fastapi_sdk.schemas import BaseSchema
from tempest_fastapi_sdk.slides import (
    ABNT_MARKDOWN_CSS,
    MarkdownPdf,
    MarkdownPdfRenderer,
    SlideDeck,
    SlideRenderer,
    SlideTheme,
    SlideVocabulary,
    parse_slides,
)
from tempest_fastapi_sdk.utils import PPTX_MEDIA_TYPE, build_content_disposition

PT_BR: SlideVocabulary = SlideVocabulary(
    lesson_pattern=r"^Aula\s+(\d+)\s*:\s*(.+?)\s*$",
    notes_pattern=r"^(?:Notas|Nota)\s*:\s*(.+)$",
    conclusion_marker="conclus",
    lesson_label="Aula {index}",
    conclusion_label="Conclusão",
    language="pt-BR",
)

slides: SlideRenderer = SlideRenderer(vocabulary=PT_BR)
markdown_pdf: MarkdownPdfRenderer = MarkdownPdfRenderer(stylesheet=ABNT_MARKDOWN_CSS)


class SlideSourceSchema(BaseSchema):
    """O texto estruturado de uma aula."""

    text: str = Field(title="Texto", description="Fonte no formato Aula/seção/tópicos.")
    theme: SlideTheme = Field(default=SlideTheme.DEFAULT, title="Tema")


class MarkdownSourceSchema(BaseSchema):
    """Um documento Markdown."""

    text: str = Field(title="Texto", description="O Markdown do documento.")
    title: str | None = Field(default=None, title="Título")


app = FastAPI()
register_exception_handlers(app, catalog=default_message_catalog())


@app.post("/api/slides/pptx")
async def slides_pptx(payload: SlideSourceSchema) -> Response:
    """Devolve a aula como apresentação PowerPoint."""
    deck: SlideDeck = parse_slides(payload.text, vocabulary=PT_BR)
    content: bytes = await slides.pptx(deck, theme=payload.theme)
    return Response(
        content=content,
        media_type=PPTX_MEDIA_TYPE,
        headers={"Content-Disposition": build_content_disposition(f"{deck.title}.pptx")},
    )


@app.post("/api/markdown/pdf")
async def markdown_to_pdf(payload: MarkdownSourceSchema) -> Response:
    """Devolve o Markdown diagramado em PDF no padrão ABNT."""
    document: MarkdownPdf = await markdown_pdf.render(payload.text, title=payload.title)
    return Response(
        content=document.content,
        media_type=PDF_MEDIA_TYPE,
        headers={"Content-Disposition": build_content_disposition(f"{document.title}.pdf")},
    )
```

Medido com o `TestClient`:

```text
POST /api/slides/pptx {"text": "Aula 1: Portas lógicas\n..."}
200  application/vnd.openxmlformats-officedocument.presentationml.presentation
     attachment; filename="Portas lgicas.pptx"; filename*=UTF-8''Portas%20l%C3%B3gicas.pptx

POST /api/slides/pptx {"text": "   "}   (Accept-Language: pt-BR)
422  {"detail": "O texto de origem está vazio", "code": "SLIDES_SOURCE_EMPTY", "details": {}}

POST /api/markdown/pdf {"text": "# Introdução\n\nTexto.", "title": "TCC"}
200  application/pdf
     attachment; filename="TCC.pdf"; filename*=UTF-8''TCC.pdf
```

Pedaço por pedaço:

- **Os renderizadores são criados uma vez**, no módulo: guardam o vocabulário,
  a folha de estilo e o semáforo de `max_concurrent`, que só limita alguma
  coisa se for compartilhado entre os pedidos.
- **`PPTX_MEDIA_TYPE` / `DOCX_MEDIA_TYPE`** vêm de `tempest_fastapi_sdk.utils`,
  e `PDF_MEDIA_TYPE` de `tempest_fastapi_sdk.pdf`. Uma imagem
  `python:slim` não tem `/etc/mime.types`, e adivinhar pelo `mimetypes` serve o
  `.pptx` como `application/octet-stream` só em produção.
- **`build_content_disposition`** monta o header a partir do título, que é
  texto do usuário: tira caractere de controle, para uma quebra de linha não
  dividir a resposta, e manda o nome UTF-8 em `filename*`.
- **Nenhum `try`/`except`.** O erro do parser já é a resposta certa.

!!! warning "A rota não põe auth nem limite sozinha"
    Gerar PPTX e PDF é CPU, e o cliente segura a conexão até acabar. Ponha a
    autenticação e o rate limit do serviço nessas rotas — rota de documento
    sem isso é a que acaba pública.

??? info "Vindo do `slide-generator` do Leviathan"
    O módulo foi portado desse serviço. O que mudou no caminho:

    - **Escape no HTML.** O template do gabarito marcava pergunta e resposta
      com `| safe`, e um `<script>` no texto rodava na página que embutisse a
      pré-visualização. Aqui tudo é escapado, e a tag é removida antes.
    - **Política de assets.** O Markdown em PDF usava o fetcher padrão do
      WeasyPrint, que lia `file://` do host e fazia requisição a qualquer
      `http://`. Agora texto cru não busca nada e o zip lê só o que trouxe.
    - **Limites do zip.** Número de entradas e tamanho descompactado são
      conferidos antes de extrair.
    - **Margens ABNT.** O original tinha `margin: 3cm 2cm` — 3 cm em cima *e
      embaixo*, 2 cm dos lados. A NBR 14724 pede 3 cm em cima e à esquerda,
      2 cm embaixo e à direita, que é o que `ABNT_MARKDOWN_CSS` aplica.
    - **Mermaid fixo.** O original carregava `mermaid@10` (versão flutuante) e
      abria um navegador por diagrama; aqui é `mermaid@10.9.8` e um navegador
      por documento.
    - **Vocabulário.** Os marcadores eram só pt-BR e fixos no código; agora
      são o `SlideVocabulary` / `AnswerKeyVocabulary`, com padrão inglês.

## Recap

- `parse_slides` lê o texto num `SlideDeck`; `SlideRenderer` escreve HTML e
  Markdown (síncronos) e PDF, PPTX e DOCX (`async`, numa thread, com
  `max_concurrent`).
- Os marcadores e os rótulos moram no `SlideVocabulary` — inglês por padrão.
  Texto em português pede o seu, passado ao parser **e** ao renderizador.
- Seção grande vira mais de um slide: 6 tópicos ou 700 caracteres de
  parágrafo por slide, sem cortar parágrafo.
- `parse_answer_key` + `AnswerKeyRenderer` fazem o mesmo para gabarito; tag
  HTML no texto é removida em todo formato.
- `MarkdownPdfRenderer` diagrama Markdown com a folha que você passa —
  `DEFAULT_MARKDOWN_CSS`, `ABNT_MARKDOWN_CSS` ou a sua.
- Texto cru não busca nenhuma URL; o zip lê só o que trouxe e é conferido
  antes de extrair.
- Mermaid pede `[slides-mermaid]` + `playwright install chromium`; diagrama
  que falha vira caixa de erro, e o PDF sai.
- Erros são `AppException` com `code` — deixe propagar.

Próximo: [Geração de PDF](pdf.md) para documentos tipados (recibo, orçamento,
relatório), ou [Planilhas (.xlsx)](spreadsheets.md) quando quem recebe precisa
mexer nos números.
