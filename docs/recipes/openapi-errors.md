# Erros no OpenAPI (Swagger / ReDoc)

O SDK serializa **toda** `AppException` num envelope único —
`{detail, code, details}`. Isso é ótimo pro cliente... desde que ele saiba
**quais** `code` esperar. E é aí que estava o buraco: nada disso aparecia no
OpenAPI.

Esta receita fecha o buraco em quatro passos. Os dois primeiros já resolvem o
problema do frontend; os dois últimos são ergonomia e proteção contra drift. 🚀

## O problema, medido

Pegue uma rota real que levanta seis exceptions:

```python
# src/api/routers/jobs.py
from uuid import UUID

from fastapi import APIRouter

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    CandidateDoesNotHaveCoinsException,
    CategoryNotFoundException,
    ServiceFullException,
    ServiceNotFoundException,
    ServiceOwnerCannotApplyException,
)
from src.schemas import CandidateResponseSchema

router: APIRouter = APIRouter(prefix="/api/jobs")


@router.post("/{service_id}/candidates", status_code=201)
async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
    """Inscreve o usuário autenticado num serviço."""
    raise NotImplementedError
```

Pergunte ao OpenAPI o que essa rota devolve:

```pycon
>>> spec["paths"]["/api/jobs/{service_id}/candidates"]["post"]["responses"].keys()
dict_keys(['201', '422'])
```

Dois status. Mas o fluxo real produz **quatro**:

| Status | `code` | Exception |
| --- | --- | --- |
| 404 | `SERVICE_NOT_FOUND` | `ServiceNotFoundException` |
| 404 | `CATEGORY_NOT_FOUND` | `CategoryNotFoundException` |
| 403 | `SERVICE_OWNER_CANNOT_APPLY` | `ServiceOwnerCannotApplyException` |
| 409 | `SERVICE_FULL` | `ServiceFullException` |
| 409 | `CANDIDATE_ALREADY_EXISTS` | `CandidateAlreadyExistsException` |
| 400 | `CANDIDATE_DOES_NOT_HAVE_COINS` | `CandidateDoesNotHaveCoinsException` |

Repare nos pares: **dois 404 e dois 409**. Documentar só o status não resolve —
o front precisa do `code` para escolher a mensagem e a ação de recuperação.

!!! danger "O custo prático"
    Sem os codes no schema, o cliente gerado não tem enum de erro, o front
    escreve `if (res.status === 409)` sem saber que existem dois 409 com
    recuperações diferentes, e código de erro novo é descoberto em produção.

## Passo 1 — declare o `code` no corpo da classe

Antes de qualquer ferramenta, uma condição: o `code` precisa ser **legível sem
instanciar a exception**.

O SDK aceita passar `code=` no raise site, e isso funciona igual em runtime. Mas
esconde o valor real de qualquer leitor estático:

```python
# ⚠️ funciona, mas o `code` fica invisível para introspecção
class CategoryInUseException(ConflictException):
    """Categoria ainda referenciada por serviços."""


raise CategoryInUseException("...", code="CATEGORY_IN_USE")
```

```pycon
>>> CategoryInUseException.code            # atributo de classe
'CONFLICT'
>>> CategoryInUseException("x").code       # instância
'CATEGORY_IN_USE'
```

Ler o valor certo exigiria **instanciar**, e instanciar exige conhecer a
assinatura de cada `__init__` — que varia. Ou seja: nenhuma ferramenta consegue
montar o `responses` a partir das classes.

A forma por atributo de classe já funciona hoje e é introspectável:

```python
# src/core/exceptions.py
from typing import Any, ClassVar
from uuid import UUID

from tempest_fastapi_sdk import ConflictException


class CategoryInUseException(ConflictException):
    """Categoria ainda referenciada por serviços."""

    code: str = "CATEGORY_IN_USE"
    details_example: ClassVar[dict[str, Any]] = {
        "category_id": "8f2c1e40-0000-4000-8000-000000000000"
    }

    def __init__(self, category_id: UUID | str) -> None:
        """Inicializa a exception.

        Args:
            category_id (UUID | str): Categoria que não pode ser removida.
        """
        super().__init__(
            message="Não é possível remover uma categoria com serviços.",
            details={"category_id": str(category_id)},
        )
```

