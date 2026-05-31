# Helpers brasileiros {#brazilian-helpers}

Validadores de documentos (CPF, CNPJ, CEP) e normalizador/validador de números de telefone para formatos BR. Pura stdlib — sem dependências extras.

## CPF / CNPJ / telefone {#cpf-cnpj-phone}


`tempest_fastapi_sdk.utils.regex` traz padrões de regex prontos para uso, validadores, normalizadores e tipos Pydantic para os campos de identidade/contato que aparecem em quase toda API brasileira. Nenhum extra necessário — pura stdlib + Pydantic (já uma dependência core).

| Símbolo | Tipo | Finalidade |
| --- | --- | --- |
| `CPF_PATTERN`, `CNPJ_PATTERN`, `CPF_CNPJ_PATTERN`, `PHONE_BR_PATTERN` | `re.Pattern[str]` | Regex compilada (entrada mascarada ou crua). |
| `is_valid_cpf`, `is_valid_cnpj`, `is_valid_cpf_cnpj` | `(str) -> bool` | Verificação de formato **+** cálculo dos dígitos verificadores. Sequências de dígitos todos iguais são rejeitadas. |
| `is_valid_phone_br` | `(str) -> bool` | Formato de telefone BR: `+55` opcional, DDD opcional, nono dígito opcional. |
| `normalize_cpf`, `normalize_cnpj`, `normalize_cpf_cnpj`, `normalize_phone_br` | `(str) -> str` | Remove a máscara deixando apenas dígitos; lança `ValueError` se inválido. |
| `only_digits` | `(str) -> str` | Remove todo caractere que não seja dígito. |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR` | `Annotated[str, AfterValidator(...)]` | Tipos de campo Pydantic prontos para uso — validam + normalizam automaticamente. |

#### Uso em schema {#schema-usage}

```python
from pydantic import EmailStr, Field

from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CPF, CPFOrCNPJ, PhoneBR


class CustomerCreateSchema(BaseSchema):
    """Payload for POST /customers.

    `document` accepts CPF or CNPJ in masked or raw form and is
    stored digits-only after validation. `phone` is normalized the
    same way. Invalid values surface as a Pydantic `ValidationError`
    (HTTP 422 via the SDK exception handler).
    """

    name: str = Field(min_length=1, max_length=128)
    email: EmailStr
    document: CPFOrCNPJ
    phone: PhoneBR
```

Entrada válida:

```json
{
    "name": "Ana",
    "email": "ana@example.com",
    "document": "529.982.247-25",
    "phone": "+55 (11) 98888-7777"
}
```

Após a validação:

```python
CustomerCreateSchema(...).document  # "52998224725"
CustomerCreateSchema(...).phone     # "5511988887777"
```

#### Validação manual (services, controllers, queue handlers) {#manual-validation-services-controllers-queue-handlers}

```python
from tempest_fastapi_sdk.utils import (
    is_valid_cpf_cnpj,
    normalize_cpf_cnpj,
    only_digits,
)

if not is_valid_cpf_cnpj(raw_document):
    raise ValidationException(message="Documento inválido")

document_digits = normalize_cpf_cnpj(raw_document)
```

#### Filtrando pelos dígitos armazenados {#filtering-by-stored-digits}

Os normalizadores removem as máscaras antes de salvar, então filtros de repositório e constraints de unicidade trabalham todos sobre a forma canônica de apenas dígitos:

```python
await repo.get({"document": normalize_cpf_cnpj(query)})
```

---


## CEP (código postal) {#cep-zipcode}


`CEP` é um tipo `Annotated[str, AfterValidator(normalize_cep)]` — coloque-o em um schema Pydantic e os valores recebidos são aceitos como `"01310-100"` ou `"01310100"`, normalizados para 8 dígitos e rejeitados (`ValidationError` → envelope HTTP 422) quando não correspondem ao formato. CEPs não têm dígitos verificadores, então a validação é apenas de formato.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CEP


class AddressCreateSchema(BaseSchema):
    cep: CEP
    street: str
    number: str
```

