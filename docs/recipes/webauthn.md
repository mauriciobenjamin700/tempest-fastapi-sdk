# WebAuthn / passkeys

O TOTP prova que o usuário tem um segredo compartilhado. Uma página de phishing
que pede o código e o repassa em tempo real derrota isso — o servidor recebe um
código válido e não tem como saber de onde ele veio.

WebAuthn resolve o problema por construção: a assinatura é **atada à origem**
que pediu. Uma credencial registrada em `app.example.com` não produz nada que
uma página em `app-example.com` consiga usar, porque o navegador se recusa a
usá-la ali. É essa propriedade — e não "sem senha" — que justifica a feature.

!!! info "Extra necessário"
    ```bash
    uv add "tempest-fastapi-sdk[auth,webauthn]"
    ```
    O extra traz o `fido2` (Yubico), que faz o parsing CBOR, as chaves COSE, os
    formatos de atestação e a verificação de assinatura. O módulo importa sem o
    extra; quem nunca constrói um `WebAuthnService` não paga nada.

## O que você vai montar

Duas cerimônias, de duas requisições cada:

```text
Registro    POST /auth/webauthn/register/begin      (bearer) → options + challenge_id
            navigator.credentials.create(options.publicKey)
            POST /auth/webauthn/register/complete   (bearer) → credencial salva

Login       POST /auth/webauthn/authenticate/begin            → options + challenge_id
            navigator.credentials.get(options.publicKey)
            POST /auth/webauthn/authenticate/complete         → access + refresh token
```

Mais duas rotas de gerenciamento: `GET /auth/webauthn/credentials` lista o que a
conta registrou e `POST /auth/webauthn/credentials/delete` remove uma.

## Passo 1 — a tabela de credenciais

Uma credencial é uma chave **pública**. Diferente de um hash de senha, um
vazamento completo dessa tabela não autentica ninguém.

```python
# src/db/models.py

from tempest_fastapi_sdk import (
    BaseUserModel,
    make_user_token_model,
    make_web_authn_credential_model,
)


class UserModel(BaseUserModel):
    """The project's user table."""

    __tablename__ = "users"


UserTokenModel = make_user_token_model(user_table="users")
UserWebAuthnCredentialModel = make_web_authn_credential_model(user_table="users")
```

Gere a migration como sempre — `tempest db revision -m "webauthn credentials"`.

## Passo 2 — as settings

```python
# src/core/settings.py

from tempest_fastapi_sdk import BaseAppSettings
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class Settings(AuthSettings, JWTSettings, BaseAppSettings):
    """Application settings."""


settings = Settings(
    JWT_SECRET="change-me-32-chars-minimum-secret",
    AUTH_WEBAUTHN_ENABLED=True,
    AUTH_WEBAUTHN_RP_ID="example.com",
    AUTH_WEBAUTHN_RP_NAME="Acme",
)
```

!!! danger "`AUTH_WEBAUTHN_RP_ID` é a fronteira de segurança"
    É o domínio ao qual a credencial fica atada. Precisa ser o domínio da
    origem do site ou um sufixo registrável dele: `example.com` cobre
    `app.example.com`; o contrário é inválido e o navegador recusa a cerimônia.
    Em desenvolvimento, use `localhost`.

    Trocar o `rp_id` depois **invalida toda credencial já registrada** — elas
    ficam atadas ao valor antigo. Escolha o domínio mais amplo que você vai
    querer, não o mais específico.

!!! warning "Origens em desenvolvimento"
    Por padrão o `fido2` aceita `https://<rp_id>` e seus subdomínios. Um
    frontend Vite em `http://localhost:5173` não cai nessa regra, então a
    cerimônia falha. Liste as origens explicitamente:

    ```python
    settings = Settings(
        JWT_SECRET="change-me-32-chars-minimum-secret",
        AUTH_WEBAUTHN_ENABLED=True,
        AUTH_WEBAUTHN_RP_ID="localhost",
        AUTH_WEBAUTHN_ALLOWED_ORIGINS=["http://localhost:5173"],
    )
    ```

    Quando a lista está preenchida ela é a allowlist **inteira** — a regra
    default deixa de valer. Cada entrada é uma página autorizada a gastar a
    credencial, então mantenha exata e nunca coloque um valor de produção junto
    de um de desenvolvimento no mesmo arquivo.

## Passo 3 — montar o router

```python
# src/api/app.py

from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    UserAuthService,
    WebAuthnService,
    make_auth_router,
)

from src.core.settings import settings
from src.db.models import UserModel, UserTokenModel, UserWebAuthnCredentialModel


def create_app() -> FastAPI:
    db = AsyncDatabaseManager(db_url=settings.DATABASE_URL)
    service = UserAuthService(
        user_model=UserModel,
        token_model=UserTokenModel,
        auth_settings=settings,
        jwt_settings=settings,
    )
    webauthn = WebAuthnService(
        user_model=UserModel,
        credential_model=UserWebAuthnCredentialModel,
        auth_settings=settings,
    )
    app = FastAPI()
    app.include_router(
        make_auth_router(
            service,
            session_factory=db.session_dependency,
            webauthn=webauthn,
        ),
    )
    return app
```

`AUTH_WEBAUTHN_ENABLED=True` sem passar `webauthn=` levanta `RuntimeError` no
`create_app`. A aplicação não sobe com endpoints que responderiam 500 — o mesmo
critério do `recovery_code_model` no MFA.

## Passo 4 — o frontend

O navegador fala em `ArrayBuffer`; a API fala em base64url. Deixe o próprio
navegador converter com `PublicKeyCredential.parseCreationOptionsFromJSON` e
`.toJSON()` — sem eles, você reimplementa a conversão nos dois sentidos.