```pycon
>>> CategoryInUseException.code, CategoryInUseException.status_code
('CATEGORY_IN_USE', 409)
```

`code` (e `status_code`, quando o do pai está errado) no corpo da classe;
`__init__` só monta `message` e `details`.

!!! tip "`details_example` é só documentação"
    O `details_example` **nunca** é lido em runtime — ele só popula o exemplo do
    OpenAPI. Declare-o quando a exception anexa contexto que vale mostrar pra
    quem consome a API.

    Anote-o como `ClassVar[dict[str, Any]]`: além de ser o que a regra de
    tipagem total do projeto pede, é o que silencia o `RUF012` do ruff
    ("mutable default value for class attribute").

### O aviso que pega o defeito silencioso

Desde a **v0.160.0**, uma subclasse que não declara `code` próprio e por isso
herda um genérico do SDK avisa na criação da classe:

```pycon
>>> class CategoryInUseException(ConflictException):
...     """Categoria ainda referenciada por serviços."""
InheritedErrorCodeWarning: src.core.exceptions.CategoryInUseException declares
no `code`, so it inherits the generic ConflictException.code = 'CONFLICT'.
Clients cannot tell it apart from any other 409 response and
`error_responses()` cannot document it. Declare `code = "..."` in the class body.
```

Esse é um defeito real e silencioso: num serviço em produção uma subclasse ficou
**meses** emitindo `code: "CONFLICT"`, indistinguível de qualquer outro 409 para
o cliente.

!!! info "Quando o aviso **não** dispara"
    - A subclasse declara `code` — o caminho documentado.
    - A subclasse declara `message_key` — ela já localiza sob chave própria.
    - O `code` herdado é **de domínio** (declarado por um ancestral do próprio
      projeto). Especializar `DomainConflictException` é intencional, não
      defeito.

Se o padrão do raise site for deliberado no seu projeto, silencie por categoria:

```python
import warnings

from tempest_fastapi_sdk import InheritedErrorCodeWarning

warnings.filterwarnings("ignore", category=InheritedErrorCodeWarning)
```

## Passo 2 — `error_responses(*exceptions)`

Agora o núcleo. Passe as classes, receba o dict que o `responses=` do FastAPI
espera:

```python
# src/api/routers/jobs.py
from uuid import UUID

from fastapi import APIRouter
from tempest_fastapi_sdk import error_responses

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    CandidateDoesNotHaveCoinsException,
    CategoryNotFoundException,
    ServiceFullException,
    ServiceNotFoundException,
    ServiceOwnerCannotApplyException,
)
from src.schemas import CandidateResponseSchema

router: APIRouter = APIRouter(prefix="/api/jobs")


@router.post(
    "/{service_id}/candidates",
    status_code=201,
    responses=error_responses(
        ServiceNotFoundException,
        CategoryNotFoundException,
        ServiceOwnerCannotApplyException,
        ServiceFullException,
        CandidateAlreadyExistsException,
        CandidateDoesNotHaveCoinsException,
    ),
)
async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
    """Inscreve o usuário autenticado num serviço."""
    raise NotImplementedError
```

Pergunte de novo ao OpenAPI:

```pycon
>>> spec["paths"]["/api/jobs/{service_id}/candidates"]["post"]["responses"].keys()
dict_keys(['201', '400', '403', '404', '409', '422'])
```

Os quatro status apareceram. E os dois 404 continuam distinguíveis:

```pycon
>>> resp = spec["paths"]["/api/jobs/{service_id}/candidates"]["post"]["responses"]
>>> resp["404"]["description"]
'SERVICE_NOT_FOUND | CATEGORY_NOT_FOUND'
>>> list(resp["404"]["content"]["application/json"]["examples"])
['SERVICE_NOT_FOUND', 'CATEGORY_NOT_FOUND']
>>> resp["404"]["content"]["application/json"]["schema"]
{'$ref': '#/components/schemas/ErrorResponseSchema'}
```

