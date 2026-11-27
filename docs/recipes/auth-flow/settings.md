---
description: "Consulte as variáveis de ambiente do fluxo de auth por grupo tipado: JWT, senha, e-mail, TTL, URLs, páginas, idioma, entrega de token, OAuth e conta desativada."
---

# Settings: variáveis de ambiente

Consulte as variáveis de ambiente do fluxo em grupos tipados: JWT, política de senha, e-mail, TTL, URLs, páginas, idioma, entrega de token e mais.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md).

Toda a config do flow vem de mixins de settings. Mixe-as na sua classe `Settings`
(tabela de todos os mixins e a regra de ordem em [Settings](../settings.md)):

```python
# src/core/settings.py
from tempest_fastapi_sdk import (
    AuthSettings,
    BaseAppSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    ServerSettings,
)


class Settings(
    ServerSettings,
    DatabaseSettings,
    EmailSettings,
    JWTSettings,
    AuthSettings,
    BaseAppSettings,
):
    pass


settings: Settings = Settings()
```

!!! info "Nome do atributo **é** o nome da env var"
    Cada atributo das tabelas abaixo é lido de uma variável de ambiente de **mesmo nome**, case-sensitive, **sem prefixo**. `AUTH_PASSWORD_MIN_LENGTH` no `.env` → `settings.AUTH_PASSWORD_MIN_LENGTH`. Todos têm default — você só seta o que quer mudar.

As variáveis se dividem em **dois mixins** e **seis grupos de concern**. Elas estão separadas de propósito: senha não é a mesma coisa que e-mail, e autenticação (JWT) não é a mesma coisa que ativação de conta.

## Grupo 1 — Autenticação / JWT (`JWTSettings`)

Controla a assinatura e validade dos tokens que o login devolve. **É o mesmo `JWT_SECRET` que a dependency `get_current_user` usa pra validar** (veja [Pegando o `current_user`](current-user.md)).

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `JWT_SECRET` | `str` (≥32 bytes) | `change-me-…-32` | Segredo HMAC que assina o JWT. **Obrigatório trocar em produção.** |
| `JWT_ALGORITHM` | `str` | `HS256` | Algoritmo JOSE. `HS256`/`HS512` (segredo simétrico) ou `RS256` (par de chaves). |
| `JWT_ACCESS_TTL_SECONDS` | `int` (≥1) | `3600` | Validade do **access token** (1 h). Curto por design — renove via refresh. |
| `JWT_REFRESH_TTL_SECONDS` | `int` (≥1) | `604800` | Validade do **refresh token** (7 dias). |
| `JWT_ISSUER` | ``str | None`` | `None` | Claim `iss`. `None` omite o claim. |

!!! danger "`JWT_SECRET` default vaza tokens"
    O default `change-me-change-me-change-me-32` existe só pra subir local. Em produção, **qualquer um** com o default consegue forjar um JWT válido. Gere um segredo forte (`openssl rand -base64 48`) e injete por secret manager — nunca commite.

