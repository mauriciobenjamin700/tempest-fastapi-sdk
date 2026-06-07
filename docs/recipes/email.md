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

!!! warning "STARTTLS vs SSL/TLS — não trave contra servidor plain"
    Porta **587** → `SMTP_USE_TLS=true` (default): conecta em texto e faz
    upgrade via STARTTLS. Porta **465** → `SMTP_USE_SSL=true`: conecta já
    cifrado. **Servidor SMTP plain (MailHog em `:1025`, ou `:25`)** não
    fala STARTTLS — deixe **`SMTP_USE_TLS=false`** e `SMTP_USE_SSL=false`,
    senão o aiosmtplib levanta `SMTPException: SMTP STARTTLS extension not
    supported by server.` O `.env.example` gerado pelo `tempest new` com o
    extra `[email]` já vem com os dois desligados para o MailHog.

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
