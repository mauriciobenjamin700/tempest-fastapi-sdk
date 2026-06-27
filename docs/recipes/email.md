# Email transacional

Envio de email via SMTP com `EmailUtils` — corpo texto + alternativa HTML,
anexos, e renderização de templates Jinja2. Requer o extra `[email]`
(`aiosmtplib` + `jinja2` + `email-validator`).

## Configuração

`EmailSettings` traz os campos SMTP prontos (`SMTP_HOST`, `SMTP_PORT`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDR`, `SMTP_USE_TLS`,
`SMTP_USE_SSL`, `SMTP_TIMEOUT_SECONDS`). Componha no seu `Settings` e use
`email_kwargs()` para construir o utilitário sem mapear campo a campo:

```python
# src/core/mailer.py
from tempest_fastapi_sdk import EmailUtils

from src.core.settings import settings


mailer = EmailUtils(**settings.email_kwargs())
```

`email_kwargs()` faz a ponte dos nomes SMTP para os do `EmailUtils`:
`SMTP_USE_TLS` (STARTTLS, porta 587) → `use_starttls`; `SMTP_USE_SSL`
(TLS desde o início, porta 465) → `use_tls`.

!!! tip "STARTTLS vs SSL/TLS — oportunístico por padrão"
    Porta **587** → `SMTP_USE_TLS=true` (default): conecta em texto e faz
    upgrade via STARTTLS. Porta **465** → `SMTP_USE_SSL=true`: conecta já
    cifrado. O STARTTLS é **oportunístico**: o `EmailUtils` só faz o
    upgrade quando o servidor anuncia STARTTLS, então um servidor plain
    (**MailHog em `:1025`**, ou `:25`) funciona de cara — sem mais
    `SMTPException: SMTP STARTTLS extension not supported by server.`
    (desde a v0.38.1). Para forçar texto puro sem nem tentar o upgrade,
    use `SMTP_USE_TLS=false`; o `.env.example` gerado pelo `tempest new`
    com `[email]` já vem assim para o MailHog.

## Produção: SMTP real e credenciais

Em produção o SMTP **não é opcional** — você manda email por um provedor
real (Gmail/Workspace, AWS SES, SendGrid, Mailgun, ...) e isso exige
**host, porta, usuário e senha**. A regra de ouro: essas credenciais
**vêm sempre do ambiente** (`.env` / secret manager / variáveis do
container), **nunca** ficam no código nem versionadas no Git.

!!! danger "Credencial SMTP é segredo — trate como senha de banco"
    - `SMTP_PASSWORD` **nunca** entra no repositório. Mantenha `.env` no
      `.gitignore` e versione só um `.env.example` com valores fake.
    - Para Gmail/Workspace **não use a senha da conta** — gere uma
      **App Password** (<https://myaccount.google.com/apppasswords>) com
      2FA ligado. A senha normal falha com `535 Authentication failed`.
    - O `SMTP_FROM_ADDR` deve ser um endereço de um domínio que você
      controla e autenticou (SPF/DKIM/DMARC), senão o email cai em spam
      ou é rejeitado.

Os serviços de produção já seguem esse padrão — declare os campos SMTP no
seu `Settings` e leia tudo do ambiente:

```python
# src/core/settings.py
from pydantic import Field
from tempest_fastapi_sdk import BaseAppSettings, EmailSettings


class Settings(BaseAppSettings, EmailSettings):
    """Settings da aplicação. SMTP herdado de EmailSettings."""

    FRONTEND_URL: str = Field(
        default="http://localhost:3000",
        description="Base URL do frontend (usada nos links dos emails).",
    )


settings: Settings = Settings()
```

```ini
# .env.example  (commitado — valores fake)
# .env real (NÃO commitado) tem as credenciais de verdade
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=seu_email@example.com
SMTP_PASSWORD="sua_app_password"
SMTP_FROM_ADDR=seu_email@example.com
SMTP_USE_TLS=true        # STARTTLS na 587
SMTP_USE_SSL=false       # use true (e PORT=465) para TLS implícito
```

Provedores comuns e a combinação de porta/TLS:

| Provedor            | `SMTP_HOST`            | `SMTP_PORT` | `SMTP_USE_TLS` (STARTTLS) | `SMTP_USE_SSL` (TLS implícito) |
| ------------------- | ---------------------- | ----------- | ------------------------- | ------------------------------ |
| Gmail / Workspace   | `smtp.gmail.com`       | `587`       | `true`                    | `false`                        |
| Gmail (TLS direto)  | `smtp.gmail.com`       | `465`       | `false`                   | `true`                         |
| AWS SES             | `email-smtp.<região>.amazonaws.com` | `587` | `true`            | `false`                        |
| SendGrid            | `smtp.sendgrid.net`    | `587`       | `true`                    | `false`                        |
| MailHog (dev local) | `localhost`            | `1025`      | `false`                   | `false`                        |

!!! note "Como alofans-api e transport-backend fazem"
    São os dois padrões em produção: **alofans-api** usa **Gmail na porta
    587 (STARTTLS) com App Password** — `SMTP_USE_TLS=true`. O
    **transport-backend** usa **porta 465 (TLS implícito)** —
    `SMTP_USE_SSL=true`, `SMTP_PORT=465`. Os dois leem `SMTP_*` do
    ambiente e nunca embutem a senha no código. Escolha STARTTLS (587) ou
    SSL (465) conforme o seu provedor; **não ligue os dois ao mesmo
    tempo**.

## Enviar um email

Cada `send()` abre uma conexão SMTP nova. `to` aceita uma string ou um
iterável; o `body` (texto puro) é sempre enviado e o `html` vira a
alternativa multipart quando presente.

```python
await mailer.send(
    to="ana@example.com",
    subject="Bem-vinda!",
    body="Sua conta foi criada.",
    html="<p>Sua conta foi <strong>criada</strong>.</p>",
)
```

Parâmetros opcionais por mensagem: `cc`, `bcc`, `attachments`
(`Iterable[Path]`), `reply_to` e `from_addr` (sobrescreve o remetente
padrão). Qualquer erro SMTP é re-levantado como
`aiosmtplib.errors.SMTPException` para o chamador tratar.

## Templates Jinja2

Passe `template_dir=` na construção e renderize com `render_template()` —
o ambiente Jinja2 é criado preguiçosamente na primeira chamada e memoizado.
Autoescape liga para `.html` / `.htm` / `.xml`.

```python
# src/core/mailer.py
mailer = EmailUtils(
    host=settings.SMTP_HOST,
    port=settings.SMTP_PORT,
    from_addr=settings.SMTP_FROM_ADDR,
    template_dir="src/templates/emails",
)

html: str = mailer.render_template(
    "welcome.html",
    {"user_name": "Ana", "app_url": "https://app.example.com"},
)
await mailer.send(
    to="ana@example.com",
    subject="Bem-vinda!",
    body="Bem-vinda, Ana!",
    html=html,
)
```

!!! info "Templates bundled do fluxo de auth"
    O fluxo bundled de auth (`make_auth_router`) já manda email de
    ativação e reset de senha usando templates embutidos
    (`activation.html`, `password_reset.html`). Coloque arquivos de
    mesmo nome no seu `template_dir` para sobrescrevê-los. Veja
    [Auth flow](auth-flow.md).

## Exemplo: reset de senha

Serviço que manda um link de reset com um JWT de vida curta. Note que
`request_reset` retorna em silêncio para um email não cadastrado — evita
enumeração de contas.

```python
# src/services/password_reset.py
from datetime import timedelta

from tempest_fastapi_sdk import EmailUtils, JWTUtils

from src.db.repositories import UserRepository


class PasswordResetService:
    def __init__(
        self,
        repo: UserRepository,
        tokens: JWTUtils,
        mailer: EmailUtils,
    ) -> None:
        self.repo: UserRepository = repo
        self.tokens: JWTUtils = tokens
        self.mailer: EmailUtils = mailer

    async def request_reset(self, email: str) -> None:
        """Manda um link de reset para ``email`` (silencioso se não existe)."""
        user = await self.repo.get_or_none({"email": email})
        if user is None:
            return
        token: str = self.tokens.encode(
            {"sub": str(user.id), "purpose": "password_reset"},
            ttl=timedelta(minutes=15),
        )
        reset_url: str = f"https://app.example.com/reset-password?token={token}"
        await self.mailer.send(
            to=user.email,
            subject="Redefina sua senha",
            body=f"Abra para redefinir: {reset_url}",
            html=f'<p>Clique <a href="{reset_url}">aqui</a> para redefinir.</p>',
        )
```

## Recap

- Instale `[email]` e componha `EmailSettings` no seu `Settings`.
- Uma instância de `EmailUtils` por app; `send()` é async e abre conexão por chamada.
- `body` texto é obrigatório; `html` é a alternativa multipart opcional.
- `render_template()` (com `template_dir`) gera o HTML a partir de Jinja2.
- O fluxo bundled de auth já envia ativação/reset com templates sobrescrevíveis.
