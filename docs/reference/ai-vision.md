---
description: "Índice dos módulos: agentes, modelos generativos self-hosted, modelops (export, bench, quantização), visão computacional, reconhecimento facial e busca aproximada de texto."
---

# Referência: IA e visão computacional

Agentes, modelos generativos self-hosted, modelops (export, bench, quantização), visão computacional, reconhecimento facial e busca aproximada de texto.

[« Índice da referência](../reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`agents`](ai-vision/agents.md) | Agentes de IA sobre os modelos que você já hospeda: ferramentas, memória e o loop de execução. |
| [`faces`](ai-vision/faces.md) | Detecção e reconhecimento facial em ONNX Runtime: detector, reconhecedor, alinhamento e pacotes de modelo. |
| [`genai`](ai-vision/genai.md) | IA generativa self-hosted: geração de texto, embeddings, VLM, moderação, cache, métricas e router. |
| [`genai.audio`](ai-vision/genai-audio.md) | Áudio self-hosted: fala para texto e texto para fala. |
| [`genai.hub`](ai-vision/genai-hub.md) | Ciclo de vida dos pesos do HuggingFace que o serviço hospeda: download, cache e verificação. |
| [`genai.image`](ai-vision/genai-image.md) | Geração de imagem local sobre o `diffusers` do HuggingFace. |
| [`genai.inventory`](ai-vision/genai-inventory.md) | O que está carregado em memória agora e quanto isso custa. |
| [`genai.rag`](ai-vision/genai-rag.md) | RAG: contexto para o LLM a partir de busca na web e de PDFs. |
| [`modelops`](ai-vision/modelops.md) | Modelops: exportar, quantizar e medir os modelos que o serviço serve. |
| [`text`](ai-vision/text.md) | Busca aproximada de texto sem modelo: normalização, distância de edição e lookup fuzzy. |
| [`vision`](ai-vision/vision.md) | Inferência de visão computacional (extra `[vision]`): schemas, mappers e o serviço sobre o `ort-vision-sdk`. |
