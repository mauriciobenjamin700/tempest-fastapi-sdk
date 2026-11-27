# Subir, console e banco

Use para subir o serviço (`tempest serve`), abrir o console (`tempest shell`) e operar o banco (`tempest db`).

Parte da receita [CLI](../cli.md).

## Subir o serviço — `tempest serve`

```bash
tempest serve                              # host/port/reload vindos das settings
tempest serve --reload                     # liga o reload nesta execução
tempest serve --host 0.0.0.0 --port 9000   # sobrescreve
tempest serve --workers 4                  # múltiplos processos (sem reload)
tempest serve --app src.api.app:create_app # app fora dos lugares convencionais
```

O `main.py` scaffoldado já sobe o uvicorn programaticamente; este comando
serve para o projeto que ainda não tem esse arquivo, ou para quando você
quer sobrescrever host/porta sem editar `.env`. A resolução é a mesma do
`run_server`: flag > settings do projeto > default do SDK
(`127.0.0.1`, `8000`, sem reload).

!!! note "O uvicorn recebe a *string*, nunca o objeto"
    O reloader reimporta o alvo no processo filho. Entregar a instância já
    importada funciona — e transforma `--reload` em nada, em silêncio. Por
    isso o comando importa o candidato uma vez só para validar, e passa
    adiante `"src.server:app"`.

`--reload` e `--workers` juntos saem com código 2: o uvicorn não honra os
dois. E `--no-reload` **desliga** de verdade, mesmo com `SERVER_RELOAD=true`
no `.env` — a flag tem três estados (ligado, desligado, "decida você").

## Console interativo — `tempest shell`

Um REPL com o projeto já carregado, para não escrever script descartável
toda vez que a pergunta é "quantos pedidos esse usuário tem?".

```bash
tempest shell
```

```text
tempest shell — Python 3.11.12, top-level await enabled
available: WidgetModel, db, select, session, settings, text
session: open on sqlite+aiosqlite:///./app.db
>>> rows = await session.execute(select(WidgetModel))
>>> rows.scalars().all()
[<WidgetModel id=...>]
```

O que está no prompt: `settings` (a instância do projeto), cada model
mapeado **que o projeto define** (a `BaseModel` do SDK fica de fora — query
na classe base nunca é o que você quis dizer), `select`, `text`, o
`AsyncDatabaseManager` como `db` e uma `session` já aberta.

!!! info "`await` no nível de cima, de propósito"
    O console compila com `ast.PyCF_ALLOW_TOP_LEVEL_AWAIT` e roda a corrotina
    **no mesmo loop** em que a sessão foi aberta. Expor um helper
    `run(coro)` pareceria equivalente e não é: sessão dirigida de um segundo
    loop levanta `MissingGreenlet` no primeiro lazy load.

`--no-db` começa sem conectar — útil quando o banco está fora do ar e você
só quer inspecionar settings e models.

## Banco de dados — `tempest db`

Wrapper Alembic. Usa o `AlembicHelper` por trás, então a configuração (`alembic.ini` + `env.py`) continua sendo a fonte da verdade.

Resolução do `DATABASE_URL` na seguinte ordem:

1. Flag `--database-url`.
2. Env var `DATABASE_URL`.
3. Settings no `core/settings.py` do projeto, sob qualquer raiz de código
   (`src` ou `app`). Vale **qualquer** instância de
   `pydantic_settings.BaseSettings` — os nomes `settings` e `config` são
   tentados primeiro, e depois o módulo é varrido por tipo —, **ou** o
   resultado de um factory zero-arg que devolva uma (`get_settings()`, ou
   qualquer callable anotado com retorno `BaseSettings`).
4. `DATABASE_URL` no `.env` do projeto.
5. `sqlalchemy.url` do `alembic.ini`.

