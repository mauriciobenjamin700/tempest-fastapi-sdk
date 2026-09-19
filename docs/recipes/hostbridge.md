# Controle do host (arquivos, comandos, energia)

Seu serviço roda dentro do WSL e precisa agir na **máquina** que o hospeda:
ler um arquivo em `C:\Users\...`, rodar um comando PowerShell, abrir o
seletor de arquivos nativo, desligar o computador no fim do expediente.
`tempest_fastapi_sdk.hostbridge` é essa camada — a mesma que antes vivia
como um serviço separado em cada projeto que precisava dela.

Nada aqui é montado sozinho: `create_app` não conhece este módulo, e o
router existe porque um serviço pediu.

```bash
uv add "tempest-fastapi-sdk"
```

O sistema, os arquivos e os comandos não precisam de extra nenhum — é
biblioteca padrão e FastAPI. Ler PDF precisa de `[pdf-read]`, e ler com
**layout e tabelas** precisa de `[pdf-layout]`.

!!! danger "Isto é um shell naquela máquina"
    Quem alcança estes métodos roda comando como o usuário do host e desliga
    o computador. Duas consequências, e nenhuma é opcional:
    `allowed_base_paths` começa **vazio** — negando todo caminho até alguém
    configurar — e `make_hostbridge_router` **exige** dependências de auth,
    recusando uma lista vazia. O lado que escreve (comandos, escrita,
    remoção, energia) fica atrás de `destructive=True` e vem desligado.

## A primeira leitura

`HostBridge` recebe um `HostBridgeConfig` e nada mais. Tudo é `async`, e o
que toca disco ou processo roda fora do event loop:

```python
import asyncio

from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig


async def main() -> None:
    bridge = HostBridge(
        HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
    )

    info = await bridge.host_info()
    print(info.computer_name, info.os_version)

    file = await bridge.read_text("C:\\Users\\me\\Documents\\notas.txt")
    print(file.size_bytes, file.content[:80])


asyncio.run(main())
```

Repare no caminho: ele foi escrito na forma **Windows** e funcionou. O
bridge aceita as duas — `C:\Users\me` e `/mnt/c/Users/me` — e normaliza
antes de checar, com `wslpath` quando ele existe.

!!! warning "A ordem importa: resolver primeiro, checar depois"
    O caminho é resolvido (`..` colapsado, link simbólico seguido) **antes**
    de ser comparado com `allowed_base_paths`. Checar antes de resolver é o
    bug clássico: `/mnt/c/Users/me/../../../etc/passwd` começa com uma base
    permitida e termina fora dela.

## As configurações, vindas do ambiente

Em um serviço, o lugar dessas opções é o `Settings`. Componha o mixin e
splat o mapper — a tradução campo → argumento mora num lugar só:

```python
from tempest_fastapi_sdk.settings import HostBridgeSettings, ServerSettings
from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig


class Settings(HostBridgeSettings, ServerSettings):
    """Só o que é específico do serviço fica aqui."""


settings = Settings()
bridge = HostBridge(HostBridgeConfig(**settings.hostbridge_kwargs()))
```

As variáveis de ambiente são os próprios nomes dos campos:
`HOST_ALLOWED_BASE_PATHS`, `HOST_POWERSHELL_BINARY`, `HOST_CMD_BINARY`,
`HOST_COMMAND_TIMEOUT` e `HOST_MAX_FILE_READ_BYTES`.

!!! info "`HOST_ALLOWED_BASE_PATHS` não tem default permissivo"
    Vazio nega tudo. É a direção segura de falhar: um bridge que pode rodar
    PowerShell não deveria também poder ler o disco inteiro só porque
    ninguém lembrou de configurar.

## Expondo como HTTP

`make_hostbridge_router` devolve um `APIRouter` pronto para
`include_router`. Ele **exige** a lista de dependências — é a assinatura que
força a decisão de auth, em vez de um aviso na documentação:

```python
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import make_token_dependency, register_exception_handlers
from tempest_fastapi_sdk.hostbridge import (
    HostBridge,
    HostBridgeConfig,
    make_hostbridge_router,
)

app = FastAPI()
register_exception_handlers(app)

bridge = HostBridge(
    HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
)

app.include_router(
    make_hostbridge_router(
        bridge,
        dependencies=[Depends(make_token_dependency("um-segredo-longo"))],
    )
)
```

Isso monta o lado que **lê**:

| Método | Rota | O que faz |
| --- | --- | --- |
| `GET` | `/system/info` | Nome da máquina, usuário, SO, uptime |
| `GET` | `/system/files` | Lê um arquivo de texto |
| `GET` | `/system/files/list` | Lista um diretório |
| `GET` | `/system/files/pdf` | Extrai um PDF, página a página |
| `POST` | `/system/files/pick` | Abre o seletor de arquivos nativo |

