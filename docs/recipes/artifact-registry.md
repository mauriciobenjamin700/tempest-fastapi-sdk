# Registro de artefatos versionados

Alguns serviços servem **binários que trocam em runtime**: modelos ONNX, bundles de regras, arquivos de configuração compilados. O padrão é sempre o mesmo — um artefato lógico (`name`) tem **várias versões**, e exatamente **uma está "ativa"** por vez. Um operador sobe uma versão nova no object storage e a ativa pelo painel admin; os endpoints resolvem a versão ativa a cada request, sem redeploy.

O módulo `tempest_fastapi_sdk.artifacts` entrega o **núcleo genérico** desse padrão:

- `ArtifactVersionMixin` — as colunas `(name, version, file_key, is_current)`.
- `ArtifactRegistry` — resolver a versão atual, listar as atuais, ativar uma (uma current por `name`).
- `file_digest` / `object_digest` — sha256 + tamanho, **em streaming** e **memoizados** pela identidade imutável.
- `build_manifest_entries` + `ArtifactManifestEntry` — um manifesto agnóstico de serialização.
- `make_activate_artifact_action` — a ação admin que ativa a versão selecionada.

!!! info "O que o SDK NÃO decide por você"
    O SDK fica no genérico. O domínio (nomes `.onnx`, `input_size` por modelo, o formato exato do manifesto que o PWA consome, o esquema de URL de download) mora no **seu serviço** — o registry só te dá as peças.

## Instalação

O digest de objetos e servir bytes usa o cliente MinIO, que vem no extra `[minio]`:

```bash
uv add "tempest-fastapi-sdk[minio]"
```

O resto (mixin, registry, digest de arquivo, ação admin) roda só com o core.

## 1. A tabela

Misture `ArtifactVersionMixin` numa tabela `BaseModel` concreta. `is_current` é **distinto** de `is_active` (o flag de soft-delete do `BaseModel`): uma versão pode estar ativa (não deletada) e mesmo assim não ser a servida.

```python
from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.artifacts import ArtifactVersionMixin


class ModelVersion(BaseModel, ArtifactVersionMixin):
    """Uma versão de um modelo ONNX servido em runtime."""

    __tablename__ = "model_versions"
```

Pronto — você herda `id` / `is_active` / `created_at` / `updated_at` do `BaseModel` **e** `name` / `version` / `file_key` / `is_current` do mixin.

!!! tip "Checksums não são colunas"
    Não guarde `sha256`/`size` na tabela. Eles derivam do objeto **imutável** no storage e são calculados sob demanda (e memoizados pelo `file_key`). Guardar duplicaria a verdade e abriria espaço pra divergência.

## 2. O registry

`ArtifactRegistry` recebe um `BaseRepository` ligado à sua tabela e, opcionalmente, o cliente MinIO + bucket.

```python
from tempest_fastapi_sdk import AsyncMinIOClient, BaseRepository
from tempest_fastapi_sdk.artifacts import ArtifactRegistry


def build_registry(session, storage: AsyncMinIOClient) -> ArtifactRegistry[ModelVersion]:
    """Monta o registry para a tabela de versões de modelo."""
    repository: BaseRepository[ModelVersion] = BaseRepository(session, model=ModelVersion)
    return ArtifactRegistry(repository, minio=storage, bucket="models")
```

As três operações:

```python
# A versão servida de "detect" (ou None quando nenhuma foi ativada ainda).
current = await registry.current("detect")

# A versão atual de cada artefato (uma current por name).
rows = await registry.list_current()

# Ativa uma versão: liga is_current nela e desliga nos irmãos de mesmo name,
# tudo numa transação só.
activated = await registry.activate(version_id)
```

!!! note "Uma current por `name`, garantida na escrita"
    `activate` faz um `UPDATE ... SET is_current=False WHERE name=<name>` seguido de ligar o flag na linha alvo, no mesmo commit. Não há constraint de banco — a invariante é mantida pela ação, exatamente como no serviço de referência.

## 3. O digest (streaming + memoizado)

Os dois helpers devolvem `(sha256, size)` **sem carregar o arquivo inteiro na memória** (chunks de 1 MiB) e memoizam o resultado, porque a identidade é imutável:

