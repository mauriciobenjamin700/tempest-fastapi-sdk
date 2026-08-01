# Seu primeiro projeto

Você tem o `uv` ([página 1](uv.md)) e sabe escolher a versão do Python ([página 2](python-versions.md)). Agora vamos criar um projeto do zero, instalar o SDK e subir uma API que responde de verdade — em menos de dez comandos.

Existem dois caminhos. Faça o **A** primeiro: ele é mais lento, mas é onde você entende o que cada arquivo faz. O **B** é o atalho que você vai usar em todo projeto real depois.

## Caminho A — do zero, entendendo cada peça

### 1. Crie o projeto

```bash
uv init minha-api --python 3.13
cd minha-api
```

O `uv` cria o esqueleto e já inicializa um repositório Git:

```text
minha-api/
├── .git/
├── .gitignore
├── .python-version      # 3.13 — a versão fixada para este checkout
├── README.md
├── main.py              # um "hello world" de exemplo
└── pyproject.toml       # nome, versão e dependências do projeto
```

O `pyproject.toml` recém-criado:

```toml
[project]
name = "minha-api"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.13"
dependencies = []
```

!!! info "`pyproject.toml` é a identidade do projeto"
    É o arquivo padrão do ecossistema Python moderno (definido pela [PEP 621](https://peps.python.org/pep-0621/)). Tudo que o projeto precisa para ser instalado e resolvido está nele — não existe mais `requirements.txt` no fluxo com `uv`.

### 2. Instale o SDK

```bash
uv add "tempest-fastapi-sdk[sqlite]" uvicorn
```

O `uv` resolve, baixa, cria o `.venv/`, grava o `uv.lock` e atualiza o `pyproject.toml`:

```toml
dependencies = [
    "tempest-fastapi-sdk[sqlite]>=0.172.1",
    "uvicorn>=0.52.0",
]
```

!!! note "Por que `[sqlite]` e por que `uvicorn`?"
    `[sqlite]` traz o driver assíncrono `aiosqlite` — o SDK não escolhe banco por você (veja a tabela completa em **[Instalação »](../installation.md)**). O `uvicorn` é o servidor que executa a aplicação; o `fastapi` já vem junto com o SDK, não precisa adicionar.

### 3. Escreva a API

Substitua o conteúdo de `main.py` por este arquivo completo:

```python
# main.py
from fastapi import FastAPI

from tempest_fastapi_sdk import BaseSchema, register_exception_handlers
from tempest_fastapi_sdk.exceptions import NotFoundException


class TaskSchema(BaseSchema):
    """A single task exposed by the API."""

    id: int
    title: str
    done: bool = False


app: FastAPI = FastAPI(title="Minha primeira API")
register_exception_handlers(app)

TASKS: dict[int, TaskSchema] = {
    1: TaskSchema(id=1, title="Instalar o uv", done=True),
    2: TaskSchema(id=2, title="Escrever meu primeiro endpoint"),
}


@app.get("/tasks")
async def list_tasks() -> list[TaskSchema]:
    """Return every task."""
    return list(TASKS.values())


@app.get("/tasks/{task_id}")
async def get_task(task_id: int) -> TaskSchema:
    """Return one task by id.

    Raises:
        NotFoundException: When no task carries the given id.
    """
    task = TASKS.get(task_id)
    if task is None:
        raise NotFoundException("Tarefa nao encontrada")
    return task
```

### 4. Suba o servidor

```bash
uv run uvicorn main:app --reload --port 8000
```

```text
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

!!! tip "Reparou que você não ativou o `.venv`?"
    O `uv run` faz isso por você — e ainda sincroniza o ambiente antes, caso o `pyproject.toml` tenha mudado. Esse é o hábito a criar: prefixe tudo com `uv run`.

### 5. Veja funcionando

Abra <http://127.0.0.1:8000/docs> no navegador: o FastAPI gera a documentação interativa sozinho, a partir dos tipos que você anotou. Dá para chamar os endpoints por ali.

Pelo terminal, em outra aba:

```bash
curl http://127.0.0.1:8000/tasks
```

```json
[
  {"id": 1, "title": "Instalar o uv", "done": true},
  {"id": 2, "title": "Escrever meu primeiro endpoint", "done": false}
]
```

Agora peça uma tarefa que não existe:

```bash
curl -i http://127.0.0.1:8000/tasks/42
```

```text
HTTP/1.1 404 Not Found
content-type: application/json

{"detail": "Tarefa nao encontrada", "code": "NOT_FOUND", "details": {}}
```

### O que o SDK fez por você

Três linhas do exemplo carregam o peso todo:

| Linha | Efeito |
| --- | --- |
| `class TaskSchema(BaseSchema)` | schema Pydantic já configurado com as convenções do projeto (lê de objetos ORM, normaliza strings, valida em atribuição) |
| `register_exception_handlers(app)` | qualquer `AppException` levantada em qualquer camada vira uma resposta JSON no mesmo envelope, com o status certo |
| `raise NotFoundException(...)` | você levanta uma exceção de domínio; quem traduz para HTTP 404 é o handler, não o seu endpoint |

Sem o SDK, o terceiro item vira `raise HTTPException(status_code=404, detail=...)` espalhado pelos routers, e o formato do erro muda de endpoint para endpoint. Esse envelope único é o que torna um cliente (React, Flutter) capaz de tratar erro de um jeito só.

## Caminho B — o atalho de projeto real

Um serviço de verdade tem camadas (router → controller → service → repository), settings, migrations, Docker. Digitar isso à mão toda vez é desperdício: a CLI `tempest` gera o esqueleto inteiro.

```bash
# 1. instale a CLI isolada do sistema (não entra no projeto)
uv tool install "tempest-fastapi-sdk[all]"

# 2. gere o serviço
tempest new minha-api
cd minha-api

# 3. instale as dependências geradas e rode os testes
uv sync
uv run pytest
```

O que sai disso:

```text
minha-api/
├── main.py                 # one-liner que chama run() de src.server
├── pyproject.toml
├── .env.example
└── src/
    ├── server.py           # uvicorn programático + app importável
    ├── api/                # routers, dependencies, factory do app
    ├── controllers/        # orquestração fina sobre os services
    ├── services/           # lógica de negócio
    ├── schemas/            # DTOs de request/response
    ├── db/
    │   ├── models/
    │   └── repositories/
    └── core/               # settings + constants + exceptions
```

Suba o serviço:

```bash
uv run python main.py
```

!!! warning "CLI global e projeto são ambientes diferentes"
    `uv tool install` coloca o comando `tempest` num ambiente próprio, disponível em qualquer diretório. O projeto gerado tem o **seu** `pyproject.toml` e o **seu** `.venv`, criados pelo `uv sync`. Não misture `pip install` global com o `uv sync` do projeto: você acaba com duas instalações do SDK e depurando a errada.

## O dia a dia, em cinco comandos

| Comando | Quando usar |
| --- | --- |
| `uv add <pacote>` | precisa de uma biblioteca nova |
| `uv sync` | acabou de clonar o repositório, ou alguém mudou as dependências |
| `uv run <comando>` | rodar qualquer coisa (servidor, testes, script) |
| `uv run pytest` | rodar a suíte de testes |
| `uv lock --upgrade` | atualizar as versões travadas no `uv.lock` |

## Recapitulando

- `uv init` cria o projeto, `uv add` instala, `uv run` executa — nessa ordem.
- O `.venv` é criado e mantido pelo `uv`; ativar na mão é opcional.
- `BaseSchema` + `register_exception_handlers` + exceções de domínio já entregam uma API com contrato de erro consistente.
- Para projeto real, `tempest new` gera o layout em camadas inteiro.

Daqui você tem dois caminhos, ambos bons:

- **[Arquitetura »](../architecture.md)** — por que router → controller → service → repository, e o que mora em cada camada.
- **[Tutorial »](../tutorial.md)** — a feature *Users* completa, do model ao endpoint paginado.

E quando quiser aprofundar em qualquer ferramenta do stack: **[Documentação oficial de referência »](references.md)**.

## Documentação oficial

| Recurso | Link |
| --- | --- |
| Trabalhando com projetos (`uv`) | <https://docs.astral.sh/uv/guides/projects/> |
| Gerenciando dependências (`uv`) | <https://docs.astral.sh/uv/concepts/projects/dependencies/> |
| Primeiros passos com FastAPI | <https://fastapi.tiangolo.com/tutorial/first-steps/> |
| Modelos do Pydantic | <https://docs.pydantic.dev/latest/concepts/models/> |
| Rodando o Uvicorn | <https://www.uvicorn.org/> |
