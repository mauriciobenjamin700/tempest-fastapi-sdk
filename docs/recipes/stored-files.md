# Arquivo no serviço — `StoredFileServiceMixin`

Uma entidade quase sempre carrega **uma** chave de storage: o avatar do
usuário, o banner de um evento, a capa de um produto, um anexo. E todo
serviço que cuida disso reescreve a mesma dança na mão:

1. resolver a entidade,
2. subir o arquivo novo e apagar o antigo,
3. gravar a chave nova no model,
4. dar `commit`,
5. devolver uma URL temporária de download.

O `StoredFileServiceMixin` faz esse fluxo **uma vez**, parametrizado pelo
**nome do campo** — então um mesmo serviço cuida de vários campos de arquivo
sem repetição. Ele monta por cima do [`UploadUtils`](uploads.md) (upload +
validação) e do [`AsyncMinIOClient`](storage.md) (presigned URL); requer os
extras `[upload]` e `[minio]`.

!!! info "Escopo: o caso comum"
    Cobre **uma chave por campo → URL presigned**. Thumbnails, variantes
    (S/M/L), bucket público/CDN e galerias (um-para-muitos) ficam de fora —
    pra esses, componha o `UploadUtils` direto.

## Misturando no seu serviço

O mixin não constrói nada: ele lê dois colaboradores de `self` —
`upload_utils` e `storage`. Quem mistura continua dono da configuração
(tamanho, tipos aceitos, bucket):

```python
from tempest_fastapi_sdk import (
    AsyncMinIOClient,
    BaseService,
    StoredFileServiceMixin,
    UploadUtils,
)

from src.db.models import UserModel
from src.db.repositories import UserRepository
from src.schemas import UserResponseSchema


class UserService(
    BaseService[UserRepository, UserResponseSchema],
    StoredFileServiceMixin[UserModel],
):
    def __init__(
        self,
        repository: UserRepository,
        storage: AsyncMinIOClient,
        upload_utils: UploadUtils,
    ) -> None:
        super().__init__(repository)
        self.storage = storage
        self.upload_utils = upload_utils
```

A ordem das bases importa: `BaseService` traz o `repository`; o mixin só
adiciona os métodos de arquivo por cima.

### Como a herança funciona

O serviço herda de **duas** bases genéricas ao mesmo tempo — é composição por
herança múltipla, e cada peça tem um papel:

```python
class UserService(
    BaseService[UserRepository, UserResponseSchema],  # (1) estado + CRUD
    StoredFileServiceMixin[UserModel],                # (2) métodos de arquivo
):
    ...
```

1. **`BaseService[Repo, Response]`** vem **primeiro** na MRO (method resolution
   order). É ela que define o `__init__(repository)` e guarda o `self.repository`
   — por isso o seu `super().__init__(repository)` cai nela. Os dois parâmetros
   genéricos amarram o **tipo do repositório** e o **schema de resposta**.
2. **`StoredFileServiceMixin[Model]`** vem **depois**. Ela **não tem `__init__`
   nem estado próprio** — só empilha `set_file` / `file_url` / `file_urls` /
   `clear_file` por cima. O único genérico (`Model`) mantém o retorno de
   `set_file`/`clear_file` preciso (`UserModel`, não um `Any` qualquer).

!!! info "Por que o mixin não constrói nada"
    Um mixin que criasse o `storage`/`upload_utils` roubaria do serviço a
    configuração (bucket, tamanho máximo, tipos aceitos). Em vez disso ele
    **lê os colaboradores de `self`** via *structural typing* (Protocols
    `SupportsUpload` e `SupportsPresign`): qualquer objeto com os métodos certos
    serve. Consequência prática: **importar o mixin não puxa os extras**
    `[upload]`/`[minio]` — eles só entram quando você de fato instancia um
    `UploadUtils` / `AsyncMinIOClient`.

!!! note "`repository: Any` no mixin — e o mypy"
    O mixin declara `repository: Any` só como anotação. Sem isso, o mypy
    reclamaria de **campo `repository` conflitante** entre as duas bases (a
    `BaseService` tipa ele como `RepositoryT`). Com `Any` no mixin, a base
    concreta vence e os métodos públicos continuam precisos via `Model` — nada
    de `# type: ignore` no seu serviço.

## Trocar o arquivo — `set_file`

