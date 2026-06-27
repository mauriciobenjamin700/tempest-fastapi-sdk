# Helpers brasileiros

Validadores de documentos (CPF, CNPJ, CEP) e normalizador/validador de telefone para formatos BR. Pura stdlib — sem deps extras.

## CPF / CNPJ / telefone


`tempest_fastapi_sdk.utils.regex` traz padrões de regex prontos, validadores, normalizadores e tipos Pydantic para os campos de identidade/contato que aparecem em quase toda API brasileira. Sem extra — pura stdlib + Pydantic (já é uma dependência core).

| Símbolo | Tipo | Propósito |
| --- | --- | --- |
| `CPF_PATTERN`, `CNPJ_PATTERN`, `CPF_CNPJ_PATTERN`, `PHONE_BR_PATTERN` | `re.Pattern[str]` | Regex compilada (entrada com máscara ou crua). |
| `is_valid_cpf`, `is_valid_cnpj`, `is_valid_cpf_cnpj` | `(str) -> bool` | Match de formato **+** matemática dos dígitos verificadores. Sequências de dígitos iguais rejeitadas. |
| `is_valid_phone_br` | `(str) -> bool` | Formato de telefone BR: `+55` opcional, DDD opcional, nono dígito opcional. |
| `normalize_cpf`, `normalize_cnpj`, `normalize_cpf_cnpj`, `normalize_phone_br` | `(str) -> str` | Remove a máscara deixando só dígitos; levanta `ValueError` se inválido. |
| `only_digits` | `(str) -> str` | Remove todo caractere que não é dígito. |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR` | `Annotated[str, AfterValidator(...)]` | Tipos de campo Pydantic plug-and-play — validam + normalizam automaticamente. |

#### Uso em schema

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

#### Validação manual (services, controllers, handlers de fila)

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

#### Filtrando pelos dígitos armazenados

Os normalizadores removem as máscaras antes de salvar, então os filtros de repository e as constraints de unicidade funcionam sobre a forma canônica só-dígitos:

```python
await repo.get({"document": normalize_cpf_cnpj(query)})
```

---


## CEP


`CEP` é um tipo `Annotated[str, AfterValidator(normalize_cep)]` — coloque-o em um schema Pydantic e os valores de entrada são aceitos como `"01310-100"` ou `"01310100"`, normalizados para 8 dígitos, e rejeitados (`ValidationError` → envelope HTTP 422) quando não casam com o formato. CEPs não têm dígitos verificadores, então a validação é só de formato.

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import CEP


class AddressCreateSchema(BaseSchema):
    cep: CEP
    street: str
    number: str
```

Variantes imperativas: `is_valid_cep(value)`, `normalize_cep(value)`, mais `CEP_PATTERN` para uso de regex cru. Use-os dentro de services / handlers de fila onde você não quer um round-trip Pydantic.


## Estados e municípios

Toda aplicação brasileira acaba precisando de um `<select>` de estado e cidade, ou de validar que a UF e o município que chegaram no payload existem de verdade. O SDK embute essa tabela — **27 estados e 5606 municípios** — então você não precisa chamar a API do IBGE nem versionar um JSON por serviço.

!!! info "Offline e sem dependências"
    Os dados moram em `tempest_fastapi_sdk/utils/data/br_locations.json` e são carregados sob demanda na primeira chamada, depois ficam em cache pelo processo todo. Zero rede, zero extra a instalar.

A UF é uma `StrEnum` e cada estado conhece sua macro-região oficial do IBGE:

```python
from tempest_fastapi_sdk import UF, Region, list_states, get_state, states_by_region


# Todos os 27 estados, ordenados pela sigla.
states = list_states()
print(len(states))  # 27

# Um estado específico (sigla em qualquer caixa, ou um membro de UF).
sp = get_state("sp")
print(sp.uf, sp.name, sp.region)        # UF.SP São Paulo Region.SOUTHEAST
print(len(sp.cities), sp.cities[:2])    # 645 ['Adamantina', 'Adolfo']

# Agrupando por região.
sudeste = states_by_region(Region.SOUTHEAST)
print([state.uf.value for state in sudeste])  # ['ES', 'MG', 'RJ', 'SP']
```

Cada item é um `StateBR` (`uf: UF`, `name: str`, `region: Region`, `cities: list[str]`), pronto pra retornar direto de um endpoint.

### Validando UF e cidade em um schema

