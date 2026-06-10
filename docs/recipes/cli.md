# CLI


Instalar o `tempest-fastapi-sdk` expõe um console script `tempest`. Ele faz dois trabalhos: criar um novo serviço em camadas a partir do esqueleto preferido do SDK e rodar os quatro gates de qualidade (`ruff check`, `ruff format`, `mypy`, `pytest`) sem copiar e colar os mesmos comandos em cada projeto.

```bash
tempest --help                                  # lista todos os comandos
tempest --version                               # mostra a versão do SDK
```

!!! tip "Erro de uso? O help completo aparece junto"
    Quando você digita um comando inexistente, uma opção inválida ou
    esquece um argumento obrigatório, o `tempest` imprime o `--help`
    **completo** daquele comando (todos os parâmetros, defaults e
    descrições) **antes** da linha de erro — em vez do `Try '... --help'`
    enxuto do Click. Você corrige na hora, sem reexecutar com `--help`.

    ```bash
    tempest user create            # esqueceu --email
    # ... help completo do `user create` (todas as opções) ...
    # Error: Missing option '--email' / '-e'.
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
├── docker-compose.yaml      # serviços baseados nos extras escolhidos
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

O `pyproject.toml` gerado fixa a versão atual do SDK (`tempest-fastapi-sdk[auth,admin]>=<versão>` por padrão — mude com `--extras`). O `.env.example` criado usa a nomenclatura de settings da v0.8.0 (`SERVER_HOST`/`SERVER_PORT`/`SERVER_DEBUG`/`SERVER_RELOAD`/`LOG_LEVEL`/…), e `src/server.py` delega a `tempest_fastapi_sdk.run_server` para que o uvicorn seja importado de forma preguiçosa e os testes possam importar o app sem ele. Regras de validação: o nome do projeto deve casar com `^[a-z][a-z0-9_]*$` e não pode colidir com uma palavra-chave do Python, então `tempest new Bad-Name` e `tempest new class` saem com código 2 antes de qualquer arquivo ser escrito.

### `docker-compose.yaml` baseado nos extras

Desde a v0.25.0 o scaffold gera um `docker-compose.yaml` com **apenas** os serviços que os extras escolhidos precisam — sem ZooKeeper, Kafka ou qualquer outra coisa que você não vai usar.

| Extra | Container subido | Porta(s) exposta(s) |
|-------|------------------|---------------------|
| (sempre) | `postgres:18-alpine` | 5432 |
| `[cache]` | `redis:8-alpine` | 6379 |
| `[queue]` / `[tasks]` | `rabbitmq:4-management-alpine` | 5672 (AMQP) + 15672 (UI) |
| `[minio]` | `minio/minio` + bootstrap mc | 9000 (API) + 9001 (Console) |
| `[email]` | `mailhog/mailhog` | 1025 (SMTP) + 8025 (UI) |

Exemplo — projeto que usa cache + uploads em S3 + emails:

```bash
tempest new my_service --extras auth,cache,minio,email
```

Gera:

- `postgres`, `redis`, `minio` (+ `minio-bootstrap` que cria o bucket `uploads`), `mailhog`
- `.env.example` com `REDIS_URL`, `MINIO_*`, `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` (MailHog é plain — sem STARTTLS)

!!! info "Credenciais vêm do `.env`, não estão hardcoded no compose"
    A partir da v0.37.0, nenhuma credencial é gravada direto no
    `docker-compose.yaml`. Cada bloco `environment:` usa a forma
    `${VAR:-default}`, e o Docker Compose resolve `VAR` a partir do
    `.env` ao lado do compose. O `:-default` mantém o stack subindo
    antes de você copiar `.env.example` para `.env` — mas defina
    segredos reais no `.env` para qualquer deploy não-descartável.
    Variáveis lidas pelo compose: `POSTGRES_USER` / `POSTGRES_PASSWORD`
    / `POSTGRES_DB`, `RABBITMQ_DEFAULT_USER` / `RABBITMQ_DEFAULT_PASS`
    / `RABBITMQ_DEFAULT_VHOST`, `MINIO_ROOT_USER` /
    `MINIO_ROOT_PASSWORD` — todas com seus padrões já no `.env.example`.

Subir tudo:

```bash
docker compose up -d
```

Derrubar mantendo volumes:

```bash
docker compose down
```

Derrubar limpando volumes:

```bash
docker compose down -v
```

As tags das imagens são fixadas — atualize por `pyproject.toml` da SDK, não no projeto gerado. Versões atuais (v0.26.0+): `postgres:18-alpine`, `redis:8-alpine`, `rabbitmq:4-management-alpine`.

### Regerar o `docker-compose.yaml` num projeto existente

Quando você muda os extras instalados (`uv add "tempest-fastapi-sdk[minio]"`) ou o SDK bumpa as imagens, regenere com:

```bash
tempest generate --docker                        # lê extras do pyproject.toml local
tempest generate --docker --extras cache,minio   # força extras explicitos
tempest generate --docker --name my-svc          # sobrescreve prefixo do container
tempest generate --docker --force                # sobrescreve compose existente
```

O comando lê o `[project] name` + extras do `pyproject.toml` do diretório atual (use `--path` pra outro). Recusa overwrite sem `--force` pra não pisar em edits manuais. O `.env.example` é atualizado de forma idempotente — re-rodar não duplica blocos.

### Gerar as camadas `src` dos extras — `tempest generate --src`

As camadas sempre presentes (`api`, `controllers`, `services`, `schemas`, `db`, `core`, `utils`) já vêm no scaffold. As camadas que só fazem sentido com um extra específico — `[queue]` (FastStream) e `[tasks]` (TaskIQ) — **não** entram no esqueleto base: deixar pacotes placeholder vazios em todo serviço contraria as regras de layout. Quando você adiciona um desses extras a um projeto existente (`uv add "tempest-fastapi-sdk[queue]"`), gere a camada correspondente com:

```bash
tempest generate --src                           # lê extras do pyproject.toml local
tempest generate --src --extras tasks            # força extras explícitos
tempest generate --src --force                   # sobrescreve arquivos existentes
tempest generate --docker --src                  # compose + camadas numa tacada
```

Mapeamento extra → camada gerada:

| Extra | Arquivos criados (sob `src/` ou `app/`) |
|-------|------------------------------------------|
| `[queue]` | `queue/__init__.py` (broker + `AsyncBrokerManager` + `get_broker`), `queue/handlers.py` (subscriber de exemplo) |
| `[tasks]` | `tasks/__init__.py` (broker + `AsyncTaskBrokerManager` + `get_task_manager`), `tasks/jobs.py` (task de exemplo) |

A raiz (`src` ou `app`) é detectada automaticamente, e os imports gerados (`from src.queue import broker`) já apontam pra ela. A operação é **idempotente**: arquivos existentes são **mantidos** a menos que você passe `--force`, então um handler editado à mão nunca é sobrescrito silenciosamente — o arquivo irmão que ainda não existe é criado normalmente. Extras sem camada associada (ex.: só `[cache]`) não geram nada e o comando avisa.

!!! note "`tempest new` já gera as camadas dos extras escolhidos"
    Um `tempest new my_service --extras auth,queue` já entrega
    `src/queue/` pronto — o `generate --src` é pra quando você adiciona
    o extra **depois** de criar o projeto.

Depois de gerar:

```bash
cd my_service
uv sync                                         # instala SDK + ferramentas de dev
cp .env.example .env
uv run python main.py                           # serve no HOST:PORT configurado
uv run pytest                                   # o smoke test embutido
```

### Banco de dados — `tempest db`

Wrapper Alembic. Usa o `AlembicHelper` por trás, então a configuração (`alembic.ini` + `env.py`) continua sendo a fonte da verdade.

Resolução do `DATABASE_URL` na seguinte ordem:

1. Flag `--database-url`.
2. Env var `DATABASE_URL`.
3. `src.core.settings.settings.DATABASE_URL` (quando rodando no diretório do projeto scaffoldado).
4. `sqlalchemy.url` do `alembic.ini`.

```bash
tempest db init                                  # cria alembic.ini + alembic/env.py
tempest db revision -m "init users table"        # autogenerate por padrão
tempest db revision -m "manual change" --manual  # cria arquivo vazio pra editar
tempest db upgrade                               # alembic upgrade head
tempest db upgrade <rev>                         # upgrade até rev específico
tempest db downgrade                             # rollback de 1 step
tempest db downgrade <rev>                       # rollback até rev específico
tempest db current                               # imprime revision aplicado
tempest db history                               # histórico de revisions
tempest db history -v                            # com message body completo
tempest db seed                                  # roda src.db.seeds:seed
tempest db seed --seed src.db.fixtures:demo      # callable customizado
```

#### Popular o banco — `tempest db seed`

Roda um callable de seed do projeto dentro de uma sessão gerenciada (commit no sucesso, rollback no erro). O callable recebe uma `AsyncSession` posicional e pode ser sync ou async; o que ele insere é decisão sua — o SDK só cuida do ciclo de vida da sessão. Por padrão importa `src.db.seeds:seed`.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import CategoryModel


async def seed(session: AsyncSession) -> int:
    """Popula categorias iniciais. Retorna a contagem (opcional)."""
    session.add_all([CategoryModel(name="Livros"), CategoryModel(name="Games")])
    await session.flush()
    return 2
```

