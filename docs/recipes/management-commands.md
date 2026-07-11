# Management commands (`tempest <cmd>` do projeto)

Plugue comandos próprios na CLI `tempest` — do jeito que o Django deixa
você registrar `manage.py <comando>`. Um script de backfill, um seed
custom, um "reprocessa a fila": vira `tempest backfill`, com help e
tratamento de erro iguais aos comandos embutidos.

## O problema

Todo serviço acumula scripts operacionais soltos (`scripts/backfill.py`,
`python -m app.tools.resync`). Cada um com jeito próprio de rodar, sem
`--help`, sem padrão. Faltava um lugar canônico: a mesma CLI que já roda
`tempest db upgrade` e `tempest check-config`.

## Convenção: `src/commands.py`

Exponha um `typer.Typer` chamado `commands` num módulo `src/commands.py`
(ou `app/commands.py`, ou `commands.py` na raiz):

```python
import typer

commands: typer.Typer = typer.Typer()


@commands.command("backfill")
def backfill(dry_run: bool = False) -> None:
    """Recalcula os contadores desnormalizados."""
    typer.echo(f"backfill (dry_run={dry_run})")
```

Rode da raiz do projeto:

```bash
tempest backfill --dry-run
```

Aparece no `tempest --help` junto dos comandos embutidos. Toda a força do
Typer está disponível: argumentos, opções, tipos, help — tudo tipado.

## Apontando o local

Auto-detecta `src.commands` / `app.commands` / `commands`. Para outro
lugar (ou vários), configure no `pyproject.toml`:

```toml
[tool.tempest]
commands = "src.management"
# ou vários módulos:
commands = ["src.billing.commands", "src.ops.commands"]
```

## Colisão com comando embutido

Se um comando do projeto tiver o mesmo nome de um embutido (`new`, `db`,
`check`, `check-config`, `version`, …), o embutido **vence** e o do
projeto é pulado com um aviso no stderr. Escolha outro nome.

## Grupos aninhados

Como é Typer puro, dá pra agrupar. Um sub-Typer vira `tempest ops <cmd>`:

```python
import typer

commands: typer.Typer = typer.Typer()
ops: typer.Typer = typer.Typer()
commands.add_typer(ops, name="ops")


@ops.command("resync")
def resync() -> None:
    """Reprocessa a fila de sincronização."""
    ...
```

```bash
tempest ops resync
```

!!! note "Descoberta é best-effort, mas erros aparecem"
    Sem módulo de comandos, a CLI segue normal. Se você **configurou**
    `[tool.tempest] commands` e o módulo não importa (ou não expõe um
    Typer), o `tempest` avisa no stderr — mas nunca deixa de rodar os
    comandos embutidos por causa disso.

!!! tip "Rode da raiz do projeto"
    A descoberta adiciona o diretório atual ao `sys.path` para importar
    `src.commands`. Rode `tempest` da raiz do projeto (onde vive o
    `pyproject.toml`), como você já faz com `tempest db` / `check-config`.

## Recap

- Exponha `commands: typer.Typer` em `src/commands.py`; vira
  `tempest <cmd>`.
- `[tool.tempest] commands` aponta outro módulo (string ou lista).
- Colisão com embutido → embutido vence, projeto pulado com aviso.
- Typer puro: args/options/tipos/help/grupos aninhados de graça.
