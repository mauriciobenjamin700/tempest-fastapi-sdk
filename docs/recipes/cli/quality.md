---
description: "Use para rodar os gates de qualidade (ruff, mypy, pytest) pela CLI e saber quais passos do SDK o `tempest check` executa, incluindo `lint-schemas`."
---

# Gates de qualidade e `tempest check`

Use para rodar os gates de qualidade e saber quais passos do SDK o `tempest check` executa.

Parte da receita [CLI](../cli.md).

## Gates de qualidade

!!! info "Esses comandos vêm do `tempest-cli` (v0.226.0)"
    O gate de qualidade não tem nada de FastAPI — é `ruff`, `mypy` e
    `pytest`. Desde a v0.226.0 ele vive num pacote próprio,
    [`tempest-cli`](https://pypi.org/project/tempest-cli/), que o extra
    `[cli]` do SDK declara e que o SDK monta na CLI dele.

    **Nada muda para você**: `tempest check` é o mesmo comando, com as
    mesmas flags e o mesmo `[tool.tempest] typing_strictness`. O que
    muda é que quem **não** usa FastAPI agora consegue instalar só o
    gate:

    ```bash
    uv add --dev tempest-cli
    tempest-cli check
    ```

    Por que separar: para chegar nesses quatro comandos era preciso
    instalar FastAPI, SQLAlchemy, Alembic e Pydantic — 38,7 MB de
    dependências e cerca de 0,5 s de import por invocação, medidos, para
    comandos que não tocam em nada disso.

    Os dois nunca divergem porque há uma implementação só: o SDK chama
    `register_commands(app)` do `tempest_cli`. Importar
    `tempest_fastapi_sdk.cli.lint` ou `.pr_prompt` continua funcionando
    e devolve as mesmas funções.

Os comandos de lint chamam a ferramenta do projeto, procurando o executável nesta ordem:

1. o `$VIRTUAL_ENV` ativado;
2. o `.venv` mais próximo, subindo a partir do diretório atual — o ambiente do projeto, mesmo sem ativação;
3. o diretório do interpretador que roda o próprio `tempest`;
4. o `PATH`, pulando shim de gerenciador de versão (pyenv/asdf) que não despacha para lugar nenhum;
5. `uv run --with <tool> <tool>`, quando o `uv` está no `PATH`.

O projeto vem antes do ambiente da CLI de propósito: com `uv tool install`, a CLI tem o próprio `ruff`, e um projeto que fixa outra versão continua rodando a que fixou.

```bash
tempest lint                                    # ruff check .
tempest fix                                     # ruff check --fix-only . + ruff format . + ruff check .   (escreve)
tempest fix --unsafe                            # também aplica os --unsafe-fixes do ruff
tempest format                                  # ruff format .          (escreve)
tempest fmt-check                               # ruff format --check .   (somente leitura)
tempest type                                    # mypy .
tempest test                                    # pytest -n auto -p no:cacheprovider  (paralelo, o padrão)
tempest test tests/api/                         # com filtro de caminho
tempest test -w 4                               # quatro workers
tempest test --serial                           # em série (--lf / --ff, depurador)
tempest check                                   # lint + fmt-check + type + lint-layers + openapi-errors + lint-schemas + test, para no primeiro erro
tempest check --rule IMP002 --strict            # o mesmo gate, com o lint-layers só nessa regra e aviso derrubando
```

`test` e `check` rodam a suíte em paralelo por padrão desde o `tempest-cli`
0.6.0 (o extra `[cli]` declara `tempest-cli>=0.7.0`); sem o pytest-xdist no
ambiente do pytest, avisam e rodam em série. Quando usar, o banco por worker
e o que fazer com teste que só falha em paralelo estão na
[receita de testes](../testing.md#a-suite-em-paralelo-o-padrao-do-tempest-test).

`tempest fix` é a passada única de "organize o projeto" — ordena e remove imports duplicados, descarta imports não usados, normaliza aspas de strings, remove espaços em branco no fim das linhas e então roda `ruff format` para alinhar indentação, comprimento de linha, linhas em branco e a quebra de linha final. Rode-o antes do push quando o CI fica pegando detalhes de estilo.

!!! info "O relatório sai do arquivo já formatado (`tempest-cli` 0.5.1)"
    O `tempest fix` roda três passadas: `ruff check --fix-only`,
    `ruff format` e, por fim, `ruff check`, que é de onde saem o relatório
    e o exit code. Uma violação que o formatter resolveu não aparece mais
    nem derruba o comando. O que sobra depois da formatação continua saindo
    com código ≠ 0, então o CI segue falhando nas pendências reais.

!!! tip "Comentários e strings longos são encurtados, com prova de AST (`tempest-cli` 0.5.1)"
    O `ruff format` não quebra comentário nem string literal — igual ao
    Black. Depois dele, o `tempest fix` quebra cada comentário `E501` no
    limite (um comentário no fim da linha sobe para cima dela) e divide cada
    string literal simples nos espaços, em partes concatenadas dentro de
    parênteses. Cada edição só fica quando a AST do arquivo é idêntica antes
    e depois, então o programa não muda. Com `line-length = 88`, isto:

    ```python
    """Long lines."""

    MESSAGE: str = "this is a deliberately long plain string literal that goes well beyond the configured line length limit"


    def f() -> int:
        """Return one."""
        return 1  # this trailing comment is deliberately long so that it overflows the configured line length
    ```

    vira isto:

    ```python
    """Long lines."""

    MESSAGE: str = (
        "this is a deliberately long plain string literal that goes well beyond the "
        "configured line length limit"
    )


    def f() -> int:
        """Return one."""
        # this trailing comment is deliberately long so that it overflows the configured
        # line length
        return 1
    ```

    Pragma de ferramenta (`noqa`, `type: ignore`, `fmt:`, …), f-string,
    string com aspas triplas e palavra maior que uma linha ficam como estão.
    Para essas linhas — e para o que o formatter não tem onde quebrar, como
    um nome pontilhado — o `tempest fix` nomeia o token que estoura, o tipo
    e a ação que resolve, e separa as pendências entre as que não têm
    autofix e as que o `tempest fix --unsafe` corrigiria. Uma f-string
    longa, por exemplo, sai assim (saída 1):

    ```text
    1 line(s) still too long after ruff format — the line-length setting was applied; the formatter has nowhere to break these:
      src/longs.py:4 [string] ` this is a deliberately long f-string literal that goes well beyond the limit`
        ruff format does not split string literals, and fix could not split this one safely (an f-string, a triple-quoted string, or a word longer than a line). Break it into implicitly concatenated parts inside parentheses.
    1 residual violation(s): 1 with no autofix (edit by hand).
    ```

Todo comando retorna o exit code da ferramenta subjacente, então `tempest check` é seguro para conectar ao CI (`tempest check || exit 1`) ou a hooks de pre-commit. Quando nenhum dos cinco passos acha a ferramenta, o wrapper sai com `127` em vez de falhar silenciosamente, dizendo o que instalar:

```text
error: 'ruff' was not found in this project's environment, on PATH, and 'uv' is unavailable to run it. Install it with 'uv add --dev ruff' — or the whole set with 'uv add --dev "tempest-cli[tools]"' — and retry.
```

## Os passos do SDK no `tempest check`

O `tempest check` vem do `tempest-cli`, que não sabe nada de FastAPI. O SDK
entrega a ele três passos próprios, que rodam **depois do `mypy` e antes do
`pytest`**, nesta ordem, com o mesmo contrato dos outros: o primeiro exit code
diferente de zero para o gate.

| Passo | O que roda | Como acha o projeto |
| --- | --- | --- |
| `lint-layers` | as [regras de camada](lint-layers.md) | lê `src/` ou `app/` com `ast` |
| `openapi-errors` | o mesmo que `tempest openapi-errors --check` ([receita](../openapi-errors.md#passo-4-tempest-openapi-errors-check)) | lê `src/` ou `app/` com `ast`, sem importar nada |
| `lint-schemas` | o mesmo que `tempest lint-schemas` | importa `src.schemas` (ou `app.schemas`) num **processo filho** |

A raiz do projeto é o alvo do `check` (`.` por padrão), ou o pai dele quando o
alvo é o próprio `src`/`app` (`tempest check src`). O `lint-schemas` importa o
pacote num processo separado, rodado da raiz do projeto, para os módulos do
projeto não ficarem carregados no processo que roda o `pytest` em seguida. O
import de `src.schemas` passa pelo `src/__init__.py`, que monta os settings, e
um settings com `EnvironmentSettings` não sobe sem `ENV`: quando o shell não
define `ENV`, o filho recebe `ENV=test` — o mesmo valor que o `conftest.py` do
`tempest new` fixa. Um `ENV` exportado no shell vence.

Num projeto recém-criado pelo `tempest new`, os três passam (trecho da saída,
exit code `0`):

```text
$ lint-layers
Layer rules: no findings.
$ openapi-errors
Every route's declared errors match its flow (src).
$ lint-schemas
Schema metadata: src.schemas is fully documented.
$ pytest -n auto -p no:cacheprovider
```

Com um router cujas dependências levantam 401 e 404 não declarados, o gate
para no `openapi-errors` e o `pytest` nem roda (exit code `1`):

```text
$ lint-layers
Layer rules: no findings.
$ openapi-errors
src/api/routers/items.py:19  GET /
  undocumented: ItemNotFoundException, UnauthorizedException
1 route(s) with drift, 2 undocumented exception(s).
```

Projeto sem `src/` nem `app/` não falha: o passo diz que pulou e passa. O
mesmo vale para o `lint-schemas` sem pacote `schemas`. Já um `src.schemas` que
não importa **falha** (exit `2`, com o traceback): o pacote existe e está
quebrado.

Para migrar um projeto existente um gate de cada vez, desligue o passo em
`[tool.tempest]`:

```toml
[tool.tempest]
check_openapi_errors = false
check_lint_schemas = false
```

Com a chave em `false`, o passo imprime `skipped: [tool.tempest]
check_openapi_errors = false.` e passa. Valor que não é booleano sai com
exit `2`. Os comandos avulsos (`tempest openapi-errors`,
`tempest lint-schemas`) não leem essas chaves.


### Settings obrigatório no `lint-schemas`

O `lint-schemas` importa o `src.schemas` num processo filho com `ENV=test`
quando o shell não define `ENV` — e, com `ENV=test`, o `EnvironmentSettings`
descarta o `.env`. Um `Settings` com campo obrigatório sem default (um segredo
como `ADMIN_SECRET_KEY`, que não deve ter placeholder) não importa, e o passo
sai com `2`. Depois do traceback vem o motivo:

```text
hint: src.schemas was imported in a child process with ENV=test (ENV=test discards the .env, so a required setting without a default is missing). Give the child the settings it needs under [tool.tempest] lint_schemas_env = { NAME = "value" }, or drop the step with check_lint_schemas = false.
```

Dê ao filho o que ele precisa, só para este passo:

```toml
[tool.tempest]
lint_schemas_env = { ADMIN_SECRET_KEY = "lint-only" }
```

A precedência é: o default `ENV=test`, depois a tabela, depois o shell — um
valor que o shell exporta nunca é trocado pelo placeholder. Valor que não é
string sai com `2`.
