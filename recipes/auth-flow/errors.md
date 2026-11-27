# Códigos de erro do seu produto

Use para trocar os códigos de erro do fluxo pelos do seu produto com `AuthExceptions`.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md).

Serviço que já existe tem clientes tratando `code`s próprios —
`ERROR_USER_INVALID_CREDENTIALS`, `USER_ALREADY_EXISTS`, `INVALID_PASSWORD`. O
SDK responde `UNAUTHORIZED`, `CONFLICT`, `VALIDATION_ERROR`, e o catálogo troca
a **mensagem**, não o código. Sem uma costura, adotar o `UserAuthService`
significava sobrescrever método só para trocar a exceção.

`AuthExceptions` é essa costura: diz qual classe sobe em cada ponto de recusa.

```python
# src/services/auth.py

from tempest_fastapi_sdk import AuthExceptions, UserAuthService
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    ForbiddenException,
    InvalidTokenException,
    UnauthorizedException,
    ValidationException,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel


class InvalidCredentialsError(UnauthorizedException):
    """E-mail ou senha errados."""

    code: str = "ERROR_USER_INVALID_CREDENTIALS"


class UserDeactivatedError(ForbiddenException):
    """Conta desativada, revelada a quem acertou a senha."""

    code: str = "USER_ACCOUNT_DEACTIVATED"


class UserAlreadyExistsError(ConflictException):
    """E-mail já cadastrado."""

    code: str = "USER_ALREADY_EXISTS"


class InvalidPasswordError(ValidationException):
    """Senha curta ou fraca."""

    code: str = "INVALID_PASSWORD"


class PasswordTooLongError(ValidationException):
    """Senha acima do limite do bcrypt."""

    code: str = "PASSWORD_TOO_LONG"


class InvalidResetTokenError(InvalidTokenException):
    """Link de uso único que não vale."""

    code: str = "INVALID_PASSWORD_RESET_TOKEN"


auth_service = UserAuthService(
    user_model=UserModel,
    token_model=UserTokenModel,
    auth_settings=settings,
    jwt_settings=settings,
    exceptions=AuthExceptions(
        invalid_credentials=InvalidCredentialsError,
        account_inactive=UserDeactivatedError,
        email_taken=UserAlreadyExistsError,
        password_too_short=InvalidPasswordError,
        password_too_long=PasswordTooLongError,
        password_too_weak=InvalidPasswordError,
        invalid_token=InvalidResetTokenError,
    ),
)
```

Medido com `register_exception_handlers(app, catalog=default_message_catalog())`
e `AUTH_REVEAL_INACTIVE_ACCOUNT=true`:

| Requisição | Sem `exceptions=` | Com o mapa acima |
|------------|-------------------|------------------|
| login, senha errada | `401` `UNAUTHORIZED` | `401` `ERROR_USER_INVALID_CREDENTIALS` |
| login, conta desativada, senha certa | `403` `ACCOUNT_INACTIVE` | `403` `USER_ACCOUNT_DEACTIVATED` |
| signup, senha curta | `422` `VALIDATION_ERROR` | `422` `INVALID_PASSWORD` |
| signup, 73 bytes | `422` `VALIDATION_ERROR` | `422` `PASSWORD_TOO_LONG` |
| signup, e-mail repetido | `409` `CONFLICT` | `409` `USER_ALREADY_EXISTS` |

Cada campo é tipado com a classe default, então a sua **precisa** herdar dela.
É isso que segura o status: um `InvalidCredentialsError` é um
`UnauthorizedException`, então continua `401`, e o caminho da recusa — inclusive
o bcrypt único do login — é o mesmo. Muda a classe e, com ela, o `code`.

!!! tip "A mensagem do seu `code`"
    Nas violações de senha e na conta desativada o SDK passa um `message_key`
    próprio (`PASSWORD_TOO_SHORT`, `ACCOUNT_INACTIVE`, …), então o `detail`
    continua traduzido mesmo com a sua classe. Nos outros pontos o catálogo
    procura o **seu** `code`; sem tradução para ele, o `detail` é a mensagem em
    inglês do SDK (`invalid email or password`). Acrescente as suas com
    `default_message_catalog().merge({"pt-BR": {"ERROR_USER_INVALID_CREDENTIALS": "..."}})`.

!!! note "O que `invalid_token` cobre"
    Os links de uso único: ativação, troca de senha, troca de e-mail e
    verificação — desconhecido, usado, expirado, ou apontando para usuário que
    não existe mais. Desde a v0.305.0 a troca de senha com usuário removido é
    `InvalidTokenException` como os irmãos, não mais `404`. Refresh token fica de
    fora: é outro contrato com o cliente.

**Recap.** `AuthExceptions` troca a classe — e o `code` — em cada ponto de
recusa, sem override e sem mudar status nem custo. As violações de senha já
saem com `message_key` próprio, e `AUTH_REVEAL_INACTIVE_ACCOUNT` mostra o `403`
só a quem provou a senha.
