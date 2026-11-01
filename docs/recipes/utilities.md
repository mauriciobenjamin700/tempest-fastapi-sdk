# Utilitários diversos

Helpers pequenos e sem estado do SDK base: nenhum extra, nenhuma
configuração. Cada um resolve uma dor que todo serviço acaba reescrevendo, e
quase sempre reescreve com o mesmo defeito: `datetime` sem fuso, IP lido de
um header que o cliente controla, senha temporária que a própria política
recusa.

Esta página cobre os helpers genéricos. As famílias maiores de `utils`
(documento brasileiro, campos validados, upload, download, retry, métricas)
têm receita própria, listadas em [Onde mora o resto](#onde-mora-o-resto).

## Datas em UTC: `utcnow` / `to_utc`

`datetime.utcnow()` devolve um `datetime` **sem fuso**, que o Python compara
como hora local em qualquer conta seguinte. `utcnow()` devolve o agora já
com `tzinfo=UTC`. `to_utc(value)` converte qualquer `datetime` para UTC, e
lê o valor sem fuso como se já estivesse em UTC.

```python
from datetime import datetime, timedelta, timezone

from tempest_fastapi_sdk import to_utc, utcnow

created_at: datetime = utcnow()
naive: datetime = to_utc(datetime(2026, 8, 2, 10, 30))
brasilia: datetime = to_utc(
    datetime(2026, 8, 2, 10, 30, tzinfo=timezone(timedelta(hours=-3)))
)

print(created_at)
print(naive)
print(brasilia)
```

```text
2026-11-01 12:25:28.636506+00:00
2026-08-02 10:30:00+00:00
2026-08-02 13:30:00+00:00
```

O valor sem fuso **não** é convertido: `10:30` continua `10:30`, só ganha o
`+00:00`. Já o horário de Brasília vira `13:30` em UTC.

## Filtrar e estender um dict: `modify_dict`

Remove chaves e acrescenta outras num passo só, devolvendo um dict **novo**:

```python
from typing import Any

from tempest_fastapi_sdk import modify_dict

payload: dict[str, Any] = {"id": 1, "password": "x", "name": "Ana"}
safe: dict[str, Any] = modify_dict(payload, exclude=["password"], include={"role": "user"})

print(safe)
print(payload)
```

```text
{'id': 1, 'name': 'Ana', 'role': 'user'}
{'id': 1, 'password': 'x', 'name': 'Ana'}
```

O original fica intacto.

## IP do cliente: `get_client_ip`

Rate limit, auditoria e bloqueio por IP dependem de saber quem chamou. Atrás
de um proxy, o peer da conexão é o proxy, e o IP real vem num header. O
problema é que o cliente também pode mandar esse header.

`get_client_ip` só confia em **um** header, o que você nomeia em
`trusted_header`, e só quando o valor é exatamente **um** IP. Sem
`trusted_header`, usa o peer da conexão.

```python
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import get_client_ip

app: FastAPI = FastAPI()


@app.get("/whoami")
async def whoami(request: Request) -> dict[str, str]:
    return {
        "trusted": get_client_ip(request, trusted_header="x-real-ip"),
        "peer": get_client_ip(request),
    }


client: TestClient = TestClient(app)
print(client.get("/whoami", headers={"X-Real-IP": "203.0.113.7"}).json())
print(client.get("/whoami", headers={"X-Real-IP": "203.0.113.7, 10.0.0.1"}).json())
```

```text
{'trusted': '203.0.113.7', 'peer': 'testclient'}
{'trusted': 'testclient', 'peer': 'testclient'}
```

Na segunda chamada o header traz uma lista, não um IP, e é ignorado: a
resposta cai no peer. Quando nem header nem peer existem, o retorno é
`"unknown"`.

!!! warning "Nomeie o header que **a sua borda** escreve"
    `trusted_header` só é seguro quando o proxy na frente do serviço
    sobrescreve esse header em toda requisição. Se ninguém o sobrescreve, o
    cliente escolhe o IP que você vai registrar. Na dúvida, deixe `None`.

### Em middleware ASGI: `get_client_ip_from_scope`

Middleware ASGI puro não tem `Request`, tem o `scope`. A regra é a mesma:

```python
from typing import Any

from tempest_fastapi_sdk import get_client_ip_from_scope

scope: dict[str, Any] = {
    "type": "http",
    "headers": [(b"x-real-ip", b"198.51.100.9")],
    "client": ("10.0.0.2", 5000),
}

print(get_client_ip_from_scope(scope, trusted_header="x-real-ip"))
print(get_client_ip_from_scope({"type": "http", "headers": [], "client": None}))
```

```text
198.51.100.9
unknown
```

### Validar um valor: `parse_client_ip`

É a peça que as duas funções acima usam para decidir se o header vale.
Devolve o IP na forma canônica, ou `None`:

```python
from tempest_fastapi_sdk import parse_client_ip

for value in ["203.0.113.7", " 203.0.113.7 ", "203.0.113.7, 10.0.0.1", "::ffff:1.2.3.4", "nao-e-ip"]:
    print(repr(value), parse_client_ip(value))
```

```text
'203.0.113.7' 203.0.113.7
' 203.0.113.7 ' 203.0.113.7
'203.0.113.7, 10.0.0.1' None
'::ffff:1.2.3.4' ::ffff:1.2.3.4
'nao-e-ip' None
```

??? info "Por que a forma canônica importa"
    O Python 3.12 escreve o IPv6 mapeado `::ffff:1.2.3.4` como
    `::ffff:102:304`; o 3.13 em diante, como `::ffff:1.2.3.4`. Duas réplicas
    em versões diferentes contariam o mesmo cliente em dois baldes de rate
    limit. `parse_client_ip` devolve a forma pontuada nas duas.

## Tokens opacos: `generate_opaque_token` / `verify_opaque_token`

Para API key, link de convite, token de reset: gere o par
`(plaintext, hash)`, **mostre o plaintext uma vez** e guarde só o hash
(SHA-256). A verificação é em tempo constante.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)