### Por que `examples` e não uma entrada por exception

Restrição do próprio OpenAPI: **um único response object por status code**. Com
dois 404 no mesmo endpoint, não existe forma de emitir uma entrada por exception.
Então o helper **agrupa por status** e distingue os codes via `examples`:

```json
{
  "404": {
    "description": "SERVICE_NOT_FOUND | CATEGORY_NOT_FOUND",
    "content": {
      "application/json": {
        "schema": {"$ref": "#/components/schemas/ErrorResponseSchema"},
        "examples": {
          "SERVICE_NOT_FOUND": {
            "summary": "Serviço não existe.",
            "value": {"detail": "...", "code": "SERVICE_NOT_FOUND", "details": {}}
          },
          "CATEGORY_NOT_FOUND": {
            "summary": "Categoria não existe.",
            "value": {"detail": "...", "code": "CATEGORY_NOT_FOUND", "details": {}}
          }
        }
      }
    }
  }
}
```

Swagger UI e ReDoc renderizam esse mapa como um **seletor** — o front vê os codes
lado a lado com o payload de cada um. ✅

!!! check "Nenhum texto digitado duas vezes"
    O `summary` sai do `__doc__` da classe (que a convenção do projeto já exige),
    e o `detail` sai do `message` da classe — ou do `MessageCatalog`, se você
    passar um.

### `ErrorResponseSchema`

O `model` de toda entrada é
[`ErrorResponseSchema`](../reference.md), o envelope que os handlers do SDK
realmente emitem:

```python
from typing import Any

from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class ErrorResponseSchema(BaseSchema):
    """Corpo JSON que os handlers do SDK emitem em qualquer falha."""

    detail: str = Field(description="Mensagem legível. Localizada com catálogo.")
    code: str = Field(description="Identificador estável. Faça branch nisso.")
    details: dict[str, Any] = Field(default_factory=dict)
```

Antes ele não existia — quem quisesse declarar `responses={409: ...}` na mão não
tinha para onde apontar e precisava redigitar o shape inline em cada rota.

!!! warning "Faça branch no `code`, nunca no `detail`"
    O `detail` muda com o locale negociado quando há um `MessageCatalog`
    registrado. O `code` é o contrato estável.

### Localizando os exemplos

Por padrão `error_responses` **não** localiza — usa o `message` da classe, para o
spec não escolher um idioma implicitamente. Passe um catálogo quando quiser:

```python
from tempest_fastapi_sdk import default_message_catalog, error_responses

CATALOG = default_message_catalog().merge(
    {
        "pt-BR": {"SERVICE_NOT_FOUND": "Serviço não encontrado"},
        "en-US": {"SERVICE_NOT_FOUND": "Service not found"},
    }
)

responses = error_responses(
    ServiceNotFoundException,
    catalog=CATALOG,
    locale="pt-BR",
)
```

Um catálogo parcial degrada para o `message` da classe em vez de apagar o
exemplo — o mesmo fallback que o handler usa em runtime.

### Ajustando a descrição

```python
responses = error_responses(
    ServiceFullException,
    CandidateAlreadyExistsException,
    descriptions={409: "O usuário não pode se inscrever agora"},
)
```

Status não listados mantêm o resumo `"CODE_A | CODE_B"` gerado.

## Passo 3 — `@raises(...)` + `TempestAPIRouter`

Mesma informação, escrita ao lado do handler em vez de dentro da lista de
argumentos do decorator de rota:

