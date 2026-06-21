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

- Misture `StoredFileServiceMixin[Model]` no serviço e exponha `upload_utils`
  + `storage`.
- `set_file(ref, file, *, field, subdir=...)` → sobe, troca a antiga,
  persiste. Detach-safe.
- `file_url(key, *, expires=...)` → URL presigned ou `None`.
- `clear_file(ref, *, field)` → apaga + zera (no-op se já vazio).
- Caso comum (uma chave + presigned). Pra resize/variantes/galeria, use o
  [`UploadUtils`](uploads.md) direto.
