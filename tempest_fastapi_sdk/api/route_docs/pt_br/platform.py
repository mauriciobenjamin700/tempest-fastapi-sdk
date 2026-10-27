"""Brazilian Portuguese (``pt-BR``) OpenAPI text of the non-auth routers.

Mirrors :mod:`tempest_fastapi_sdk.api.route_docs.en_us.platform` key by
key. Unlike the English table, every entry here sets both fields: an
English entry left ``None`` falls back to the endpoint name and docstring,
which are English, so the Portuguese one has to say it explicitly.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

PLATFORM_ROUTES: dict[str, RouteDoc] = {
    "app_errors.report_app_error": RouteDoc(
        summary="Registrar um erro informado pela aplicação cliente",
        description=(
            "Grava um relato de erro enviado pela aplicação cliente.\n\n"
            "Nem plano nem conta ativa bloqueiam esta rota: quem está com "
            "o app quebrado é justamente quem mais precisa que o relato "
            "chegue."
        ),
    ),
    "app_errors.list_app_errors": RouteDoc(
        summary="Listar os erros informados para investigação",
        description=(
            "Pagina os relatos gravados, do mais novo para o mais antigo. "
            "Responde ``200`` com a página vazia quando nada casa com os "
            "filtros; ``start_date`` depois de ``end_date`` responde "
            "**422**."
        ),
    ),
    "health.liveness": RouteDoc(
        summary="Sonda de liveness",
        description=(
            'Devolve ``{"status": "ok"}`` se o processo está de pé. Não '
            "consulta nenhuma dependência externa."
        ),
    ),
    "health.readiness": RouteDoc(
        summary="Sonda de readiness",
        description=(
            "Devolve o estado de cada dependência e responde **503** "
            "quando alguma checagem falha."
        ),
    ),
    "logs.read_logs": RouteDoc(
        summary="Ler os logs estruturados da aplicação",
        description=(
            "Devolve uma página filtrada dos registros de log, do mais "
            "novo para o mais antigo, com os metadados de paginação. O "
            "tamanho da página é limitado por ``max_page_size``."
        ),
    ),
    "logs.clear_logs": RouteDoc(
        summary="Esvaziar os logs estruturados da aplicação",
        description=(
            "Esvazia os arquivos de log apontados pela fonte escolhida. É "
            "a contraparte do endpoint de leitura, sobre os mesmos "
            "arquivos e atrás da mesma proteção. Devolve os nomes dos "
            "arquivos esvaziados ou apagados."
        ),
    ),
    "pdf.list_documents": RouteDoc(
        summary="Listar os documentos disponíveis e os schemas dos payloads",
        description=(
            "Todo documento que este serviço sabe renderizar, com o JSON "
            "Schema completo de cada payload — o suficiente para um "
            "cliente montar o formulário sem ler o código do SDK."
        ),
    ),
    "pdf.render_document": RouteDoc(
        summary="Renderizar um documento disponível em PDF",
        description=(
            "Valida o ``payload`` contra o schema do documento escolhido e "
            "devolve os bytes do PDF.\n\n"
            "Um nome de documento desconhecido responde **404** listando "
            "os que existem; um payload que não passa na validação "
            "responde **422** apontando o campo. A renderização roda numa "
            "thread de trabalho, então um documento lento não trava o "
            "event loop — mas ocupa uma das vagas do renderizador, e é por "
            "isso que esta rota deve ficar atrás do rate limit do serviço."
        ),
    ),
    "push.register": RouteDoc(
        summary="Registrar o dispositivo",
        description=(
            "Grava o dispositivo que vai receber as notificações, ligado "
            "ao usuário autenticado. Responde "
            '``{"status": "registered"}``.'
        ),
    ),
    "push.unregister": RouteDoc(
        summary="Remover o dispositivo",
        description=(
            "Remove o dispositivo de quem chama (idempotente).\n\n"
            "A remoção é restrita ao usuário autenticado: um token "
            "registrado por outra conta fica onde está e a resposta é o "
            "mesmo ``200`` de um token desconhecido, então a rota nunca "
            "revela de quem é um token. Responde "
            '``{"status": "unregistered"}``.'
        ),
    ),
    "push.get_vapid_public_key": RouteDoc(
        summary="Obter a chave pública VAPID",
        description=(
            "Devolve a chave pública VAPID com que o navegador faz a "
            "inscrição.\n\n"
            'Pública e sem autenticação. Responde ``""`` quando a chave '
            "não está configurada, o que permite ao frontend esconder o "
            "opt-in em vez de falhar."
        ),
    ),
    "sessions.login": RouteDoc(
        summary="Autenticar e iniciar uma sessão",
        description=(
            "Autentica com e-mail e senha e abre uma sessão no servidor. "
            "A resposta grava o cookie de sessão e traz os dados do login."
        ),
    ),
    "sessions.logout": RouteDoc(
        summary="Revogar a sessão atual",
        description=("Encerra a sessão do cookie atual no servidor e apaga o cookie."),
    ),
    "sessions.me": RouteDoc(
        summary="Devolver a sessão ativa do cookie atual",
        description=("Devolve a sessão no servidor que o cookie atual identifica."),
    ),
    "sessions.list_sessions": RouteDoc(
        summary="Listar todas as sessões do usuário atual",
        description=("Devolve uma entrada por sessão ativa de quem chama."),
    ),
    "sessions.revoke_one": RouteDoc(
        summary="Revogar uma sessão específica pelo id público",
        description=(
            "Revoga uma das sessões de quem chama pelo id público.\n\n"
            "Revogar a própria sessão também apaga o cookie, para o "
            "navegador parar de apresentar um identificador que não "
            "resolve mais. Responde ``204`` sem corpo."
        ),
    ),
    "sse.issue_ticket": RouteDoc(
        summary="Emitir um ticket de SSE",
        description=(
            "Troca a sessão autenticada por um ticket de vida curta que "
            "abre `GET {prefix}/stream?{ticket_param}=...`. O ticket "
            "autoriza só esse stream; peça um novo antes de cada "
            "reconexão."
        ),
    ),
    "sse.stream": RouteDoc(
        summary="Abrir o stream de eventos",
        description=(
            "Server-Sent Events. Autentique com `?{ticket_param}=` obtido "
            "em `POST {prefix}/ticket`."
        ),
    ),
    "tool_spec.tool_spec": RouteDoc(
        summary="Especificação de ferramentas",
        description=(
            "Devolve o manifesto de ferramentas que este serviço expõe, "
            "gerado a cada requisição."
        ),
    ),
    "tool_spec.tool_spec_static": RouteDoc(
        summary="Especificação de ferramentas",
        description="Devolve o manifesto de ferramentas que este serviço expõe.",
    ),
    "voice.transcribe": RouteDoc(
        summary="Transcrever uma conversa, separada por falante",
        description=(
            "Recebe uma gravação e devolve a transcrição com uma entrada "
            "por turno de fala.\n\n"
            "**Passe `num_speakers` sempre que souber** — numa ligação "
            "entre duas pessoas, numa entrevista, num atendimento você "
            "sabe. Agrupar só por limiar é a parte menos confiável do "
            "pipeline.\n\n"
            "Os turnos trazem `speaker`, um índice de cluster estável "
            "dentro desta gravação e sem significado entre gravações. Um "
            "turno com `speaker = -1` é fala que caiu fora de todo turno "
            "detectado: ele é transcrito em vez de descartado, porque "
            "perder palavras em silêncio é pior."
        ),
    ),
    "voice.enroll": RouteDoc(
        summary="Cadastrar a voz de quem chama",
        description=(
            "Grava uma impressão de voz do **usuário autenticado** — o id "
            "do usuário vem da sessão, nunca do corpo.\n\n"
            "`consent_reference` é obrigatório e identifica o que a "
            "pessoa aceitou: uma versão de política, um documento "
            "assinado, um ticket. Impressão de voz é dado pessoal "
            "sensível e exige consentimento específico e destacado; uma "
            "referência em branco responde **422**.\n\n"
            "O áudio é usado para calcular o vetor e descartado. Nada "
            "guarda a gravação."
        ),
    ),
    "voice.list_profiles": RouteDoc(
        summary="Listar os perfis de voz cadastrados de quem chama",
        description=(
            "O que está cadastrado, quando e sob qual consentimento. O "
            "embedding em si **não** é devolvido: a pessoa precisa saber "
            "que um perfil existe, não receber por HTTP uma cópia do "
            "próprio template biométrico.\n\n"
            "Responde `200` com a lista vazia quando não há nenhum."
        ),
    ),
    "voice.erase": RouteDoc(
        summary="Apagar todos os perfis de voz que quem chama cadastrou",
        description=(
            "Apaga os dados biométricos de quem chama. É o pedido que a "
            "LGPD torna incondicional (Art. 18, VI), e é por isso que é "
            "um endpoint e não um chamado de suporte.\n\n"
            "Apagar quando não há nada para apagar responde `200` com "
            "`deleted: 0` — um resultado de sucesso, não um erro."
        ),
    ),
    "webpush.subscribe": RouteDoc(
        summary="Inscrever o dispositivo",
        description=(
            "Grava a inscrição do dispositivo enviada pelo navegador (o "
            "payload de ``PushSubscription.toJSON()``), ligada ao usuário "
            'autenticado. Responde ``{"status": "subscribed"}``.'
        ),
    ),
    "webpush.unsubscribe": RouteDoc(
        summary="Cancelar a inscrição do dispositivo",
        description=(
            "Remove a inscrição do dispositivo de quem chama "
            "(idempotente).\n\n"
            "A remoção é restrita ao usuário autenticado: um endpoint "
            "registrado por outra conta fica onde está e a resposta é o "
            "mesmo ``200`` de um endpoint desconhecido, então a rota nunca "
            "revela de quem ele é. Responde "
            '``{"status": "unsubscribed"}``.'
        ),
    ),
    "webpush.get_vapid_public_key": RouteDoc(
        summary="Obter a chave pública VAPID",
        description=(
            "Devolve a chave pública VAPID com que o navegador faz a "
            "inscrição.\n\n"
            'Pública e sem autenticação. Responde ``""`` quando a chave '
            "não está configurada (Web Push desligado), o que permite ao "
            "frontend esconder o opt-in."
        ),
    ),
}
"""Operation text of the non-auth routers, keyed ``<router>.<endpoint name>``."""

PLATFORM_TEXTS: dict[str, str] = {
    "health.readiness.503": "Pelo menos uma dependência não está pronta.",
    "pdf.render_document.200": "O documento renderizado.",
    "logs.read_logs.source": (
        "Quais arquivos de log ler. 'all' junta todos os níveis; '500' "
        "devolve só os registros isolados de 500 não tratado."
    ),
    "logs.read_logs.q": "Busca por trecho na mensagem, sem diferenciar maiúsculas.",
    "logs.read_logs.start": "Só registros a partir deste instante ISO-8601.",
    "logs.read_logs.end": "Só registros até este instante ISO-8601.",
    "logs.read_logs.page": "Número da página (começa em 1).",
    "logs.read_logs.page_size": "Itens por página.",
    "logs.clear_logs.source": (
        "Quais arquivos de log esvaziar. 'all' esvazia todos os níveis "
        "**e** o stream isolado de 500; '500' esvazia só este último."
    ),
    "logs.clear_logs.delete_backups": (
        "Apagar também os backups rotacionados (info.log.1, "
        "info.log.2026-10-20, ...). Desligado por padrão: apagá-los não "
        "tem volta."
    ),
    "app_errors.list_app_errors.page": "Página, começando em 1.",
    "app_errors.list_app_errors.page_size": "Itens por página.",
    "voice.transcribe.audio": "A gravação.",
    "voice.enroll.audio": "Pelo menos 3 segundos de fala.",
    "voice.enroll.consent_reference": "Evidência do consentimento da pessoa.",
}
"""Parameter and response descriptions declared on the non-auth routes."""

__all__: list[str] = ["PLATFORM_ROUTES", "PLATFORM_TEXTS"]
