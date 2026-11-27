# Referência: Web, UI e utilitários

Páginas SSR e a camada `ui`, utilitários, geolocalização, controle do host, system checks e as ferramentas de teste.

[« Índice da referência](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`checks`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/checks.md) | System checks estilo Django: validação de configuração no startup. |
| [`geo`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/geo.md) | Geolocalização sem API paga: distância, rotas, geometria e geocoding. |
| [`hostbridge`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/hostbridge.md) | Controle da máquina em que o serviço roda: energia, arquivos, PDFs e comandos. |
| [`ssr`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ssr.md) | Renderização no servidor: componentes Python tipados renderizados para HTML. |
| [`testing`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/testing.md) | Helpers de teste para quem consome o SDK: app, banco, cliente e fixtures. |
| [`testing.fakes`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/testing-fakes.md) | Dublês dos terceiros com que um serviço conversa. |
| [`ui`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ui.md) | A camada `ui`: páginas, componentes, formulários e CSS tipados, tudo em Python. |
| [`ui.components`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ui-components.md) | Componentes: as peças reutilizáveis de que as páginas são feitas. |
| [`ui.css`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ui-css.md) | CSS tipado: stylesheets, design tokens e a rota que os serve. |
| [`ui.forms`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ui-forms.md) | Formulários gerados de schemas Pydantic e lidos de volta para eles. |
| [`ui.layout`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ui-layout.md) | Layout: os contêineres estruturais em que uma página organiza o conteúdo. |
| [`ui.pages`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/ui-pages.md) | Páginas: uma classe por tela, composta de componentes. |
| [`utils`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils/utils.md) | Utilitários: validação BR, senha, HTTP client, retry, upload, datas e formatação. |
