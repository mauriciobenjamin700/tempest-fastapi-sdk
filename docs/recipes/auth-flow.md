# Bundled auth flow (signup / activate / login / reset)

Desde v0.31.0 o SDK fornece o ciclo completo de conta local — signup com email/senha, ativação por link, login com JWT pair, reset de senha — via `UserAuthService` + `make_auth_router`. **Cinco endpoints prontos pra mount**, templates Jinja2 bundled, settings flags controlando se o link sai por e-mail ou no body da resposta, e quatro modos pré-pensados pra dev / staging / produção / CI.

## Conteúdo da receita

1. **[Setup mínimo](#setup-minimo)** — instalação dos extras + wiring de quatro objetos (`AsyncDatabaseManager`, `EmailUtils`, `UserAuthService`, `make_auth_router`).
2. **[UserTokenModel concreto](#usertokenmodel-concreto)** — `BaseUserTokenModel` é abstrato, projeto cria a tabela final.
3. **[Endpoints](#endpoints)** — tabela dos 5 endpoints + payload + comportamento.
4. **[Settings — variáveis de ambiente](#settings-variaveis-de-ambiente)** — env vars em **seis grupos** (JWT, política de senha, fluxo de e-mail, TTL, URLs/templates, páginas backend) — cada uma em tabela tipada, não num blob.
5. **[Anatomia de um e-mail: como link, template e URL se encaixam](#anatomia-de-um-e-mail)** — desambigua os três conceitos que mais confundem.
6. **[Cinco modos de operação](#cinco-modos-de-operacao)** — produção, dev com SMTP local (Mailhog / smtp4dev), dev sem SMTP, CI sem ativação e **backend-only** (links e páginas servidas direto pelo backend).
7. **[Mailhog vs smtp4dev — qual escolher pra dev local](#mailhog-vs-smtp4dev)** — comparativo + receitas docker-compose copy-paste.
8. **[Customizando os templates de e-mail](#customizando-templates)** — override do `activation.html` e `password_reset.html` + variáveis disponíveis no contexto Jinja2.
9. **[Segurança](#seguranca)** — como o token é armazenado, TTL, anti-enumeração.
10. **[Próximos passos](#proximos-passos)**.

---

## Setup mínimo

Requer:

- `[auth]` (bcrypt + PyJWT) — obrigatório, sempre.
- `[email]` (aiosmtplib + Jinja2 + email-validator) — opcional; quando ausente, os links vão no body da resposta em vez de e-mail.

```bash
uv add "tempest-fastapi-sdk[auth,email]>=0.31.0"
```

```python
# src/api/app.py
from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    EmailUtils,
    UserAuthService,
    make_auth_router,
)
from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel

db = AsyncDatabaseManager(settings.DATABASE_URL)

# EmailUtils — só instancie se [email] estiver instalado E você quiser e-mail
# real (modos A e B abaixo). Nos modos C e D, passe email=None pro service.
emails = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    username=settings.SMTP_USERNAME,
    password=settings.SMTP_PASSWORD,
    from_addr=settings.SMTP_FROM_ADDR,
    template_dir="emails",  # diretório onde seus templates custom moram
)

auth_service = UserAuthService(
    db=db,                    # necessário pra current_user_dependency (ver seção final)
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,   # mistura AuthSettings (ver seção 4)
    jwt_settings=settings,    # mistura JWTSettings
    email=emails,             # ou None — controla envio real vs link no body
)

app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
    ),
)
```

!!! tip "TL;DR de quatro objetos"
    `AsyncDatabaseManager` → conexão. `EmailUtils` → SMTP + Jinja2. `UserAuthService` → regras de negócio (5 métodos). `make_auth_router` → cola tudo em 5 endpoints HTTP.

---

## UserTokenModel concreto

`BaseUserTokenModel` é abstrato — projeto cria a tabela concreta porque a FK pra `users` precisa do nome da sua tabela. Exemplo `src/db/models/user_token.py`:

```python
from uuid import UUID

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseUserTokenModel


class UserTokenModel(BaseUserTokenModel):
    """Concrete token table for activation / reset / email-verification."""

    __tablename__ = "user_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

E importar em `src/db/models/__init__.py` pra Alembic ver:

```python
from src.db.models.user import UserModel
from src.db.models.user_token import UserTokenModel

__all__: list[str] = ["UserModel", "UserTokenModel"]
```

Gerar migration (a primeira vez, bootstrape o Alembic com `tempest db init`):

```bash
# Só na primeira vez — gera alembic/, alembic.ini e env.py:
uv run tempest db init

# A partir daí, ciclo normal de revisão:
uv run tempest db revision -m "users + user_tokens"
uv run tempest db upgrade
```

---

## Endpoints

| Método | Path | Body / Output | Comportamento |
|--------|------|---------------|---------------|
| POST | `/auth/signup` | `SignupSchema` → `SignupResponseSchema` | Cria user. Emite e-mail (modos A/B) **ou** devolve link no body (modo C). Se `AUTH_AUTO_ACTIVATE=True`, user nasce ativo e JWT pair volta direto (modo D). |
| POST | `/auth/activate/{token}` | — → `ActivationResponseSchema` | Consome token + `is_active=True` + emite JWT pair. |
| POST | `/auth/login` | `LoginSchema` → `LoginResponseSchema` | Email + senha → JWT pair. Erros genéricos (não enumera contas). |
| POST | `/auth/password-reset/request` | `PasswordResetRequestSchema` → `PasswordResetResponseSchema` | Sempre HTTP 202 + corpo genérico. Link via e-mail (A/B) ou no body (C). |
| POST | `/auth/password-reset/confirm` | `PasswordResetConfirmSchema` → `LoginResponseSchema` | Consome token + grava nova senha + emite JWT pair. |

---

## Settings — variáveis de ambiente

Toda a config do flow vem de mixins de settings. Mixe-as na sua classe `Settings`:

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

### Grupo 1 — Autenticação / JWT (`JWTSettings`)

Controla a assinatura e validade dos tokens que o login devolve. **É o mesmo `JWT_SECRET` que a dependency `get_current_user` usa pra validar** (veja [Pegando o `current_user`](#pegando-o-current_user-da-requisicao)).

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `JWT_SECRET` | `str` (≥32 bytes) | `change-me-…-32` | Segredo HMAC que assina o JWT. **Obrigatório trocar em produção.** |
| `JWT_ALGORITHM` | `str` | `HS256` | Algoritmo JOSE. `HS256`/`HS512` (segredo simétrico) ou `RS256` (par de chaves). |
| `JWT_ACCESS_TTL_SECONDS` | `int` (≥1) | `3600` | Validade do **access token** (1 h). Curto por design — renove via refresh. |
| `JWT_REFRESH_TTL_SECONDS` | `int` (≥1) | `604800` | Validade do **refresh token** (7 dias). |
| `JWT_ISSUER` | `str \| None` | `None` | Claim `iss`. `None` omite o claim. |

!!! danger "`JWT_SECRET` default vaza tokens"
    O default `change-me-change-me-change-me-32` existe só pra subir local. Em produção, **qualquer um** com o default consegue forjar um JWT válido. Gere um segredo forte (`openssl rand -base64 48`) e injete por secret manager — nunca commite.

### Grupo 2 — Política de senha (`AuthSettings`)

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_PASSWORD_MIN_LENGTH` | `int` (≥1) | `12` | Comprimento mínimo aceito no signup **e** no reset. |
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

### Grupo 3 — Controle do fluxo de e-mail (`AuthSettings`)

Decidem **se** e **como** o link chega ao usuário. Mapeiam direto nos [cinco modos de operação](#cinco-modos-de-operacao).

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_AUTO_ACTIVATE` | `bool` | `false` | `true` = user nasce ativo, pula activation, signup devolve JWT pair direto (Modo D). **Nunca em produção.** |
| `AUTH_RETURN_TOKEN_IN_RESPONSE` | `bool` | `false` | `true` = link de ativação/reset vai no corpo JSON em vez do e-mail (Modo C). |

### Grupo 4 — TTL dos tokens de conta (`AuthSettings`)

Validade dos tokens **de uso único** (ativação / reset) — distintos dos JWT do grupo 1.

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_ACTIVATION_TTL_SECONDS` | `int` (≥60) | `604800` | Validade do token de ativação (7 dias). |
| `AUTH_PASSWORD_RESET_TTL_SECONDS` | `int` (≥60) | `3600` | Validade do token de reset (1 h). Curto é mais seguro. |

### Grupo 5 — URLs e templates de e-mail (`AuthSettings`)

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_ACTIVATION_URL_TEMPLATE` | `str` | `http://localhost:3000/activate?token={token}` | URL que vai no e-mail; `{token}` é substituído. **Aponta pro frontend** (exceto no Modo E). |
| `AUTH_PASSWORD_RESET_URL_TEMPLATE` | `str` | `http://localhost:3000/reset-password?token={token}` | Idem, para reset. |
| `AUTH_ACTIVATION_TEMPLATE` | `str` | `activation.html` | Nome do arquivo Jinja2 do **HTML do e-mail** de ativação, resolvido no `template_dir` do `EmailUtils`. |
| `AUTH_PASSWORD_RESET_TEMPLATE` | `str` | `password_reset.html` | Idem, para reset. |

!!! warning "URL template ≠ Jinja2 template"
    `*_URL_TEMPLATE` é uma string `.format()` com `{token}` — é o **link**. `*_TEMPLATE` é o nome de um arquivo `.html` — é o **e-mail que embrulha o link**. Confundir os dois é o erro nº 1. Detalhe completo em [Anatomia de um e-mail](#anatomia-de-um-e-mail).

### Grupo 6 — Páginas renderizadas pelo backend (Modo E, `AuthSettings`)

Só relevantes quando `AUTH_BACKEND_LINKS=true`. Veja o [Modo E](#cinco-modos-de-operacao) para o fluxo completo.

| Env var | Tipo | Default | O que faz |
|---------|------|---------|-----------|
| `AUTH_BACKEND_LINKS` | `bool` | `false` | `true` = monta 3 endpoints HTML extras; o link do e-mail aponta pro **backend**, não pro frontend. |
| `AUTH_LOGIN_URL` | `str \| None` | `None` | URL de login no botão "Ir pro login" das páginas de sucesso. `None` esconde o botão. |
| `AUTH_ACTIVATION_SUCCESS_TEMPLATE` | `str` | `activation_success.html` | Página HTML de ativação OK. |
| `AUTH_ACTIVATION_ERROR_TEMPLATE` | `str` | `activation_error.html` | Página HTML de ativação com erro. |
| `AUTH_PASSWORD_RESET_FORM_TEMPLATE` | `str` | `password_reset_form.html` | Form HTML de nova senha. |
| `AUTH_PASSWORD_RESET_SUCCESS_TEMPLATE` | `str` | `password_reset_success.html` | Página HTML de reset OK. |
| `AUTH_PASSWORD_RESET_ERROR_TEMPLATE` | `str` | `password_reset_error.html` | Página HTML de reset com erro. |

!!! note "MFA / TOTP tem suas próprias vars"
    Quando `AUTH_MFA_ENABLED=true`, o `AuthSettings` ainda expõe `AUTH_MFA_ISSUER`, `AUTH_MFA_RECOVERY_CODES_COUNT`, `AUTH_MFA_TOKEN_TTL_SECONDS` e `AUTH_MFA_VERIFY_WINDOW`. Ficam fora do escopo desta receita (signup/activate/login/reset) — são cobertos na receita de MFA.

---

## Anatomia de um e-mail

Três conceitos diferentes que parecem o mesmo. Eis o que cada um faz, exatamente uma vez, em pseudo-código:

```text
1. SDK gera um token opaco aleatório (string de 64 chars).
2. AUTH_ACTIVATION_URL_TEMPLATE.format(token=…)  →  link com o token embutido.
3. Renderiza AUTH_ACTIVATION_TEMPLATE (Jinja2 HTML) passando { user, activation_url, expires_at }.
4. EmailUtils.send(to=user.email, subject=..., html=<HTML renderizado>).
```

Em prosa:

- **Token opaco** — string aleatória que o SDK gera, hasheia (SHA-256) e grava na tabela `user_tokens`. O plaintext sai pelo e-mail **uma única vez**; o banco só guarda o hash.
- **URL template** (`AUTH_ACTIVATION_URL_TEMPLATE`) — formato literal pra montar a URL que vai pro usuário clicar. **Aponta pro frontend, não pro backend.** O frontend recebe `?token=…`, capta da query string e chama `POST /auth/activate/{token}` no backend.
- **Jinja2 template** (`AUTH_ACTIVATION_TEMPLATE`) — nome do arquivo HTML dentro do `template_dir` do `EmailUtils`. É **o HTML do e-mail**, não a URL. Recebe o contexto `{ user, activation_url, expires_at }` e renderiza o markup final.

!!! warning "URL template ≠ Jinja2 template"
    `AUTH_ACTIVATION_URL_TEMPLATE` é uma string Python `.format()`-style — só tem o placeholder `{token}`. **Não confunda** com o arquivo `.html` que o Jinja2 renderiza. A URL formatada **é injetada como variável** no contexto do Jinja2 sob o nome `activation_url`, e o template HTML embrulha ela num botão.

Fluxo visual:

```mermaid
sequenceDiagram
    participant U as Usuário
    participant F as Frontend
    participant API as Backend (SDK)
    participant E as SMTP
    participant DB as Banco

    U->>F: preenche email + senha
    F->>API: POST /auth/signup
    API->>DB: INSERT user (is_active=false) + INSERT token (hash, TTL)
    API->>API: token plaintext + AUTH_ACTIVATION_URL_TEMPLATE.format(token=…)
    API->>API: render Jinja2 (user, activation_url, expires_at)
    alt AUTH_RETURN_TOKEN_IN_RESPONSE=false
        API->>E: SMTP send (HTML renderizado)
        API->>F: 201 + {message: "check your email"}
    else AUTH_RETURN_TOKEN_IN_RESPONSE=true
        API->>F: 201 + {activation_url: "https://app/activate?token=..."}
    end
    Note right of F: usuário (ou dev) abre a URL
    F->>API: POST /auth/activate/{token}
    API->>DB: hash(token) match? expirou? já usou?
    API->>F: 200 + JWT pair
```

---

## Cinco modos de operação

| Modo | Quando usar | Flags | Onde o link aparece |
|------|-------------|-------|--------------------|
| **A. Produção (SPA)** | SaaS público, e-mail real, frontend SPA dono das páginas | `AUTH_AUTO_ACTIVATE=false`<br>`AUTH_RETURN_TOKEN_IN_RESPONSE=false`<br>`AUTH_BACKEND_LINKS=false`<br>SMTP real (Mailgun, SES, Postmark…) | Inbox real → frontend processa o token |
| **B. Dev local com SMTP fake** | Desenvolvimento diário sem mandar e-mail real | `AUTH_AUTO_ACTIVATE=false`<br>`AUTH_RETURN_TOKEN_IN_RESPONSE=false`<br>SMTP apontando pra Mailhog (`localhost:1025`) ou smtp4dev (`localhost:2525`) | UI web do Mailhog/smtp4dev em `localhost:8025` / `localhost:5000` |
| **C. Dev sem SMTP** | Validação rápida sem subir nenhum container de e-mail | `AUTH_AUTO_ACTIVATE=false`<br>`AUTH_RETURN_TOKEN_IN_RESPONSE=true`<br>`email=None` ou SMTP inválido | Body da resposta HTTP do signup |
| **D. CI / testes** | Suite de testes que não exercita activation | `AUTH_AUTO_ACTIVATE=true` | Nenhum — signup já devolve JWT pair |
| **E. Backend-only** *(v0.32.0+)* | Quer 100% de controle no backend — sem responsabilidade no frontend. Ideal pra APIs sem SPA, MVPs, intranets. | `AUTH_BACKEND_LINKS=true`<br>URL templates apontam pro **backend** (`https://api.example.com/auth/activate/{token}`)<br>`AUTH_LOGIN_URL=https://app.example.com/login` (opcional, mostra botão "Ir pro login" nas páginas HTML) | Backend renderiza HTML success/error direto — usuário só clica no link do e-mail |

### Modo A — produção

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false
SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_USERNAME=postmaster@mg.example.com
SMTP_PASSWORD=...                          # secret, não commitar
SMTP_FROM_ADDR=noreply@example.com
AUTH_ACTIVATION_URL_TEMPLATE=https://app.example.com/activate?token={token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=https://app.example.com/reset?token={token}
```

Fluxo: signup → e-mail real chega no inbox → usuário clica → frontend chama `POST /auth/activate/{token}` → login.

### Modo B — dev com SMTP local (Mailhog ou smtp4dev)

Mesmo `.env` do modo A, mas apontando o SMTP para um container local que **intercepta** os e-mails em vez de mandar de verdade. **Use este modo no dia-a-dia** — o fluxo é idêntico ao de produção, então você pega bugs de template, encoding, charset, etc. ao mesmo tempo que evita spammar e-mail real.

```bash
# .env.dev
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false
SMTP_HOST=localhost
SMTP_PORT=1025                             # Mailhog SMTP padrão
SMTP_USERNAME=                             # vazio — Mailhog não autentica
SMTP_PASSWORD=
SMTP_FROM_ADDR=dev@local
AUTH_ACTIVATION_URL_TEMPLATE=http://localhost:5173/activate?token={token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=http://localhost:5173/reset?token={token}
```

Abra `http://localhost:8025` (Mailhog) ou `http://localhost:5000` (smtp4dev) pra ver os e-mails interceptados. Veja a seção **[Mailhog vs smtp4dev](#mailhog-vs-smtp4dev)** abaixo.

### Modo C — dev sem SMTP (link no body)

Sem container de SMTP nenhum. O signup devolve o link de ativação no JSON da resposta:

```bash
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=true
AUTH_ACTIVATION_URL_TEMPLATE=http://localhost:5173/activate?token={token}
```

Request:

```bash
curl -X POST localhost:8000/auth/signup \
  -H 'content-type: application/json' \
  -d '{"email":"dev@local","password":"abcdefghijkl","name":"Dev"}'
```

Resposta (formato real do `SignupResponseSchema`):

```json
{
  "user_id": "0193e9ea-7c4b-7c8e-bc05-2a3a8d9f7e10",
  "activation_required": true,
  "activation_url": "http://localhost:5173/activate?token=aBcD...xYz",
  "access_token": null,
  "refresh_token": null
}
```

Cole a URL no navegador / curl pra exercitar `POST /auth/activate/{token}`.

### Modo D — CI / testes (skip total)

```bash
AUTH_AUTO_ACTIVATE=true
```

Signup pula ativação inteira e devolve `{access_token, refresh_token}` direto. Use **só em testes** ou quando o produto for interno e cada usuário já é confiável.

### Modo E — backend-only (v0.32.0+)

Quando você prefere que **toda** a experiência do link aconteça no backend, sem nenhuma página no frontend, ative `AUTH_BACKEND_LINKS=True`. O router passa a montar **três endpoints HTML** adicionais — `GET /auth/activate/{token}`, `GET /auth/password-reset/{token}` e `POST /auth/password-reset/{token}` (form-encoded). O e-mail aponta o usuário direto pra esses endpoints; o backend ativa a conta / processa o reset / renderiza HTML success ou error — usando templates Jinja2 bundled que você pode shadowar.

```bash
# .env — Modo E (backend-only)
AUTH_BACKEND_LINKS=true
AUTH_AUTO_ACTIVATE=false
AUTH_RETURN_TOKEN_IN_RESPONSE=false

# IMPORTANTE: URL templates apontam pro BACKEND, não pro frontend.
AUTH_ACTIVATION_URL_TEMPLATE=https://api.example.com/auth/activate/{token}
AUTH_PASSWORD_RESET_URL_TEMPLATE=https://api.example.com/auth/password-reset/{token}

# Opcional: URL do seu login. Quando setado, aparece um botão "Ir pro login"
# nas páginas de success/error renderizadas pelo backend. Quando null,
# o botão é omitido (puro server-side, zero acoplamento com frontend).
AUTH_LOGIN_URL=https://app.example.com/login

SMTP_HOST=smtp.mailgun.org
SMTP_PORT=587
SMTP_FROM_ADDR=noreply@example.com
```

Fluxo:

```mermaid
sequenceDiagram
    participant U as Usuário
    participant E as Inbox
    participant API as Backend
    participant DB as Banco

    U->>API: POST /auth/signup
    API->>DB: INSERT user (is_active=false) + token (hash, TTL)
    API->>E: e-mail com link https://api.example.com/auth/activate/{token}
    U->>E: clica no link
    E->>API: GET /auth/activate/{token}
    API->>DB: hash(token) válido? não-usado? não-expirado?
    alt token válido
        API->>DB: is_active=true + token.used_at=now
        API->>U: HTML activation_success.html (botão "Ir pro login" se AUTH_LOGIN_URL setado)
    else token inválido / expirado
        API->>U: HTML activation_error.html (HTTP 400)
    end
```

Password reset segue padrão similar: GET renderiza form HTML; POST (form-encoded) consome o token e renderiza success/error.

**Templates HTML bundled (override droppando o mesmo nome no `template_dir`):**

| Template | Endpoint que renderiza | Variáveis Jinja2 disponíveis |
|----------|------------------------|------------------------------|
| `activation_success.html` | `GET /auth/activate/{token}` (sucesso) | `user`, `login_url` |
| `activation_error.html` | `GET /auth/activate/{token}` (falha) | `reason`, `login_url` |
| `password_reset_form.html` | `GET /auth/password-reset/{token}` | `user`, `form_action`, `min_length`, `error`, `login_url` |
| `password_reset_success.html` | `POST /auth/password-reset/{token}` (sucesso) | `user`, `login_url` |
| `password_reset_error.html` | `POST /auth/password-reset/{token}` (token inválido) | `reason`, `login_url` |

**Pra override:** passe `template_dir` no `make_auth_router` e crie arquivos de mesmo nome.

```python
app.include_router(
    make_auth_router(
        auth_service,
        session_factory=db.session_dependency,
        template_dir="src/templates/auth",   # opcional
    ),
)
```

**Trade-offs do Modo E:**

- ✅ **Zero dependência do frontend** — backend é fonte única da verdade do fluxo de auth.
- ✅ **MVP em minutos** — sem precisar criar rotas SPA pra processar tokens.
- ✅ **Funciona em projetos sem frontend** — APIs públicas, intranets, ferramentas internas.
- ⚠️ **JWT não é entregue automaticamente** — após ativação, o usuário precisa fazer login manualmente (clicando em "Ir pro login" e usando as credenciais). Por design: zero leak de token em URL, history, ou server logs.
- ⚠️ **Requer `[email]` extra** (Jinja2) pra renderizar as páginas HTML — mesma dependência do template de e-mail.
- ⚠️ **CSRF na form de reset** — o form HTML usa POST tradicional sem token CSRF. Aceita a request porque o token de reset é one-shot + TTL curto + bound a um user específico, mas considere acoplar `CSRFMiddleware` se atacantes conseguirem prever URLs ativas.

Os endpoints **JSON** (`POST /auth/activate/{token}`, `POST /auth/password-reset/confirm`) continuam montados — você pode usar Modo E + manter SPA endpoints ao mesmo tempo.

---

## Mailhog vs smtp4dev

Os dois interceptam SMTP local e renderizam os e-mails numa UI web. Diferenças relevantes:

| Aspecto | Mailhog | smtp4dev |
|---------|---------|----------|
| Imagem Docker | `mailhog/mailhog:latest` | `rnwood/smtp4dev:latest` |
| Porta SMTP padrão | `1025` | `2525` (configurável) |
| Porta da UI | `8025` | `5000` |
| Tamanho da imagem | ~10 MB | ~120 MB (.NET) |
| Multi-conta / multi-inbox | não — uma única caixa | sim — filtra por destinatário |
| API HTTP / REST | sim (`/api/v2/messages`) | sim (Swagger built-in) |
| Validação de DKIM / SPF | não | sim |
| Manutenção upstream | arquivado em 2020, ainda funciona | ativa |

**Sugestão:** comece com Mailhog (mais leve, zero-config) e migre pra smtp4dev quando precisar de multi-inbox ou inspeção de DKIM. Para o ciclo signup → activate → reset, **Mailhog é suficiente**.

### `docker-compose.yaml` — Mailhog

```yaml
services:
  mailhog:
    image: mailhog/mailhog:latest
    container_name: mailhog
    ports:
      - "1025:1025"  # SMTP — aponte SMTP_HOST aqui
      - "8025:8025"  # UI web
```

`SMTP_PORT=1025`, abra `http://localhost:8025`.

### `docker-compose.yaml` — smtp4dev

```yaml
services:
  smtp4dev:
    image: rnwood/smtp4dev:latest
    container_name: smtp4dev
    ports:
      - "2525:25"     # SMTP — aponte SMTP_HOST aqui
      - "5000:80"     # UI web
    environment:
      - ServerOptions__HostName=smtp4dev
```

`SMTP_PORT=2525`, abra `http://localhost:5000`.

!!! tip "Já tem `tempest generate --docker`?"
    Em v0.32+ o gerador de docker-compose vai aceitar `--with mailhog` como atalho. Hoje (v0.31.x) você cola um dos blocos acima no `docker-compose.yaml` gerado pela CLI.

---

## Customizando templates

O SDK ship dois templates Jinja2 bundled (`activation.html` + `password_reset.html`) — HTML responsivo, inline styles, mobile-friendly. Você nunca precisa mexer neles pra um MVP funcionar. Quando quiser branding próprio, basta criar um arquivo com o **mesmo nome** dentro do `template_dir` que você passou no `EmailUtils`:

```text
emails/                            # ← template_dir="emails"
├── activation.html                # override do default do SDK
└── password_reset.html            # override do default do SDK
```

`EmailUtils` usa um `ChoiceLoader` interno do Jinja2 que procura **primeiro** no seu diretório e **só cai** no template bundled se não achar. Você pode sobrescrever um, o outro, ou ambos — sem precisar copiar o template inteiro.

### Variáveis disponíveis no contexto Jinja2

| Variável | Tipo | Em quais templates | Exemplo |
|----------|------|--------------------|---------|
| `user` | instância de `UserModel` | ambos | `{{ user.email }}`, `{{ user.name }}` (quando seu modelo expõe a coluna) |
| `activation_url` | `str` | `activation.html` | `https://app.example.com/activate?token=aBcD...xYz` |
| `reset_url` | `str` | `password_reset.html` | `https://app.example.com/reset?token=aBcD...xYz` |
| `expires_at` | `datetime` (UTC, timezone-aware) | ambos | use `{{ expires_at.strftime("%d/%m/%Y %H:%M UTC") }}` |

### Exemplo: `emails/activation.html` enxuto

```html
<!doctype html>
<html lang="pt-BR">
  <body style="font-family: sans-serif; max-width: 480px; margin: auto;">
    <h1>Bem-vindo(a){% if user.name %}, {{ user.name }}{% endif %}!</h1>
    <p>Para ativar sua conta, clique no botão abaixo:</p>
    <p>
      <a href="{{ activation_url }}"
         style="display: inline-block; padding: 12px 24px;
                background: #4f46e5; color: white;
                text-decoration: none; border-radius: 6px;">
        Ativar conta
      </a>
    </p>
    <p style="color: #6b7280; font-size: 12px;">
      Link válido até {{ expires_at.strftime("%d/%m/%Y %H:%M UTC") }}.
      Se você não criou esta conta, ignore este e-mail.
    </p>
  </body>
</html>
```

!!! note "O Jinja2 só roda quando há e-mail real"
    Nos modos C (`AUTH_RETURN_TOKEN_IN_RESPONSE=true`) e D (`AUTH_AUTO_ACTIVATE=true`) o template Jinja2 **não é renderizado** — o link sai cru no JSON, sem HTML. Só os modos A e B (e-mail SMTP real ou interceptado) exercitam o template.

---

## Segurança

- **Token armazenado como hash SHA-256.** O plaintext sai pelo e-mail uma única vez; o banco nunca tem como reproduzir o token original. Vazamento da tabela `user_tokens` **não** permite ativação retroativa.
- **One-shot.** `used_at` é carimbado no consume; replay rejeitado com `UnauthorizedException`.
- **TTL-bounded.** `expires_at` calculado a partir de `AUTH_ACTIVATION_TTL_SECONDS` / `AUTH_PASSWORD_RESET_TTL_SECONDS`. Tokens expirados rejeitados.
- **Anti-enumeração.** `POST /auth/password-reset/request` retorna sempre HTTP 202 + corpo genérico, independente de o e-mail existir ou não. `POST /auth/login` levanta a mesma `UnauthorizedException` para email-errado vs senha-errada.
- **Password floor aplicado duas vezes.** `SignupSchema` valida no input; `UserAuthService` revalida antes do hash — defesa em profundidade caso alguém bypasse o schema.

---

## Pegando o `current_user` da requisição

`make_auth_router` **emite** o JWT pair (login/activate devolvem `access_token` + `refresh_token`). Mas e depois? Quando o frontend manda `Authorization: Bearer <access_token>` nas **suas próprias** rotas, você precisa de uma dependency que decodifica o token e resolve o usuário.

Desde a v0.49.0, o próprio `UserAuthService` constrói essa dependency — `current_user_dependency()`. Ela:

1. Lê `Authorization: Bearer <jwt>` via `HTTPBearer`.
2. Decodifica e verifica o JWT com **o mesmo `JWTUtils` que o service usou pra assinar** — sem segundo segredo pra manter sincronizado.
3. Pega o `sub` (id do usuário) do payload, abre uma sessão a partir do `db=` e devolve o `UserModel` persistido.

### 1. Declare a dependency uma vez

O service já tem `user_model`, `JWTUtils` e a sessão — então não precisa escrever `load_user` à mão. Junte as duas variantes no `src/api/dependencies/auth.py`:

```python
# src/api/dependencies/auth.py
from src.api.app import auth_service

get_current_user = auth_service.current_user_dependency()
get_current_user_or_none = auth_service.current_user_dependency(soft=True)
```

!!! info "Requer `db=` no `UserAuthService`"
    `current_user_dependency` resolve o usuário abrindo a própria sessão, então o service precisa ter sido criado com `db=` (o `AsyncDatabaseManager` do [Setup mínimo](#setup-minimo)). Como reusa o `self.jwt` interno, o token é validado com o **mesmo** segredo que assinou — o footgun de `JWT_SECRET` divergente some.

??? note "Sem `UserAuthService`? Monte a dependency na mão"
    Se o seu serviço não usa o flow bundled, a primitiva `make_jwt_user_dependency` aceita qualquer `JWTUtils` + um `user_loader` async de um argumento:

    ```python
    from uuid import UUID

    from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

    from src.api.app import db
    from src.core.settings import settings
    from src.db.models import UserModel
    from src.db.repositories import UserRepository

    tokens: JWTUtils = JWTUtils(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


    async def load_user(subject: str) -> UserModel:
        """Resolve o subject do JWT (uma string UUID) para o usuário persistido."""
        async with db.get_session_context() as session:
            repo: UserRepository = UserRepository(session)
            return await repo.get_by_id(UUID(subject))


    get_current_user = make_jwt_user_dependency(tokens, load_user)
    get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)
    ```

    Atenção: aqui o `tokens` **precisa** usar o mesmo `JWT_SECRET` / `JWT_ALGORITHM` do login, senão todo token válido é rejeitado.

### 2. Injete na rota com `Depends`

```python
# src/api/routers/users.py
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user
from src.db.models import UserModel
from src.schemas import UserResponseSchema

router: APIRouter = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    """Devolve o usuário dono do bearer token da requisição."""
    return UserResponseSchema.model_validate(current)
```

`current` **é** o `UserModel` que o service resolveu — tipado, persistido, pronto pra uso. Token ausente ou inválido → `401 UnauthorizedException` antes do corpo da rota rodar.

### 3. Auth opcional — `soft=True`

Para rotas que funcionam logado **e** anônimo (ex.: feed público que personaliza se houver login), use a variante `soft` — ela devolve `None` em vez de levantar:

```python
@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> list[PostResponseSchema]:
    """Feed público; personaliza o ranking quando há usuário logado."""
    if current is None:
        return await feed_service.public()
    return await feed_service.personalized(current.id)
```

!!! tip "Role e permission são o próximo passo"
    Quando a rota precisa de **papel** (`admin`) ou **permissão** (`users:write`) e não só "estar logado", troque por `make_role_dependency` / `make_permission_dependency`. Veja a [receita HTTP »](http.md) — mesma `JWTUtils`, mesmo padrão de `Depends`.

---

## Próximos passos

- **[Idempotência »](idempotency.md)** — proteja `POST /auth/signup` de retentativas que duplicariam linha.
- **[Storage MinIO/S3 »](storage.md)** — anexar avatar / foto de perfil já no signup.
- **[Logging »](logging.md)** — `request_id` já propaga automaticamente em cada log emitido durante o flow.
- **[Métricas »](metrics.md)** — `PrometheusMiddleware` conta `/auth/*` separadamente sem config extra.