## Grupo 2 — Política de senha (`AuthSettings`)

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_PASSWORD_MIN_LENGTH` | `int` (≥1) | `8` | Comprimento mínimo aceito no signup **e** no reset. |
| `AUTH_PASSWORD_MAX_BYTES` | `int` (≥1) | `72` | Comprimento máximo em **bytes** UTF-8. É o limite duro do bcrypt. |
| `AUTH_PASSWORD_REQUIRE_COMPLEXITY` | `bool` | `false` | `true` = exige 1 minúscula + 1 maiúscula + 1 dígito + 1 caractere especial. |

Os dois interagem — **é aqui que costuma confundir**. A regra exata:

- **`complexity=false` (default):** só o comprimento importa. Qualquer senha com `≥ AUTH_PASSWORD_MIN_LENGTH` caracteres passa, sem exigência de composição.
- **`complexity=true`:** além das 4 classes de caracteres, o piso de comprimento **efetivo** vira `max(AUTH_PASSWORD_MIN_LENGTH, 8)`. Ou seja, um `AUTH_PASSWORD_MIN_LENGTH` abaixo de 8 é **ignorado** enquanto a complexidade está ligada.

Tabela de decisão:

| `MIN_LENGTH` | `REQUIRE_COMPLEXITY` | Senha aceita quando |
|--------------|---------------------|---------------------|
| `12` | `false` | `≥ 12` chars, qualquer composição |
| `4` | `false` | `≥ 4` chars, qualquer composição (piso baixo, dev-only) |
| `4` | `true` | `≥ 8` chars (piso 8 **sobrescreve** o 4) **+** as 4 classes |
| `16` | `true` | `≥ 16` chars **+** as 4 classes |

!!! warning "O piso é fonte única da verdade"
    Os schemas de request (`SignupSchema`, `PasswordResetConfirmSchema`) **não** impõem limite próprio de comprimento — eles delegam pra essas duas vars. Baixar `AUTH_PASSWORD_MIN_LENGTH` pra `4` realmente afrouxa a validação na rota também. Não há um segundo limite escondido no schema "te protegendo".

!!! danger "O teto conta bytes, não caracteres"
    `AUTH_PASSWORD_MAX_BYTES` existe porque bcrypt **recusa** entrada acima de 72 bytes: `hashpw` levanta `ValueError`, e sem esse teto o erro subia como **500** no signup / reset / troca de senha. Bytes é a unidade que o hash vê, e 72 bytes chegam bem antes de 72 caracteres em texto não-ASCII — um emoji custa 4 bytes, uma letra acentuada 2, então `"🔒" * 19` (19 caracteres) já passa do limite e responde **422**. Só aumente o valor se você trocar o hasher por um sem esse limite.

!!! info "Cada violação tem o seu `message_key` (v0.305.0+)"
    O `code` da resposta continua `VALIDATION_ERROR`, mas o `message_key` diz
    qual regra caiu, e o catálogo default traduz cada uma:

    | `message_key` | Quando | `detail` em pt-BR (catálogo default) |
    |---------------|--------|--------------------------------------|
    | `PASSWORD_TOO_SHORT` | abaixo do piso efetivo | `A senha precisa ter pelo menos 8 caracteres` |
    | `PASSWORD_TOO_LONG` | acima de `AUTH_PASSWORD_MAX_BYTES` | `A senha pode ter no máximo 72 bytes` |
    | `PASSWORD_TOO_WEAK` | falta classe de caractere (complexidade ligada) | `A senha precisa ter letra minúscula, letra maiúscula, número e caractere especial` |

    O mesmo valor está em `PasswordPolicyViolation.code`
    (`PasswordViolationCodeEnum`), para quem chama `check_password_policy` direto.
    Para trocar também o `code`, veja
    [os códigos de erro do seu produto](errors.md).

## Grupo 3 — Controle do fluxo de e-mail (`AuthSettings`)

Decidem **se** e **como** o link chega ao usuário. Mapeiam direto nos [cinco modos de operação](modes.md#cinco-modos-de-operacao).

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_AUTO_ACTIVATE` | `bool` | `false` | `true` = user nasce ativo, pula activation, signup devolve JWT pair direto (Modo D). `ENV=production` recusa subir com ele ligado. |
| `AUTH_RETURN_TOKEN_IN_RESPONSE` | `bool` | `false` | `true` = link de ativação/reset vai no corpo JSON em vez do e-mail (Modo C). |
| `AUTH_SEND_EMAILS_IN_BACKGROUND` | `bool` | `false` | `true` = os e-mails das rotas de auth saem **depois** da resposta (`BackgroundTasks`); falha de SMTP vira log, não `503`. Veja [Em background](email-failures.md#em-background-auth_send_emails_in_background). |

## Grupo 4 — TTL dos tokens de conta (`AuthSettings`)

Validade dos tokens **de uso único** (ativação / reset) — distintos dos JWT do grupo 1.

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_ACTIVATION_TTL_SECONDS` | `int` (≥60) | `604800` | Validade do token de ativação (7 dias). |
| `AUTH_PASSWORD_RESET_TTL_SECONDS` | `int` (≥60) | `3600` | Validade do token de reset (1 h). Curto é mais seguro. |

## Grupo 5 — URLs e templates de e-mail (`AuthSettings`)

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_ACTIVATION_URL_TEMPLATE` | `str` | `http://localhost:3000/activate?token={token}` | Link de ativação que vai no e-mail (ou no corpo do `signup`, quando não há e-mail); `{token}` é substituído. Aponta para **quem vai abrir o link** — frontend, backend ou app. Tabela por modo logo abaixo. |
| `AUTH_PASSWORD_RESET_URL_TEMPLATE` | `str` | `http://localhost:3000/reset-password?token={token}` | Idem, para reset. |
| `AUTH_ACTIVATION_TEMPLATE` | `str` | `activation.html` | Nome do arquivo Jinja2 do **HTML do e-mail** de ativação, resolvido no `template_dir` do `EmailUtils`. |
| `AUTH_PASSWORD_RESET_TEMPLATE` | `str` | `password_reset.html` | Idem, para reset. |
| `AUTH_EMAIL_BRAND_NAME` | `str` | `""` | Nome no cabeçalho dos e-mails; vazio deixa o cabeçalho de fora. Veja [A marca nos e-mails](emails.md#a-marca-nos-e-mails). |
| `AUTH_EMAIL_BRAND_COLOR` | `str` | `#2563eb` | Cor (`#rgb`/`#rrggbb`) do botão e dos destaques; o texto do botão escurece sozinho numa cor clara. |
| `AUTH_EMAIL_BRAND_LOGO_URL` | `str` | `""` | URL absoluta `http(s)` do logo no cabeçalho, no lugar do nome. |
| `AUTH_EMAIL_FOOTER_TEXT` | `str` | `""` | Linha extra no rodapé (razão social, endereço). |

### Para onde o `AUTH_ACTIVATION_URL_TEMPLATE` aponta

O template é só uma string `.format()`: o SDK troca `{token}` e não sabe
quem vai abrir o resultado. Quem decide é o modo:

| Modo | Quem abre o link | Template | Rota que consome o token |
| --- | --- | --- | --- |
| [A](modes.md#modo-a-producao), [B](modes.md#modo-b-dev-com-smtp-local-mailhog-ou-smtp4dev), [C](modes.md#modo-c-dev-sem-smtp-link-no-body) | página do **frontend**, que lê `?token=` e chama a API | `https://app.example.com/activate?token={token}` | `POST /auth/activate/{token}` |
| [E — backend-only](modes.md#modo-e-backend-only-v0320) | o próprio **backend**: `AUTH_BACKEND_LINKS=true` monta uma página HTML em `GET /auth/activate/{token}` que ativa a conta no clique | `https://api.example.com/auth/activate/{token}` (com o prefixo do router, se houver) | `GET /auth/activate/{token}` |
| App mobile | o **app**, por deep link | `meuapp://ativar?token={token}` | `POST /auth/activate/{token}` |
| [D](modes.md#modo-d-ci-testes-skip-total) | ninguém — a conta nasce ativa | irrelevante | nenhuma |

**Dev sem frontend:** o default aponta para `localhost:3000`, uma página que
não existe no seu caso. Ligue o Modo E com o template no próprio backend
(`http://127.0.0.1:8000/auth/activate/{token}`), ou deixe o template como
está e mande o token para `POST /auth/activate/{token}`. O passo a passo, com
as respostas medidas, está em
[Só o backend](backend-only.md).

!!! warning "URL template ≠ Jinja2 template"
    `*_URL_TEMPLATE` é uma string `.format()` com `{token}` — é o **link**. `*_TEMPLATE` é o nome de um arquivo `.html` — é o **e-mail que embrulha o link**. Confundir os dois é o erro nº 1. Detalhe completo em [Anatomia de um e-mail](emails.md#anatomia-de-um-e-mail).

## Grupo 6 — Páginas renderizadas pelo backend (Modo E, `AuthSettings`)

Só relevantes quando `AUTH_BACKEND_LINKS=true`. Veja o [Modo E](modes.md#modo-e-backend-only-v0320) para o fluxo completo.

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_BACKEND_LINKS` | `bool` | `false` | `true` = monta 5 endpoints HTML extras; o link do e-mail aponta pro **backend**, não pro frontend. |
| `AUTH_LOGIN_URL` | ``str | None`` | `None` | URL de login no botão "Ir pro login" das páginas de sucesso. `None` esconde o botão. |
| `AUTH_ACTIVATION_SUCCESS_TEMPLATE` | `str` | `activation_success.html` | Página HTML de ativação OK. |
| `AUTH_ACTIVATION_ERROR_TEMPLATE` | `str` | `activation_error.html` | Página HTML de ativação com erro. |
| `AUTH_PASSWORD_RESET_FORM_TEMPLATE` | `str` | `password_reset_form.html` | Form HTML de nova senha. |
| `AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE` | `str` | `password_reset_success.html` | Página HTML de reset OK. |
| `AUTH_PASSWORD_RESET_ERROR_TEMPLATE` | `str` | `password_reset_error.html` | Página HTML de reset com erro. |

## Grupo 7 — Idioma dos e-mails e páginas (`AuthSettings`)

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_DEFAULT_LOCALE` | `str` | `pt-BR` | Idioma dos **e-mails** e **páginas HTML** bundled quando nenhum outro sinal existe. Aceita `pt-BR` e `en-US` (normalizado: `PT-BR`, `pt_br`, `ptbr` → `pt-BR`). |
| `AUTH_STAMP_LOCALE_IN_LINK` | `bool` | `True` | Carimba `?lang=<locale>` no link do e-mail, para a página abrir no idioma do e-mail que a produziu. |

Tem uma seção inteira só pra isso, explicada bem devagar: [Idioma dos e-mails e páginas (i18n)](i18n.md#idioma-dos-e-mails-e-paginas-i18n).

## Grupo 8 — Entrega de token: bearer / cookie / both (`AuthSettings`) *(v0.87.0+)*

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_TOKEN_DELIVERY` | ``"bearer" | "cookie" | "both"`` | `bearer` | Como o login/refresh devolvem o par JWT. Veja [Entrega de token](token-delivery.md). |
| `AUTH_COOKIE_SECURE` | `bool` | `true` | Marca os cookies como `Secure` (só trafegam via HTTPS). **Desligue só em HTTP puro** — senão o browser descarta o cookie. |
| `AUTH_COOKIE_SAMESITE` | ``"lax" | "strict" | "none"`` | `lax` | SPA cross-site precisa `none` (+ `Secure=true`). |
| `AUTH_COOKIE_DOMAIN` | ``str | None`` | `None` | `Domain` do cookie. `None` = host exato. Use `.example.com` pra compartilhar entre subdomínios. |
| `AUTH_ACCESS_COOKIE_NAME` | `str` | `access_token` | Nome do cookie do access token. |
| `AUTH_REFRESH_COOKIE_NAME` | `str` | `refresh_token` | Nome do cookie do refresh token (escopado ao path do endpoint de refresh). |

## Grupo 9 — Sistema fechado (`AuthSettings`) *(v0.272.0+)*

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_SIGNUP_ENABLED` | `bool` | `true` | `false` = `make_auth_router` não monta `POST /auth/signup`; a rota some da aplicação e do OpenAPI. Ativação, reset e o resto ficam intactos. Veja [Sistema fechado](endpoints.md#sistema-fechado-sem-porta-de-cadastro-v02720). |

## Grupo 10 — Login social (`AuthSettings` + `OAuthSettings`) *(v0.273.0+)*

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_OAUTH_ENABLED` | `bool` | `false` | `true` monta as cinco rotas `/auth/oauth/*` (`{provider}/login`, `{provider}/callback`, `{provider}/token`, `accounts` e `accounts/unlink`). Exige um client em `oauth_clients=`, um `oauth_account_model` no serviço e a coluna `name` no user model — cada um que faltar levanta `RuntimeError` na construção do router. |
| `AUTH_OAUTH_STATE_COOKIE_NAME` | `str` | `oauth_state` | Cookie que carrega o `state` CSRF entre o redirect e o callback. Sempre `HttpOnly` e sempre `SameSite=Lax`. |
| `AUTH_OAUTH_STATE_TTL_SECONDS` | `int` | `600` | Quanto tempo o usuário tem para concluir o consentimento no provedor. |
| `AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL` | `bool` | `false` | `true` liga uma identidade nova a uma conta existente cujo e-mail bate — **só** quando `email_verified is True` e a conta local tem `email_verified_at`. Desligado por padrão porque é o knob que transforma a palavra do provedor sobre um e-mail em controle de uma conta. |
| `AUTH_OAUTH_ALLOW_ACCOUNT_CREATION` | `bool` ou vazio | vazio | Se o callback pode criar conta. Vazio herda `AUTH_SIGNUP_ENABLED`, então fechar o cadastro fecha essa porta junto. |
| `OAUTH_REDIRECT_BASE_URL` | `str` | `""` | Origem pública do serviço. O redirect URI é derivado dela por `oauth_redirect_uri(provider)`. |
| `OAUTH_GOOGLE_CLIENT_ID` / `OAUTH_GOOGLE_CLIENT_SECRET` | `str` | `""` | Credenciais do console do Google. `google_kwargs()` monta o client. |
| `OAUTH_GOOGLE_EXTRA_AUDIENCES` | `list[str]` | `[]` | Os outros client ids **deste** projeto (Android, iOS). Sem eles, todo login mobile por `POST /auth/oauth/google/token` é recusado como token de outra aplicação. |
| `OAUTH_GITHUB_CLIENT_ID` / `OAUTH_GITHUB_CLIENT_SECRET` | `str` | `""` | Credenciais do OAuth app do GitHub. `github_kwargs()` monta o client. |

## Grupo 11 — Um link por vez (`AuthSettings`) *(v0.274.0+)*

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_SINGLE_ACTIVE_TOKEN` | `bool` | `true` | Emitir um token de conta gasta os outros tokens não usados **do mesmo propósito** daquele usuário, então só o link mais recente de ativação / reset / troca de e-mail abre a conta. `false` restaura o comportamento anterior à v0.274.0. Veja o [guia de migração](../../migration/0-27x.md#02740-so-o-link-mais-recente-abre-a-conta). |

!!! danger "Sem isso, a reação certa do usuário não fecha a janela"
    Um atacante dispara `POST /auth/password-reset/request` para a vítima. A
    vítima recebe um e-mail de recuperação que não pediu, desconfia, e **por
    conta própria** pede um reset e conclui — exatamente a reação correta. Com
    vários links vivos, o link do atacante continua valendo até
    `AUTH_PASSWORD_RESET_TTL_SECONDS`, e um token vazado por qualquer via
    lateral (log de proxy, extensão de browser, e-mail encaminhado, dispositivo
    compartilhado) ainda reseta a senha depois do incidente parecer resolvido.

    O escopo é estreito nas duas direções: só o mesmo propósito (pedir um reset
    não derruba uma troca de e-mail pendente) e só o mesmo usuário. A linha
    antiga é marcada `used_at`, não apagada, então a auditoria fica.

## Grupo 12 — Conta desativada (`AuthSettings`) *(v0.305.0+)*

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_REVEAL_INACTIVE_ACCOUNT` | `bool` | `false` | `true` = a senha **certa** numa conta desativada responde `403` `ACCOUNT_INACTIVE` em vez do `401` genérico. Senha errada e e-mail desconhecido continuam `401`. |

Por padrão, conta desativada responde o mesmo `401` de senha errada, para a
resposta não contar quais e-mails têm conta. Só que o dono da conta, sem saber
por que não entra, pede "esqueci a senha", troca a senha e continua sem entrar.
Ligado, quem **provou a senha** fica sabendo — e isso não enumera nada, porque
quem tem a senha já é o dono:

```console
$ curl -s -X POST localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"ana@example.com","password":"strong-pass-12-chars"}'
{"detail":"Esta conta está desativada — reative-a pelo link de ativação","code":"ACCOUNT_INACTIVE","details":{}}
```

(Com o catálogo default em `register_exception_handlers(app, catalog=default_message_catalog())`;
sem catálogo, o `detail` é `account is not active`.)

!!! check "O custo é o mesmo nos três ramos"
    A senha é verificada **antes** de olhar `is_active`, ligado ou desligado.
    E-mail desconhecido paga um bcrypt contra um hash descartável. Medido em
    `tests/auth/test_refusal_codes.py`: exatamente uma verificação por login
    recusado, nos três ramos, com a flag ligada e desligada.

!!! note "MFA / TOTP tem suas próprias vars"
    Quando `AUTH_MFA_ENABLED=true`, o `AuthSettings` ainda expõe `AUTH_MFA_ISSUER`, `AUTH_MFA_RECOVERY_CODES_COUNT`, `AUTH_MFA_TOKEN_TTL_SECONDS` e `AUTH_MFA_VERIFY_WINDOW`. Ficam fora do escopo desta receita (signup/activate/login/reset) — são cobertos na receita de MFA.