!!! tip "O nome da instância não importa mais, e o factory vale"
    Até a 0.280.0 só o nome `settings` era aceito, e a falha era um
    `ImportError` engolido por um `except Exception: return None`. Um serviço
    que chamasse a instância de `config` recebia *"no database URL. Pass
    --database-url, set DATABASE_URL, or run inside a project with
    src/core/settings.py"* — apontando para duas condições que o projeto já
    satisfazia. Hoje o que decide é o **tipo** da instância, o **factory**
    documentado (`get_settings()`) é aceito, o `.env` é lido, e quando nada
    resolve cada tentativa reporta o motivo no stderr:

    ```text
    note: importing src.core.settings failed: ImportError("cannot import name ...")
    error: no database URL. Pass --database-url, set DATABASE_URL, put it in
           .env, or expose settings with a DATABASE_URL on
           src/core/settings.py (or app/core/settings.py).
    ```

    A mesma descoberta é compartilhada por `tempest db`, `tempest doctor`,
    `tempest user` e o `env.py` gerado — um projeto cujo settings só existe
    como `get_settings()` passa a migrar fora do CLI também.

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
tempest db stamp head                            # marca o DB sem rodar migrations
tempest db squash -m "init" --yes                # colapsa o histórico em 1 migration
tempest db backup                                # dump em backups/<db>_<ts>.<ext>
tempest db backup -o dump.sql                    # plain SQL (Postgres) por extensão
tempest db restore dump.dump --yes               # restaura (clean + recreate)
tempest db seed                                  # roda src.db.seeds:seed
tempest db seed --seed src.db.fixtures:demo      # callable customizado
```

!!! danger "`stamp head` não adota um banco existente"
    `stamp` grava o ponteiro sem rodar nada. Contra um schema que o Alembic
    **não** construiu, carimbar `head` registra como aplicadas revisions que
    nunca rodaram: o banco fica com as colunas velhas enquanto `current`,
    `upgrade` e `history` respondem que está em dia, e a falha aparece semanas
    depois em produção. A revision certa para adoção é a **base** — veja
    [Migrations »](../migrations.md), que traz `AlembicHelper.adopt()` e
    `sync_schema()` para não escolher isso à mão.

### Quando o Alembic recusa o comando

Erro do Alembic que descreve o **estado do banco** — e não um bug — sai como
uma linha, com o que fazer:

```console
$ tempest db revision -m "novo campo" --autogenerate
error: the database is behind head — a migration is pending. Run `tempest db upgrade`, then try again.
       (TEMPEST_DEBUG=1 for the full traceback)
$ echo $?
1
```

Quatro condições ganham conselho: banco atrás da head, histórico com mais de
uma head, revisão que não existe em `alembic/versions/`, e revisões que não
estão na mesma linhagem. Qualquer outra mensagem sai **como o Alembic
escreveu**, sem tradução — dica errada seria pior que a mensagem crua.

!!! tip "Quer o traceback de volta?"
    `TEMPEST_DEBUG=1 tempest db revision ...` re-levanta a exceção intocada.
    Use quando o erro **não** for uma das condições acima e você precisar ver
    de onde ele veio.

!!! note "Só a CLI traduz"
    Chamar `AlembicHelper.revision(...)` direto, de um script ou do lifespan,
    continua levantando `alembic.util.exc.CommandError`. Código quer a
    exceção tipada; quem está no terminal quer a frase.

### Colapsar o histórico — `tempest db squash`

Com o tempo o diretório `alembic/versions/` cresce sem limite — cada ajuste de schema vira mais um arquivo que o Alembic precisa percorrer em todo `upgrade`. O `squash` zera esse histórico para **uma única migration raiz** que descreve o schema **atual**, sem perder os bancos já existentes.

!!! danger "Operação destrutiva — rode contra um banco de desenvolvimento"
    O `squash` faz `downgrade base` no banco configurado (DROPa todas as tabelas) para conseguir autogerar o schema completo num arquivo só. Por isso exige `--yes`. Confirme que o `DATABASE_URL` aponta para um banco de dev antes de rodar.

O fluxo é:

1. Captura o `head` atual (vira o nome do diretório de backup).
2. `downgrade base` — limpa o banco para o autogenerate enxergar o schema vazio.
3. Move as revisions antigas para `alembic/versions/_squashed_<oldhead>/` (subdiretório que o Alembic ignora). Use `--no-backup` para apagar de vez.
4. Autogera **uma** migration raiz a partir de `BaseModel.metadata`.
5. `upgrade head` recria o schema e marca a nova revision.

```bash
tempest db squash -m "init" --yes               # backup recuperável (padrão)
tempest db squash -m "init" --yes --no-backup   # apaga os arquivos antigos
```

!!! warning "Bancos de produção não são tocados"
    O `squash` só mexe no banco configurado. Depois de fazer deploy da árvore colapsada, marque os bancos de produção como migrados **sem** recriar tabelas:

    ```bash
    tempest db stamp head
    ```

!!! tip "Squash manual (sem perder dados) → `stamp --purge`"
    Se você unificar as migrations **à mão** (apaga `versions/`, escreve 1 migration baseline, mantém o banco com os dados), o `alembic_version` ainda aponta para a revision antiga — que não existe mais no tree. Um `stamp` normal falha com `Can't locate revision`. Use `--purge` para limpar o ponteiro órfão e gravar o novo baseline:

    ```bash
    tempest db stamp init_schema --purge
    ```