```python
# src/api/routers/jobs.py
from uuid import UUID

from tempest_fastapi_sdk import TempestAPIRouter, raises

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    ServiceFullException,
    ServiceNotFoundException,
)
from src.schemas import CandidateResponseSchema

router: TempestAPIRouter = TempestAPIRouter(prefix="/api/jobs")


@router.post("/{service_id}/candidates", status_code=201)
@raises(
    ServiceNotFoundException,
    ServiceFullException,
    CandidateAlreadyExistsException,
)
async def apply_to_service(service_id: UUID) -> CandidateResponseSchema:
    """Inscreve o usuário autenticado num serviço."""
    raise NotImplementedError
```

`TempestAPIRouter` é um drop-in de `fastapi.APIRouter` — mesmos argumentos,
mesmos métodos — que expande a tag em `responses=` **antes** de construir a
rota. Por isso o modelo chega em `components.schemas` como `$ref` de verdade.

```python
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)   # o `responses` é preservado no include
```

!!! warning "Ordem dos decorators"
    `@raises` tem que ficar **abaixo** de `@router.post`, para rodar primeiro e o
    decorator de rota já receber a função marcada.

!!! danger "`@raises` num `APIRouter` comum é inerte"
    A tag só é lida pelo `TempestAPIRouter`. Num `fastapi.APIRouter` puro ela
    não faz nada — nesse caso use `responses=error_responses(...)`.

Um `responses=` explícito **ganha** por status code, então uma entrada escrita à
mão sempre sobrepõe a gerada:

```python
@router.get("/x", responses={409: {"description": "escrito à mão"}})
@raises(ServiceFullException, ServiceNotFoundException)
async def read() -> CandidateResponseSchema:
    """O 409 fica com a descrição manual; o 404 continua gerado."""
    raise NotImplementedError
```

!!! question "Por que explícito e não automático?"
    A lista fica versionada no diff, `mypy`/IDE pegam rename de classe, e nada
    depende de heurística em tempo de import. Custo: uma linha por exception.

## Passo 4 — `tempest openapi-errors --check`

O risco da declaração explícita é ficar desatualizada. Como a convenção do
projeto já exige seção `Raises:` em toda docstring, dá para conferir **sem magia
em runtime**:

```bash
tempest openapi-errors --check
```

O comando percorre `router → controller → service → repository` com `ast` (sem
importar a aplicação), coleta as exceptions de cada função — dos `raise` **e**
das seções `Raises:` — e compara com o que cada rota declarou:

```text
src/api/routers/jobs.py:15  POST /{service_id}/candidates
  undocumented: CandidateAlreadyExistsException, ServiceFullException
src/api/routers/jobs.py:25  GET /{service_id}
  unreachable:  ServiceFullException
2 route(s) with drift, 2 undocumented exception(s).
```

- **undocumented** — alcançável no fluxo, ausente da rota. É o buraco na
  documentação, o caso que essa receita resolve.
- **unreachable** — declarada na rota, nunca encontrada no fluxo. Lista inflada,
  documentando erro que não pode acontecer.

Sai zero quando está em sincronia, então serve de step de CI:

```yaml
# .github/workflows/ci.yml
- name: Erros documentados no OpenAPI
  run: uv run tempest openapi-errors --check
```

Opções:

| Opção | Efeito |
| --- | --- |
| `--path DIR` | Diretório (ou arquivo) a varrer. Repetível. Default: `./src` ou `./app`. |
| `--check` | Sai não-zero quando há drift. Sem ela o relatório é informativo. |
| `--allow-unreachable` | Com `--check`, só falha em `undocumented`. Lista inflada fica aviso. |
| `--fix` | Escreve as declarações faltantes no código. Exige árvore git limpa. |
| `--dry-run` | Com `--fix`, imprime o diff em vez de gravar. Roda em árvore suja. |

