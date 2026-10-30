"""Brazilian Portuguese (``pt-BR``) OpenAPI text of the auth routes.

Mirrors :mod:`tempest_fastapi_sdk.api.route_docs.en_us.auth` key by key.
Setting names, field names, error codes and paths stay in their original
form inside the prose, because they are what the reader types.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

AUTH_ROUTES: dict[str, RouteDoc] = {
    "auth.activate": RouteDoc(
        summary="Ativar uma conta a partir do token enviado por e-mail (JSON)",
        description=(
            "Consome um **token de ativação** de uso único (o enviado no "
            "e-mail de cadastro ou devolvido por ``/signup``) e marca a "
            "conta como ativa.\n\n"
            "Esta é a variante **JSON / SPA**: o frontend lê o ``{token}`` "
            "da URL de ativação e o envia aqui por POST. Em caso de "
            "sucesso a conta é ativada **e já entra logada** — a resposta "
            "traz um par novo de ``access_token`` + ``refresh_token``, "
            "para o usuário não precisar digitar a senha logo depois de "
            "confirmar.\n\n"
            "**MFA.** Um token de ativação normalmente pertence a uma "
            "conta recém-cadastrada, então não há fator a provar. Ele "
            "também pode ser emitido por ``/activation/request`` para uma "
            "conta desativada, e essa conta pode ter TOTP cadastrado. "
            "Nesse caso — e só nele — a rota responde como ``/login``: "
            "``mfa_required=true`` mais um ``mfa_token`` de vida curta no "
            "lugar do par, trocado em ``/mfa/verify``.\n\n"
            "O token é recusado com **400** quando é desconhecido, já foi "
            "usado ou passou do prazo ``AUTH_ACTIVATION_TTL_SECONDS``.\n\n"
            "!!! note\n"
            "    Com ``AUTH_BACKEND_LINKS=True`` o SDK também monta um "
            "    **GET** ``/activate/{token}`` que renderiza uma página "
            "    HTML direto do backend — use esse quando não houver "
            "    frontend. Este endpoint POST está sempre disponível."
        ),
    ),
    "auth.login": RouteDoc(
        summary="Entrar com e-mail + senha → par JWT",
        description=(
            "Autentica um usuário **ativo** pelo e-mail e senha e devolve "
            "o par JWT ``access_token`` + ``refresh_token``.\n\n"
            "Responde **401** para credenciais erradas e para contas que "
            "existem mas nunca foram ativadas — a mensagem é genérica de "
            "propósito, para quem chama não saber qual dos dois casos "
            "aconteceu. Com ``AUTH_REVEAL_INACTIVE_ACCOUNT=True``, a senha "
            "certa numa conta desativada responde **403** "
            "``ACCOUNT_INACTIVE``.\n\n"
            "**MFA.** Quando o usuário concluiu o cadastro do TOTP (e "
            "``AUTH_MFA_ENABLED=True``), este endpoint *não* devolve o par "
            "JWT. Em vez disso devolve ``mfa_required=true`` mais um "
            "``mfa_token`` de vida curta; troque esse token pelo par JWT "
            "de verdade em ``/mfa/verify``.\n\n"
            "**Limite de tentativas.** Por padrão a 6ª senha errada para o "
            "mesmo e-mail dentro de 15 minutos — e toda tentativa depois "
            "dela, inclusive com a senha certa — responde **429** "
            "``TOO_MANY_REQUESTS`` com ``Retry-After``; um login bem-"
            "sucedido zera a contagem. O mesmo vale para um e-mail sem "
            "conta, então o 429 não revela quais e-mails existem. "
            "``login_ip_throttle`` acrescenta um limite por cliente."
        ),
    ),
    "auth.activation_request": RouteDoc(
        summary="Reenviar o link de ativação (sem credenciais)",
        description=(
            "Emite um link de ativação novo para uma conta que nunca foi "
            "ativada — o e-mail de cadastro voltou, caiu no spam ou a aba "
            "foi fechada.\n\n"
            "**Sem autenticação**, por necessidade: o login é recusado "
            "enquanto a conta está inativa, então uma rota atrás de um "
            "bearer token não ajudaria justamente quem precisa dela.\n\n"
            "**Sempre responde 202** com a mesma mensagem genérica para "
            "um endereço desconhecido, uma conta já ativa e um reenvio de "
            "verdade, para que sondar a rota não permita enumerar contas. "
            "Uma conta ativa não recebe e-mail: o link não autorizaria "
            "nada. Um e-mail que o servidor SMTP não aceita também "
            "responde **202**: a falha fica registrada no log do servidor "
            "e nenhum token é gravado."
        ),
    ),
    "auth.password_reset_request": RouteDoc(
        summary="Iniciar a redefinição de senha (pedir o link)",
        description=(
            "Inicia o fluxo de esqueci-minha-senha para o e-mail "
            "informado.\n\n"
            "**Sempre responde 202** com a mesma mensagem genérica, exista "
            "ou não uma conta com esse e-mail — isso impede que alguém "
            "descubra quais e-mails estão cadastrados sondando este "
            "endpoint. Inclui o caso do e-mail de redefinição que o "
            "servidor SMTP não aceita: a falha fica no log do servidor, "
            "nenhum token é gravado e a resposta continua **202**.\n\n"
            "Quando a conta existe, é emitido um token de redefinição de "
            "uso único (válido por ``AUTH_PASSWORD_RESET_TTL_SECONDS``). "
            "Com ``EmailUtils`` configurado o link é **enviado por "
            "e-mail** (no idioma de ``AUTH_DEFAULT_LOCALE``) e "
            "``reset_url`` fica ``null`` no corpo; sem e-mail configurado "
            "— ou com ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` — o "
            "``reset_url`` pronto para uso volta no corpo, para você "
            "conduzir o fluxo sem SMTP."
        ),
    ),
    "auth.password_reset_confirm": RouteDoc(
        summary="Concluir a redefinição de senha (gravar a nova senha) — JSON",
        description=(
            "Conclui o fluxo de esqueci-minha-senha: consome o token de "
            "redefinição de uso único e grava a nova senha.\n\n"
            "Esta é a variante **JSON / SPA** — o frontend coleta a nova "
            "senha e a envia por POST junto com o ``token`` lido da URL "
            "de redefinição. A nova senha precisa cumprir "
            "``AUTH_PASSWORD_MIN_LENGTH`` e as regras de complexidade; "
            "violações respondem **422**. Um token desconhecido, já usado "
            "ou expirado responde **400**.\n\n"
            "Em caso de sucesso a senha é atualizada **e o usuário já "
            "entra logado** — a resposta traz um par novo de "
            "``access_token`` + ``refresh_token``.\n\n"
            "**MFA.** Quando a conta concluiu o cadastro do TOTP (e "
            "``AUTH_MFA_ENABLED=True``) a redefinição *não* devolve o par "
            "JWT, exatamente como ``/login``. Do contrário, uma caixa de "
            "e-mail roubada bastaria para passar pelo segundo fator — "
            "justamente o cenário para o qual o MFA existe. Em vez disso "
            "a resposta traz ``mfa_required=true`` mais um ``mfa_token`` "
            "de vida curta; troque-o pelo par em ``/mfa/verify``.\n\n"
            "**Recuperação de conta.** Na mesma transação todas as "
            "sessões guardadas do usuário são revogadas e, com WebAuthn "
            "ligado, todas as "
            "passkeys do usuário são apagadas — uma cadastrada enquanto "
            "outra pessoa segurava a conta continuaria entrando. O dono "
            "cadastra as passkeys de novo.\n\n"
            "!!! note\n"
            "    Com ``AUTH_BACKEND_LINKS=True`` o SDK também monta "
            "    **GET/POST** ``/password-reset/{token}``, que renderizam "
            "    um formulário HTML completo e a página de resultado a "
            "    partir do backend — use esses quando não houver frontend."
        ),
    ),
    "auth.me": RouteDoc(
        summary="Devolver a conta autenticada",
        description=(
            "Resolve o ``access_token`` bearer para a conta dona dele.\n\n"
            "Dois trabalhos num endpoint só: diz ao cliente **quem** está "
            "logado sem que ele precise guardar um perfil em cache, e diz "
            "se um token guardado **ainda vale** — um **401** é o sinal "
            "para chamar ``/refresh`` antes de mandar o usuário de volta "
            "à tela de login.\n\n"
            "A resposta nunca traz o hash da senha. O FastAPI serializa "
            "pelo response model, então só os campos declarados saem; um "
            "projeto que quer colunas extras herda de ``AuthUserSchema`` e "
            "o passa como ``me_response_model``.\n\n"
            "Responde **404** quando o token é bem formado e não expirou, "
            "mas a conta por trás dele não existe mais."
        ),
    ),
    "auth.password_change": RouteDoc(
        summary="Trocar a própria senha (já logado)",
        description=(
            "Troca a senha do usuário **atualmente autenticado** (exige um "
            "``access_token`` bearer válido).\n\n"
            "Diferente do fluxo de esqueci-minha-senha, **não há token** — "
            "o usuário já está logado. Ele precisa redigitar a "
            "``current_password`` para confirmar a posse da conta; se não "
            "bater, a resposta é **401**. A ``new_password`` precisa "
            "cumprir ``AUTH_PASSWORD_MIN_LENGTH`` (e as regras de "
            "complexidade quando ``AUTH_PASSWORD_REQUIRE_COMPLEXITY=True``); "
            "violações respondem **422**.\n\n"
            "**Todas as sessões terminam**, inclusive o refresh token de "
            "quem chamou: a senha antiga pode estar com outra pessoa. A "
            "resposta é **200** com um par novo para o dispositivo que "
            "trocou a senha continuar logado — no corpo para quem chamou "
            "com bearer, em cookies para quem se autenticou pelo cookie de "
            "acesso. O par repete o ``amr`` do access token que autenticou a "
            "requisição. O access token antigo vale até expirar; descarte-o "
            "e use o novo."
        ),
    ),
    "auth.email_change_request": RouteDoc(
        summary="Iniciar a troca de e-mail (já logado)",
        description=(
            "Prepara a mudança para um e-mail novo do usuário "
            "**atualmente autenticado** (exige um ``access_token`` bearer "
            "válido). O usuário redigita a ``current_password`` para "
            "confirmar a posse da conta; se não bater, a resposta é "
            "**401**, e um endereço já em uso responde **409**.\n\n"
            "Um link de confirmação de uso único é enviado para o "
            "endereço **novo** (válido por "
            "``AUTH_EMAIL_CHANGE_TTL_SECONDS``); o e-mail da conta só muda "
            "depois que esse link é confirmado via "
            "``POST /auth/email-change/confirm``. Com "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` ou sem o extra "
            "``[email]``, o ``confirm_url`` pronto para uso volta no corpo "
            "em vez de ir por e-mail. Um e-mail de confirmação que o "
            "servidor SMTP não aceita responde **503** "
            "``EMAIL_UNAVAILABLE`` e não grava token."
        ),
    ),
    "auth.email_change_confirm": RouteDoc(
        summary="Concluir a troca de e-mail (aplicar o novo endereço) — JSON",
        description=(
            "Consome o token de uso único do link de confirmação e troca "
            "o e-mail da conta pelo endereço preparado. Um token "
            "desconhecido, já usado ou expirado responde **400**; um "
            "endereço de destino ocupado nesse meio-tempo responde "
            "**409**.\n\n"
            "Com ``AUTH_EMAIL_CHANGE_NOTIFY_OLD=True`` um aviso de "
            "segurança é enviado ao endereço anterior quando a troca dá "
            "certo; se esse aviso não puder ser enviado, a resposta é "
            "**503** ``EMAIL_UNAVAILABLE``, o endereço não muda e o link "
            "continua válido.\n\n"
            "!!! note\n"
            "    Com ``AUTH_BACKEND_LINKS=True`` o SDK também monta "
            "    **GET** ``/email-change/{token}``, que renderiza uma "
            "    página HTML de resultado a partir do backend."
        ),
    ),
    "auth.email_verify_request": RouteDoc(
        summary="Reenviar o link de verificação do e-mail atual",
        description=(
            "Emite um link de verificação novo para o e-mail atual do "
            "usuário **atualmente autenticado** (exige um ``access_token`` "
            "bearer válido). Nenhum endereço muda — confirmar o link marca "
            "a conta como ativa. Útil quando o e-mail de ativação original "
            "se perdeu. O link vai por e-mail, ou volta como "
            "``confirm_url`` com ``AUTH_RETURN_TOKEN_IN_RESPONSE=True``. "
            "Um e-mail que o servidor SMTP não aceita responde **503** "
            "``EMAIL_UNAVAILABLE`` e não grava token."
        ),
    ),
    "auth.email_verify_confirm": RouteDoc(
        summary="Confirmar o e-mail atual (marcar a conta como verificada) — JSON",
        description=(
            "Consome o token de uso único do link de verificação e marca "
            "a conta como ativa. Um token desconhecido, já usado ou "
            "expirado responde **400**."
        ),
    ),
    "auth.email_recovery_request": RouteDoc(
        summary="Recuperar uma conta cuja caixa de e-mail não está mais acessível",
        description=(
            "Ponto de entrada **sem autenticação** para quem perdeu o "
            "acesso ao próprio e-mail. Montado só com "
            "``AUTH_EMAIL_RECOVERY_ENABLED=True``.\n\n"
            "A conta é localizada pelo ``email`` atual (o antigo); a "
            "identidade é provada pela ``current_password`` e — quando a "
            "conta tem MFA cadastrado — por um ``mfa_code`` válido. Em "
            "caso de sucesso, um link de confirmação vai para o endereço "
            "**novo** e um aviso de segurança para o antigo.\n\n"
            "**Sempre responde 202** com a mesma mensagem genérica para "
            "toda falha branda (e-mail desconhecido, senha errada, código "
            "MFA ausente ou inválido), para que o endpoint não sirva para "
            "enumerar contas. Depois que a identidade está provada, um "
            "e-mail de confirmação que o servidor SMTP não aceita responde "
            "**503** ``EMAIL_UNAVAILABLE`` (como um ``new_email`` já em uso "
            "responde **409**) e não grava token."
        ),
    ),
    "auth.refresh": RouteDoc(
        summary="Trocar um refresh token por um par JWT novo",
        description=(
            "Gera um par novo de ``access_token`` + ``refresh_token`` a "
            "partir de um **refresh token** válido — sem e-mail nem senha. "
            "É assim que o cliente mantém a sessão viva depois que o "
            "``access_token`` de vida curta expira: reenvie aqui o "
            "``refresh_token`` de vida longa em vez de obrigar o usuário a "
            "entrar de novo.\n\n"
            "Um *access* token roubado e reenviado aqui é recusado com "
            "**401**. Um token expirado, malformado, revogado ou assinado "
            "com outra chave também responde **401**, e uma conta inativa "
            "responde **403**.\n\n"
            "!!! warning\n"
            "    Os dois tokens **rotacionam**: a resposta traz um refresh "
            "    token novo. Guarde esse e descarte o que você enviou.\n\n"
            '!!! info "Stateless vs. persistido no banco"\n'
            "    Quando o serviço é configurado com um "
            "    ``refresh_token_model``, o refresh token é um valor "
            "    **opaco e de uso único**: o token apresentado é invalidado "
            "    na rotação, reenviar um token já rotacionado é tratado como "
            "    roubo e **revoga a família inteira de tokens** (401), e "
            "    ``POST /auth/logout`` pode encerrar uma sessão antes da "
            "    hora. Sem esse model o SDK usa um refresh token JWT "
            "    stateless — o token antigo continua válido até a própria "
            "    expiração e não pode ser revogado."
        ),
    ),
    "auth.signup": RouteDoc(
        summary="Cadastrar uma conta nova (e-mail + senha)",
        description=(
            "Cria um usuário novo a partir de um e-mail, uma senha e um "
            "nome de exibição opcional.\n\n"
            "**Política de senha.** A senha precisa cumprir "
            "``AUTH_PASSWORD_MIN_LENGTH`` (e as regras de complexidade de "
            "caracteres quando ``AUTH_PASSWORD_REQUIRE_COMPLEXITY=True``); "
            "violações respondem **422**. Um e-mail duplicado responde "
            "**409**.\n\n"
            "**O que acontece depois depende de ``AUTH_AUTO_ACTIVATE``:**\n\n"
            "* ``AUTH_AUTO_ACTIVATE=True`` — a conta fica ativa na hora e "
            "a resposta **201** já traz o par JWT ``access_token`` + "
            "``refresh_token`` (``activation_required=false``).\n"
            "* ``AUTH_AUTO_ACTIVATE=False`` (padrão) — a conta começa "
            "inativa e um token de ativação é emitido "
            "(``activation_required=true``). Com ``EmailUtils`` "
            "configurado o link de ativação é **enviado por e-mail** (no "
            "idioma de ``AUTH_DEFAULT_LOCALE``) e ``activation_url`` é "
            "``null``; sem e-mail configurado — ou com "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` — o ``activation_url`` "
            "pronto para uso volta no corpo, para você concluir a ativação "
            "sem SMTP.\n\n"
            "**Falha de SMTP.** Quando o e-mail de ativação não pode ser "
            "enviado, a resposta é **503** ``EMAIL_UNAVAILABLE`` e nenhuma "
            "conta é criada — o mesmo endereço pode se cadastrar de novo "
            "quando o servidor voltar.\n\n"
            "**Limite de tentativas.** Com ``signup_throttle`` "
            "configurado, as tentativas além do limite para um mesmo IP "
            "de cliente respondem **429** ``TOO_MANY_REQUESTS`` com "
            "``Retry-After``."
        ),
    ),
    "auth.logout": RouteDoc(
        summary="Revogar um refresh token (logout)",
        description=(
            "Revoga um **refresh token persistido no banco** para que ele "
            "não possa mais ser trocado em ``POST /auth/refresh``, mesmo "
            "antes da expiração natural.\n\n"
            "Por padrão só a **família** de rotação do token apresentado "
            "(a linhagem que descende de um mesmo login) é revogada — "
            "assim um token descendente que esteja com um ladrão morre "
            "junto. Passe ``all_sessions=true`` para revogar **todos** os "
            "refresh tokens ativos do usuário (sair de todos os "
            "dispositivos).\n\n"
            "O endpoint é **idempotente**: um token desconhecido ou já "
            "revogado também responde **204** e nunca revela se o token "
            "existia.\n\n"
            "!!! note\n"
            "    Montado só quando o serviço é configurado com um "
            "    ``refresh_token_model``. Refresh tokens JWT stateless não "
            "    podem ser revogados, então o endpoint não existe nesse "
            "    modo."
        ),
    ),
    "auth.login_cookie": RouteDoc(
        summary="Entrar — gravar o par JWT em cookies HttpOnly",
        description=(
            "Mesma checagem de credenciais de ``POST /auth/login``, mas o "
            "``access_token`` / ``refresh_token`` voltam como cookies "
            "``HttpOnly`` em vez de no corpo — no corpo eles ficam "
            "``null``. O ramo de MFA não muda: quando um segundo fator é "
            "exigido a resposta continua trazendo ``mfa_required=true`` + "
            "``mfa_token`` e não grava cookie de sessão. Compartilha os "
            "limites de tentativa com ``POST /auth/login``: o mesmo limite "
            "por e-mail, o mesmo **429**."
        ),
    ),
    "auth.refresh_cookie": RouteDoc(
        summary="Rotacionar o par JWT a partir do cookie de refresh",
        description=(
            "Gera um par novo de ``access_token`` / ``refresh_token`` a "
            "partir do **cookie de refresh** (sem corpo) e grava o par "
            "rotacionado de volta nos cookies. Responde **401** quando o "
            "cookie de refresh está ausente, expirado, revogado ou é na "
            "verdade um access token; **403** para uma conta inativa."
        ),
    ),
    "auth.logout_cookie": RouteDoc(
        summary="Apagar os cookies de autenticação (e revogar o refresh token)",
        description=(
            "Apaga os dois cookies de autenticação. Quando o serviço é "
            "configurado com um ``refresh_token_model`` e há um cookie de "
            "refresh, a **família** de rotação dele também é revogada, "
            "para a sessão morrer no servidor. Sempre responde **204**, "
            "mesmo sem cookie nenhum (idempotente)."
        ),
    ),
    "auth.mfa_enroll": RouteDoc(
        summary=(
            "Iniciar o cadastro do TOTP (devolve o segredo e os códigos de "
            "recuperação uma vez)"
        ),
        description=(
            "Inicia o cadastro do segundo fator para o usuário "
            "**atualmente autenticado** (exige um ``access_token`` bearer "
            "válido).\n\n"
            "Gera um ``secret`` TOTP novo e uma ``provisioning_uri`` que "
            "você renderiza como QR code para um app autenticador (Google "
            "Authenticator, 1Password etc.), mais um lote de "
            "``recovery_codes`` de uso único.\n\n"
            "!!! warning\n"
            "    O segredo e os códigos de recuperação são devolvidos "
            "    **só esta vez** e nunca mais podem ser consultados — "
            "    mostre-os ao usuário na hora. O cadastro só fica ativo "
            "    depois de confirmado em ``/mfa/confirm``.\n\n"
            "Com o MFA já ativo, a resposta é **409** com o código "
            "``MFA_ALREADY_ENROLLED`` e nada muda: um bearer token sozinho "
            "não pode redefinir o segundo fator. Para trocar o segredo, "
            "chame ``/mfa/disable`` (senha + código) e cadastre de novo. "
            "Chamar de novo enquanto um cadastro ainda está pendente "
            "substitui o segredo e os códigos preparados."
        ),
    ),
    "auth.mfa_confirm": RouteDoc(
        summary="Concluir o cadastro do TOTP (verificar o primeiro código)",
        description=(
            "Ativa o cadastro de TOTP iniciado em ``/mfa/enroll`` para o "
            "usuário **atualmente autenticado**.\n\n"
            "Envie o ``code`` de 6 dígitos que o app autenticador mostra "
            "agora — isso prova que o segredo compartilhado foi guardado "
            "corretamente. Um código errado ou expirado responde **401** e "
            "o cadastro continua inativo. Em caso de sucesso, todo "
            "``/login`` seguinte desse usuário passa a ter duas etapas.\n\n"
            "**Todas as outras sessões terminam.** Confirmar revoga todos "
            "os refresh tokens do usuário, para que uma sessão aberta só "
            "com a senha não continue renovando depois do novo fator. A "
            "resposta traz um par novo cujo access token tem "
            '``amr == ["pwd", "otp"]``, para o dispositivo que confirmou '
            "continuar logado: no corpo para quem chamou com bearer, em "
            "cookies para quem se autenticou pelo cookie de acesso."
        ),
    ),
    "auth.mfa_disable": RouteDoc(
        summary="Desligar o TOTP do usuário atual",
        description=(
            "Remove a autenticação de dois fatores do usuário "
            "**atualmente autenticado** e apaga os códigos de recuperação "
            "guardados.\n\n"
            "É preciso se autenticar de novo: quem chama informa a "
            "``password`` da conta **e** um ``code`` TOTP válido no "
            "momento (ou um código de recuperação). Qualquer um dos dois "
            "errado responde **401** e o MFA continua ligado. Em caso de "
            "sucesso o endpoint responde **204** e o ``/login`` volta a "
            "ter uma etapa só."
        ),
    ),
    "auth.mfa_verify": RouteDoc(
        summary="Concluir um login em duas etapas (trocar mfa_token + código)",
        description=(
            "Segunda e última etapa de um login protegido por MFA.\n\n"
            "Quando ``/login`` responde ``mfa_required=true``, ele devolve "
            "um ``mfa_token`` de vida curta. Envie esse token aqui por "
            "POST junto com o ``code`` TOTP de 6 dígitos do momento (ou um "
            "código de recuperação de uso único). Em caso de sucesso o "
            "endpoint devolve o par ``access_token`` + ``refresh_token`` "
            "de verdade, concluindo o login.\n\n"
            "Um ``code`` ou ``mfa_token`` errado ou expirado responde "
            "**401**. Um código TOTP é aceito uma vez só: reenviá-lo, "
            "mesmo dentro da janela de 30 segundos, conta como código "
            "errado. Códigos errados demais para a mesma conta respondem "
            "**429** com ``Retry-After`` até a janela passar, mesmo que o "
            "próximo código esteja certo. Este endpoint **não** precisa "
            "de bearer token — o próprio ``mfa_token`` é a prova de que a "
            "primeira etapa deu certo."
        ),
    ),
    "auth.webauthn_register_begin": RouteDoc(
        summary="Iniciar o cadastro de uma passkey / chave de segurança",
        description=(
            "Primeira metade da cerimônia de cadastro, para o usuário "
            "**atualmente autenticado** (exige um ``access_token`` bearer "
            "válido).\n\n"
            "Só o token não basta: o corpo leva ``current_password`` e, "
            "quando a conta tem MFA ativo, um ``code`` TOTP ou de "
            "recuperação. Uma passkey entra sem nenhum dos dois, então "
            "cadastrá-la pede a mesma prova que desligar o MFA. Senha ou "
            "código errado responde **401**.\n\n"
            "Passe o ``options.publicKey`` devolvido para "
            "``navigator.credentials.create()`` e envie o que ele devolver "
            "para ``/webauthn/register/complete``, junto com o "
            "``challenge_id`` desta resposta.\n\n"
            "As credenciais que a conta já tem vão em "
            "``excludeCredentials``, então cadastrar o mesmo autenticador "
            "duas vezes é recusado pelo próprio dispositivo, em vez de "
            "criar uma duplicata que ninguém consegue distinguir."
        ),
    ),
    "auth.webauthn_register_complete": RouteDoc(
        summary="Concluir o cadastro de uma passkey / chave de segurança",
        description=(
            "Segunda metade da cerimônia de cadastro. Verifica a "
            "attestation contra o challenge emitido por "
            "``/webauthn/register/begin`` e grava a chave pública.\n\n"
            "O challenge é consumido aqui, então uma resposta capturada "
            "não pode ser reenviada. Um ``challenge_id`` errado, expirado "
            "ou já usado responde **401**; um autenticador já cadastrado "
            "responde **422**."
        ),
    ),
    "auth.webauthn_authenticate_begin": RouteDoc(
        summary="Iniciar um login sem senha",
        description=(
            "Primeira metade da cerimônia de login. **Não** precisa de "
            "bearer token — é assim que uma sessão começa.\n\n"
            "Omita ``email`` para o fluxo sem nome de usuário: as opções "
            "não trazem lista de credenciais e o autenticador oferece as "
            "contas que ele guarda. Passe ``email`` para restringir a "
            "cerimônia a uma conta, o que ajuda um autenticador que não "
            "guarda credencial detectável.\n\n"
            "Um endereço desconhecido recebe uma cerimônia normal com a "
            "lista de credenciais vazia — responder diferente "
            "transformaria este endpoint num oráculo de enumeração de "
            "contas."
        ),
    ),
    "auth.webauthn_authenticate_complete": RouteDoc(
        summary="Concluir um login sem senha (devolve o par JWT)",
        description=(
            "Segunda metade da cerimônia de login. Verifica a assertion e "
            "devolve o par ``access_token`` + ``refresh_token``.\n\n"
            "Um challenge inválido ou reenviado, uma credencial "
            "desconhecida, uma assinatura que não confere, um "
            "autenticador cujo contador de assinaturas não avançou (o "
            "sinal de dispositivo clonado da especificação) ou uma conta "
            "inativa respondem **401**.\n\n"
            "O MFA não bloqueia este endpoint: uma passkey com "
            "verificação do usuário já prova a posse *e* um fator local, "
            "que é o motivo de existir a segunda etapa."
        ),
    ),
    "auth.webauthn_list_credentials": RouteDoc(
        summary="Listar os autenticadores cadastrados do usuário atual",
        description=(
            "Toda passkey / chave de segurança que o usuário "
            "**atualmente autenticado** cadastrou, da mais antiga para a "
            "mais nova. Responde ``200`` com a lista vazia quando não há "
            "nenhuma.\n\n"
            "``backed_up`` distingue uma credencial presa ao dispositivo "
            "(perdida junto com ele) de uma sincronizada, e é isso que "
            "decide se a conta ainda precisa de um caminho de recuperação."
        ),
    ),
    "auth.webauthn_delete_credential": RouteDoc(
        summary="Remover um dos autenticadores do usuário atual",
        description=(
            "Apaga uma credencial que pertence ao usuário **atualmente "
            "autenticado**. A busca é restrita a quem chama, então um ID "
            "cadastrado por outra pessoa responde **404** exatamente como "
            "um que não existe.\n\n"
            "Remover a última passkey deixa a conta com os outros fatores "
            "— o endpoint não confere se sobrou algum, porque decidir se "
            "uma senha ainda é uma alternativa aceitável é escolha da "
            "aplicação."
        ),
    ),
    "auth.oauth_login": RouteDoc(
        summary="Iniciar o redirecionamento do login social",
        description=(
            "Gera um ``state`` contra CSRF, guarda-o num cookie "
            "``HttpOnly`` restrito ao prefixo de auth e redireciona o "
            "navegador para a tela de consentimento do provedor com "
            "**302 Found**.\n\n"
            "Aponte um link ou botão direto para esta URL — é uma "
            "navegação, não um XHR: a resposta é um redirecionamento para "
            "outra origem, então o `fetch` ou o segue de forma opaca ou "
            "falha no CORS.\n\n"
            "O cookie de state é sempre gravado com ``SameSite=Lax``, "
            "seja qual for o ``AUTH_COOKIE_SAMESITE``, porque o provedor "
            "devolve o usuário com uma **navegação de topo entre sites** "
            "— com ``Strict`` o cookie não iria junto e todo login "
            "falharia na checagem de state que ele existe para passar. "
            "Ele expira depois de ``AUTH_OAUTH_STATE_TTL_SECONDS`` e leva "
            "a chave do provedor junto do valor aleatório, para que um "
            "state gerado para um provedor não possa ser reaproveitado no "
            "callback de outro.\n\n"
            "Um ``{provider}`` não registrado responde **404**."
        ),
    ),
    "auth.oauth_callback": RouteDoc(
        summary="Concluir o login social e devolver o par JWT",
        description=(
            "Para onde o provedor devolve o usuário. Valida o ``state`` "
            "contra o cookie, troca o ``code`` por tokens, resolve a "
            "identidade para uma conta local e responde com o **mesmo par "
            "JWT** que ``POST /auth/login`` devolve — mesmo claim ``typ``, "
            "mesmo refresh token opaco, mesma família de rotação, mesmo "
            "``POST /auth/logout``.\n\n"
            "**A entrega segue ``AUTH_TOKEN_DELIVERY``**, com uma "
            "diferença em relação ao resto do router: ``both`` grava os "
            "cookies *e* preenche o corpo, em vez de montar um segundo "
            "endpoint. A URL de callback é registrada no provedor, então "
            "uma rota paralela ``/auth/cookie/...`` obrigaria cada console "
            "de provedor a guardar duas URIs de redirecionamento para um "
            "fluxo só.\n\n"
            "Responde **401** quando o state falta ou não confere (um "
            "callback forjado) ou quando o provedor informou um "
            "``error``; **422** quando o provedor não devolveu e-mail, "
            "já que a coluna é ``NOT NULL UNIQUE`` e não há o que gravar; "
            "**409** quando o e-mail já pertence a outra conta e "
            "``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL`` não permite vinculá-lo; "
            "**403** quando a identidade é nova e a criação de conta está "
            "desligada; **404** para um provedor não registrado."
        ),
    ),
    "auth.oauth_token": RouteDoc(
        summary="Entrar com um token do provedor obtido no dispositivo",
        description=(
            "A metade **token-em-mãos** do login social, para clientes "
            "sem navegador para redirecionar: um app nativo roda o SDK do "
            "próprio provedor, termina com um access token na mão e o "
            "envia aqui.\n\n"
            "A sessão devolvida é a mesma de "
            "``/oauth/{provider}/callback`` — mesmos claims, mesma família "
            "de refresh, mesmo ``POST /auth/logout``.\n\n"
            "**O token é conferido contra esta aplicação antes de "
            "qualquer busca.** O endpoint de userinfo de um provedor "
            "responde *de quem* é o token, nunca *para quem ele foi "
            "emitido* — então, sem essa checagem, qualquer app que levou "
            "a vítima por uma tela de consentimento teria um token que "
            "aqui resolve para a vítima, e enviá-lo entregaria a sessão "
            "dela. Um token emitido para outro ``client_id`` é recusado "
            "com **401** ``OAUTH_TOKEN_AUDIENCE_MISMATCH``; um provedor "
            "que não consegue responder a pergunta faz esta rota recusar "
            "com **501** ``OAUTH_AUDIENCE_UNVERIFIABLE`` em vez de confiar "
            "no token.\n\n"
            "Não há ``state`` para comparar — nada foi redirecionado — e "
            "nenhum é necessário: quem chama manda a credencial no corpo, "
            "então não existe navegação do navegador que uma página de "
            "terceiro possa forjar.\n\n"
            "Envie o token no **corpo**. Uma URL chega ao log de acesso, "
            "ao histórico do navegador e a todo header ``Referer`` no "
            "caminho, e este valor está vivo no provedor."
        ),
    ),
    "auth.oauth_list_accounts": RouteDoc(
        summary="Listar as identidades vinculadas à conta atual",
        description=(
            "Todo provedor que o usuário **atualmente autenticado** "
            "vinculou, do mais antigo para o mais novo. Responde **200** "
            "com a lista vazia para uma conta que só usou senha.\n\n"
            "``email_verified`` tem três valores de propósito: ``null`` "
            "significa que o provedor não disse nada sobre o endereço, o "
            "que não é o mesmo que dizer que ele não é verificado."
        ),
    ),
    "auth.oauth_unlink_account": RouteDoc(
        summary="Desvincular um provedor da conta atual",
        description=(
            "Remove o vínculo que pertence ao usuário **atualmente "
            "autenticado**. A busca é restrita a quem chama, então um "
            "provedor vinculado por outra pessoa responde **404** "
            "exatamente como um que nunca foi vinculado.\n\n"
            "A conta mantém a senha e os outros vínculos, então continua "
            "acessível. Desvincular o único provedor de uma conta criada "
            "por um callback deixa ``POST /auth/password-reset/request`` "
            "como caminho de volta — a mesma porta que esse fluxo sempre "
            "ofereceu, já que o e-mail já está na linha."
        ),
    ),
}
"""Operation text of the auth routes, keyed ``auth.<endpoint name>``."""

AUTH_TEXTS: dict[str, str] = {
    "auth.login.429": (
        "Logins com falha demais para este e-mail ou este cliente — tente "
        "de novo depois de ``Retry-After`` segundos."
    ),
    "auth.signup.429": (
        "Cadastros demais a partir deste cliente — tente de novo depois "
        "de ``Retry-After`` segundos."
    ),
    "auth.mfa_enroll.409": "O MFA já está ativo para esta conta.",
}
"""Response descriptions declared on the auth routes."""

__all__: list[str] = ["AUTH_ROUTES", "AUTH_TEXTS"]