```python
async def update_profile_picture(
    self, user: UUID | UserModel, image: UploadFile
) -> UserResponseSchema:
    """Sobe a foto nova, apaga a antiga e devolve o perfil com a URL."""
    updated = await self.set_file(
        user, image, field="profile_picture", subdir="profiles"
    )
    response = await self._map_to_response(updated)
    response.profile_picture_url = await self.file_url(updated.profile_picture)
    return response
```

Foi isso. Comparado às ~13 linhas manuais, o `set_file` resolve a entidade,
chama `replace` (grava a nova **antes** de apagar a antiga), grava a chave e
dá `commit` — tudo num passo.

!!! tip "Seguro com o usuário autenticado"
    O `set_file` re-resolve a entidade na sessão do request via
    `repository.resolve()`. Se você passar o `UserModel` que veio de
    `get_current_user` (que em apps mal-fiados vinha *detached*), o `resolve`
    o reanexa antes da escrita — sem o
    `InvalidRequestError: Instance is not persistent within this Session`.

## Servir a URL — `file_url`

```python
url = await self.file_url(user.profile_picture)            # 1h de validade
url = await self.file_url(user.profile_picture, expires=timedelta(minutes=5))
```

Devolve `None` quando a chave é vazia, então você pode jogar o resultado
direto num campo do schema de resposta sem `if`:

```python
response.profile_picture_url = await self.file_url(updated.profile_picture)
```

## Uma página inteira — `file_urls` *(v0.133.0+)*

Endpoint de **listagem** precisa resolver **uma chave por linha** — uma página
de candidatos, cada um com sua foto. Fazer isso num `for` com
`await self.file_url(...)` **serializa** os hops de thread (cada presign do
`minio` roda em `asyncio.to_thread`). O `file_urls` é o par em lote do
`file_url`: dispara o fan-out de uma vez e devolve um `dict` indexado pela
chave.

```python
async def _load_profile_picture_from_users(
    self, candidates: list[CandidateResponseSchema]
) -> None:
    """Preenche ``profile_picture_url`` de cada candidato numa tacada só."""
    users = [c.user for c in candidates if c.user is not None]
    urls = await self.file_urls([user.profile_picture for user in users])
    for user in users:
        user.profile_picture_url = urls.get(user.profile_picture)
```

Chaves `None`/vazias são **descartadas** e duplicatas **deduplicadas**, então o
`dict` tem uma entrada por chave não-vazia distinta. Faça o lookup com
`urls.get(row.key)` — uma linha cuja chave era vazia devolve `None`, sem `if`.

!!! info "Teto de concorrência (`max_concurrency`, padrão 16)"
    Cada presign vai pra um thread do executor default. O `file_urls` limita
    quantos rodam ao mesmo tempo com um `asyncio.Semaphore`, preservando a
    ordem — uma página grande não satura o pool. Ajuste via
    `file_urls(keys, max_concurrency=32)`.

!!! note "Fail-fast"
    Se um presign falha, o lote inteiro aborta e propaga (`asyncio.gather`
    padrão) — mesmo comportamento de assinar uma a uma.

## Remover o arquivo — `clear_file`

```python
updated = await self.clear_file(user, field="profile_picture")
```

Apaga o objeto do storage e zera o campo. Quando o campo já está vazio, é
um **no-op**: a entidade volta sem `commit` e sem chamar o storage.

## Vários campos? Mesmo mixin

`field=` é só um argumento — o mesmo serviço cuida de quantos campos quiser:

```python
await self.set_file(event, cover, field="cover_image", subdir="events/covers")
await self.set_file(event, banner, field="banner_image", subdir="events/banners")
```

## Recapitulando

- Misture `StoredFileServiceMixin[Model]` no serviço (**depois** de
  `BaseService[Repo, Response]` na MRO) e exponha `upload_utils` + `storage`.
  O mixin não tem estado próprio: lê os colaboradores de `self` via Protocol,
  então importá-lo não puxa os extras `[upload]`/`[minio]`.
- `set_file(ref, file, *, field, subdir=...)` → sobe, troca a antiga,
  persiste. Detach-safe.
- `file_url(key, *, expires=...)` → URL presigned ou `None`.
- `file_urls(keys, *, expires=..., max_concurrency=16)` → `dict` chave→URL para
  uma página inteira; descarta chaves vazias, dedup, fail-fast.
- `clear_file(ref, *, field)` → apaga + zera (no-op se já vazio).
- Caso comum (uma chave + presigned). Pra resize/variantes/galeria, use o
  [`UploadUtils`](uploads.md) direto.