plaintext, token_hash = generate_opaque_token()

print(len(plaintext), len(token_hash))
print(verify_opaque_token(plaintext, token_hash))
print(verify_opaque_token("chute", token_hash))
print(hash_opaque_token(plaintext) == token_hash)
```

```text
43 64
True
False
True
```

O plaintext tem 43 caracteres com o default `nbytes=32`; o hash é o hex de
64 caracteres que vai para a coluna. `hash_opaque_token` serve para buscar a
linha pelo token recebido.

!!! tip "Por que opaco e não JWT"
    Token opaco é revogável (apague o hash) e não carrega claim legível. Use
    para segredo de longa duração, como API key. Para sessão curta e sem
    estado, use [JWT](security.md).

## Senha gerada: `generate_password`

Conta criada por login social, ou provisionada por um admin, não tem senha
digitada, mas a coluna costuma ser `NOT NULL`. A senha gerada precisa ser
imprevisível **e** aceita pela política de senha do serviço.

```python
from tempest_fastapi_sdk import generate_password

password: str = generate_password()
longer: str = generate_password(min_length=30)

print(password, len(password))
print(len(longer))
```

```text
yUzj1w1z82o04gfhKnK?LxIE 24
30
```

O default tem 24 caracteres, e sempre traz minúscula, maiúscula, dígito e
caractere especial. Os parâmetros espelham a política
(`min_length`, `max_bytes`, `require_complexity`), então passe os mesmos
valores do `Settings`.

!!! info "Por que não `secrets.token_urlsafe`"
    Contra a política com complexidade ligada, amostrando 200 000 vezes,
    `secrets.token_urlsafe(32)` foi recusada em 26,54% das vezes e
    `secrets.token_hex(32)` em 100% (não tem maiúscula nem caractere
    especial). Gerador que passa três vezes em quatro falha de forma
    intermitente, em produção, dentro de um callback de OAuth.
    `generate_password` sorteia um caractere de cada classe primeiro, então
    passa sempre.

Política impossível falha na hora, não no cadastro:

```python
from tempest_fastapi_sdk import generate_password

try:
    generate_password(min_length=12, max_bytes=6)
except ValueError as exc:
    print(exc)
```

```text
password policy is unsatisfiable: max_bytes=6 is below the effective minimum length of 12
```

## URL assinada: `sign_path` / `verify_path`

Um link de download que expira, sem tabela de token: `sign_path` anexa
`expires` e `signature` ao caminho, e `verify_path` confere os dois na rota.

```python
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from tempest_fastapi_sdk import sign_path, verify_path
from tempest_fastapi_sdk.exceptions import AppException

now: datetime = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
path: str = "/api/files/relatorio 2026.pdf"
url: str = sign_path(
    path,
    secret="s3cr3t",
    expires_in=timedelta(minutes=10),
    purpose="files",
    now=now,
)
print(url)

query: dict[str, list[str]] = parse_qs(urlsplit(url).query)
expires: int = int(query["expires"][0])
signature: str = query["signature"][0]

verify_path(path, expires=expires, signature=signature, secret="s3cr3t", purpose="files", now=now)
print("válida")

for purpose, instant in [("files", now + timedelta(minutes=11)), ("email-link", now)]:
    try:
        verify_path(
            path,
            expires=expires,
            signature=signature,
            secret="s3cr3t",
            purpose=purpose,
            now=instant,
        )
    except AppException as exc:
        print(exc.status_code, exc.code)
