# Refresh tokens DB-backed (rotação + revogação)

Desde **v0.66.0** o fluxo de auth bundled pode trocar o refresh token JWT *stateless* por um token **opaco persistido no banco**, ganhando três coisas que um JWT puro nunca te dá: **rotação real**, **detecção de reuso** (token roubado) e **revogação** (logout que mata a sessão antes do expiry).

Tudo é **opt-in**: você passa um `refresh_token_model` pro serviço. Sem ele, o SDK mantém o comportamento stateless de sempre — zero breaking change.

## Conteúdo da receita

1. **[Stateless vs DB-backed em 30 segundos](#stateless-vs-db-backed)** — o que muda e por quê.
2. **[Setup](#setup)** — a tabela `BaseUserRefreshTokenModel`.
3. **[Wiring](#wiring)** — passar `refresh_token_model` pro serviço + router.
4. **[Como a rotação funciona](#rotacao)** — famílias, single-use, reuso.
5. **[Logout](#logout)** — o endpoint `POST /auth/logout`.
6. **[Usando só o `UserAuthService`](#service-direto)** — sem o router.
7. **[Segurança](#seguranca)**.
8. **[Próximos passos](#proximos-passos)**.

---

## Stateless vs DB-backed

Um **refresh token stateless** é só um JWT assinado com a claim `refresh`. O servidor confia nele se a assinatura bate e não expirou — **não há linha no banco**. Simples, mas:

- Não dá pra **revogar** (logout não mata nada — o token vive até o `exp`).
- Não há **rotação real**: você emite um novo par, mas o antigo continua válido em paralelo.
- Não há **detecção de reuso**: um token roubado funciona por dias sem ninguém perceber.

Um **refresh token DB-backed** é um valor **opaco** (aleatório, sem claims) cujo hash SHA-256 mora numa tabela. Cada `POST /auth/refresh`:

1. Marca o token apresentado como `used_at` (single-use).
2. Emite um token novo na **mesma família** (a linhagem de rotação daquele login).
3. Se alguém reapresentar um token **já rotacionado**, isso é o sinal clássico de roubo → **toda a família é revogada**.

!!! info "Por que opaco e não JWT-com-jti?"
    Um token opaco força o banco a ser a única fonte de verdade. Não há claims pra decodificar, não há janela entre "assinatura válida" e "linha revogada". O access token **continua** sendo um JWT stateless (curto, sem lookup por request) — só o refresh vira DB-backed.

---

## Setup

A tabela é abstrata no SDK (`BaseUserRefreshTokenModel`) — sua aplicação ship a tabela concreta, igual ao `BaseUserTokenModel` / `BaseUserRecoveryCodeModel`. Use o helper `make_user_refresh_token_model` pra amarrar a FK à sua tabela de users:

```python
# src/db/models/__init__.py
from tempest_fastapi_sdk import (
    make_user_refresh_token_model,
    make_user_token_model,
)

from src.db.models.user import UserModel

UserRefreshTokenModel = make_user_refresh_token_model(
    user_table="users",
    tablename="user_refresh_tokens",
    class_name="UserRefreshTokenModel",
)
```

Ou, se preferir uma classe escrita à mão (recomendado em produção, pra refactors e imports estáveis):

```python
# src/db/models/user_refresh_token.py
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from uuid import UUID

from tempest_fastapi_sdk import BaseUserRefreshTokenModel


class UserRefreshTokenModel(BaseUserRefreshTokenModel):
    """Tabela concreta de refresh tokens opacos."""

    __tablename__ = "user_refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
```

A tabela carrega: `token_hash` (único, indexado), `family_id` (linhagem de rotação), `expires_at`, `used_at` (rotacionado) e `revoked_at` (logout / kill de família).

!!! warning "Migration obrigatória"
    É uma tabela nova. Rode `uv run tempest db revision -m "refresh tokens"` + `uv run tempest db upgrade` antes de subir.

---

## Wiring

Passe o model concreto pro `UserAuthService`. **Só isso** liga o modo DB-backed — o router detecta sozinho e monta o `/auth/logout`:

```python
# src/api/dependencies/services.py
from tempest_fastapi_sdk import UserAuthService

from src.db.models import UserModel, UserRefreshTokenModel, UserTokenModel


def get_auth_service() -> UserAuthService:
    """Build the bundled auth service in DB-backed refresh mode."""
    return UserAuthService(
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=settings,
        jwt_settings=settings,
        refresh_token_model=UserRefreshTokenModel,  # <- liga o modo DB-backed
    )
```

O TTL do refresh token reaproveita `JWT_REFRESH_TTL_SECONDS` (default 7 dias) — nenhuma setting nova.

!!! check "Migrando de stateless"
    Adotar o modo DB-backed não invalida sessões existentes de cara, mas os refresh tokens JWT antigos **param de ser aceitos** (o `/refresh` agora procura no banco). Force um novo login após o deploy, ou rode um período de carência aceitando ambos no seu próprio handler.

---

## Rotação

Cada login (ou signup auto-ativado / ativação / reset / mfa-verify) cria um token numa **família nova**. Cada refresh rotaciona dentro da mesma família:

```text
login ──> tokenA (família F)
  │
  └─ POST /refresh (tokenA) ──> tokenA.used_at set, emite tokenB (família F)
        │
        └─ POST /refresh (tokenB) ──> tokenB.used_at set, emite tokenC (família F)
```

Se um atacante roubar `tokenA` e tentar usá-lo **depois** de você já ter rotacionado pra `tokenB`:

```text
POST /refresh (tokenA)  # tokenA.used_at != null  ->  REUSO DETECTADO
  └─ revoga TODA a família F (tokenA, tokenB, tokenC...)
  └─ 401
```

Resultado: tanto o atacante quanto a vítima são deslogados na próxima tentativa. A vítima refaz login (incômodo pequeno), o atacante perde o acesso (ganho grande).

!!! danger "Single-use é obrigatório pro reuso funcionar"
    O cliente **tem** que descartar o refresh token antigo após cada `/refresh` e guardar o novo. Reusar um token rotacionado dispara o kill de família — não é bug, é a feature.

---

## Logout

Com o modo DB-backed ligado, o router monta `POST /auth/logout`:

```python
import httpx


async def logout(refresh_token: str) -> None:
    """Revoke a session via the bundled logout endpoint."""
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        response = await client.post(
            "/auth/logout",
            json={"refresh_token": refresh_token, "all_sessions": False},
        )
        response.raise_for_status()  # 204 No Content
```

- `all_sessions=False` (default) — revoga só a **família** do token (aquele login).
- `all_sessions=True` — revoga **todos** os refresh tokens do usuário (deslogar de todo lugar).

O endpoint é **idempotente**: token desconhecido ou já revogado ainda retorna `204` e nunca vaza se o token existia.

!!! note "Ausente no modo stateless"
    Sem `refresh_token_model` o `/auth/logout` **não é montado** — um JWT stateless não pode ser revogado, então o endpoint não faria sentido.

---

## Service direto

Quem monta os próprios endpoints usa o serviço sem o router. Os três métodos:

```python
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import UserAuthService


async def issue(service: UserAuthService, session: AsyncSession, user: object) -> None:
    """Emit a DB-backed pair, rotate it, then revoke the session."""
    access, refresh = await service.issue_token_pair(session, user)
    await session.commit()

    # Rotaciona: marca o antigo como used, emite novo na mesma família.
    _user, new_access, new_refresh = await service.refresh_tokens(
        session, refresh_token=refresh
    )
    await session.commit()

    # Logout: revoga a família (ou all_sessions=True pra tudo).
    await service.revoke_refresh_token(session, refresh_token=new_refresh)
    await session.commit()
```

| Método | O que faz |
| --- | --- |
| `issue_token_pair(session, user, *, family_id=None)` | Emite `(access, refresh)`. Opaco+persistido quando há model; JWT stateless quando não. |
| `refresh_tokens(session, *, refresh_token)` | Rotaciona. Detecta reuso → revoga família. Retorna `(user, access, refresh)`. |
| `revoke_refresh_token(session, *, refresh_token, all_sessions=False)` | Logout. Revoga a família (ou tudo). Idempotente. |

!!! tip "issue_jwt_pair ainda existe"
    O `issue_jwt_pair(user)` síncrono (stateless puro) continua disponível pra back-compat. Em modo DB-backed prefira `issue_token_pair`, que recebe a `session` e persiste a linha.

---

## Segurança

- **Só o hash no banco.** O plaintext do refresh token é retornado **uma vez** na emissão; o banco guarda só o SHA-256. Um vazamento de banco não rende tokens usáveis.
- **Single-use + família.** Rotação obrigatória + kill de família transformam um roubo de refresh token de "acesso por dias" em "uma tentativa e ambos caem".
- **Access token inalterado.** Continua JWT stateless curto (`JWT_ACCESS_TTL_SECONDS`, default 1h) — sem lookup por request. O DB-backed é só pro refresh.
- **CASCADE.** A FK com `ondelete="CASCADE"` apaga os tokens junto com o usuário.

!!! warning "Troque o `JWT_SECRET`"
    O access token continua assinado com `JWT_SECRET`. O default `"change-me-change-me-change-me-32"` é placeholder — sobrescreva em produção, senão o access token é forjável (e aí o refresh DB-backed não salva).

---

## Próximos passos

- **[Auth flow (signup/reset)](auth-flow.md)** — o fluxo completo onde os tokens são emitidos.
- **[MFA (TOTP / 2FA)](mfa.md)** — segundo fator; o `mfa-verify` também emite o par DB-backed.
- **[Segurança](security.md)** — middlewares de rate limit, idempotência, CSRF.

### Recap

- `refresh_token_model=` liga o modo DB-backed — sem ele, stateless de sempre.
- Refresh vira **opaco** (hash no banco); access continua JWT.
- Rotação **single-use** + **família** = detecção de reuso → `POST /auth/refresh` mata a família num roubo.
- `POST /auth/logout` revoga sessão (ou todas com `all_sessions=true`); montado só no modo DB-backed.
