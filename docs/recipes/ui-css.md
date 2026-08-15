# CSS tipado (StyleSheet e tokens)

CSS escrito em Python, conferido pelo type checker e servido pelo próprio
app. Sem arquivo `.css` solto, sem build de frontend, sem CDN.

!!! tip "Quando usar"
    - Você precisa de **seletor, pseudo-classe ou media query** — coisas
      que estilo inline não expressa.
    - Você quer paleta, espaçamento e tipografia consistentes, com modo
      escuro, sem manter uma tabela de cores na mão.
    - Você quer que um nome de classe errado **falhe**, em vez de
      renderizar um elemento sem estilo.

    O `Style` do `tempest_core` continua sendo o certo para layout local
    de um widget. Esta receita é sobre a folha.

## Uma regra é um objeto

```python
from tempest_core import Style
from tempest_core.style import Edge

from tempest_fastapi_sdk.ui.css import Media, Rule, StyleSheet

sheet: StyleSheet = StyleSheet(
    rules=[
        Rule(".card", style=Style(padding=Edge.all(16), radius=8.0)),
        Rule(".card:hover", declarations={"cursor": "pointer"}),
        Media.min_width(768, [Rule(".card", declarations={"padding": "24px"})]),
    ],
)
print(sheet.to_css())
```

Sai exatamente o que você espera:

```css
.card {
  padding: 16px 16px 16px 16px;
  border-radius: 8px;
}

.card:hover {
  cursor: pointer;
}

@media (min-width: 768px) {
  .card {
    padding: 24px;
  }
}
```

Uma `Rule` recebe declarações de dois lugares, e a divisão é
intencional:

- **`style=`** — um `Style` tipado, convertido pela **mesma** função que
  o renderizador usa nos widgets. Regra e estilo inline com os mesmos
  valores emitem declarações idênticas.
- **`declarations=`** — mapa cru, para o que `Style` não modela:
  `display: grid`, `cursor`, `content`, e **toda** referência a token.

Vale também `layout="column"` / `"row"`, que aplica `display: flex` +
`flex-direction` do mesmo jeito que `Column` e `Row` fazem — assim
`gap`, `justify` e `align` não ficam inertes.

!!! warning "Cor em `Style` só aceita hexadecimal"
    `Style(color="var(--t-color-primary)")` levanta `invalid hex color`
    — medido contra o validador do `tempest_core`. Referência a token vai
    em `declarations`: `Rule(".btn", declarations={"color":
    theme.color("primary")})`.

## Design tokens, do `tempest_core` para o CSS

A paleta não é reinventada aqui: `ThemeTokens` adapta o `TokenSet` do
`tempest_core` (o mesmo que o cliente usa) para custom properties.

```python
from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, ThemeTokens

theme: ThemeTokens = ThemeTokens()
sheet: StyleSheet = StyleSheet(
    theme=theme,
    rules=[
        Rule(
            ".card",
            declarations={
                "padding": theme.space("md"),
                "border-radius": theme.radius("md"),
                "background": theme.color("surface"),
                "color": theme.color("on_surface"),
                "font-size": theme.font_size("body_medium"),
            },
        ),
    ],
)
```

Isso emite três blocos: `:root` com o esquema claro e todas as escalas,
`@media (prefers-color-scheme: dark)` (protegido para não vencer uma
escolha explícita por claro) e `:root[data-theme="dark"]` para o botão de
alternar. Escrever `theme.color("surface")` uma vez resolve os dois
modos.

Grupos disponíveis: `color` (39 papéis — `primary`, `on_surface`,
`error_container`, …), `space`, `radius`, `font-size`, `line-height`,
`font-weight`, `letter-spacing`, `duration`, `easing`.

Breakpoint é a exceção, e por um motivo: media query não lê `var()`.
Então ele volta como número:

