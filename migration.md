# Guia de migração

Passo a passo das mudanças que quebram compatibilidade, agrupadas por release minor. Siga a versão que casa com aquela **de onde** você está atualizando. As subpáginas, e as seções dentro de cada uma, estão listadas da mais nova para a mais antiga, então num salto de várias versões leia e aplique-as de baixo para cima.

## Faixas de versão

Uma subpágina por faixa de versão minor, da mais nova para a mais antiga.

| Subpágina | O que resolve |
| --- | --- |
| [Migração 0.37x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.374.0, 0.373.0 e 0.372.0. |
| [Migração 0.36x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-36x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.366.0 e 0.361.0. |
| [Migração 0.34x–0.35x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.353.0, 0.349.0, 0.344.0, 0.343.0, 0.342.0, 0.341.0 e 0.340.0. |
| [Migração 0.33x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.336.0, 0.335.0, 0.332.0, 0.331.0 e 0.330.0. |
| [Migração 0.32x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.327.0, 0.325.0, 0.323.0, 0.322.0, 0.321.0 e 0.320.0. |
| [Migração 0.31x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.319.0, 0.318.0, 0.316.0, 0.314.0, 0.313.0, 0.312.0 e 0.311.0. |
| [Migração 0.30x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.309.0, 0.308.0, 0.306.0, 0.305.0, 0.304.0, 0.302.0, 0.301.0 e 0.300.0. |
| [Migração 0.27x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-27x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.274.0, 0.272.0 e 0.270.0. |
| [Migração 0.26x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.269.0, 0.265.0, 0.264.0, 0.263.0, 0.261.0 e 0.260.0. |
| [Migração 0.25x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.259.0, 0.258.0, 0.257.0, 0.256.0, 0.252.0 e 0.251.0. |
| [Migração 0.22x–0.23x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-22x-0-23x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.234.0, 0.233.0 e 0.229.0. |
| [Migração 0.8–0.17x](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md) | Use ao atualizar a partir de uma versão anterior a estas releases: 0.174.0, 0.173.0, 0.138.1, 0.92.0, 0.63.0 e 0.8.0. |

## Todas as mudanças, por release

Use esta lista para achar a mudança pelo nome; cada item abre a seção na subpágina da faixa.

- [0.374.0 — `Agent` e `AIChatPipeline` rodam chamada de ferramenta escrita no texto](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03740-agent-e-aichatpipeline-rodam-chamada-de-ferramenta-escrita-no-texto)
- [0.374.0 — schema estrito na saída estruturada e imagem truncada recusada](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03740-schema-estrito-na-saida-estruturada-e-imagem-truncada-recusada)
- [0.373.0 — `BaseController` e `BaseService` ligam `ResponseT` à camada de baixo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03730-basecontroller-e-baseservice-ligam-responset-a-camada-de-baixo)
- [0.373.0 — `ObjectStat.raw` pode ser `None`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03730-objectstatraw-pode-ser-none)
- [0.373.0 — `same_site` do `SignedCookieSessionStore` tipado como `Literal`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03730-same_site-do-signedcookiesessionstore-tipado-como-literal)
- [0.373.0 — `BaseUserModel` sem colunas fantasmas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03730-baseusermodel-sem-colunas-fantasmas)
- [0.373.0 — quebras de runtime fora do checker e tipos públicos estreitados](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03730-quebras-de-runtime-fora-do-checker-e-tipos-publicos-estreitados)
- [0.372.0 — cinco regras novas no `tempest check` e `RuntimeError` sem URL no `AlembicHelper`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-37x.md#03720-cinco-regras-novas-no-tempest-check-e-runtimeerror-sem-url-no-alembichelper)
- [0.366.0 — `RoutingBackend` exige `route_through` e `matrix`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-36x.md#03660-routingbackend-exige-route_through-e-matrix)
- [0.361.0 — saque manual ganha quatro colunas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-36x.md#03610-saque-manual-ganha-quatro-colunas)
- [0.353.0 — `google_kwargs()` do Gemini vira `gemini_kwargs()`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03530-google_kwargs-do-gemini-vira-gemini_kwargs)
- [0.349.0 — Coqui no próprio extra, upload grande em 413 e `tempest check` mais rigoroso](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03490-coqui-no-proprio-extra-upload-grande-em-413-e-tempest-check-mais-rigoroso)
- [0.344.0 — token de conta apagada](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03440-token-de-conta-apagada)
- [0.343.0 — todo enum do SDK termina em `Enum`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03430-todo-enum-do-sdk-termina-em-enum)
- [0.342.0 — `ENV` obrigatório e segredo de `X-Token` vazio](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03420-env-obrigatorio-e-segredo-de-x-token-vazio)
- [0.341.0 — webhook do Mercado Pago e fórmula em `.xlsx`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03410-webhook-do-mercado-pago-e-formula-em-xlsx)
- [0.340.0 — cadastro, Web Push e SSE](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-34x-0-35x.md#03400-cadastro-web-push-e-sse)
- [0.336.0 — correções de segurança (tempo real)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03360-correcoes-de-seguranca-tempo-real)
- [0.336.0 — `password-change` devolve um par novo, `set-password` apaga passkeys](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03360-password-change-devolve-um-par-novo-set-password-apaga-passkeys)
- [0.336.0 — CORS, storage, busca textual e Stripe](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03360-cors-storage-busca-textual-e-stripe)
- [0.335.0 — `BaseUserModel` ganha a coluna `email_verified_at`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03350-baseusermodel-ganha-a-coluna-email_verified_at)
- [0.335.0 — correções de segurança que mudam comportamento](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03350-correcoes-de-seguranca-que-mudam-comportamento)
- [0.332.0 — DLQ fixa a política de ack do consumidor](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03320-dlq-fixa-a-politica-de-ack-do-consumidor)
- [0.331.0 — visual novo do admin, upload com teto no storage](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03310-visual-novo-do-admin-upload-com-teto-no-storage)
- [0.330.0 — inscrição de push não muda mais de dono](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-33x.md#03300-inscricao-de-push-nao-muda-mais-de-dono)
- [0.327.0 — regras de arquitetura da #616, como aviso](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md#03270-regras-de-arquitetura-da-616-como-aviso)
- [0.325.0 — CSRF no login do admin e o extra `[cli-ops]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md#03250-csrf-no-login-do-admin-e-o-extra-cli-ops)
- [0.323.0 — a topologia de dead-letter sobe antes dos consumidores](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md#03230-a-topologia-de-dead-letter-sobe-antes-dos-consumidores)
- [0.322.0 — falha de SMTP vira `503 EMAIL_UNAVAILABLE` e o guard confere TLS × porta](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md#03220-falha-de-smtp-vira-503-email_unavailable-e-o-guard-confere-tls-porta)
- [0.321.0 — `SubjectStorageClient` ganha três métodos](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md#03210-subjectstorageclient-ganha-tres-metodos)
- [0.320.0 — guard de produção do e-mail e dos links](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-32x.md#03200-guard-de-producao-do-e-mail-e-dos-links)
- [0.319.0 — contagem do `bulk_create_values` e do `bulk_upsert`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03190-contagem-do-bulk_create_values-e-do-bulk_upsert)
- [0.318.0 — leitores de log, `log_dir` vazio, IP do header e login do `/admin`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03180-leitores-de-log-log_dir-vazio-ip-do-header-e-login-do-admin)
- [0.316.0 — `set-password` pede o fator em conta com MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03160-set-password-pede-o-fator-em-conta-com-mfa)
- [0.316.0 — o segundo item do `UserAuthService.signup` pode ser `ActivationWithheld`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03160-o-segundo-item-do-userauthservicesignup-pode-ser-activationwithheld)
- [0.314.0 — Python 3.12 passa a ser o mínimo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03140-python-312-passa-a-ser-o-minimo)
- [0.314.0 — login social de conta com TOTP pede o segundo fator](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03140-login-social-de-conta-com-totp-pede-o-segundo-fator)
- [0.314.0 — ativação e reset por link carimbam `amr=["email"]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03140-ativacao-e-reset-por-link-carimbam-amremail)
- [0.313.0 — classe terminada em `Enum` não dobra mais o sufixo do tipo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03130-classe-terminada-em-enum-nao-dobra-mais-o-sufixo-do-tipo)
- [0.313.0 — o 429 do auth tem código próprio](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03130-o-429-do-auth-tem-codigo-proprio)
- [0.313.0 — refresh token guarda o `amr`, e `/mfa/confirm` responde `200`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03130-refresh-token-guarda-o-amr-e-mfaconfirm-responde-200)
- [0.312.0 — Mercado Pago: Pix vencido e busca de chargebacks](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03120-mercado-pago-pix-vencido-e-busca-de-chargebacks)
- [0.311.0 — Mercado Pago: a API de Payments saiu do SDK](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-31x.md#03110-mercado-pago-a-api-de-payments-saiu-do-sdk)
- [0.309.0 — outras mudanças de comportamento](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03090-outras-mudancas-de-comportamento)
- [0.309.0 — chave desconhecida em `filters` levanta erro](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03090-chave-desconhecida-em-filters-levanta-erro)
- [0.308.0 — o CLI `tempest` virou o extra `[cli]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03080-o-cli-tempest-virou-o-extra-cli)
- [0.306.0 — código TOTP de uso único pede a coluna `totp_last_step`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03060-codigo-totp-de-uso-unico-pede-a-coluna-totp_last_step)
- [0.306.0 — `make_logs_router` recusa segredo vazio](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03060-make_logs_router-recusa-segredo-vazio)
- [0.306.0 — download só vai `inline` para tipo seguro](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03060-download-so-vai-inline-para-tipo-seguro)
- [0.305.0 — token de conta desativada não autentica mais](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03050-token-de-conta-desativada-nao-autentica-mais)
- [0.305.0 — troca de senha com usuário removido é link inválido](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03050-troca-de-senha-com-usuario-removido-e-link-invalido)
- [0.305.0 — o signup grava os campos do schema que são coluna](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03050-o-signup-grava-os-campos-do-schema-que-sao-coluna)
- [0.304.0 — o leitor CSV do Google Sheets tem limite por padrão](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03040-o-leitor-csv-do-google-sheets-tem-limite-por-padrao)
- [0.304.0 — o SQLite confere chave estrangeira](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03040-o-sqlite-confere-chave-estrangeira)
- [0.304.0 — erro cru de ferramenta não chega mais ao modelo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03040-erro-cru-de-ferramenta-nao-chega-mais-ao-modelo)
- [0.302.0 — constraint composta leva todas as colunas no nome](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03020-constraint-composta-leva-todas-as-colunas-no-nome)
- [0.301.0 — CSS, `DataTable`, formulários, SSR e sessões](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03010-css-datatable-formularios-ssr-e-sessoes)
- [0.300.0 — o anexo do chat registra quem fez o upload](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03000-o-anexo-do-chat-registra-quem-fez-o-upload)
- [0.300.0 — o resto das quebras: IA, chat, métricas e migrations](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-30x.md#03000-o-resto-das-quebras-ia-chat-metricas-e-migrations)
- [0.274.0 — só o link mais recente abre a conta](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-27x.md#02740-so-o-link-mais-recente-abre-a-conta)
- [0.272.0 — os checks passaram a ver o que já estava errado](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-27x.md#02720-os-checks-passaram-a-ver-o-que-ja-estava-errado)
- [0.270.0 — `PixCharge.raw` usa a grafia do fio](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-27x.md#02700-pixchargeraw-usa-a-grafia-do-fio)
- [0.270.0 — `PaymentStatus` ganhou `UNKNOWN`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-27x.md#02700-paymentstatus-ganhou-unknown)
- [0.269.0 — `Charge.expires_in` virou `int`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md#02690-chargeexpires_in-virou-int)
- [0.265.0 — array opcional em corpo de request virou `| None`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md#02650-array-opcional-em-corpo-de-request-virou-none)
- [0.264.0 — o idioma passa a ser do fluxo, não de cada ponta](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md#02640-o-idioma-passa-a-ser-do-fluxo-nao-de-cada-ponta)
- [0.263.0 — `AdminAuthBackend` virou genérica](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md#02630-adminauthbackend-virou-generica)
- [0.261.0 — o router WebSocket manda um `hello` antes de tudo](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md#02610-o-router-websocket-manda-um-hello-antes-de-tudo)
- [0.260.0 — a spec da OpenPix foi refrescada, e os métodos mudaram de nome](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-26x.md#02600-a-spec-da-openpix-foi-refrescada-e-os-metodos-mudaram-de-nome)
- [0.259.0 — dinheiro da OpenPix passa a ser `int`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md#02590-dinheiro-da-openpix-passa-a-ser-int)
- [0.258.0 — os arquivos de log passam a rotacionar](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md#02580-os-arquivos-de-log-passam-a-rotacionar)
- [0.257.0 — os fakes de agente passam a chamar o parâmetro de `tools`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md#02570-os-fakes-de-agente-passam-a-chamar-o-parametro-de-tools)
- [0.256.0 — o 429 do `RateLimitMiddleware` passa a ser JSON](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md#02560-o-429-do-ratelimitmiddleware-passa-a-ser-json)
- [0.252.0 — `:memory:` do SQLite ganha conexão por sessão](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md#02520-memory-do-sqlite-ganha-conexao-por-sessao)
- [0.251.0 — a assinatura de webhook do Mercado Pago passa a seguir o algoritmo do provedor](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-25x.md#02510-a-assinatura-de-webhook-do-mercado-pago-passa-a-seguir-o-algoritmo-do-provedor)
- [0.234.0 — modelo gerado se constrói pelo nome Python, e o type-checker aceita](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-22x-0-23x.md#02340-modelo-gerado-se-constroi-pelo-nome-python-e-o-type-checker-aceita)
- [0.233.0 — dois enums gerados da OpenPix mudaram de nome](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-22x-0-23x.md#02330-dois-enums-gerados-da-openpix-mudaram-de-nome)
- [0.229.0 — saída estruturada do Ollama sai de `/api/generate` para `/api/chat`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-22x-0-23x.md#02290-saida-estruturada-do-ollama-sai-de-apigenerate-para-apichat)
- [0.174.0 — erros que eram 500 viram 422, e o `order_by` é validado](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md#01740-erros-que-eram-500-viram-422-e-o-order_by-e-validado)
- [0.173.0 — token só vale onde foi emitido pra valer, e cache não é mais compartilhado](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md#01730-token-so-vale-onde-foi-emitido-pra-valer-e-cache-nao-e-mais-compartilhado)
- [0.138.1 — `BaseAppSettings` tem que ser a **última** base](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md#01381-baseappsettings-tem-que-ser-a-ultima-base)
- [0.92.0 — coluna `payload` no token de usuário](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md#0920-coluna-payload-no-token-de-usuario)
- [0.63.0 — usuário autenticado carregado na sessão de request](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md#0630-usuario-autenticado-carregado-na-sessao-de-request)
- [0.8.0 — renomeação de `ServerSettings`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration/0-8-0-17x.md#080-renomeacao-de-serversettings)
