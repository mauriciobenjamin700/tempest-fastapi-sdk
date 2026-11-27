# IA generativa self-hosted

Rodar modelos do HuggingFace no **seu próprio hardware** — sem API
externa, sem enviar dados pra fora. Esta página é o índice do módulo
`tempest_fastapi_sdk.genai`: cada tarefa mora numa subpágina, e a primeira
é onde toda adoção começa — saber, *antes* de baixar gigabytes de pesos, se
a máquina aguenta o modelo.

!!! info "Como o módulo foi construído"
    O módulo saiu em fatias, e a ordem abaixo é o caminho de leitura
    recomendado — cada subpágina assume as anteriores. Depois da v0.108 vieram
    saída estruturada (v0.142), diarização (v0.223), correção de STT (v0.235)
    e o runtime Coqui do extra `[genai-audio]` (v0.252), todos documentados
    nas subpáginas correspondentes.
    - **v0.96:** `genai.hardware` — sondagem + `can_run` / `recommend`.
    - **v0.97:** `genai.rag` — contexto RAG (busca web SearXNG + leitura de
      PDF) pra injetar nas LLMs (subpágina [Contexto RAG](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/rag.md)).
    - **v0.98:** `TextGenerator` — LLM local + quantização int4/int8
      (subpágina [Gerar texto](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/text.md#gerar-texto-com-llm-local)).
    - **v0.99:** `Embedder`, `BatchScheduler`, `ModelRegistry` — embeddings
      + escala (subpágina [Embeddings e escala](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/embeddings.md)).
    - **v0.102:** `SpeechToText` / `TextToSpeech` — áudio (subpágina
      [Áudio (voz)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/audio.md)).
    - **v0.107:** backend Ollama — `OllamaGenerator` / `OllamaEmbedder`,
      LLM local sem torch (subpágina [Backend Ollama](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/ollama.md)).
    - **v0.108:** memória de longo prazo, pipeline de chat com IA e
      visão/tools — `ChatMemory` / `AIChatPipeline` (subpáginas
      [Memória de longo prazo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/memory.md) e
      [Pipeline de chat com IA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/chat.md)).

O extra `[genai]` (transformers + torch + accelerate) só é necessário pra
**rodar** modelos. As funções de capacidade **importam sem o extra** — o
`torch` só é usado (quando presente) pra ver a VRAM real da GPU.

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [A máquina aguenta? Hardware e estimativa](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/hardware.md) | Use antes de baixar pesos: sonda CPU, RAM e GPU (inclusive em container) e estima se o modelo cabe. |
| [Gerar texto (local e hospedado)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/text.md) | Use para gerar texto com um LLM local (`TextGenerator`) ou um backend compatível com OpenAI (DeepSeek, Groq, OpenRouter, vLLM), com custo por chamada e `extra_body`. |
| [Backend Ollama](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/ollama.md) | Use para rodar LLM e embeddings pelo Ollama, sem torch: geração, RAG, busca híbrida, reranking e o mesmo router trocando torch por Ollama. |
| [Memória de longo prazo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/memory.md) | Use quando o assistente precisa lembrar fatos entre conversas (`ChatMemory`), em memória ou no Postgres com pgvector. |
| [Pipeline de chat com IA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/chat.md) | Use para montar o chat completo (histórico, memória, ferramentas) com `AIChatPipeline`, o endpoint pronto `make_ai_chat_router` e streaming. |
| [Embeddings e escala](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/embeddings.md) | Use para gerar embeddings (torch ou ONNX), agrupar inferência concorrente em batch e compartilhar e inspecionar os modelos carregados. |
| [Áudio: STT e TTS](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/audio.md) | Use para transcrever áudio (STT) e gerar voz (TTS), escolher idioma e modelo de voz pela licença e por onde roda. |
| [Efeitos sonoros e música](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/sound.md) | Use para gerar efeitos sonoros e música (MusicGen) e servir o resultado por HTTP em `/audio`. |
| [Contexto RAG](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/rag.md) | Use para injetar contexto na LLM: busca web (SearXNG), extração do corpo das páginas, leitura de PDFs e RAG sobre um corpus próprio. |
| [Config tipada e saída estruturada](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/structured.md) | Use quando o modelo precisa devolver um objeto Pydantic preenchido, e para fixar os parâmetros de geração com `GenerationConfig`. |
| [Moderação, uso, métricas e cache](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/operations.md) | Use em produção: moderar conteúdo, contabilizar uso por usuário, expor métricas e tracing, contar tokens e cachear gerações. |
| [Visão multimodal (VLM local)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/vision.md) | Use para perguntar sobre uma imagem a um modelo de visão e linguagem rodando local. |
| [Endpoints prontos e cache Redis](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/router.md) | Use para expor o módulo por HTTP com `make_genai_router` e compartilhar o cache de embeddings entre workers com `RedisEmbeddingCache`. |
| [Quem falou o quê: diarização](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/genai/diarization.md) | Use para separar um áudio por falante (sherpa-onnx), reconhecer quem é por perfil de voz e servir isso por HTTP ou CLI. |

## Recap

- **`GenerationConfig`** — parâmetros de geração tipados e reutilizáveis
  no lugar de `**kwargs`.
- **`make_genai_router`** — monta só os endpoints dos objetos injetados;
  `/generate/stream` faz streaming de tokens via SSE.
- **`RedisEmbeddingCache`** — cache de vetores compartilhado; `Embedder`
  aceita cache sync ou async no mesmo call site.
- **Backend Ollama** — `OllamaGenerator` / `OllamaEmbedder` usam a mesma
  superfície (router, `Retriever`, `GenerationConfig`) via daemon Ollama
  local: sem torch, sem pesos, sem `load()`; `TextBackend` é a costura pra
  outras engines.
- **`ChatMemory` / `ChromaVectorStore`** — memória de longo prazo por
  usuário com recall por similaridade + recência (`[genai-chroma]`);
  `ChromaVectorStore` é um `VectorStore` persistente pra RAG genérico.
- **`PgChatMemory`** — a mesma memória numa tabela pgvector do Postgres do
  serviço, sem `[genai-chroma]`; as deleções entram na sua transação.
- **`AIChatPipeline` / `make_ai_chat_router`** — chatbot completo
  in-process (memória + RAG web + tool-calling + TTS opcional); um router
  stateless (`/chat` + `/chat/stream` SSE) mata o microserviço de inferência.
- **`can_run` / `recommend`** — respondem se o host roda o modelo e o que
  fazer se não rodar, **antes** do download.
- **RAG sobre corpus** — `Retriever` + `VectorStore` (`InMemoryVectorStore`
  / `PgVectorStore` pgvector): indexe chunks uma vez, recupere top-k por
  similaridade.
- **Áudio** — `SpeechToText` (faster-whisper) transcreve; `TextToSpeech`
  (Coqui TTS) sintetiza — voz local ponta a ponta.
- **RAG** — `WebSearch`/`SearxngBackend` (busca), `ContentExtractor`
  (corpo das páginas), `PdfReader` (PDF → texto/chunks) e `build_context`
  (bloco pro prompt), todos sob o extra `[genai-rag]`.
- **`probe_hardware`** — snapshot de CPU/RAM/GPU/disco; degrada sem os
  extras.
- **`estimate_model_bytes` / `bytes_per_param`** — a matemática da
  estimativa, testável e reutilizável.
- Tudo importa sem o extra `[genai]`; instale-o pra rodar modelos de fato
  (fatias seguintes do módulo).