Variantes imperativas: `is_valid_cep(value)`, `normalize_cep(value)`, além de `CEP_PATTERN` para uso direto da regex. Use-as dentro de services / queue handlers onde você não quer um round-trip pelo Pydantic.


## Helpers utilitários (utcnow, to_utc, modify_dict) {#utility-helpers-utcnow-to_utc-modify_dict}


Pequenos helpers stateless de `tempest_fastapi_sdk.utils` dos quais o próprio SDK depende e que aparecem em todos os services. Disponíveis sem nenhum extra.

| Helper | Assinatura | Finalidade |
| --- | --- | --- |
| `utcnow()` | `() -> datetime` | Hora atual como um datetime UTC timezone-aware — o SDK usa isso para os defaults de `created_at` / `updated_at`. |
| `to_utc(value)` | `(datetime) -> datetime` | Coage datetimes naive para UTC (assumido como UTC) e datetimes aware para UTC via `astimezone`. Usado pelos field validators de `BaseResponseSchema`. |
| `modify_dict(data, exclude=None, include=None)` | `(dict, list[str] \| None, dict \| None) -> dict` | Filtro + merge em uma única passagem. Remove chaves sensíveis antes do logging ou mescla campos computados ao mapear payloads para models ORM. |

#### Timestamps do mesmo jeito em todo lugar {#timestamps-the-same-way-everywhere}

`utcnow` é o "now" canônico do SDK. Use-o para timestamps de soft-delete, `iat` / `exp` de JWT, trilhas de auditoria — qualquer coisa onde misturar datetimes naive e aware te queimaria mais tarde.

```python
from datetime import timedelta

from tempest_fastapi_sdk import to_utc, utcnow


now = utcnow()                      # timezone-aware UTC
expires_at = now + timedelta(hours=1)

# Normalize whatever the caller gave you
incoming = request.json()["scheduled_for"]              # naive or aware
scheduled_for = to_utc(datetime.fromisoformat(incoming))
```

Um datetime naive é marcado como UTC (não convertido do horário local) para que seja previsível em workers headless e containers Docker onde `time.timezone` é uma incógnita.

#### Remova chaves sensíveis antes do logging / mapeamento {#drop-sensitive-keys-before-logging-mapping}

`modify_dict` é o pequeno utilitário que alimenta `BaseSchema.to_dict(exclude=..., include=...)` e `BaseModel.update_from_dict(...)`. Use-o diretamente quando não quiser recorrer a round-trips pelo Pydantic:

```python
from tempest_fastapi_sdk import LogUtils, modify_dict

log = LogUtils("app.users")

payload = {"email": "ana@example.com", "password": "s3cr3t", "name": "Ana"}

# Strip password before logging
log.info("user_signup", **modify_dict(payload, exclude=["password"]))

# Merge a computed hash before persisting
user_row = modify_dict(
    payload,
    exclude=["password"],
    include={"password_hash": passwords.hash(payload["password"])},
)
```

`include` prevalece sobre `data`, então ele também serve como um helper de "definir ou sobrescrever" sem mutar o dict de origem.

#### Onde cada um dos outros helpers está documentado {#where-every-other-helper-is-documented}

Cada helper tem sua própria recipe — esta seção é o mapa rápido:

| Helper | Recipe |
| --- | --- |
| `PasswordUtils`, `JWTUtils` | [Recipe de autenticação](http.md#authentication) |
| `EmailUtils` | [Recipe de email transacional](http.md#transactional-email) |
| `UploadUtils` | [Recipe de upload de arquivos](http.md#file-uploads) |
| `DownloadUtils`, `build_content_disposition` | [Servindo arquivos privados pela API](http.md#serving-private-files-through-the-api-downloadutils) |
| `LogUtils` + `configure_logging` | [Recipe de logging estruturado & request IDs](logging.md) |
| `MetricsUtils` (CPU/memória/disco/GPU) | [Recipe de métricas de sistema](metrics.md) |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR`, `is_valid_*`, `normalize_*`, `only_digits` | [CPF / CNPJ / telefone](#cpf-cnpj-phone) |
