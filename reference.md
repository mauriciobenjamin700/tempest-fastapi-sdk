# Referência da API

Gerada automaticamente a partir das docstrings do SDK via [`mkdocstrings`](https://mkdocstrings.github.io/). Todo símbolo exportado no `__all__` de `tempest_fastapi_sdk` e dos seus submódulos públicos está documentado nas páginas abaixo — com assinatura completa, parâmetros, tipo de retorno e exceções levantadas. O código-fonte de cada símbolo fica no repositório e no pacote instalado; a página não o repete.

!!! info "Cobertura verificada, não prometida"
    `tests/test_reference_coverage.py` renderiza as páginas da referência e compara as âncoras emitidas com o `__all__` de cada módulo listado em `PUBLIC_MODULES`, então um símbolo novo nesses módulos que não chegue aqui quebra o `make check`.

    **`tempest_fastapi_sdk.integrations.*` fica fora dessa lista**, e por isso fora da verificação: as integrações são código gerado de uma especificação de terceiro, com centenas de símbolos por provedor (686 só no OpenPix), e o que garante que elas não driftam é o drift test de cada uma, não esta página. Os módulos de integração documentados na referência estão porque alguém os escreveu à mão.

    Três grupos ficam de fora **de propósito**, cada um com o motivo registrado no allowlist do teste: os aliases BR pré-0.76 sem sufixo `Field` (`CPF`, `CNPJ`, `CEP`, `CPFOrCNPJ`, `PhoneBR`) e `AsyncBrokerManager`, todos **deprecados** — documentá-los convidaria ao uso; e `Classifier` / `Detector` / `Segmenter` / `DetectClassify` / `DetectClassifyResults` / `fuse_detect_classify`, que são reexports do `ort-vision-sdk` e pertencem à documentação daquele projeto. Os schemas e mappers que o SDK escreve em volta deles — `DetectClassifySchema`, `to_detect_classify_schemas` — são nossos e estão documentados na referência.

!!! tip "Buscando"
    Use a barra de busca no topo da página (ou pressione `/`) para pular para um símbolo pelo nome. O índice full-text inclui as docstrings, então buscas como "soft delete" ou "request id" caem na classe certa.

## Páginas

Abra o tema e depois o módulo: cada tema é um índice, e cada módulo tem a própria página. Numa página por tema o HTML chegava a 22 MB (#878); por módulo, nenhuma passa de 2 MB.

| Página | O que documenta |
| --- | --- |
| **[Superfície de topo »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/top.md)** | Os nomes que `tempest_fastapi_sdk` reexporta na raiz (`from tempest_fastapi_sdk import X`), agrupados pela página do módulo que os documenta, cada um com link para a âncora dele. |
| **[Admin, dados e API »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/admin-data-api.md)** | O painel admin, a camada de dados (models, repository, sessão, migrations), a integração com o FastAPI, autenticação, autorização e sessões, as exceções, os schemas base e as settings. |
| **[Documentos e arquivos »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/documents-files.md)** | PDF, planilhas, slides, object storage e upload, exportação e apagamento de dados (LGPD), registro de artefatos e a prévia de compartilhamento. |
| **[Geração de cliente OpenAPI »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/openapi.md)** | Os geradores de cliente tipado: HTTP a partir de um documento OpenAPI (`tempest openapi-client`) e WebSocket a partir de um documento AsyncAPI. |
| **[IA e visão computacional »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision.md)** | Agentes, modelos generativos self-hosted, modelops (export, bench, quantização), visão computacional, reconhecimento facial e busca aproximada de texto. |
| **[Integrações e pagamentos »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/integrations-payments.md)** | O contrato canônico de pagamentos, os provedores (Mercado Pago, OpenPix, Stripe, RevenueCat), o backend de upload do Google Drive, a carteira com saque Pix, o gateway de WhatsApp e o relatório de erros de app. |
| **[Tempo real e mensageria »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging.md)** | Fila e tarefas, WebSocket e salas, SSE, Web Push e push unificado, chat, comentários, event sourcing, feature flags e MCP. |
| **[Web, UI e utilitários »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/web-ui-utils.md)** | Páginas SSR e a camada `ui`, utilitários, geolocalização, controle do host, system checks e as ferramentas de teste. |
