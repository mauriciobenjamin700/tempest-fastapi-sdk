---
description: "Use para gravar uploads numa pasta ou Shared Drive do Google Drive via service account com `GoogleDriveUploadStorage`, do Cloud Console aos erros."
---

# Google Drive (uploads)

Seu serviço recebe arquivos — contratos, comprovantes, fotos — e a empresa
quer vê-los numa pasta do Google Drive, não num bucket S3. Esta receita liga
o `UploadUtils` a uma pasta (ou a um Shared Drive) da empresa por meio de uma
**service account**: sem login de usuário, sem tela de consentimento.

O backend é o `GoogleDriveUploadStorage`. Ele implementa o mesmo protocolo
`UploadStorage` do disco local e do MinIO, então a sua rota de upload não
muda nada: só o objeto passado em `backend=`.

```bash
uv add "tempest-fastapi-sdk[upload,google-drive]>=0.373.0"
```

O extra `[google-drive]` traz `pyjwt` e `cryptography` (o `httpx` já vem no
pacote base). Não há `google-api-python-client` nem `google-auth`: a troca de
token de uma service account é um JWT assinado e um POST, e o SDK faz isso
sozinho.

## Passo 1 — Consiga a service account e a chave JSON

Você vai sair deste passo com um arquivo JSON — a credencial da service
account. Dá para fazer pelo console (clicando) ou pelo `gcloud` (copiando
comandos); escolha um.

