"""Brazilian Portuguese (``pt-BR``) OpenAPI text of the AI routers.

Mirrors :mod:`tempest_fastapi_sdk.api.route_docs.en_us.ai` key by key.
Every English entry is ``None`` (the endpoint name and docstring), so
every entry here sets both fields.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

AI_ROUTES: dict[str, RouteDoc] = {
    "agent.run_agent": RouteDoc(
        summary="Executar o agente até o fim",
        description=(
            "Executa o agente até ele terminar e devolve o registro "
            "inteiro: a resposta, o trace, os metadados dos artefatos e o "
            "motivo da parada."
        ),
    ),
    "agent.stream_agent": RouteDoc(
        summary="Acompanhar os passos do agente por SSE",
        description=(
            "Transmite os passos do agente conforme eles terminam.\n\n"
            "Cada evento ``step`` carrega um passo, para a interface "
            "mostrar o agente trabalhando em vez de um spinner. O stream "
            "termina junto com a execução, com um evento ``done`` que "
            'carrega ``{"run_id": ...}`` — o id que a URL do artefato '
            "precisa."
        ),
    ),
    "agent.list_runs": RouteDoc(
        summary="Listar as execuções recentes",
        description=(
            "Lista as execuções mais recentes de quem chama, da mais nova "
            "para a mais antiga. Responde ``200`` com a lista vazia quando "
            "não há nenhuma."
        ),
    ),
    "agent.get_artifact": RouteDoc(
        summary="Baixar um artefato de uma execução",
        description=(
            "Devolve os bytes de um artefato de uma execução guardada, com "
            "o media type do artefato. ``name`` pode conter ``/``.\n\n"
            "Responde **404** quando a execução não está mais guardada, "
            "pertence a outra pessoa ou não tem esse artefato — um "
            "artefato só é acessível enquanto a execução continua no "
            "buffer."
        ),
    ),
    "ai_chat.chat": RouteDoc(
        summary="Responder a uma mensagem do chat",
        description=(
            "Produz a resposta completa e fundamentada de um turno do "
            "chat: o texto e tudo o que saiu com ele (fontes, memórias "
            "recuperadas, ferramentas usadas, áudio opcional)."
        ),
    ),
    "ai_chat.chat_stream": RouteDoc(
        summary="Transmitir a resposta do chat por SSE",
        description=(
            "Transmite a resposta token a token num ``text/event-stream``, "
            "terminando com um evento ``done``."
        ),
    ),
    "genai.generate": RouteDoc(
        summary="Gerar texto a partir de um prompt",
        description=(
            "Gera a continuação do prompt e devolve o texto inteiro. "
            "Prompt ou ``max_new_tokens`` acima do limite do router "
            "respondem **422**."
        ),
    ),
    "genai.generate_stream": RouteDoc(
        summary="Transmitir a geração de texto por SSE",
        description=(
            "Gera a continuação do prompt token a token num "
            "``text/event-stream``, terminando com um evento ``done``."
        ),
    ),
    "genai.chat": RouteDoc(
        summary="Responder a uma lista de mensagens",
        description=(
            "Gera a resposta do assistente para a conversa enviada. "
            "Mensagens demais, ou caracteres demais somando todas, "
            "respondem **422**."
        ),
    ),
    "genai.embed": RouteDoc(
        summary="Gerar embeddings de textos",
        description=(
            "Transforma um ou vários textos em vetores e informa a dimensão deles."
        ),
    ),
    "genai.rag_context": RouteDoc(
        summary="Montar o contexto de RAG para uma consulta",
        description=(
            "Busca no corpus e devolve um bloco de contexto pronto para "
            "entrar no prompt, com até ``top_k`` trechos."
        ),
    ),
    "genai.transcribe": RouteDoc(
        summary="Transcrever um arquivo de áudio",
        description=(
            "Transcreve o áudio enviado e devolve o texto, o idioma, a "
            "duração e os segmentos. ``language`` força o idioma; sem ele, "
            "o idioma é detectado. Upload acima do limite responde "
            "**422** antes de ser lido inteiro na memória."
        ),
    ),
    "genai.synthesize": RouteDoc(
        summary="Sintetizar fala a partir de texto",
        description=(
            "Converte o texto em fala e devolve os bytes do áudio em ``audio/wav``."
        ),
    ),
    "genai.list_models": RouteDoc(
        summary="Listar os modelos carregados na memória",
        description=(
            "Informa quais modelos estão residentes na memória agora — os "
            "carregados primeiro, e entre eles os ociosos há mais tempo — "
            "e, quando ``probe`` é verdadeiro, a situação de memória do "
            "host.\n\n"
            "Passe ``probe=false`` para pular a sondagem do host (NVML "
            "quando o ``pynvml`` está instalado, senão um contexto CUDA "
            "pelo torch), a única parte deste endpoint que custa alguma "
            "coisa. O relatório é montado numa thread de trabalho, para "
            "não travar o event loop."
        ),
    ),
    "genai.render_image": RouteDoc(
        summary="Gerar uma imagem a partir de um prompt",
        description=(
            "Gera exatamente uma imagem e devolve os bytes dela, porque o "
            "corpo da resposta é a própria imagem: ``config.num_images`` "
            "acima de ``1`` responde **422** em vez de gerar um lote e "
            "devolver só a primeira.\n\n"
            "A seed que produziu a imagem vem no header ``X-Image-Seed``, "
            "para o cliente conseguir reproduzi-la."
        ),
    ),
    "prediction.predict": RouteDoc(
        summary="Fazer a predição de um lote de linhas",
        description=(
            "Devolve os rótulos, as probabilidades e o tempo de inferência "
            "das linhas enviadas. Um lote com mais linhas que o limite, ou "
            "com linhas que não têm a largura que o modelo espera, "
            "responde **422** — é erro do cliente, não um 500."
        ),
    ),
    "prediction.model_info": RouteDoc(
        summary="Descrever o modelo carregado",
        description=(
            "Informa o que está carregado e como está rodando, incluindo "
            "os execution providers **de fato** em uso e a configuração "
            "de threads."
        ),
    ),
    "prediction.sync_model": RouteDoc(
        summary="Sincronizar o modelo com o registry",
        description=(
            "Recarrega o modelo a partir do registry quando há uma versão "
            "mais nova publicada, e devolve o modelo em serviço depois da "
            "checagem — o mesmo de antes quando não havia nada novo.\n\n"
            "Quando a versão muda, o monitor é zerado: os contadores "
            "descrevem o modelo anterior. Responde **503** quando o "
            "registry não responde ou o arquivo novo não carrega; o "
            "modelo anterior continua servindo nos dois casos."
        ),
    ),
    "prediction.monitor_report": RouteDoc(
        summary="Relatório de latência, drift e distribuição das predições",
        description=(
            "Devolve o que o dispositivo mediu: latência, drift da entrada "
            "e distribuição das predições. O drift vem da janela atual "
            "quando ela já tem linhas suficientes, e da última janela "
            "completa até lá."
        ),
    ),
    "vision.classify": RouteDoc(
        summary="Classificar uma imagem",
        description=(
            "Classifica a imagem enviada. Upload grande demais, imagem com "
            "pixels demais ou bytes que não são imagem respondem **422**."
        ),
    ),
    "vision.detect": RouteDoc(
        summary="Detectar objetos numa imagem",
        description=(
            "Detecta os objetos da imagem enviada, cada um com a caixa, a "
            "classe e a confiança. Upload grande demais, imagem com pixels "
            "demais ou bytes que não são imagem respondem **422**."
        ),
    ),
    "vision.segment": RouteDoc(
        summary="Segmentar as instâncias de uma imagem",
        description=(
            "Segmenta as instâncias da imagem enviada, cada uma com a "
            "caixa, a classe e a confiança. Upload grande demais, imagem "
            "com pixels demais ou bytes que não são imagem respondem "
            "**422**."
        ),
    ),
}
"""Operation text of the AI routers, keyed ``<router>.<endpoint name>``."""

__all__: list[str] = ["AI_ROUTES"]
