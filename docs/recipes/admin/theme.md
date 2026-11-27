---
description: "Use para trocar cores, logo, favicon, fonte, raio, rodapé e modo escuro do admin por parâmetros tipados do `AdminTheme`."
---

# Customizar a aparência — `AdminTheme`

Use para mudar a aparência do painel (cores, logo, favicon, fonte, modo escuro) sem forkar a folha de estilo, passando um `AdminTheme`.

Parte da receita [Painel admin](../admin.md).

O CSS do admin é todo dirigido por **CSS custom properties** em `:root`. Em vez de forkar a folha de estilo, você passa um `AdminTheme` com **parâmetros tipados e documentados** — cores, logo, favicon, fonte, raio, rodapé, modo escuro — e a SDK injeta um bloco `<style>` no `<head>` (depois do `admin.css`, então ele vence).

```python
# src/admin/site.py
from tempest_fastapi_sdk import AdminSite, AdminTheme

theme: AdminTheme = AdminTheme(
    accent="#7c3aed",                       # cor primária (links, botões, item ativo)
    accent_hover="#6d28d9",                 # tom de hover do accent
    header_bg="#1e1b4b",                    # fundo do header/sidebar (texto fica claro sozinho)
    radius="10px",                          # raio de botões, inputs, cards, tabelas
    font_family="'Inter', system-ui, sans-serif",
    logo_url="/admin/static/logo.svg",      # imagem no header (no lugar do texto)
    favicon_url="/admin/static/favicon.ico",
    footer_text="Servus | 2026",
    dark_mode=False,                         # superfícies escuras
)

site: AdminSite = AdminSite(title="Servus Admin", brand="Servus", theme=theme)
```

`AdminTheme()` sem argumentos é um **no-op**: reproduz a aparência padrão. Você só define o que quer mudar.

!!! tip "A regra de ouro"
    Cada campo do `AdminTheme` mapeia para uma variável CSS de `:root` (ou
    para um pedaço de chrome, como o logo). É tudo tipado — o autocomplete
    do editor lista as opções e o mypy valida — e nenhuma string precisa
    ser um nome de classe CSS ou seletor.

| Campo | Tipo | Padrão | Efeito |
|-------|------|--------|--------|
| `accent` | `str` | `"#2563eb"` | Cor primária: links, botões, item ativo da sidebar |
| `accent_hover` | `str` | `"#1d4ed8"` | Tom de hover/ativo do `accent` |
| `danger` | `str` | `"#b91c1c"` | Ações destrutivas e mensagens de erro |
| `header_bg` | `str` | `"#ffffff"` | Fundo do header |
| `sidebar_bg` | ``str | None`` | `None` | Fundo da sidebar (cai pra `header_bg`) |
| `header_fg` | ``str | None`` | `None` | Cor do texto do header e da sidebar (`None` escolhe pelo contraste do fundo) |
| `page_bg` | ``str | None`` | `None` | Fundo do conteúdo (padrão do modo) |
| `radius` | `str` | `"8px"` | Raio de botões, inputs, cards, tabelas |
| `font_family` | ``str | None`` | `None` | `font-family` do painel inteiro |
| `logo_url` | ``str | None`` | `None` | Imagem no header em vez do texto |
| `logo_alt` | `str` | `"Logo"` | `alt` da imagem do logo |
| `favicon_url` | ``str | None`` | `None` | Favicon da aba |
| `footer_text` | `str` | `"Powered by tempest-fastapi-sdk"` | Texto do rodapé |
| `dark_mode` | `bool` | `False` | Superfícies escuras (e o chrome, se `header_bg` for o padrão) |
| `custom_css_url` | ``str | None`` | `None` | Folha de estilo extra, linkada por último |

!!! info "Modo escuro"
    `dark_mode=True` troca as **superfícies de conteúdo** (fundo da página,
    cards, texto, linhas da tabela, inputs, bordas) para uma paleta escura.
    O header e a sidebar escurecem junto enquanto `header_bg` for o padrão
    claro; um `header_bg` seu é mantido. `accent` e as outras cores
    continuam valendo. Um `page_bg` explícito vence o modo escuro do
    conteúdo (as superfícies ficam claras), mas o chrome padrão escurece
    mesmo assim.

!!! info "Texto do header escolhido pelo fundo"
    O header e a sidebar são claros por padrão. Com um `header_bg` (ou
    `sidebar_bg`) em hex escuro, o texto deles passa para claro sozinho —
    a SDK calcula a luminância do fundo. Cor que não é hex
    (`"rebeccapurple"`, `rgb(...)`) é tratada como escura; se o palpite
    errar, fixe a cor com `header_fg`.

!!! warning "Escape hatch para o resto"
    Para o que os campos não cobrem, aponte `custom_css_url` para a sua
    própria folha de estilo. Ela é linkada **depois** do tema, então
    sobrescreve tudo — inclusive o `AdminTheme`.

!!! danger "Valores são do desenvolvedor, não do usuário final"
    Os caracteres `< > { } "` são rejeitados em qualquer campo de texto
    (`ValueError` na construção), porque quebrariam o `<style>` injetado ou
    um atributo HTML. Nunca derive valores de `AdminTheme` de entrada de
    usuário final.

**Recap:** instancie `AdminTheme` com os campos que quer mudar, passe via
`AdminSite(theme=...)`, e a aparência muda em todas as páginas (login,
dashboard, list, detail, forms) sem tocar em CSS. Para customização total,
`custom_css_url`.
