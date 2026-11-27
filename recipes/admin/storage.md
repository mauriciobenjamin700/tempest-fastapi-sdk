# Navegador de storage — ver, pré-visualizar e editar arquivos

Use para abrir o bucket na tela do admin: listar, pré-visualizar, subir, editar texto, renomear e apagar, com o `StorageBrowserService`.

Parte da receita [Painel admin](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin.md).

O admin já grava em object storage (`upload_fields=`), mas o operador não
tinha tela para ver o que o bucket guarda, abrir um arquivo ou apagar um
upload perdido. `StorageBrowserService` é essa tela: lista as pastas,
pré-visualiza o arquivo e faz upload, troca de conteúdo, edição de texto no
lugar, renomeação e exclusão.

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import StorageBrowserService
from tempest_fastapi_sdk.storage import LocalObjectStorageClient

from src.core.settings import settings
from src.db.models import UserModel

app: FastAPI = FastAPI()
db: AsyncDatabaseManager = AsyncDatabaseManager(settings.DATABASE_URL)
store: LocalObjectStorageClient = LocalObjectStorageClient(
    "/var/lib/app/storage",
    public_url="https://api.example.com",
    signing_secret=settings.JWT_SECRET,
)

app.include_router(
    make_admin_router(
        AdminSite(title="Ops"),
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET_KEY,
        storage=StorageBrowserService(store, label="uploads"),
    )
)
```

Com MinIO/S3 é a mesma linha: `StorageBrowserService(AsyncMinIOClient(...))`.
O serviço usa só cinco chamadas — `list_objects`, `stat_object`,
`stream_object`, `put_object` e `remove_object` (o protocolo
`AdminStorageClient`) —, que os dois clientes do SDK implementam.

Isso monta as rotas abaixo e um item "Storage" na barra lateral:

| Rota | O que faz |
| --- | --- |
| `GET /admin/storage?prefix=` | uma pasta: subpastas primeiro, depois arquivos com tamanho, tipo e data; paginada |
| `GET /admin/storage/object?key=` | um arquivo: metadados, pré-visualização, editor de texto e os forms de escrita |
| `GET /admin/storage/raw?key=` | os bytes, para a pré-visualização (`&download=true` força download) |
| `POST /admin/storage/upload` | sobe um arquivo na pasta atual |
| `POST /admin/storage/object/replace` | troca o conteúdo, mantendo a chave e os metadados |
| `POST /admin/storage/object/edit` | salva o editor de texto |
| `POST /admin/storage/object/rename` | move para outra chave, pasta incluída |
| `POST /admin/storage/object/delete` | apaga |

Vamos por partes.

**Pastas não existem.** Em object storage, pasta é só o prefixo da chave até
a `/`. Por isso a pasta aparece assim que um arquivo é gravado debaixo dela e
some quando o último sai. No upload, o campo "Save as" aceita `a/b.txt` e cria
as pastas no caminho.

**A pré-visualização depende do tipo.** Imagem, vídeo, áudio e PDF são
renderizados pelo browser a partir da rota `raw`. Texto (`text/*`, JSON, YAML,
XML, CSV) aparece no próprio HTML, escapado, num `<textarea>` editável até
`max_text_bytes` (256 KiB por padrão). O resto oferece só o download.

!!! danger "SVG e HTML nunca são renderizados"
    A rota `raw` responde na origem do admin. Um HTML renderizado ali roda
    script com a sessão de quem está logado, e SVG carrega `<script>` do mesmo
    jeito. Por isso a rota só concede `inline` aos tipos de
    `INLINE_SAFE_MEDIA_TYPES` — SVG e HTML sempre saem como `attachment` — e
    toda resposta leva `X-Content-Type-Options: nosniff` e uma CSP com
    `sandbox`. Na página, SVG e HTML aparecem como código-fonte escapado.

**A edição de texto recusa escrita concorrente.** O editor envia de volta o
ETag com que abriu; se o arquivo mudou no meio, o save é recusado com
"changed since you opened it" em vez de apagar a escrita do outro. E o
`\r\n` que o browser manda vira `\n` quando o original não tinha `\r\n`,
para salvar um arquivo Unix sem mudança não reescrever toda linha.

**Renomear custa um download e um upload.** Object storage não tem rename:
o serviço copia bytes, tipo e metadados para a chave nova e apaga a antiga.
Chave nova já ocupada é recusada.

!!! tip "Somente leitura"
    `StorageBrowserService(store, read_only=True)` mantém listagem,
    pré-visualização e download, esconde os forms e responde `403` a toda
    escrita. A permissão de abrir a tela é da `surface_policy`, com
    `AdminSurfaceEnum.STORAGE` — veja [Console SQL, tasks e
    logs](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/permissions.md#console-sql-tasks-e-logs-surface_policy).

!!! info "Limites"
    `max_upload_bytes` (25 MiB) vale para upload e troca de conteúdo,
    `max_text_bytes` (256 KiB) para pré-visualização e edição de texto, e
    `page_size` (100) para a listagem. Chave com segmento vazio, `.` ou `..`,
    barra invertida, caractere de controle ou mais de 1024 bytes é recusada
    antes de chegar no store. Recusas voltam como mensagem vermelha na
    própria tela.