!!! warning "É um guia, não uma prova"
    Duas imprecisões conhecidas, ambas escolhidas para **super**estimar em vez
    de esconder buraco:

    - **Chamada com receptor não tipado resolve por nome.** `self.svc.get_by_id()`
      resolve pelo **tipo** de `self.svc` quando o atributo está anotado — e a
      busca fica restrita à hierarquia daquela classe. Sem anotação, cai em
      resolução por nome: dois `get_by_id` em classes diferentes viram um só nó
      e as exceptions se misturam. Isso infla o conjunto alcançável (pode
      limpar um `unreachable` de verdade) em vez de esconder um buraco. Tipar
      os atributos — que a convenção do projeto já exige — é o que dá precisão.
    - **Método herdado de fora da árvore varrida não é seguido**, com uma
      exceção importante: as classes que você **configura** no construtor da
      base. Um `not_found_exception=CoinPackNotFoundException` no
      `super().__init__()` do repository é atribuído aos métodos herdados que
      de fato o levantam (`get`, `get_by_id`, `resolve`, `delete`,
      `soft_delete`, `restore`), seguindo a cadeia
      controller → `service` → `repository`. O mesmo vale para os
      `*_conflict_exception`. Fora disso — um método herdado do SDK que não
      levanta classe configurada — nenhuma aresta é criada, então declare na
      seção `Raises:` o que a base levanta por você.
    - **Raise dinâmico é invisível.** `raise EXCEPTION_MAP[key]` não é
      resolvível estaticamente.

    !!! info "Corrigido em 0.170.0"
        Antes de 0.170.0 **toda** chamada resolvia por nome, e o decorator da
        rota entrava no grafo — então `@router.delete(...)` registrava uma
        chamada a `delete` e alcançava qualquer `delete` do projeto. Numa
        árvore onde o único `delete` era o de `CategoryRepository`, todas as
        rotas DELETE eram reportadas levantando `CategoryInUseException`.
        `get` e `post` colidem do mesmo jeito onde existam métodos com esses
        nomes.

    Os dois pontos cegos são cobertos declarando a exception na seção `Raises:`
    da função — que a convenção do projeto já exige, e que o analisador lê.

!!! tip "Aponte `--path` para a árvore inteira"
    A alcançabilidade é limitada ao que foi varrido. Varrer só o arquivo do
    router faz as chamadas para o service não resolverem, e **toda** declaração
    passa a parecer `unreachable`.

Análise de call graph fica fora do runtime de propósito: é frágil demais para
dirigir um response schema em produção, mas perfeitamente aceitável num check
que sai não-zero.

## Passo 5 — `--fix` escreve as declarações por você

Num projeto que já existe, o passo 4 costuma apontar dezenas de rotas. Repetir a
mão o que o analisador já sabe é trabalho mecânico — `--fix` faz o mapeamento
Exception → rota e grava o resultado:

```bash
tempest openapi-errors --fix --dry-run   # veja o diff primeiro
tempest openapi-errors --fix             # grave
```

Numa rota que ainda não declara nada, ele injeta o parâmetro e o import:

```diff
+from tempest_fastapi_sdk import error_responses
+
+from src.core.exceptions import CandidateAlreadyExistsException, ServiceFullException

-@router.post("/{service_id}/candidates", status_code=201)
+@router.post(
+    "/{service_id}/candidates",
+    status_code=201,
+    responses=error_responses(
+        CandidateAlreadyExistsException, ServiceFullException
+    ),
+)
 async def apply_to_service(service_id: str) -> CandidateResponseSchema:
```

Numa rota que já declara parte, ele **acrescenta** ao que existe — a ordem
original é preservada:

```diff
-    responses=error_responses(ServiceNotFoundException),
+    responses=error_responses(ServiceNotFoundException, ServiceFullException),
```

Um `@raises(...)` existente também é estendido no lugar. Já a rota que não
declara nada sempre recebe `error_responses`, nunca `@raises`: `@raises` só é
lido pelo `TempestAPIRouter`, então injetá-lo num projeto de `APIRouter` puro
produziria um decorator que não faz nada — o pior resultado possível para uma
ferramenta que existe para fechar um buraco de documentação. Um `@raises` já
presente prova que o projeto optou por esse estilo; aí ele é respeitado.

### As três garantias

