# CLI


Instalar o `tempest-fastapi-sdk` expõe um console script `tempest`. Ele faz dois trabalhos: criar um novo serviço em camadas a partir do esqueleto preferido do SDK e rodar os quatro gates de qualidade (`ruff check`, `ruff format`, `mypy`, `pytest`) sem copiar e colar os mesmos comandos em cada projeto.

```bash
tempest --help                                  # lista todos os comandos
tempest --version                               # mostra a versão do SDK
```

#### Gerar um novo serviço

```bash
tempest new my_service                          # gera em ./my_service
tempest new my_service --path ~/projects        # diretório-pai customizado
tempest new my_service \
    --bind-host 0.0.0.0 \                       # HOST padrão no .env.example
    --bind-port 9090 \                          # PORT padrão no .env.example
    --extras auth,upload                        # extras do SDK fixados
tempest new my_service --force                  # sobrescreve diretório existente
```

O esqueleto casa com a arquitetura em camadas documentada neste README:

```text
my_service/
├── main.py                  # one-liner → src.server.run()
├── pyproject.toml           # fixa tempest-fastapi-sdk + ruff/mypy/pytest
├── .env.example             # HOST/PORT/DATABASE_URL/JWT_SECRET/CORS_ORIGINS
├── .gitignore
├── README.md
├── src/
│   ├── server.py            # uvicorn.run() + app FastAPI no nível do módulo
│   ├── api/
│   │   ├── app.py           # create_app() conecta middleware + handlers do SDK
│   │   ├── routers/         # router de negócio placeholder
│   │   └── dependencies/    # auth.py (require_token) + factories
│   ├── controllers/         # orquestração entre services
│   ├── services/            # lógica de negócio
│   ├── schemas/             # DTOs Pydantic
│   ├── core/                # settings.py + exceptions.py
│   ├── db/
│   │   ├── models/
│   │   └── repositories/
│   └── utils/
└── tests/
    └── test_smoke.py        # garante que /api/ e /health/liveness sobem
```

O `pyproject.toml` gerado fixa a versão atual do SDK (`tempest-fastapi-sdk[auth]>=<versão>` por padrão — mude com `--extras`). O `.env.example` criado usa a nomenclatura de settings da v0.8.0 (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), e `src/server.py` delega a `tempest_fastapi_sdk.run_server` para que o uvicorn seja importado de forma preguiçosa e os testes possam importar o app sem ele. Regras de validação: o nome do projeto deve casar com `^[a-z][a-z0-9_]*$` e não pode colidir com uma palavra-chave do Python, então `tempest new Bad-Name` e `tempest new class` saem com código 2 antes de qualquer arquivo ser escrito.

Depois de gerar:

```bash
cd my_service
uv sync                                         # instala SDK + ferramentas de dev
cp .env.example .env
uv run python main.py                           # serve no HOST:PORT configurado
uv run pytest                                   # o smoke test embutido
```

#### Gates de qualidade

Os comandos de lint chamam a ferramenta do projeto. Eles procuram o executável no `PATH` primeiro e, caso contrário, caem para `uv run <tool>` para que um virtualenv local do projeto funcione sem ativação manual.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix . + ruff format .   (escreve)
tempest fix --unsafe                            # também aplica os --unsafe-fixes do ruff
tempest format                                  # ruff format .          (escreve)
tempest fmt-check                               # ruff format --check .   (somente leitura)
tempest type                                    # mypy .
tempest test                                    # pytest
tempest test tests/api/                         # pytest com filtro de caminho
tempest check                                   # lint + fmt-check + type + test, para no primeiro erro
```

`tempest fix` é a passada única de "organize o projeto" — ordena e remove imports duplicados, descarta imports não usados, normaliza aspas de strings, remove espaços em branco no fim das linhas e então roda `ruff format` para alinhar indentação, comprimento de linha, linhas em branco e a quebra de linha final. Rode-o antes do push quando o CI fica pegando detalhes de estilo.

!!! info "O `ruff format` roda sempre — mesmo com erros restantes"
    O `ruff check --fix` sai com código ≠ 0 quando sobra **qualquer**
    violação que ele não consegue corrigir sozinho (uma string ou
    comentário longo demais, um nome indefinido, …). O `tempest fix`
    roda o `ruff format` mesmo assim, então uma única linha não-corrigível
    não impede a formatação do arquivo inteiro — as linhas de **código**
    longas continuam sendo quebradas e as linhas em branco extras
    removidas. O exit code do lint ainda é propagado depois, então o CI
    continua falhando nas pendências reais.

!!! warning "Strings e comentários longos não quebram"
    Nem o `ruff format` nem o `tempest fix` quebram **strings literais ou
    comentários** longos — comportamento idêntico ao Black. Essas linhas
    `E501` permanecem e precisam ser encurtadas à mão ou silenciadas com
    `# noqa: E501`.

Todo comando retorna o exit code da ferramenta subjacente, então `tempest check` é seguro para conectar ao CI (`tempest check || exit 1`) ou a hooks de pre-commit. Quando nem o executável nem o `uv` estão no `PATH`, o wrapper imprime `error: '<tool>' is not on PATH and 'uv' is unavailable` e sai com `127` em vez de falhar silenciosamente.
