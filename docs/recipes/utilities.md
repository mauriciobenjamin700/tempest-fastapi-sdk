# Utilitários diversos

Helpers stateless pequenos do SDK base (sem extra). Cada um resolve uma dor
recorrente sem você reescrever.

## Datas em UTC — `utcnow` / `to_utc`

`utcnow()` devolve o "agora" **timezone-aware** em UTC (nunca o
`datetime.utcnow()` ingênuo, fonte clássica de bug); `to_utc(value)`
normaliza qualquer `datetime` para UTC (assume UTC quando vier ingênuo).

```python
from datetime import datetime

from tempest_fastapi_sdk import to_utc, utcnow

created_at: datetime = utcnow()                  # 2026-06-07T19:00:00+00:00
normalized: datetime = to_utc(some_naive_dt)     # vira aware/UTC
```

## Filtrar/estender dict — `modify_dict`

Remove chaves e mescla novas num passo só, devolvendo um **novo** dict
(não muta o original):

```python
from tempest_fastapi_sdk import modify_dict

payload = {"id": 1, "password": "x", "name": "Ana"}
safe = modify_dict(payload, exclude=["password"], include={"role": "user"})
# {"id": 1, "name": "Ana", "role": "user"}
```

## IP do cliente — `get_client_ip`

Resolve o IP a partir do request, opcionalmente confiando em **um** header
setado pela borda (proxy/LB). Sem `trusted_header`, usa só o peer direto —
não confie cegamente em `X-Forwarded-For` exposto ao mundo.

```python
from fastapi import APIRouter, Request

from tempest_fastapi_sdk import get_client_ip

router = APIRouter()


@router.get("/whoami")
async def whoami(request: Request) -> dict[str, str]:
    ip: str = get_client_ip(request, trusted_header="x-real-ip")
    return {"ip": ip}   # "unknown" quando nem o header nem o peer existem
```

## Tokens opacos — `generate_opaque_token` / `verify_opaque_token`

Para API keys, tokens de reset/convite etc.: gere um par
`(plaintext, hash)`, **mostre o plaintext uma vez** ao usuário e persista só
o hash (SHA-256). A verificação é constant-time.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)

plaintext, token_hash = generate_opaque_token()   # mostre plaintext 1x; salve token_hash
# ... mais tarde, ao receber o token de volta:
ok: bool = verify_opaque_token(submitted, token_hash)
# hash_opaque_token(x) para re-hashear sob demanda
```

!!! tip "Por que opaco e não JWT"
    Tokens opacos são revogáveis (basta apagar o hash) e não carregam
    claims legíveis. Use-os para segredos de longa duração (API keys);
    use [JWT](http.md) para sessões de curta duração sem estado.

## Recap

- `utcnow()` / `to_utc()` — sempre timezone-aware em UTC.
- `modify_dict(data, exclude=, include=)` — filtra + estende sem mutar.
- `get_client_ip(request, trusted_header=)` — IP do cliente, com header confiável opt-in.
- `generate_opaque_token()` / `verify_opaque_token()` — segredos hash-and-store, verificação constant-time.