Quando o callable devolve um `int`, a CLI mostra a contagem: `Seeded via src.db.seeds:seed (2 rows).`

### Usuários — `tempest user`

Insere/lista usuários direto no banco usando o `UserModel` concreto do projeto (default `src.db.models:UserModel`). Útil pra bootstrapear o primeiro admin sem rodar SQL manual.

```bash
# Cria usuário comum
tempest user create --email ana@example.com --password senha-forte-12 --no-admin

# Cria admin (pode logar no /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Pede senha interativamente (não fica no shell history)
tempest user create --email admin@local --admin

# Modelo customizado fora do layout scaffoldado — DEVE ser subclasse de BaseUserModel
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# Promove/rebaixa um usuário existente (liga/desliga is_admin)
tempest user promote --email ana@example.com    # vira admin
tempest user revoke  --email ana@example.com    # volta a ser comum

# Lista
tempest user list                                # todos
tempest user list --admin                        # só admins
```

!!! tip "Sem `--admin`/`--no-admin`, o create pergunta"
    Quando você **não** passa nem `--admin` nem `--no-admin` num
    terminal interativo, o `tempest user create` pergunta
    `Should this user be an administrator? [y/N]`. Em execuções
    não-interativas (CI, pipes, scripts) o prompt é pulado e o usuário
    nasce comum (`is_admin=False`) — passe `--admin` explicitamente pra
    criar admin sem TTY.

`tempest user promote` / `tempest user revoke` localizam o usuário por email (case-insensitive) e só alternam `is_admin`. Quando nenhum usuário casa com o email, saem com código 1 e a mensagem `no user found`.

Resolução do `DATABASE_URL` igual ao `tempest db` (env var > settings > alembic.ini).

### Segredos — `tempest secrets`

Gera e rotaciona os segredos da aplicação (`JWT_SECRET` / `TOKEN_SECRET` por padrão), reescrevendo as linhas correspondentes no `.env` **no lugar** — fazendo backup do arquivo antigo antes — e deixando as outras linhas intactas.

```bash
# Rotaciona JWT_SECRET e TOKEN_SECRET no .env (gera .env.bak)
tempest secrets rotate

# Só imprime os novos valores (não escreve nada) — pra pipar num secret manager
tempest secrets rotate --print

# Chaves e arquivo customizados
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# Mais entropia, sem backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotacionar `JWT_SECRET` invalida todo token assinado com o valor antigo: usuários são deslogados e links de reset/ativação pendentes param de funcionar. Rotacione numa janela de manutenção e reinicie o serviço pra carregar os novos valores.

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