!!! check "Só acrescenta, nunca remove"
    Findings `unreachable` são deliberadamente ignorados. Alcançabilidade
    resolve por nome de chamada e não enxerga raise dinâmico, então apagar uma
    declaração baseado nela removeria declaração correta. Curadoria de lista
    inflada continua manual.

!!! check "Edições ancoradas na AST"
    Cada inserção é posicionada no parêntese de fechamento de um nó de call, não
    numa regex. Nada depende de como o decorator está formatado, e o resto do
    arquivo — comentários, layout — não é tocado.

    A vírgula separadora é derivada do próprio código, via `tokenize`: um
    decorator quebrado em linhas carrega **trailing comma** (o formato que o
    `ruff format` produz), e prefixar outra vírgula ali geraria `,,` —
    `SyntaxError`. Tokenizar é o que torna a checagem confiável: uma `,` ou um
    `#` dentro de string (`description="a, b"`) é token próprio, então varrer
    texto cru de trás para frente erraria. Corrigido em 0.168.3.

    O resultado passa por
    `ruff check --select I --fix` e `ruff format`, então o import novo cai na
    posição ordenada e o decorator quebrado em linhas sai formatado.

    A formatação usa **a config do seu projeto**, não os defaults do ruff: o
    arquivo temporário é criado ao lado do arquivo reescrito, e o ruff resolve
    settings subindo a árvore de diretórios. Seu `line-length` e suas seções de
    `isort` valem, então o que o comando escreve passa no `ruff format --check`
    do seu próprio CI.

!!! note "Sem ruff, ele avisa em vez de fingir"
    A normalização depende de um ruff que realmente rode — no `PATH`, importável
    no interpretador atual (`python -m ruff`), ou via `uv run ruff`. Cada
    candidato é testado com `--version` antes de ser usado. Não achando nenhum, a
    gravação acontece de todo jeito (o splice já é Python válido) e o comando diz
    o que ficou de fora:

    ```text
    note: no working ruff found, so the new import stays where it was spliced
    and a long decorator is not wrapped. Run `tempest fix` afterwards to sort
    and format.
    ```

    Sem esse aviso você descobriria pelo `ruff check` do CI reclamando de um
    arquivo que o próprio comando acabou de escrever.

!!! check "Árvore git limpa obrigatória"
    Com a árvore limpa, `git diff` é a revisão e `git checkout` é o desfazer —
    a rede de segurança real de uma ferramenta que edita código que você
    escreveu. Com mudança pendente, o comando sai 1:

    ```text
    error: the working tree has uncommitted changes. Commit or stash them
    first — with a clean tree, `git diff` reviews what this wrote and
    `git checkout` undoes it.
    ```

    `--dry-run` é somente leitura, então roda em árvore suja sem reclamar.

!!! warning "Uma exception que ele não consegue importar não é escrita"
    O import é derivado do arquivo que define a classe. Quando o mesmo nome é
    definido em mais de um arquivo — ou fora da raiz varrida — a resolução é
    ambígua e escrever um import errado quebraria a aplicação. Nesse caso a
    rota é pulada e o nome sai listado como `unresolved`; declare essa à mão.

Rode `--check` depois: a segunda passada deve reportar sincronia.

## Recapitulando

1. **Declare `code` no corpo da classe.** É a única forma introspectável; o
   `InheritedErrorCodeWarning` avisa quando você esquece.
2. **`error_responses(*exceptions)`** monta o `responses=` agrupado por status,
   com os codes num mapa de `examples` e o corpo apontando para
   `ErrorResponseSchema`.
3. **`@raises(...)` + `TempestAPIRouter`** dizem a mesma coisa junto do handler,
   sem repetir o parâmetro.
4. **`tempest openapi-errors --check`** compara declaração e fluxo nas duas
   direções e serve de gate de CI.
5. **`tempest openapi-errors --fix`** grava o que falta — com `--dry-run` para
   ver o diff antes, e exigindo árvore git limpa para que `git checkout` seja o
   desfazer.

Com os passos 1 e 2 o `openapi.json` passa a ser a fonte única, e o cliente
gerado já vem com os codes. 🎉
