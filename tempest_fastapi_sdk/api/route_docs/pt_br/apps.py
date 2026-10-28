"""Brazilian Portuguese (``pt-BR``) OpenAPI text of the feature routers.

Mirrors :mod:`tempest_fastapi_sdk.api.route_docs.en_us.apps` key by key.
An English entry left ``None`` falls back to the endpoint name and
docstring, which are English, so every entry here sets both fields.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

APPS_ROUTES: dict[str, RouteDoc] = {
    "chat.start_conversation": RouteDoc(
        summary="Iniciar uma conversa",
        description=(
            "Cria uma conversa com os participantes informados; quem chama "
            "entra automaticamente como participante."
        ),
    ),
    "chat.list_conversations": RouteDoc(
        summary="Listar as conversas de quem chama",
        description=(
            "Lista as conversas de quem chama, as fixadas primeiro e "
            "depois as mais recentes, cada uma com o próprio "
            "``unread_count``. As arquivadas só entram com "
            "``include_archived=true``. Responde ``200`` com a lista vazia "
            "quando não há nenhuma."
        ),
    ),
    "chat.get_conversation": RouteDoc(
        summary="Obter uma conversa",
        description=(
            "Devolve uma conversa com os participantes. Responde **403** "
            "quando quem chama não participa dela."
        ),
    ),
    "chat.update_conversation": RouteDoc(
        summary="Editar o título ou a descrição de um grupo",
        description=(
            "Altera o título ou a descrição de um grupo. Responde **403** "
            "quando quem chama não é admin nem dono."
        ),
    ),
    "chat.set_preferences": RouteDoc(
        summary="Fixar, arquivar ou silenciar uma conversa",
        description=(
            "Fixa, arquiva ou silencia a conversa só para quem chama — os "
            "outros participantes não veem a mudança. Responde **403** "
            "quando quem chama não participa dela."
        ),
    ),
    "chat.add_participants": RouteDoc(
        summary="Adicionar membros a um grupo",
        description=(
            "Adiciona os ``participant_ids`` do corpo ao grupo; os outros "
            "campos são ignorados aqui. Com ``share_history=true``, quem "
            "entra recebe o histórico anterior. Responde **403** quando "
            "quem chama não é admin nem dono."
        ),
    ),
    "chat.remove_participant": RouteDoc(
        summary="Remover um membro de um grupo",
        description=(
            "Remove um participante do grupo. Responde **403** quando quem "
            "chama não é admin nem dono."
        ),
    ),
    "chat.leave_conversation": RouteDoc(
        summary="Sair de uma conversa",
        description=(
            "Remove quem chama da conversa. Responde **403** quando quem "
            "chama não participa dela."
        ),
    ),
    "chat.mark_read": RouteDoc(
        summary="Marcar a conversa como lida",
        description=(
            "Avança a marca de leitura de quem chama até a mensagem "
            "informada. Responde **403** quando quem chama não participa "
            "da conversa."
        ),
    ),
    "chat.post_message": RouteDoc(
        summary="Enviar uma mensagem",
        description=(
            "Grava uma mensagem na conversa, com quem chama como "
            "remetente. Responde **403** quando quem chama não participa "
            "dela."
        ),
    ),
    "chat.list_messages": RouteDoc(
        summary="Paginar o histórico de mensagens",
        description=(
            "Pagina o histórico da conversa, da mensagem mais antiga para "
            "a mais nova. ``page_size`` acima do máximo do router responde "
            "**422**; ``with_receipts=true`` anexa as contagens de entrega "
            "e leitura. Responde **403** quando quem chama não participa "
            "da conversa."
        ),
    ),
    "chat.edit_message": RouteDoc(
        summary="Editar uma mensagem enviada",
        description=(
            "Troca o texto de uma mensagem. Responde **403** quando quem "
            "chama não é o remetente."
        ),
    ),
    "chat.revoke_message": RouteDoc(
        summary="Apagar uma mensagem para todos",
        description=(
            "Apaga a mensagem para todos os participantes e deixa no lugar "
            "um registro de mensagem apagada, que é o que a resposta "
            "devolve. Responde **403** quando quem chama não é o "
            "remetente."
        ),
    ),
    "chat.react": RouteDoc(
        summary="Reagir a uma mensagem",
        description=(
            "Registra a reação de quem chama, substituindo a anterior, e "
            "devolve a mensagem com as reações recalculadas."
        ),
    ),
    "chat.unreact": RouteDoc(
        summary="Remover a reação de uma mensagem",
        description=(
            "Remove a reação de quem chama e devolve a mensagem com as "
            "reações recalculadas."
        ),
    ),
    "chat.forward_message": RouteDoc(
        summary="Encaminhar uma mensagem",
        description=(
            "Encaminha a mensagem para outras conversas e devolve uma "
            "mensagem por destino. Responde **403** quando quem chama não "
            "participa de alguma das conversas de destino."
        ),
    ),
    "chat.stream_messages": RouteDoc(
        summary="Acompanhar as mensagens novas por SSE",
        description=(
            "Abre um ``text/event-stream`` com as mensagens novas da "
            "conversa. O stream não segura sessão de banco enquanto está "
            "aberto, e termina quando quem chama deixa de participar da "
            "conversa.\n\n"
            "Responde **403** quando quem chama não participa da conversa "
            "e **404** quando o tempo real não está habilitado."
        ),
    ),
    "hostbridge.host_info": RouteDoc(
        summary="Informações do host",
        description=(
            "Informa o nome do host, o usuário, a versão do sistema "
            "operacional e o uptime."
        ),
    ),
    "hostbridge.read_file": RouteDoc(
        summary="Ler um arquivo de texto do host",
        description=(
            "Lê um arquivo de texto do host no ``encoding`` informado "
            "(``utf-8`` por padrão)."
        ),
    ),
    "hostbridge.list_dir": RouteDoc(
        summary="Listar um diretório do host",
        description="Lista o conteúdo de um diretório do host.",
    ),
    "hostbridge.read_pdf": RouteDoc(
        summary="Ler um PDF do host",
        description=(
            "Lê um PDF do host, página por página, com o ``extractor`` "
            "escolhido; ``password`` abre um PDF protegido."
        ),
    ),
    "hostbridge.pick_file": RouteDoc(
        summary="Escolher um arquivo no seletor nativo do host",
        description=(
            "Abre o seletor de arquivos nativo do host e devolve o que foi escolhido."
        ),
    ),
    "hostbridge.run_command": RouteDoc(
        summary="Executar um comando no host",
        description=("Executa um comando no host e devolve tudo o que ele produziu."),
    ),
    "hostbridge.write_file": RouteDoc(
        summary="Escrever um arquivo no host",
        description="Escreve texto num arquivo do host.",
    ),
    "hostbridge.delete_file": RouteDoc(
        summary="Apagar um arquivo do host",
        description=(
            "Apaga um arquivo, ou um diretório vazio, do host. Com "
            "``missing_ok=true``, um caminho que não existe não é erro."
        ),
    ),
    "hostbridge.shutdown": RouteDoc(
        summary="Agendar o desligamento do host",
        description="Agenda o desligamento do host.",
    ),
    "hostbridge.restart": RouteDoc(
        summary="Agendar a reinicialização do host",
        description="Agenda a reinicialização do host.",
    ),
    "hostbridge.abort_shutdown": RouteDoc(
        summary="Cancelar o desligamento agendado",
        description=(
            "Cancela um desligamento ou uma reinicialização que ainda não aconteceu."
        ),
    ),
    "hostbridge.lock": RouteDoc(
        summary="Bloquear a sessão do host",
        description="Bloqueia a sessão ativa do host.",
    ),
    "hostbridge.logoff": RouteDoc(
        summary="Encerrar a sessão do usuário do host",
        description="Faz o logoff do usuário ativo do host.",
    ),
    "htmx.htmx_js": RouteDoc(
        summary="Servir o HTMX embutido",
        description=(
            "Serve a cópia do HTMX que vem dentro do pacote, como "
            "``application/javascript``, sem depender de CDN."
        ),
    ),
    "htmx.confirm_js": RouteDoc(
        summary="Servir o script do data-confirm",
        description=(
            "Serve o listener que vem dentro do pacote por trás do "
            "atributo ``data-confirm``, como ``application/javascript``."
        ),
    ),
    "revenuecat.receive_revenuecat_webhook": RouteDoc(
        summary="Receber um webhook da RevenueCat",
        description=(
            "Autentica a entrega, grava o evento na inbox e responde "
            "``200``. Um ``event.id`` repetido não grava nada e também "
            "responde ``200``; ``recorded`` diz se a entrega era nova. O "
            "processamento acontece depois, fora desta rota."
        ),
    ),
    "reviews.add_comment": RouteDoc(
        summary="Comentar num alvo",
        description=(
            "Publica um comentário no alvo, com quem chama como autor. "
            "``parent_id`` transforma o comentário em resposta."
        ),
    ),
    "reviews.list_comments": RouteDoc(
        summary="Paginar os comentários de um alvo",
        description=("Pagina os comentários do alvo, do mais antigo para o mais novo."),
    ),
    "reviews.rate": RouteDoc(
        summary="Avaliar um alvo",
        description=(
            "Grava a nota de 0 a 5 estrelas de quem chama para o alvo, "
            "criando ou substituindo a anterior."
        ),
    ),
    "reviews.aggregate": RouteDoc(
        summary="Obter o resumo das avaliações de um alvo",
        description=(
            "Devolve a média, o total e a distribuição por estrela das "
            "avaliações do alvo."
        ),
    ),
    "wallet.get_balance": RouteDoc(
        summary="Consultar o saldo da carteira",
        description=("Devolve o saldo de quem chama, separado em retido e disponível."),
    ),
    "wallet.get_statement": RouteDoc(
        summary="Paginar o extrato da carteira",
        description=(
            "Pagina os lançamentos da carteira de quem chama, do mais "
            "novo para o mais antigo."
        ),
    ),
    "wallet.withdraw": RouteDoc(
        summary="Sacar o saldo para a chave Pix",
        description=(
            "Paga o saldo disponível de quem chama — ou só "
            "``amount_cents`` dele — para a chave Pix cadastrada, e "
            "devolve o débito com a resposta do provedor."
        ),
    ),
    "web_app.index": RouteDoc(
        summary="Servir o shell da aplicação",
        description=(
            "Serve o documento de entrada da aplicação (o ``index.html`` "
            "do build, ou o que o substitui), sempre com "
            "``Cache-Control: no-cache``."
        ),
    ),
    "web_app.asset": RouteDoc(
        summary="Servir um arquivo do build",
        description=(
            "Serve um arquivo do build. Um caminho que não aponta para um "
            "arquivo devolve o shell da aplicação, para o roteamento no "
            "cliente funcionar num refresh; com o fallback desligado, "
            "responde **404**."
        ),
    ),
    "zap.receive_zap": RouteDoc(
        summary="Receber um webhook do zap-api",
        description=(
            "Confere a assinatura da entrega e a entrega ao handler do "
            "evento: mensagem recebida ou atualização de status. Responde "
            "``200`` com o nome do ``event`` quando o handler termina, e "
            "**401** quando a assinatura não confere."
        ),
    ),
}
"""Operation text of the feature routers, keyed ``<router>.<endpoint name>``."""

__all__: list[str] = ["APPS_ROUTES"]
