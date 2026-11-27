# Bundled auth flow (signup / activate / login / reset)

Desde v0.31.0 o SDK fornece o ciclo completo de conta local — signup com email/senha, ativação por link, login com JWT pair, reset de senha — via `UserAuthService` + `make_auth_router`. **Endpoints prontos pra mount** (incluindo `POST /auth/refresh` desde v0.65.0), templates Jinja2 bundled, settings flags controlando se o link sai por e-mail ou no body da resposta, e cinco modos pré-pensados: produção, dev com SMTP local, dev sem SMTP, CI e backend-only.

!!! tip "Vá direto ao seu caso"
    A tabela abaixo mapeia **o que o usuário quer fazer** → a seção e os
    endpoints. Comece pelo seu caso; volte ao [setup](auth-flow/setup.md#setup-minimo) só
    quando precisar ligar as peças.

    | Quero… | Está logado? | Seção | Endpoints |
    | --- | --- | --- | --- |
    | Criar conta + ativar por e-mail | — | [Setup](auth-flow/setup.md#setup-minimo) | `signup` → `activate/{token}` |
    | **Testar tudo sem frontend** (curl, Swagger, app mobile) | — | **[Só o backend](auth-flow/backend-only.md)** | `signup` → `activate/{token}` → `login` → rota protegida |
    | **O e-mail de ativação não chegou** | ❌ não | **[Reenviar a ativação](auth-flow/recovery.md#reenviar-a-ativacao)** | `activation/request` → `activate/{token}` |
    | Entrar | — | [Endpoints](auth-flow/endpoints.md) | `login` |
    | **Esqueci minha senha** | ❌ não | **[Recuperação de senha](auth-flow/recovery.md#recuperacao-de-senha)** | `password-reset/request` → `password-reset/confirm` |
    | Trocar minha senha | ✅ sim | [Trocar senha logado](auth-flow/recovery.md#trocar-a-senha-logado) | `password-change` |
    | Trocar meu e-mail | ✅ sim | [Trocar e-mail](auth-flow/email-change.md#trocar-e-mail-logado) | `email-change/request` → `email-change/confirm` |
    | Re-verificar meu e-mail atual | ✅ sim | [Re-verificar e-mail](auth-flow/email-change.md#re-verificar-o-e-mail-atual) | `email-verify/request` → `email-verify/confirm` |
    | **Perdi acesso ao meu e-mail** | ❌ não | **[Recuperação de e-mail](auth-flow/email-change.md#recuperacao-de-e-mail)** | `email-recovery/request` → `email-change/confirm` |
    | Minha sessão expirou | — | [Refresh](auth-flow/email-change.md#renovando-a-sessao-com-o-refresh-token) | `refresh` |

    **Os dois "esqueci"**: senha perdida → [Recuperação de senha](auth-flow/recovery.md#recuperacao-de-senha); caixa de e-mail perdida → [Recuperação de e-mail](auth-flow/email-change.md#recuperacao-de-e-mail).

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [Setup mínimo e `UserTokenModel`](auth-flow/setup.md) | Use para ligar o fluxo: extras, os quatro objetos (`AsyncDatabaseManager`, `EmailUtils`, `UserAuthService`, `make_auth_router`) e o `UserTokenModel` concreto. |
| [Endpoints](auth-flow/endpoints.md) | Consulte a tabela de todos os endpoints com payload e comportamento: sistema fechado, conta criada pelo admin, campos extras no cadastro e limite de tentativas. |
| [Só o backend: do cadastro à rota protegida](auth-flow/backend-only.md) | Use para testar cadastro → ativação → login → rota protegida sem frontend (curl, Swagger, app mobile) e o mesmo ciclo num teste. |
| [Reenviar ativação e recuperar senha](auth-flow/recovery.md) | Use quando o e-mail de ativação não chegou, quando o usuário esqueceu a senha ou quer trocá-la logado. |
| [Trocar e recuperar e-mail](auth-flow/email-change.md) | Use para trocar o e-mail logado, re-verificar o atual, recuperar a conta quando a caixa se perdeu e renovar a sessão com o refresh token. |
| [Settings: variáveis de ambiente](auth-flow/settings.md) | Consulte as variáveis de ambiente do fluxo em grupos tipados: JWT, política de senha, e-mail, TTL, URLs, páginas, idioma, entrega de token e mais. |
| [Códigos de erro do seu produto](auth-flow/errors.md) | Use para trocar os códigos de erro do fluxo pelos do seu produto com `AuthExceptions`. |
| [E-mails: anatomia, marca e templates](auth-flow/emails.md) | Use para entender como link, template e URL se encaixam, pôr a sua marca nos e-mails e sobrescrever os templates Jinja2. |
| [Idioma dos e-mails, páginas e `/docs`](auth-flow/i18n.md) | Use para escolher o idioma padrão, guardar a preferência do usuário, traduzir você mesmo e definir o idioma da documentação das rotas. |
| [Cinco modos de operação e SMTP local](auth-flow/modes.md) | Use para escolher o modo (produção, dev com SMTP local, dev sem SMTP, CI, backend-only) e subir Mailhog ou smtp4dev. |
| [E-mail desligado e SMTP fora do ar](auth-flow/email-failures.md) | Use para saber o que cada rota responde com `EMAIL_ENABLED=false` ou com o SMTP falhando, e quando mandar e-mail em background. |
| [Entrega de token: bearer, cookie ou os dois](auth-flow/token-delivery.md) | Use para decidir se o par de JWT sai no corpo, em cookie ou nos dois. |
| [Pegar o `current_user` da requisição](auth-flow/current-user.md) | Use para injetar o usuário autenticado nas suas rotas, com auth opcional (`soft=True`) e guards dentro do service. |

## Segurança

- **Token armazenado como hash SHA-256.** O plaintext sai pelo e-mail uma única vez; o banco nunca tem como reproduzir o token original. Vazamento da tabela `user_tokens` **não** permite ativação retroativa.
- **One-shot.** `used_at` é carimbado no consume; replay rejeitado com `UnauthorizedException`.
- **TTL-bounded.** `expires_at` calculado a partir de `AUTH_ACTIVATION_TTL_SECONDS` / `AUTH_PASSWORD_RESET_TTL_SECONDS`. Tokens expirados rejeitados.
- **Anti-enumeração.** `POST /auth/password-reset/request` retorna sempre HTTP 202 + corpo genérico, independente de o e-mail existir ou não. `POST /auth/login` levanta a mesma `UnauthorizedException` para email-errado, senha-errada e conta inativa, e as três pagam uma verificação bcrypt (e-mail desconhecido é conferido contra um hash descartável), então o tempo de resposta também não separa os casos. Medido nesta máquina (bcrypt custo 12, `UserAuthService.login` direto, mediana de N=21): senha errada 153,8 ms, e-mail inexistente 153,8 ms; antes da correção, 0,5 ms para o e-mail inexistente.
- **Password floor aplicado duas vezes.** `SignupSchema` valida no input; `UserAuthService` revalida antes do hash — defesa em profundidade caso alguém bypasse o schema.

## Recap

- O fluxo bundled cobre signup, ativação, login, reset de senha e troca de
  e-mail — e o `UserTokenModel` concreto é a única tabela que o seu serviço
  precisa ship.
- Recuperação de senha e recuperação de **e-mail** são fluxos diferentes: um é
  para quem esqueceu a senha, o outro para quem perdeu o acesso à caixa.
- Os cinco modos de operação existem porque "quem renderiza a página" muda por
  projeto: API pura, backend renderizando, SPA, ou combinação.
- `AUTH_TOKEN_DELIVERY` decide se o par de JWT sai no corpo, no cookie ou nos
  dois — e cookie cross-site exige `SameSite=none` mais `Secure`.
- Template e idioma são sobrescrevíveis, mas o default funciona sem você criar
  arquivo nenhum.
- Prefixo do router e URL do template têm de concordar: montar em `/api` com
  template apontando para a raiz dá 404 no clique do usuário, não no deploy.

## Próximos passos

- **[Idempotência »](idempotency.md)** — proteja `POST /auth/signup` de retentativas que duplicariam linha.
- **[Storage MinIO/S3 »](storage.md)** — anexar avatar / foto de perfil já no signup.
- **[Logging »](logging.md)** — `request_id` já propaga automaticamente em cada log emitido durante o flow.
- **[Métricas »](metrics.md)** — `PrometheusMiddleware` conta `/auth/*` separadamente sem config extra.