**Recap:** `squash` troca um histórico que cresce sem fim por uma migration inicial limpa; `stamp` reconcilia os bancos que já estão no schema final (use `--purge` quando a revision gravada não existir mais). Backup recuperável por padrão — o Git é a sua segunda rede.

### Backup e restore — `tempest db backup` / `tempest db restore`

Snapshot do banco para um arquivo e volta. A estratégia muda por dialeto, mas a CLI é a mesma:

- **PostgreSQL** — `pg_dump` / `pg_restore`. O formato vem da extensão do arquivo: `.dump` → custom (`pg_dump -Fc`, comprimido, restaura com `pg_restore`), `.sql` → plain (`psql`). Force com `--plain` / `--custom`. Exige os client tools do Postgres no `PATH`.
- **SQLite** — cópia do arquivo do banco.

!!! warning "Pré-requisito: client tools do Postgres"
    `backup` / `restore` num banco **PostgreSQL** dependem dos binários `pg_dump`, `pg_restore` e `psql` disponíveis no `PATH`. Eles **não** vêm com o pacote Python — são instalados pelo sistema operacional. Sem eles, a CLI falha com uma mensagem clara (`'pg_dump' not found on PATH`).

    **SQLite não precisa de nada** — o backup é uma cópia de arquivo feita pela stdlib.

    === "Debian / Ubuntu"
        ```bash
        sudo apt-get update && sudo apt-get install -y postgresql-client
        ```

    === "Fedora / RHEL"
        ```bash
        sudo dnf install -y postgresql
        ```

    === "Arch"
        ```bash
        sudo pacman -S postgresql
        ```

    === "macOS (Homebrew)"
        ```bash
        brew install libpq && brew link --force libpq
        ```

    === "Windows"
        ```powershell
        choco install postgresql   # Chocolatey
        scoop install postgresql   # ou Scoop
        ```

    !!! tip "Confira a instalação"
        ```bash
        pg_dump --version && pg_restore --version && psql --version
        ```
        Combine a versão do client com a do servidor (`pg_dump` de uma major mais nova lê servidores mais antigos, mas o contrário falha).

```bash
tempest db backup                       # backups/<db>_<YYYYMMDD-HHMMSS>.dump
tempest db backup -o snapshot.sql       # plain SQL (Postgres) pela extensão
tempest db backup -o snap.dump --custom # força formato custom
tempest db restore snapshot.sql --yes   # restaura (psql)
tempest db restore snap.dump --yes      # restaura (pg_restore --clean --if-exists)
```

!!! danger "Restore sobrescreve o banco de destino"
    Por padrão o restore é **clean + recreate**: dropa os objetos existentes antes de recriar, então o resultado é uma cópia fiel do backup (`pg_restore --clean --if-exists`; no plain dropa/recria o schema `public`; no SQLite sobrescreve o arquivo). Por isso exige `--yes`. Use `--no-clean` para aplicar o dump por cima do schema atual.

!!! info "A senha do Postgres não vaza no `ps`"
    A URL é parseada em `-h/-p/-U/-d` e a senha vai via `PGPASSWORD` no environment do subprocesso — nunca na linha de comando.

**Recap:** `backup` tira um snapshot (formato pela extensão no Postgres, cópia no SQLite); `restore --yes` traz de volta, limpando o destino por padrão.

### Detectar drift de model — `tempest db check`

O `tempest db revision --autogenerate` grava um diff quando você pede. Nada
percebe quando alguém edita um model e **esquece** de pedir — até o deploy
que precisava da coluna.

```bash
tempest db check
```

Sem drift:

```text
No drift: the models match the migration tree.
```

Com drift, ele nomeia as operações que uma revision conteria e sai com
código 1:

```text
error: New upgrade operations detected: [('add_column', ...'colour'...)]
       Run `tempest db revision -m "<message>"` to record them.
```

!!! tip "É um passo de CI"
    Rode depois do `tempest db upgrade` no pipeline. A comparação precisa do
    banco em `head` — quando ele está atrás, o Alembic diz isso e a mensagem
    passa direto, sem virar "drift".

### Popular o banco — `tempest db seed`

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