```python
from tempest_fastapi_sdk.ui.css import Media, Rule, ThemeTokens

theme: ThemeTokens = ThemeTokens()
wide = Media.min_width(
    theme.breakpoint("lg"),
    [Rule(".sidebar", declarations={"display": "block"})],
)
```

Além de `min_width`, há `max_width`, `dark()` e `reduced_motion()`.

## Servindo a folha

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import make_css_router

app: FastAPI = FastAPI()
app.include_router(make_css_router(app_stylesheet(), path="/static/app.css"))
```

O CSS é renderizado **uma vez**, quando o router é construído — nenhuma
requisição paga o custo de percorrer as regras. A resposta leva um
`ETag` derivado do conteúdo, e um `If-None-Match` que bata recebe `304`
sem corpo.

Do lado da página, aponte o `<link>`:

```python
from tempest_core import Text

from tempest_fastapi_sdk.ssr import html_response

response = html_response(
    Text(content="Olá", tag="h1"),
    title="Início",
    stylesheets=["/static/app.css"],
)
```

## A folha pronta, e a sua por cima

`app_stylesheet()` compõe o que quase todo serviço quer: tokens, reset
mínimo, regras de formulário e regras de componente.

```python
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, ThemeTokens

theme: ThemeTokens = ThemeTokens()
own: StyleSheet = StyleSheet(
    reset=False,
    rules=[
        Rule(
            ".page-title",
            declarations={
                "margin": "0",
                "font-size": theme.font_size("headline_small"),
                "color": theme.color("on_background"),
            },
        ),
    ],
)
sheet: StyleSheet = app_stylesheet(theme=theme, extra=own)
```

As suas regras entram por último, então vencem no empate de
especificidade — a cascata normal. `merge()` faz o mesmo entre duas
folhas quaisquer, e `StyleSheet(reset=False)` desliga o reset.

## Nome de classe errado deve doer

Um typo em `class="crad"` não quebra nada: o elemento simplesmente
aparece sem estilo, e alguém descobre em produção. `StyleSheet.cls()`
transforma isso em erro na hora do render:

```python
from tempest_core import Column, Text

from tempest_fastapi_sdk.ui.css import Rule, StyleSheet, cls

sheet: StyleSheet = StyleSheet(rules=[Rule(".card", declarations={"padding": "16px"})])

Column(tag="section", attrs=sheet.cls("card"), children=[Text(content="oi")])
Column(tag="section", attrs=cls("card", "card--wide"), children=[])
```

`sheet.cls("crad")` levanta `KeyError` listando as classes que existem.
A função solta `cls()` não valida — use quando a classe vier de outra
folha (a de um design system externo, por exemplo).

Dá para levar isso ao teste, sem depender de disciplina:

```python
import re

from tempest_fastapi_sdk.ui import app_stylesheet


def test_page_uses_only_defined_classes(html: str) -> None:
    """Falha se a página usar uma classe que a folha não define."""
    used = {
        name
        for attribute in re.findall(r'class="([^"]+)"', html)
        for name in attribute.split()
    }
    assert used <= app_stylesheet().class_names()
```

Esse teste existe na suíte do próprio SDK, e achou duas classes sem
regra na primeira vez que rodou.

## Recap

- `Rule` + `Media` + `StyleSheet` cobrem seletor, pseudo-classe e media
  query — o que estilo inline não alcança.
- `style=` para valores tipados, `declarations=` para o resto e para
  todo token (`Style` só aceita cor hexadecimal).
- `ThemeTokens` traduz o token set do `tempest_core` em custom
  properties, com claro e escuro de uma vez.
- `make_css_router` serve a folha renderizada uma única vez, com `ETag`
  e `304`.
- `app_stylesheet()` já traz tokens, reset, formulários e componentes;
  suas regras entram por `extra=`.
- `sheet.cls(...)` transforma typo de classe em `KeyError`.

Veja também: [Camada UI »](ui.md) e
[Formulários a partir de schemas Pydantic »](ui-forms.md).