!!! tip "Os nomes de menu mudam"
    O console do Google Cloud renomeia e reorganiza menus de tempos em
    tempos. Se algum nome abaixo não bater com a tela, siga os guias
    oficiais: [Criar contas de serviço](https://cloud.google.com/iam/docs/service-accounts-create)
    e [Criar e excluir chaves de conta de serviço](https://cloud.google.com/iam/docs/keys-create-delete).

### 1.1 — Um projeto no Google Cloud

A service account mora dentro de um projeto do Google Cloud. Entre no
[console](https://console.cloud.google.com/) com a conta Google da empresa e,
no seletor de projetos no topo da página, escolha um projeto existente ou
clique em **Novo projeto**. Anote o **ID do projeto** (ex.:
`minha-empresa-uploads`).

### 1.2 — Habilite a Google Drive API

Sem isso, toda chamada à API do Drive é recusada. Em **APIs e serviços →
Biblioteca**, procure **Google Drive API** e clique em **Ativar**.

### 1.3 — Crie a service account

Em **IAM e administrador → Contas de serviço**, clique em **Criar conta de
serviço**:

1. Dê um nome (ex.: `uploader`). O console sugere o **ID** e mostra o e-mail
   que a conta vai ter: `uploader@minha-empresa-uploads.iam.gserviceaccount.com`.
2. Na etapa de papéis (*roles*), **não conceda nenhum** e conclua. A conta
   não precisa de papel IAM no projeto para usar o Drive: o acesso aos
   arquivos vem do **compartilhamento** da pasta (Passo 2), não do IAM.

### 1.4 — Gere a chave JSON

Abra a conta criada, vá na aba **Chaves → Adicionar chave → Criar nova
chave**, escolha **JSON** e confirme. O navegador baixa um arquivo assim
(valores encurtados):

```json
{
  "type": "service_account",
  "project_id": "minha-empresa-uploads",
  "private_key_id": "3f2a…",
  "private_key": "-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----\n",
  "client_email": "uploader@minha-empresa-uploads.iam.gserviceaccount.com",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

Os dois campos que importam para a receita são o `client_email` (você o usa
no Passo 2) e o arquivo inteiro (vai nas settings no Passo 3).

!!! warning "Criar chave bloqueado pela organização"
    Se o botão de criar chave falhar com uma mensagem de política da
    organização, a sua organização no Google Cloud bloqueia chaves de
    service account (a restrição `iam.disableServiceAccountKeyCreation`).
    Quem administra a organização pode abrir uma exceção para este projeto;
    não há como contornar do lado do SDK, que autentica justamente com a
    chave.

=== "Pelo gcloud"

    O mesmo passo inteiro, para quem tem o [gcloud CLI](https://cloud.google.com/sdk/docs/install)
    instalado e autenticado (`gcloud auth login`):

    ```bash
    gcloud projects create minha-empresa-uploads
    gcloud config set project minha-empresa-uploads
    gcloud services enable drive.googleapis.com
    gcloud iam service-accounts create uploader --display-name="Uploads do serviço"
    gcloud iam service-accounts keys create drive-sa.json \
        --iam-account=uploader@minha-empresa-uploads.iam.gserviceaccount.com
    ```

    O último comando grava a chave em `drive-sa.json`, no diretório atual.

!!! warning "Esse arquivo é uma senha"
    Quem tem o JSON age como a conta de serviço. Guarde-o no gerenciador de
    segredos do seu deploy, nunca no repositório — se ele estiver na pasta do
    projeto durante o desenvolvimento, ponha o nome no `.gitignore`. Se vazar,
    apague a chave na aba **Chaves** da conta e gere outra. O SDK nunca
    escreve a chave, o JWT assinado nem o token de acesso em log ou em
    mensagem de erro, e o `repr` das credenciais esconde a chave privada.

## Passo 2 — Compartilhe a pasta com o `client_email`

A service account é uma conta como outra qualquer no Drive: só enxerga o que
for compartilhado com ela. Pegue o `client_email` do JSON e escolha onde os
arquivos vão morar.

### Shared Drive (recomendado)

Shared Drive (drive compartilhado) existe em contas **Google Workspace**; uma
conta Gmail pessoal não tem. No [Google Drive](https://drive.google.com/):

1. Em **Drives compartilhados**, crie um (ou abra um existente).
2. Clique no nome do drive → **Gerenciar membros**, cole o `client_email` e
   dê o papel de **Gerente de conteúdo** (ou superior — o `delete` do backend
   apaga arquivos, e papéis abaixo desse não apagam).
3. Opcional: crie uma pasta dentro do drive para os uploads.

### Pasta comum (Meu Drive)

Clique com o botão direito na pasta → **Compartilhar**, cole o `client_email`
e dê o papel de **Editor**. Leia o aviso "Por que Shared Drive" abaixo antes
de escolher este caminho.

### Os IDs que vão para as settings

Abra a pasta no navegador. O **id da pasta** é o último segmento da URL:
`https://drive.google.com/drive/folders/`**`1AbCdEfGhIjKlMnOp`** — esse é o
`GOOGLE_DRIVE_FOLDER_ID`.

Se a pasta está num Shared Drive, abra a **raiz** do drive: o último
segmento da URL dele é o id do drive (`GOOGLE_DRIVE_DRIVE_ID`, opcional — ele
restringe as buscas a esse drive). Se você grava direto na raiz do Shared
Drive, o mesmo valor serve de id da pasta.

!!! info "Por que Shared Drive"
    Arquivo enviado pela service account para uma pasta de **Meu Drive** de
    uma pessoa pertence à service account, e a documentação e os fóruns do
    Google descrevem que service accounts não têm cota de armazenamento
    própria — o upload é recusado com `storageQuotaExceeded`. Num Shared
    Drive o arquivo pertence ao drive da empresa. Isto **não foi medido**
    neste repositório (não há credencial real aqui); leia
    [Shared drives overview](https://developers.google.com/workspace/drive/api/guides/about-shareddrives)
    antes de decidir. Toda chamada do SDK à API do Drive envia `supportsAllDrives=true`,
    então as duas formas funcionam do lado do cliente.

### Confira antes de ligar o serviço

Um script de um minuto prova que a chave e o compartilhamento estão certos:
ele grava um arquivo de teste na pasta e o apaga.

```python title="check_drive.py"
import asyncio
from collections.abc import AsyncIterator

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.utils import UploadResult

PROBE_KEY: str = "tempest-credentials-check.txt"


async def probe_bytes() -> AsyncIterator[bytes]:
    """Yield the tiny payload of the check file."""
    yield b"tempest credentials check"


async def main() -> None:
    """Upload and delete a probe file to prove the key and the sharing."""
    storage: GoogleDriveUploadStorage = GoogleDriveUploadStorage.from_service_account(
        folder_id="1AbCdEfGhIjKlMnOp",
        credentials_path="drive-sa.json",
    )
    try:
        result: UploadResult = await storage.write_stream(
            PROBE_KEY, probe_bytes(), content_type="text/plain"
        )
        print("upload ok:", result.url)
        print("apagado:", await storage.delete(PROBE_KEY))
    finally:
        await storage.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

Se der certo, ele imprime o link do arquivo e `apagado: True`. Se der errado,
a exceção diz o que falta:

| Exceção | O que significa | O que fazer |
| --- | --- | --- |
| `GoogleDriveException` com `reason` `invalid_private_key` | o `private_key` do JSON não é uma chave RSA PEM válida (arquivo editado, truncado ou colado com as quebras de linha erradas) | baixe a chave de novo (Passo 1.4) |
| `GoogleDriveFolderNotFoundException` | o Drive respondeu `404` para a pasta: id errado ou pasta não compartilhada com o `client_email` (a mensagem cita o e-mail) | confira o id na URL e o compartilhamento (Passo 2) |
| `GoogleDriveException` com outro `reason` | o Drive recusou a chamada por outro motivo (API não habilitada, papel sem permissão, cota) — o `details` traz o `status` e o `reason` que o Google mandou | leia o `reason` e volte ao passo correspondente |

As exceções e o mapeamento são do SDK e estão cobertos pela suíte; **qual**
status o Google devolve em cada caso (por exemplo, `404` para pasta não
compartilhada) é o que a [documentação de erros](https://developers.google.com/workspace/drive/api/guides/handle-errors)
descreve, e não foi medido contra o Google neste repositório.

## Passo 3 — Configure as settings

Componha o mixin `GoogleDriveSettings`:

| Variável | O que é |
| --- | --- |
| `GOOGLE_DRIVE_CREDENTIALS_PATH` | caminho do JSON da service account |
| `GOOGLE_DRIVE_CREDENTIALS_JSON` | o JSON inteiro, inline (vence o caminho quando os dois existem) |
| `GOOGLE_DRIVE_FOLDER_ID` | a pasta onde os arquivos são gravados |
| `GOOGLE_DRIVE_DRIVE_ID` | opcional: o Shared Drive que contém a pasta — as buscas ficam restritas a ele |

```bash
GOOGLE_DRIVE_CREDENTIALS_PATH=/run/secrets/drive-sa.json
GOOGLE_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOp
```

`settings.google_drive_kwargs()` devolve um `GoogleDriveStorageKwargs`
(`TypedDict`) pronto para o splat em
`GoogleDriveUploadStorage.from_service_account(...)` — o type-checker confere
o splat contra a assinatura.

## Passo 4 — Ligue no `UploadUtils`

Um serviço completo:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile

from tempest_fastapi_sdk import UploadUtils
from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveUploadStorage,
)
from tempest_fastapi_sdk.settings import GoogleDriveSettings


class Settings(GoogleDriveSettings):
    """Settings do serviço: só o Google Drive, neste exemplo."""


settings: Settings = Settings()
storage: GoogleDriveUploadStorage = GoogleDriveUploadStorage.from_service_account(
    **settings.google_drive_kwargs()
)
uploads: UploadUtils = UploadUtils(
    backend=storage,
    max_size_bytes=20 * 1024 * 1024,
    allowed_extensions={"pdf", "png", "jpg"},
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Fecha o cliente HTTP que o backend criou."""
    yield
    await storage.aclose()


app: FastAPI = FastAPI(lifespan=lifespan)


@app.post("/api/contracts")
async def upload_contract(file: UploadFile) -> dict[str, str]:
    """Valida e envia o contrato para a pasta do Drive."""
    key = await uploads.save(file, subdir="contracts")
    return {"key": str(key)}


@app.delete("/api/contracts/{name}")
async def delete_contract(name: str) -> dict[str, bool]:
    """Apaga o contrato do Drive (sem passar pela lixeira)."""
    return {"deleted": await uploads.delete(f"contracts/{name}")}
```

Pedaço por pedaço:

- **`from_service_account(...)`** lê o JSON, cria um `httpx.AsyncClient`
  próprio e monta o `GoogleDriveClient` com o
  `ServiceAccountTokenProvider`. Por ter criado o cliente, o backend o fecha
  no `aclose()` — daí o `lifespan`. Passe `http_client=` para reusar um
  cliente seu (aí quem fecha é você).
- **`UploadUtils(backend=storage, ...)`** continua validando extensão, MIME,
  tamanho e magic bytes antes de qualquer byte sair do serviço.
- **`uploads.save(...)`** devolve a *key* (`contracts/<uuid>.pdf`). É ela que
  você guarda no banco, igual ao disco local e ao MinIO.

## Como a key vira arquivo

O Drive identifica arquivo por **id**, não por caminho, e aceita dois
arquivos com o mesmo nome na mesma pasta. Então o backend:

- grava a key em `appProperties` (propriedades privadas do app), sob
  `tempest_key`;
- usa o último segmento da key como `name`, só para quem navega na pasta;
- acha o arquivo com a busca
  `'<pasta>' in parents and appProperties has { key='tempest_key' and value='<key>' } and trashed = false`,
  com aspas e barra invertida da key **escapadas** para a sintaxe de busca do
  Drive.

`exists(key)` responde se a busca acha algo; `delete(key)` apaga de vez
(`files.delete`, sem lixeira) todo arquivo que casar e devolve `False` quando
nada casou.

!!! note "Limite da key"
    A [documentação de propriedades](https://developers.google.com/workspace/drive/api/guides/properties)
    limita cada propriedade a 124 bytes (nome + valor, em UTF-8). Com
    `tempest_key` sobram 113 bytes para a key; mais que isso é recusado
    com `ValidationException` (`422`, `code="VALIDATION_ERROR"`) **antes**
    de qualquer requisição — a mensagem diz quantos bytes a propriedade
    ocupa e cita o limite de 124, e `details` traz `property`, `bytes` e
    `limit_bytes`. O mesmo vale para cada par de `metadata=`. A key
    gerada pelo `UploadUtils` (uuid + extensão) cabe com folga; cuidado com
    `keep_original_name=True` e nomes longos.

### Mesma key duas vezes: o conteúdo é substituído

Gravar uma key que já existe **substitui o conteúdo do mesmo arquivo**: mesmo
id, mesmo link, mídia nova (`files.update` resumível). Nenhum arquivo novo é
criado, e `exists` continua achando um só — o mesmo comportamento do disco
local e do MinIO ao sobrescrever.

!!! warning "Duas escritas simultâneas de uma key nova"
    Até onde a documentação do Drive mostra, não há criação condicional
    (não medido). Se duas requisições gravarem a
    **mesma key nova** ao mesmo tempo, as duas podem criar arquivo. Nesse
    caso `exists` responde `True`, a próxima escrita substitui o primeiro e
    `delete` apaga todos. A key padrão do `UploadUtils` (uuid) não colide.

## `presigned_url` levanta erro

O Drive **não tem URL pré-assinada**: ele entrega o arquivo a quem tem
permissão no Drive, não por um link temporário assinado. Por isso
`storage.presigned_url(key)` levanta `PresignedUrlUnsupportedException`
(`501`, `code="PRESIGNED_URL_UNSUPPORTED"`) em vez de devolver um link
permanente que alguém trataria como temporário.

Para servir o arquivo ao seu usuário, faça uma rota autenticada sua. Quando o
que você quer é o link do Drive (num painel interno, por exemplo), use
`await storage.web_view_link(key)`; o `UploadResult.url` de cada escrita
também carrega esse `webViewLink`. Ele é **permanente** e só abre para quem
tem acesso ao arquivo no Drive.

### Download no painel admin

O painel admin (`make_admin_router`) com `upload_fields` usa o mesmo backend.
A rota de download de um campo de upload pede `presigned_url` e, quando o
backend não tem URL pré-assinada — devolveu `None` **ou** levantou
`PresignedUrlUnsupportedException` —, procura um link permanente: se o
backend implementa o protocolo `SupportsWebViewLink`
(`async def web_view_link(self, key: str, /) -> str | None`), a rota
responde `303` para esse link. O `GoogleDriveUploadStorage` implementa, então
o download no admin **redireciona para o link do Drive**, que só abre para
quem tem acesso ao arquivo lá. Sem link (arquivo apagado no Drive, ou um
backend seu sem `web_view_link`), a rota cai no caminho de sempre: arquivo
local, ou `404` "file not available".

Um backend seu que também queira isso só precisa do método:

```python
from tempest_fastapi_sdk.utils import SupportsWebViewLink


class MyStorage:
    """Exemplo: um backend com link permanente por key."""

    async def web_view_link(self, key: str) -> str | None:
        """Devolve o link permanente do arquivo, ou None."""
        return f"https://files.example.com/{key}"


assert isinstance(MyStorage(), SupportsWebViewLink)
```

## Upload resumível, em pedaços

O backend usa o [upload resumível](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable)
do Drive v3:

- O stream sobe em pedaços múltiplos de 256 KiB (`chunk_size=`, padrão 8 MiB)
  — a memória fica em pouco mais de um pedaço (um pedaço mais o chunk que o
  backend lê à frente), não no arquivo.
- Cada pedaço que não é o último vai com `Content-Range: bytes a-b/*`; o
  Drive responde `308` com o header `Range` dizendo quanto guardou, e o
  próximo pedaço começa logo depois. Bytes que o Drive não guardou são
  reenviados; o resto, não.
- O backend lê **um pedaço à frente**: um pedaço só sai com `/*` quando já se
  sabe que vêm mais bytes depois dele. Por isso o último `PUT` sempre leva
  bytes **e** o total (`bytes a-b/<total>`), inclusive quando o tamanho é
  múltiplo exato do pedaço (256 KiB, 512 KiB, 8 MiB…). Um `PUT` vazio
  `bytes */<total>` é também a forma de **consultar** a sessão; terminar o
  upload com ele deixava um fake HTTP que o trata como consulta preso em
  `upload_stalled`. A exceção é o arquivo de **0 bytes**, que não tem byte
  para levar: ele fecha com `bytes */0`.
- São repetidos com backoff exponencial (0,5 s, 1 s, 2 s…, até 16 s), até
  `max_retries` vezes (padrão 5): `429`, `5xx`, erro de transporte e
  **`403` de limite de taxa** — o Drive informa cota estourada como `403` com
  `reason` `rateLimitExceeded` ou `userRateLimitExceeded`
  ([tratamento de erros](https://developers.google.com/workspace/drive/api/guides/handle-errors)).
  Outro `403` (`insufficientFilePermissions`, `storageQuotaExceeded`…) sobe
  na hora. Vale para chamada de metadado e para pedaço; num pedaço, o
  backend pergunta à sessão quanto ela tem antes de repetir. O arquivo
  inteiro nunca é reenviado.
- **`Retry-After`:** num `429` ou `503` com esse header em segundos, a
  espera é a que o Drive pediu, limitada a `backoff_max_seconds` (16 s por
  padrão); header ausente ou em formato de data cai no backoff exponencial.
- Um `308` que não avança nada (o Drive guardou zero bytes do pedaço) espera o
  backoff antes de reenviar; depois de `max_retries` respostas assim em
  sequência, vira `GoogleDriveException` com `reason="upload_stalled"`. Um
  `308` dizendo que o Drive tem **menos** do que ele já tinha confirmado —
  bytes que o backend já descartou da memória — não tem conserto e sobe na
  hora com `reason="upload_range_regressed"`.
- Um `404` num pedaço ou na consulta significa que a **sessão resumível**
  expirou ou se perdeu: vira `GoogleDriveException` com
  `reason="upload_session_not_found"` e uma mensagem dizendo para enviar de
  novo — não o `GoogleDriveFolderNotFoundException`, que fica para o `404`
  da busca e da abertura da sessão.
- A primeira leitura do stream acontece **antes** de a sessão ser aberta.
  Então o `content_validator` do `UploadUtils.save(...)` (e o `validator` de
  quem chama `storage.write_stream(...)` direto), que roda no primeiro
  chunk, recusa sem abrir sessão nenhuma. O `max_size_bytes` interrompe o
  stream no meio, e a sessão é abandonada antes do último pedaço. Pelo
  [guia do upload resumível](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable),
  o arquivo é criado quando a sessão se completa, então uma escrita
  interrompida **deve** não deixar arquivo na pasta — isso não foi medido
  contra o Google real.
- Quando a escrita falha, por qualquer motivo, o backend fecha (`aclose`) o
  gerador que você passou, então o `finally` dele roda na hora.

## Erros

| Situação | Exceção | Status |
| --- | --- | --- |
| Pasta não existe ou não foi compartilhada (`404`) | `GoogleDriveFolderNotFoundException` — a mensagem nomeia a pasta e o `client_email` com quem compartilhar | `502` |
| Sessão resumível expirou ou se perdeu (`404` num pedaço) | `GoogleDriveException`, `reason="upload_session_not_found"` | `502` |
| Retries esgotados (`429`, `5xx`, `403` de limite de taxa) | `GoogleDriveException` com o `status` e o `reason` da última resposta | `502` |
| Sessão parou de aceitar bytes / perdeu bytes confirmados | `GoogleDriveException`, `reason="upload_stalled"` / `"upload_range_regressed"` | `502` |
| `private_key` do JSON não é uma chave PEM RSA | `GoogleDriveException`, `reason="invalid_private_key"`, antes de qualquer requisição; a mensagem nomeia o `client_email` e nunca a chave | `502` |
| Qualquer outra recusa do Drive ou do endpoint de token | `GoogleDriveException`, com `details={"status": <http>, "reason": <error.errors[0].reason>}` (ou o `error` OAuth, como `invalid_grant`) | `502` |
| `presigned_url` | `PresignedUrlUnsupportedException` | `501` |
| Validador rejeitou | `InvalidFileTypeException` | `415` |
| Key (ou propriedade de `metadata`) passa de 124 bytes | `ValidationException` | `422` |
| Stream passou de `max_size_bytes` | `FileTooLargeException` | `413` |

Pela [documentação de erros](https://developers.google.com/workspace/drive/api/guides/handle-errors), o Drive deve responder `404` tanto
para pasta inexistente quanto para pasta que a conta não enxerga (não medido
aqui), por isso a mensagem fala do caso mais comum: compartilhar.

## Trocar a service account por outro token

A troca de token fica atrás do protocolo `AccessTokenProvider`, um método só
(`async def access_token(self) -> str`). Um provedor de token OAuth de
usuário entra no lugar do `ServiceAccountTokenProvider` sem mudar o cliente:

```python
import httpx

from tempest_fastapi_sdk.integrations.storage.google_drive import (
    GoogleDriveClient,
    GoogleDriveUploadStorage,
)


class StaticTokenProvider:
    """Exemplo: um token obtido fora do SDK."""

    def __init__(self, token: str) -> None:
        """Guarda o token."""
        self.token: str = token

    async def access_token(self) -> str:
        """Devolve o token guardado."""
        return self.token


http: httpx.AsyncClient = httpx.AsyncClient()
client: GoogleDriveClient = GoogleDriveClient(
    token_provider=StaticTokenProvider("ya29.…"),
    http_client=http,
)
storage: GoogleDriveUploadStorage = GoogleDriveUploadStorage(
    client, folder_id="1AbCdEfGhIjKlMnOp"
)
```

## O que foi medido e o que não foi

A suíte confere **o fio**, com `httpx.MockTransport`: o JWT (decodificado com
a chave pública de um par RSA gerado no teste — `alg`, `kid`, `iss`, `scope`,
`aud`, `iat`, `exp`), o corpo do POST de token, o cache e a troca única sob
concorrência, os headers e o `Content-Range` de cada pedaço, a retomada
depois de um `308` curto, o fechamento com bytes e total num tamanho
múltiplo exato do pedaço (contra um fake que trata `bytes */<total>` vazio
como consulta), o backoff de um `308` sem avanço e o erro imediato de um
`308` que recua, o retry de `503`/`429` e de `403` de limite de taxa, o
`Retry-After` e o seu teto, o `404` de sessão, a busca escapada,
`supportsAllDrives` em toda chamada, o mapeamento de erro e o redirect do
download do admin para o `web_view_link`.

**Não medido contra o Google real** (não há credencial neste repositório):
cota de service account em Meu Drive, o comportamento em Shared Drive, o
limite de 124 bytes de propriedade, a validade de uma semana da sessão
resumível, o momento em que o arquivo passa a existir, se o Drive fecha o
upload de 0 bytes com o `PUT` vazio `bytes */0`, o formato real do `403` de
limite de taxa e do `404` de sessão expirada, e se o Drive manda
`Retry-After` — tudo isso vem da documentação oficial linkada acima. O teste
`tests/integrations/storage/google_drive/test_live.py` faz a volta completa
(sobe, sobrescreve, confere `exists`, apaga) quando
`TEST_GOOGLE_DRIVE_CREDENTIALS` e `TEST_GOOGLE_DRIVE_FOLDER_ID` estão no
ambiente:

```bash
TEST_GOOGLE_DRIVE_CREDENTIALS=/caminho/sa.json \
TEST_GOOGLE_DRIVE_FOLDER_ID=1AbCdEfGhIjKlMnOp \
uv run pytest -m network tests/integrations/storage/google_drive/test_live.py
```

## Recap

- `uv add "tempest-fastapi-sdk[upload,google-drive]>=0.373.0"`, service account com
  chave JSON, Drive API habilitada, pasta (de preferência num Shared Drive)
  compartilhada com o `client_email`.
- `GoogleDriveSettings` + `GoogleDriveUploadStorage.from_service_account(**settings.google_drive_kwargs())`.
- `UploadUtils(backend=storage)` — a rota de upload não muda.
- A key mora em `appProperties`; key repetida substitui o conteúdo do mesmo
  arquivo; `delete` apaga sem lixeira.
- `presigned_url` levanta `PresignedUrlUnsupportedException`; o link do
  Drive está em `web_view_link` e em `UploadResult.url`, e o download do
  admin redireciona para ele.
- O último pedaço sempre leva bytes e o total; `429`, `5xx` e `403` de
  limite de taxa são repetidos, respeitando `Retry-After`; `404` no meio do
  upload é sessão perdida, não pasta.
- Recusa do Drive vira `GoogleDriveException` com status e `reason`; pasta
  não compartilhada vira `GoogleDriveFolderNotFoundException` dizendo com
  quem compartilhar.
