# Google Drive (uploads)

Seu serviço recebe arquivos — contratos, comprovantes, fotos — e a empresa
quer vê-los numa pasta do Google Drive, não num bucket S3. Esta receita liga
o `UploadUtils` a uma pasta (ou a um Shared Drive) da empresa por meio de uma
**service account**: sem login de usuário, sem tela de consentimento.

O backend é o `GoogleDriveUploadStorage`. Ele implementa o mesmo protocolo
`UploadStorage` do disco local e do MinIO, então a sua rota de upload não
muda nada: só o objeto passado em `backend=`.

```bash
uv add "tempest-fastapi-sdk[upload,google-drive]"
```

O extra `[google-drive]` traz `pyjwt` e `cryptography` (o `httpx` já vem no
pacote base). Não há `google-api-python-client` nem `google-auth`: a troca de
token de uma service account é um JWT assinado e um POST, e o SDK faz isso
sozinho.

## Passo 1 — Crie a service account e habilite a Drive API

No [Google Cloud console](https://console.cloud.google.com/):

1. Escolha (ou crie) um projeto.
2. Em **APIs e serviços → Biblioteca**, habilite a **Google Drive API**.
3. Em **IAM e administrador → Contas de serviço**, crie uma conta de serviço.
4. Na conta criada, **Chaves → Adicionar chave → JSON**. O navegador baixa um
   arquivo assim (valores encurtados):

```json
{
  "type": "service_account",
  "project_id": "minha-empresa",
  "private_key_id": "3f2a…",
  "private_key": "-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----\n",
  "client_email": "uploader@minha-empresa.iam.gserviceaccount.com",
  "token_uri": "https://oauth2.googleapis.com/token"
}
```

!!! warning "Esse arquivo é uma senha"
    Quem tem o JSON age como a conta de serviço. Guarde-o no gerenciador de
    segredos do seu deploy, nunca no repositório. O SDK nunca escreve a
    chave, o JWT assinado nem o token de acesso em log ou em mensagem de
    erro, e o `repr` das credenciais esconde a chave privada.

## Passo 2 — Compartilhe a pasta com o `client_email`

A service account só enxerga o que for compartilhado com ela. Pegue o
`client_email` do JSON e:

- **Shared Drive (recomendado):** adicione a conta como membro do Shared
  Drive, com papel de **Gerente de conteúdo** (ou superior — o `delete` apaga
  arquivos).
- **Pasta comum:** compartilhe a pasta com a conta como **Editor**.

O **id da pasta** é o último segmento da URL dela:
`https://drive.google.com/drive/folders/`**`1AbCdEfGhIjKlMnOp`**.

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
    com `InvalidFileTypeException` **antes** de qualquer requisição. A key
    gerada pelo `UploadUtils` (uuid + extensão) cabe com folga; cuidado com
    `keep_original_name=True` e nomes longos.

### Mesma key duas vezes: o conteúdo é substituído

Gravar uma key que já existe **substitui o conteúdo do mesmo arquivo**: mesmo
id, mesmo link, mídia nova (`files.update` resumível). Nenhum arquivo novo é
criado, e `exists` continua achando um só — o mesmo comportamento do disco
local e do MinIO ao sobrescrever.

!!! warning "Duas escritas simultâneas de uma key nova"
    O Drive não tem create condicional. Se duas requisições gravarem a
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

## Upload resumível, em pedaços

O backend usa o [upload resumível](https://developers.google.com/workspace/drive/api/guides/manage-uploads#resumable)
do Drive v3:

- O stream sobe em pedaços múltiplos de 256 KiB (`chunk_size=`, padrão 8 MiB)
  — a memória fica em um pedaço, não no arquivo.
- Cada pedaço que não é o último vai com `Content-Range: bytes a-b/*`; o
  Drive responde `308` com o header `Range` dizendo quanto guardou, e o
  próximo pedaço começa logo depois. Bytes que o Drive não guardou são
  reenviados; o resto, não.
- `429` e `5xx` num pedaço são repetidos com backoff exponencial (0,5 s,
  1 s, 2 s…, até 16 s), depois de perguntar à sessão quanto ela tem — até
  `max_retries` vezes (padrão 5). O arquivo inteiro nunca é reenviado.
- O `content_validator` roda no primeiro pedaço **antes** de a sessão ser
  aberta, e o `max_size_bytes` interrompe o stream no meio. Segundo o guia
  do Google, o arquivo só passa a existir quando o último pedaço chega,
  então uma escrita interrompida não deixa arquivo na pasta.

## Erros

| Situação | Exceção | Status |
| --- | --- | --- |
| Pasta não existe ou não foi compartilhada (`404`) | `GoogleDriveFolderNotFoundException` — a mensagem nomeia a pasta e o `client_email` com quem compartilhar | `502` |
| Qualquer outra recusa do Drive ou do endpoint de token | `GoogleDriveException`, com `details={"status": <http>, "reason": <error.errors[0].reason>}` (ou o `error` OAuth, como `invalid_grant`) | `502` |
| `presigned_url` | `PresignedUrlUnsupportedException` | `501` |
| Validador rejeitou / key longa demais | `InvalidFileTypeException` | `415` |
| Stream passou de `max_size_bytes` | `FileTooLargeException` | `413` |

O Drive responde `404` tanto para pasta inexistente quanto para pasta que a
conta não enxerga, por isso a mensagem fala do caso mais comum:
compartilhar.

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
depois de um `308` curto, o retry de `503`/`429`, a busca escapada,
`supportsAllDrives` em toda chamada e o mapeamento de erro.

**Não medido contra o Google real** (não há credencial neste repositório):
cota de service account em Meu Drive, o comportamento em Shared Drive, o
limite de 124 bytes de propriedade, a validade de uma semana da sessão
resumível e o momento em que o arquivo passa a existir — tudo isso vem da
documentação oficial linkada acima. O teste
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

- `uv add "tempest-fastapi-sdk[upload,google-drive]"`, service account com
  chave JSON, Drive API habilitada, pasta (de preferência num Shared Drive)
  compartilhada com o `client_email`.
- `GoogleDriveSettings` + `GoogleDriveUploadStorage.from_service_account(**settings.google_drive_kwargs())`.
- `UploadUtils(backend=storage)` — a rota de upload não muda.
- A key mora em `appProperties`; key repetida substitui o conteúdo do mesmo
  arquivo; `delete` apaga sem lixeira.
- `presigned_url` levanta `PresignedUrlUnsupportedException`; o link do
  Drive está em `web_view_link` e em `UploadResult.url`.
- Recusa do Drive vira `GoogleDriveException` com status e `reason`; pasta
  não compartilhada vira `GoogleDriveFolderNotFoundException` dizendo com
  quem compartilhar.