`UFField` aceita a sigla em qualquer caixa (`"sp"`, `" RJ "`) e devolve um membro de `UF`. `CityNameField` só apara espaços — a validação cruzada "esta cidade existe nesta UF" é regra de negócio, então roda no service com `is_valid_city` / `normalize_city`:

```python
from tempest_fastapi_sdk import BaseSchema
from tempest_fastapi_sdk.utils import UFField, CityNameField


class AddressCreateSchema(BaseSchema):
    uf: UFField
    city: CityNameField
    street: str
    number: str
```

```python
from tempest_fastapi_sdk import UF, is_valid_city, normalize_city
from tempest_fastapi_sdk.exceptions import ValidationException


def validate_address(uf: UF, city: str) -> str:
    """Garante que a cidade pertence à UF e devolve o nome canônico.

    Args:
        uf (UF): A unidade federativa do endereço.
        city (str): O nome da cidade vindo do payload.

    Returns:
        str: O nome do município em caixa canônica (ex.: "São Paulo").

    Raises:
        ValidationException: Se a cidade não existe na UF informada.
    """
    if not is_valid_city(uf, city):
        raise ValidationException(f"city {city!r} not found in {uf.value}")
    return normalize_city(uf, city)
```

!!! tip "A busca de cidade ignora acentos e caixa"
    `is_valid_city("SP", "sao paulo")` e `normalize_city("rj", "RIO DE JANEIRO")` funcionam — a comparação derruba acentos, caixa e espaços nas pontas. `normalize_city` sempre devolve o nome canônico em caixa correta (`"São Paulo"`, `"Rio de Janeiro"`).

### Choices prontas para o `<select>` do frontend

Os mesmos dados servem para **dois papéis**: validar a entrada (os campos `UFField` / `CityNameField` acima) **e** alimentar os dropdowns do frontend. Para o segundo, `uf_choices`, `region_choices` e `city_choices` devolvem `list[ChoiceBR]` — cada item é um `value` (o que você guarda/envia) + `label` (o que o usuário vê), exatamente o formato que um `<option>` quer:

```python
from uuid import UUID

from fastapi import APIRouter

from tempest_fastapi_sdk.utils import ChoiceBR, city_choices, region_choices, uf_choices

router = APIRouter(prefix="/api/localidades", tags=["localidades"])


@router.get("/ufs")
def list_uf_choices() -> list[ChoiceBR]:
    """Choices de UF: value = sigla, label = nome do estado."""
    return uf_choices()


@router.get("/regioes")
def list_region_choices() -> list[ChoiceBR]:
    """Choices das 5 macro-regiões do IBGE."""
    return region_choices()


@router.get("/ufs/{uf}/cidades")
def list_city_choices(uf: str) -> list[ChoiceBR]:
    """Choices de cidade de uma UF: value = label = nome do município."""
    return city_choices(uf)
```

O `value` de `uf_choices()` é a sigla — o mesmo valor que `UFField` valida na volta, então o que o `<select>` envia já entra direto no seu schema:

```python
uf_choices()[0]          # ChoiceBR(value="AC", label="Acre")
region_choices()[0]      # ChoiceBR(value="Norte", label="Norte")
city_choices("sp")[0]    # ChoiceBR(value="Adamantina", label="Adamantina")
```

!!! info "Por que `ChoiceBR` e não uma tupla?"
    `ChoiceBR` é um schema Pydantic (`value: str`, `label: str`), então serializa como `{"value": ..., "label": ...}` no JSON e aparece tipado no OpenAPI/Swagger — sem o "campo mágico sem tipo". Para o caso clássico estado→cidade, o front chama `/ufs`, e ao escolher uma UF chama `/ufs/{uf}/cidades`.

### Variantes imperativas

| Função | O que faz |
| --- | --- |
| `is_valid_uf(value)` | `True` se a sigla existe (qualquer caixa/espaço). |
| `normalize_uf(value)` | Devolve o `UF`; levanta `ValueError` se inválido. |
| `cities_by_uf(uf)` | Lista de municípios da UF, ordenada. |
| `is_valid_city(uf, city)` | `True` se a cidade pertence à UF (ignora acentos/caixa). |
| `normalize_city(uf, city)` | Nome canônico do município; levanta `ValueError` se não existe. |

!!! note "Endpoint de estados/cidades para o frontend"
    Para `<select>`, prefira `uf_choices()` / `region_choices()` / `city_choices(uf)` (formato `value`/`label`). Se precisar do estado inteiro com a lista de cidades junto, `list_states()` devolve cada `StateBR` com seu `cities`. Como é tudo em memória, não toca o banco.