```

```text
/api/files/relatorio%202026.pdf?expires=1793535000&signature=nozJSjnE1ujH8np8VTXahFq6JFcMwod0p5p1M9lJJiI
válida
403 SIGNED_URL_EXPIRED
403 SIGNED_URL_INVALID
```

- **`path` é o caminho decodificado**, como a rota vê em
  `request.scope["path"]`. O espaço vira `%20` na URL, mas você assina e
  verifica `relatorio 2026.pdf`.
- **`purpose` amarra a assinatura a um uso.** URL assinada para `files` não
  vale para `email-link`, mesmo com o mesmo segredo.
- **`now=`** existe para teste; em produção, omita.

Servir arquivo do disco com URL assinada já pronta está em
[Storage em disco local](local-storage.md).

## Payload de formulário: `form_encode`

Algumas APIs (Stripe é a mais conhecida) recebem `application/x-www-form-urlencoded`
com objeto aninhado em notação de colchete. `form_encode` achata o dict:

```python
from typing import Any

from tempest_fastapi_sdk import form_encode

payload: dict[str, Any] = {
    "amount": 1000,
    "metadata": {"user_id": 42},
    "items": [{"price": "price_123", "quantity": 2}],
    "paid": True,
    "note": None,
}

print(form_encode(payload))
```

```text
{'amount': '1000', 'metadata[user_id]': '42', 'items[0][price]': 'price_123', 'items[0][quantity]': '2', 'paid': 'true'}
```

`None` **some** em vez de virar `""`: nessas APIs string vazia apaga o campo,
então mandar `""` apagaria um dado que você nem quis tocar. O resultado vai
direto em `httpx` como `data=`.

## Contraste de cor: `is_light` / `relative_luminance`

Cor de marca escolhida pelo cliente, texto por cima: preto ou branco?
`is_light` responde pela luminância relativa do WCAG.

```python
from tempest_fastapi_sdk.utils import is_light, relative_luminance

for color in ["#ffffff", "#000", "#2563eb", "#facc15", "rebeccapurple"]:
    print(color, relative_luminance(color), is_light(color))
```

```text
#ffffff 1.0 True
#000 0.0 False
#2563eb 0.15315153582201535 False
#facc15 0.6356386777636567 True
rebeccapurple None False
```

Fundo claro (`True`) pede texto escuro. Cor que não é hex (`rebeccapurple`,
`rgb(...)`) volta `None` na luminância e é tratada como escura.

!!! note "Import de `tempest_fastapi_sdk.utils`"
    Os dois não são re-exportados no topo do pacote, só em
    `tempest_fastapi_sdk.utils`.

## Onde mora o resto

| Família | Exemplos | Receita |
| --- | --- | --- |
| Documento, telefone, CEP, moeda BR | `is_valid_cpf`, `normalize_phone_br`, `format_currency_br` | [Helpers brasileiros](br-helpers.md) |
| Campos Pydantic prontos | `CPFField`, `PriceField`, `PixKeyField`, `SlugField` | [Campos validados](fields.md) |
| Upload e tipo de arquivo | `UploadUtils`, `read_upload_capped`, `sniff_mime` | [Uploads](uploads.md) |
| Download e `Content-Disposition` | `DownloadUtils`, `build_content_disposition` | [Downloads](downloads.md) |
| Retry e cliente HTTP | `async_retry`, `RetryPolicy`, `HTTPClient` | [HTTP client](http-client.md) |
| Senha, JWT, throttle de login | `PasswordUtils`, `check_password_policy`, `AttemptThrottle` | [Auth flow](auth-flow.md) e [Segurança](security.md) |
| TOTP | `TOTPHelper` | [MFA](mfa.md) |
| E-mail | `EmailUtils` | [E-mail transacional](email.md) |
| Métricas de sistema | `MetricsUtils` | [Métricas](metrics.md) |
| Logging | `LogUtils` | [Logging](logging.md) |
| Arquivo em storage | `FileStoreUtils` | [File store](file-store.md) |

## Recap

- `utcnow()` / `to_utc()`: sempre com fuso, em UTC; valor sem fuso é lido
  como UTC.
- `modify_dict(data, exclude=, include=)`: filtra e estende sem mutar.
- `get_client_ip` / `get_client_ip_from_scope`: um header confiável, opt-in,
  e só quando ele traz exatamente um IP; `parse_client_ip` é a regra.
- `generate_opaque_token()`: plaintext uma vez, hash no banco, verificação em
  tempo constante.
- `generate_password()`: senha que a política aceita sempre, por construção.
- `sign_path` / `verify_path`: link que expira, preso a um `purpose`.
- `form_encode`: dict aninhado em notação de colchete, sem mandar `None`.
- `is_light`: texto escuro ou claro sobre uma cor qualquer.
