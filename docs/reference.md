# Referência da API

Gerada automaticamente a partir das docstrings do SDK via [`mkdocstrings`](https://mkdocstrings.github.io/). Todo símbolo exportado no `__all__` de `tempest_fastapi_sdk` e dos seus submódulos públicos está documentado nas páginas abaixo — com assinatura completa, parâmetros, tipo de retorno, exceções levantadas e link para o código-fonte.

!!! info "Cobertura verificada, não prometida"
    `tests/test_reference_coverage.py` renderiza as páginas da referência e compara as âncoras emitidas com o `__all__` de cada módulo listado em `PUBLIC_MODULES`, então um símbolo novo nesses módulos que não chegue aqui quebra o `make check`.

    **`tempest_fastapi_sdk.integrations.*` fica fora dessa lista**, e por isso fora da verificação: as integrações são código gerado de uma especificação de terceiro, com centenas de símbolos por provedor (686 só no OpenPix), e o que garante que elas não driftam é o drift test de cada uma, não esta página. Os módulos de integração documentados na referência estão porque alguém os escreveu à mão.

    Três grupos ficam de fora **de propósito**, cada um com o motivo registrado no allowlist do teste: os aliases BR pré-0.76 sem sufixo `Field` (`CPF`, `CNPJ`, `CEP`, `CPFOrCNPJ`, `PhoneBR`) e `AsyncBrokerManager`, todos **deprecados** — documentá-los convidaria ao uso; e `Classifier` / `Detector` / `Segmenter` / `DetectClassify` / `DetectClassifyResults` / `fuse_detect_classify`, que são reexports do `ort-vision-sdk` e pertencem à documentação daquele projeto. Os schemas e mappers que o SDK escreve em volta deles — `DetectClassifySchema`, `to_detect_classify_schemas` — são nossos e estão documentados na referência.

!!! tip "Buscando"
    Use a barra de busca no topo da página (ou pressione `/`) para pular para um símbolo pelo nome. O índice full-text inclui as docstrings, então buscas como "soft delete" ou "request id" caem na classe certa.

## Páginas

A referência é dividida por tema: numa página só, o HTML passava de 70 MB.

| Página | O que documenta |
| --- | --- |
| **[Superfície de topo »](reference/top.md)** | Todo símbolo que `tempest_fastapi_sdk` reexporta na raiz, documentado no caminho de importação curto (`from tempest_fastapi_sdk import X`). A maioria também aparece nas páginas temáticas, no módulo onde é definida; na 0.371.0, 208 dos 832 nomes da raiz só estavam documentados aqui. |
| **[Admin, dados e API »](reference/admin-data-api.md)** | O painel admin, a camada de dados (models, repository, sessão, migrations), a integração com o FastAPI, as exceções, os schemas base e as settings. |
| **[Documentos e arquivos »](reference/documents-files.md)** | PDF, planilhas, slides, exportação e apagamento de dados (LGPD), registro de artefatos e a prévia de compartilhamento. |
| **[Geração de cliente OpenAPI »](reference/openapi.md)** | O gerador de clientes tipados a partir de um documento OpenAPI (`tempest openapi-client`). |
| **[IA e visão computacional »](reference/ai-vision.md)** | Agentes, modelos generativos self-hosted, modelops (export, bench, quantização), visão computacional, reconhecimento facial e busca aproximada de texto. |
| **[Integrações e pagamentos »](reference/integrations-payments.md)** | O contrato canônico de pagamentos, os provedores (Mercado Pago, OpenPix, RevenueCat), o backend de upload do Google Drive, a carteira com saque Pix, o gateway de WhatsApp e o relatório de erros de app. |
| **[Tempo real e mensageria »](reference/realtime-messaging.md)** | Fila e tarefas, WebSocket, SSE, Web Push, chat, comentários, event sourcing, feature flags e MCP. |
| **[Web, UI e utilitários »](reference/web-ui-utils.md)** | Páginas SSR e a camada `ui`, utilitários, geolocalização, controle do host, system checks e as ferramentas de teste. |