#### Recapitulando

- `UF` (StrEnum, 27 siglas) + `Region` (5 macro-regiões do IBGE).
- `StateBR` / `CityBR` para respostas tipadas; `ChoiceBR` (`value`/`label`) para dropdowns.
- `list_states`, `get_state`, `cities_by_uf`, `states_by_region` para consultar a tabela embutida.
- `uf_choices`, `region_choices`, `city_choices` para `<select>` do frontend.
- `UFField` / `CityNameField` para campos de schema; `is_valid_*` / `normalize_*` para validação imperativa no service.


## Helpers utilitários (utcnow, to_utc, modify_dict)


Pequenos helpers stateless de `tempest_fastapi_sdk.utils` dos quais o próprio SDK depende e que aparecem em todo serviço. Disponíveis sem nenhum extra.

| Helper | Assinatura | Propósito |
| --- | --- | --- |
| `utcnow()` | `() -> datetime` | Horário atual como datetime UTC ciente de timezone — o SDK usa isto para os defaults de `created_at` / `updated_at`. |
| `to_utc(value)` | `(datetime) -> datetime` | Converte datetimes naive para UTC (assumido UTC) e datetimes aware para UTC via `astimezone`. Usado pelos field validators de `BaseResponseSchema`. |
| `modify_dict(data, exclude=None, include=None)` | `(dict, list[str] \| None, dict \| None) -> dict` | Filtro + merge em uma passada. Remove chaves sensíveis antes de logar ou faz merge de campos computados ao mapear payloads para modelos ORM. |

#### Timestamps do mesmo jeito em todo lugar

`utcnow` é o "agora" canônico do SDK. Use-o para timestamps de soft-delete, `iat` / `exp` de JWT, trilhas de auditoria — qualquer coisa onde misturar datetimes naive e aware te queimaria depois.

```python
from datetime import datetime, timedelta

from fastapi import Request

from tempest_fastapi_sdk import to_utc, utcnow


now = utcnow()                      # timezone-aware UTC
expires_at = now + timedelta(hours=1)


async def parse_scheduled(request: Request) -> datetime:
    """Normalize whatever the caller gave you to a timezone-aware UTC datetime."""
    payload = await request.json()                              # request.json() is async
    incoming: str = payload["scheduled_for"]                    # naive or aware ISO-8601
    return to_utc(datetime.fromisoformat(incoming))
```

Um datetime naive é marcado como UTC (não convertido do horário local) para ser previsível em workers headless e containers Docker onde `time.timezone` é incerto.

#### Remova chaves sensíveis antes de logar / mapear

`modify_dict` é o pequeno utilitário que alimenta `BaseSchema.to_dict(exclude=..., include=...)` e `BaseModel.update_from_dict(...)`. Use-o diretamente quando não quiser chamar round-trips do Pydantic:

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

`include` vence sobre `data`, então ele dobra como um helper de "definir ou sobrescrever" sem mutar o dict de origem.

#### Onde cada outro helper está documentado

Todo helper tem sua própria receita — esta seção é o mapa rápido:

| Helper | Receita |
| --- | --- |
| `PasswordUtils`, `JWTUtils` | [Receita de autenticação](http.md#autenticacao) |
| `EmailUtils` | [Receita de e-mail transacional](http.md#e-mail-transacional) |
| `UploadUtils` | [Receita de upload de arquivos](http.md#upload-de-arquivos) |
| `DownloadUtils`, `build_content_disposition` | [Servindo arquivos privados pela API](http.md#servindo-arquivos-privados-pela-api-downloadutils) |
| `LogUtils` + `configure_logging` | [Receita de logging estruturado & request IDs](logging.md) |
| `MetricsUtils` (CPU/memória/disco/GPU) | [Receita de métricas do sistema](metrics.md) |
| `CPF`, `CNPJ`, `CPFOrCNPJ`, `PhoneBR`, `is_valid_*`, `normalize_*`, `only_digits` | [CPF / CNPJ / telefone](#cpf-cnpj-telefone) |
| `UF`, `Region`, `StateBR`, `CityBR`, `ChoiceBR`, `UFField`, `CityNameField`, `list_states`, `get_state`, `cities_by_uf`, `states_by_region`, `uf_choices`, `region_choices`, `city_choices`, `is_valid_uf`, `normalize_uf`, `is_valid_city`, `normalize_city` | [Estados e municípios](#estados-e-municipios) |