```javascript
// registro (o usuário já está logado)
const begin = await fetch("/auth/webauthn/register/begin", {
  method: "POST",
  headers: { Authorization: `Bearer ${accessToken}` },
}).then((r) => r.json());

const credential = await navigator.credentials.create(
  PublicKeyCredential.parseCreationOptionsFromJSON(begin.options.publicKey),
);

await fetch("/auth/webauthn/register/complete", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${accessToken}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    challenge_id: begin.challenge_id,
    credential: credential.toJSON(),
    name: "MacBook",
  }),
});
```

```javascript
// login sem senha e sem digitar e-mail
const begin = await fetch("/auth/webauthn/authenticate/begin", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({}),
}).then((r) => r.json());

const assertion = await navigator.credentials.get(
  PublicKeyCredential.parseRequestOptionsFromJSON(begin.options.publicKey),
);

const tokens = await fetch("/auth/webauthn/authenticate/complete", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    challenge_id: begin.challenge_id,
    credential: assertion.toJSON(),
  }),
}).then((r) => r.json());
```

!!! tip "Com e sem e-mail"
    Omitir `email` no `begin` é o fluxo **descoberto**: as options não levam
    lista de credenciais e o autenticador oferece as contas que ele guarda.
    Passar `email` estreita a cerimônia para uma conta — útil para chave de
    segurança que não armazena credencial residente.

    Um e-mail que não existe devolve uma cerimônia normal com lista vazia, e
    não um erro. Responder diferente transformaria o endpoint em oráculo de
    enumeração de contas.

## O que o SDK verifica por você

| Verificação | Onde | Por quê |
| --- | --- | --- |
| Assinatura + origem + `rp_id` | `fido2` | O núcleo da resistência a phishing. |
| Desafio usado uma vez só | `WebAuthnChallengeStore.pop` | Uma resposta capturada não se repete. |
| Contador de assinatura avançou | `authenticate_complete` | Sinal de autenticador clonado, previsto na spec. Autenticadores que sempre reportam `0` (a maioria das passkeys de plataforma) ficam de fora — para eles o contador não carrega informação. |
| Credencial já registrada | `register_complete` | Unicidade é da tabela, não da conta: a mesma chave em duas contas viraria duas linhas indistinguíveis. |
| Conta ativa | `authenticate_complete` | Desativar o usuário precisa fechar o caminho da passkey também. |
| Delete escopado ao dono | `delete_credential` | Um ID de outra conta responde 404 igual a um que não existe. |

## Multi-worker: o store de desafios

O estado entre as duas metades da cerimônia mora no servidor — o cliente não
pode alterá-lo, então cookie não serve. O default é em processo, correto para
um worker só. Com mais de uma réplica, uma cerimônia que começa na A e termina
na B não encontra estado nenhum:

```python
# src/api/app.py

from fastapi import FastAPI
from redis.asyncio import Redis

from tempest_fastapi_sdk import RedisWebAuthnChallengeStore, WebAuthnService

from src.core.settings import settings
from src.db.models import UserModel, UserWebAuthnCredentialModel


def build_webauthn() -> WebAuthnService:
    redis: Redis = Redis.from_url(settings.REDIS_URL)
    return WebAuthnService(
        user_model=UserModel,
        credential_model=UserWebAuthnCredentialModel,
        auth_settings=settings,
        challenge_store=RedisWebAuthnChallengeStore(redis),
    )
```

O store Redis usa `GETDEL`: ler e apagar viram uma operação só, então duas
conclusões concorrentes da mesma cerimônia não podem ambas achar o estado.

## Recuperação de conta

`backed_up` na listagem diz se a credencial é **sincronizada** (passkey de
iCloud / Google Password Manager, sobrevive à perda do aparelho) ou **atada ao
dispositivo** (uma chave de segurança física, não sobrevive).

A diferença decide o seu produto, não o SDK:

- Só passkeys sincronizadas? A recuperação já existe: o usuário entra na conta
  do provedor em outro aparelho.
- Chave física? Registre **duas** e guarde uma, ou mantenha um segundo fator
  (senha + MFA) como caminho de recuperação.

O endpoint de delete não impede remover a última credencial. Se uma senha ainda
é um fallback aceitável é decisão da aplicação, e o SDK não tem como saber.

## Relação com o MFA

`POST /auth/webauthn/authenticate/complete` **não** passa pelo desafio de MFA.
Isso é deliberado: uma passkey com verificação de usuário
(`AUTH_WEBAUTHN_USER_VERIFICATION="required"`) já prova posse do autenticador
*e* um fator local (PIN, biometria) — que é exatamente o que o segundo passo
existe para provar. Exigir TOTP em cima transformaria o login mais forte no mais
incômodo.

Os dois convivem: a mesma conta pode ter senha + TOTP e passkeys, e usa o que
tiver à mão.

## Recap

- `make_web_authn_credential_model(user_table=...)` cria a tabela; ela guarda só
  chave pública.
- `WebAuthnService` roda as duas cerimônias; `make_auth_router(webauthn=...)`
  monta as seis rotas quando `AUTH_WEBAUTHN_ENABLED` está ligado.
- `AUTH_WEBAUTHN_RP_ID` é a fronteira de segurança, e mudá-lo invalida tudo que
  já foi registrado.
- `AUTH_WEBAUTHN_ALLOWED_ORIGINS` é para desenvolvimento, e substitui a regra
  default por inteiro.
- Desafio é uso único; contador que não avança é recusado; delete é escopado ao
  dono.
- Multi-réplica exige `RedisWebAuthnChallengeStore`.

Próximo: [MFA (TOTP / 2FA)](mfa.md) para o segundo fator clássico, ou
[Refresh tokens](refresh-tokens.md) para revogar a sessão que a passkey abriu.
