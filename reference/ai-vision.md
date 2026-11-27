# Referência: IA e visão computacional

Agentes, modelos generativos self-hosted, modelops (export, bench, quantização), visão computacional, reconhecimento facial e busca aproximada de texto.

[« Índice da referência](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`agents`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/agents.md) | Agentes de IA sobre os modelos que você já hospeda: ferramentas, memória e o loop de execução. |
| [`faces`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/faces.md) | Detecção e reconhecimento facial em ONNX Runtime: detector, reconhecedor, alinhamento e pacotes de modelo. |
| [`genai`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/genai.md) | IA generativa self-hosted: geração de texto, embeddings, VLM, moderação, cache, métricas e router. |
| [`genai.audio`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/genai-audio.md) | Áudio self-hosted: fala para texto e texto para fala. |
| [`genai.hub`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/genai-hub.md) | Ciclo de vida dos pesos do HuggingFace que o serviço hospeda: download, cache e verificação. |
| [`genai.image`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/genai-image.md) | Geração de imagem local sobre o `diffusers` do HuggingFace. |
| [`genai.inventory`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/genai-inventory.md) | O que está carregado em memória agora e quanto isso custa. |
| [`genai.rag`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/genai-rag.md) | RAG: contexto para o LLM a partir de busca na web e de PDFs. |
| [`modelops`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/modelops.md) | Modelops: exportar, quantizar e medir os modelos que o serviço serve. |
| [`text`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/text.md) | Busca aproximada de texto sem modelo: normalização, distância de edição e lookup fuzzy. |
| [`vision`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/ai-vision/vision.md) | Inferência de visão computacional (extra `[vision]`): schemas, mappers e o serviço sobre o `ort-vision-sdk`. |