```python
from tempest_fastapi_sdk.artifacts import file_digest, object_digest

# Arquivo em disco (o fallback empacotado), cacheado pelo caminho:
sha256, size = await file_digest("/opt/models/detect.onnx")

# Objeto no MinIO, cacheado por (bucket, key):
sha256, size = await object_digest(storage, "models", "detect/1.2.0.onnx")
```

## 4. O manifesto

`build_manifest_entries` percorre as versões atuais e pede o `(sha256, size)` de cada uma a um **`digest_source` que você fornece** — é aí que você decide de onde vêm os bytes (MinIO para versões ativas, disco para o fallback empacotado).

```python
from tempest_fastapi_sdk.artifacts import (
    ArtifactManifestEntry,
    build_manifest_entries,
    object_digest,
)


async def model_digest(row: ModelVersion) -> tuple[str, int]:
    """Digest da versão atual a partir do objeto no MinIO."""
    return await object_digest(storage, "models", row.file_key)


entries: list[ArtifactManifestEntry] = await build_manifest_entries(
    registry, digest_source=model_digest
)
```

Cada `ArtifactManifestEntry` tem `name`, `version`, `file_key`, `sha256`, `size`. O **envelope final** (URL de download, versão global do manifesto, campos de domínio como `input_size`) você monta por cima:

```python
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/models", tags=["models"])


class ModelManifest(BaseModel):
    """Formato de manifesto que o seu PWA consome."""

    models: list[dict]


@router.get("/manifest")
async def manifest() -> ModelManifest:
    """Manifesto que o cliente consulta pra detectar versões novas."""
    entries = await build_manifest_entries(registry, digest_source=model_digest)
    return ModelManifest(
        models=[
            {
                "name": e.name,
                "url": f"/models/{e.name}.onnx",
                "sha256": e.sha256,
                "size": e.size,
            }
            for e in entries
        ]
    )
```

## 5. A ação admin "Ativar versão"

`make_activate_artifact_action` devolve uma `AdminAction` cujo handler ativa a linha selecionada (limpando os irmãos de mesmo `name`). O handler carrega o marcador `@admin_action`, então você registra passando `action.handler`:

```python
from tempest_fastapi_sdk import AdminModel
from tempest_fastapi_sdk.artifacts import make_activate_artifact_action

activate = make_activate_artifact_action(label="Ativar versão")

site.register(AdminModel(model=ModelVersion, actions=[activate.handler]))
```

Selecione **uma** versão na lista, escolha "Ativar versão" — o painel liga `is_current` nela e desliga nas outras do mesmo modelo. Selecionar zero ou várias devolve um aviso.

!!! check "Por que passar `action.handler`"
    `AdminModel(actions=[...])` espera funções decoradas com `@admin_action` (ele lê o marcador `__admin_action__`). A fábrica devolve a `AdminAction`, e o `.handler` é a função decorada — então `action.handler` é exatamente o que o `AdminModel` sabe registrar.

## 6. Servir os bytes (com fallback)

Resolva a versão ativa; se existir, faça streaming do MinIO; senão, sirva o arquivo empacotado em disco.

```python
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse


@router.get("/{name}.onnx", response_model=None)
async def download(name: str) -> StreamingResponse | FileResponse:
    """Serve a versão ativa do MinIO, ou o arquivo empacotado."""
    active = await registry.current(name)
    if active is not None:
        return await storage.download_response(
            active.file_key,
            bucket="models",
            media_type="application/octet-stream",
            filename=f"{name}.onnx",
        )
    return FileResponse(f"/opt/models/{name}.onnx", filename=f"{name}.onnx")
```

## Recap

- **Misture** `ArtifactVersionMixin` numa tabela `BaseModel` → colunas `name` / `version` / `file_key` / `is_current`.
- **`ArtifactRegistry`** dá `current` / `list_current` / `activate` (uma current por `name`, numa transação).
- **`file_digest` / `object_digest`** streamam o sha256 + tamanho e memoizam pela identidade imutável.
- **`build_manifest_entries`** monta entradas agnósticas; você fornece o `digest_source` e o envelope final.
- **`make_activate_artifact_action`** é a ação admin de ativação — registre `action.handler`.

O padrão genérico vem do SDK; os detalhes de domínio (nomes de arquivo, `input_size`, o shape do manifesto do PWA) ficam no seu serviço. 🚀