O lado que **escreve** — `POST /system/exec`, `POST`/`DELETE
/system/files`, `POST /system/{shutdown,restart,abort,lock,logoff}` — só
aparece com `destructive=True`:

```python
from fastapi import Depends

from tempest_fastapi_sdk import make_token_dependency
from tempest_fastapi_sdk.hostbridge import (
    HostBridge,
    HostBridgeConfig,
    make_hostbridge_router,
)

bridge = HostBridge(
    HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
)

router = make_hostbridge_router(
    bridge,
    dependencies=[Depends(make_token_dependency("um-segredo-longo"))],
    destructive=True,
)
```

!!! tip "Ligue o lado destrutivo só com alguém para confirmar"
    Um assistente que executa comandos sem confirmação humana é um assistente
    que apaga a pasta errada uma vez só. O padrão que funciona é segurar a
    chamada até o usuário aprovar, mostrando os argumentos exatos.

## Os erros, com código

Toda falha sobe como exceção do envelope do SDK, então um serviço que já
chama `register_exception_handlers` responde `{detail, code, details}` sem
fiação extra — e com a frase em PT-BR ou en-US conforme o `Accept-Language`:

| Código | Status | Quando |
| --- | --- | --- |
| `HOST_INVALID_PATH` | 400 | Caminho fora das bases, ou intraduzível |
| `HOST_FILE_NOT_FOUND` | 404 | Não existe arquivo ali |
| `HOST_FILE_TOO_LARGE` | 413 | Acima de `max_file_read_bytes` |
| `HOST_FILE_DECODE_FAILED` | 400 | Bytes não decodificam — um PDF ou imagem |
| `HOST_COMMAND_FAILED` | 500 | O comando saiu com status diferente de zero |
| `HOST_COMMAND_TIMEOUT` | 504 | O comando passou do tempo e foi morto |
| `HOST_UNAVAILABLE` | 503 | Nem PowerShell nem `wslpath` existem aqui |

`HOST_UNAVAILABLE` é o que separa "esta máquina não tem host Windows" de
"a ação falhou": num container Linux puro, o chamador degrada em vez de
relatar erro.

## Lendo um PDF do host

```python
import asyncio

from tempest_fastapi_sdk.hostbridge import HostBridge, HostBridgeConfig
from tempest_fastapi_sdk.pdf import PdfExtractor


async def main() -> None:
    bridge = HostBridge(
        HostBridgeConfig(allowed_base_paths=("/mnt/c/Users/me/Documents",)),
    )
    document = await bridge.read_pdf(
        "C:\\Users\\me\\Documents\\contrato.pdf",
        extractor=PdfExtractor.LAYOUT,
    )
    for page in document.pages:
        print(page.page_number, len(page.tables), page.text[:60])


asyncio.run(main())
```

`PdfExtractor.TEXT` é `pypdf` — rápido, prosa corrida, extra `[pdf-read]`.
`PdfExtractor.LAYOUT` é `pdfplumber`, extra `[pdf-layout]`: mais lento, mas
recupera colunas e preenche `page.tables`.

!!! warning "Não há OCR aqui"
    Página escaneada é imagem sem camada de texto: ela volta com `text=""`
    em vez de sumir — a entrada `n` é sempre a página `n`. Cheque isso e
    mande esses arquivos para outro lugar; entregar página vazia a um modelo
    é como se inventa uma resposta confiante sobre algo que ninguém leu.

## Rodando comandos

```python
import asyncio

from tempest_fastapi_sdk.hostbridge import (
    CommandSchema,
    HostBridge,
    HostBridgeConfig,
)


async def main() -> None:
    bridge = HostBridge(HostBridgeConfig())
    result = await bridge.run_command(
        CommandSchema(command="Get-Date -Format o", shell="powershell", timeout=10),
    )
    print(result.return_code, result.stdout.strip(), result.duration_seconds)


asyncio.run(main())
```

!!! note "Elevação (UAC) não acontece daqui"
    Um comando que precisa de administrador falha. Passe por `gsudo` ou por
    uma entrada pré-criada no Agendador de Tarefas do lado Windows.

## Recap

- `HostBridge` + `HostBridgeConfig` dão a superfície inteira; `HostBridgeSettings`
  é a mesma coisa vinda do ambiente, via `hostbridge_kwargs()`.
- `allowed_base_paths` vazio nega tudo, e o caminho é resolvido antes de ser
  checado.
- `make_hostbridge_router` exige auth por assinatura, e só monta o lado que
  escreve com `destructive=True`.
- Toda falha carrega um `code` — `HOST_UNAVAILABLE` avisa que esta máquina
  não tem host para controlar.
- PDF: `[pdf-read]` para texto, `[pdf-layout]` para colunas e tabelas, sem
  OCR em nenhum dos dois.
