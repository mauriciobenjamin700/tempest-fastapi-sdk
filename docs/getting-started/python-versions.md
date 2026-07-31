# Escolher a versão do Python

Com o `uv` instalado ([página anterior](uv.md)), o Python deixa de ser algo que você "tem na máquina" e passa a ser **uma escolha por projeto**. Esta página mostra como fazer essa escolha e como não ser mordido por ela depois.

## Por que a versão importa

O SDK declara o piso no `pyproject.toml`:

```toml
requires-python = ">=3.11"
```

Isso significa: **3.11 é o mínimo**, e qualquer versão acima serve. A política completa:

| Python | Status |
| --- | --- |
| 3.13 | Matriz principal do CI |
| 3.12 | Suportado |
| 3.11 | Suportado (mínimo) |
| 3.10 e anteriores | Não suportado |

!!! tip "Na dúvida, use 3.13"
    É a versão em que o SDK é testado a cada commit. Use 3.11 só quando algo fora do seu controle exigir (uma imagem Docker antiga, um servidor legado).

!!! info "Como o Python numera versões"
    `3.13.2` é `major.minor.patch`. Quem quebra compatibilidade é o **minor** (3.12 → 3.13); o patch (3.13.1 → 3.13.2) só corrige bugs e segurança. Por isso a gente fixa "3.13" e deixa o patch flutuar. O calendário de suporte de cada versão está em <https://devguide.python.org/versions/>.

## Veja o que você tem

```bash
uv python list
```

A saída lista tudo que o `uv` conhece — versões já baixadas por ele, versões instaladas pelo sistema e versões disponíveis para download:

```text
cpython-3.13.2-linux-x86_64-gnu     /home/você/.local/share/uv/python/cpython-3.13.2/bin/python3.13
cpython-3.12.9-linux-x86_64-gnu     <download available>
cpython-3.11.11-linux-x86_64-gnu    /usr/bin/python3.11
```

Só o que já está instalado:

```bash
uv python list --only-installed
```

## Instale uma versão

```bash
uv python install 3.13
```

Pode instalar várias de uma vez — útil para testar o mesmo código nas três versões suportadas:

```bash
uv python install 3.11 3.12 3.13
```

!!! note "Isso não mexe no Python do sistema"
    O `uv` guarda os interpretadores dele num diretório próprio (veja com `uv python dir`). O `python3` que o seu sistema operacional usa continua intocado — nada quebra.

## Fixe a versão do projeto

Dentro da pasta do projeto:

```bash
uv python pin 3.13
```

O comando cria um arquivo `.python-version` com uma linha:

```text
3.13
```

A partir daí, todo `uv run`, `uv sync` e `uv venv` naquele diretório usa 3.13 — para você e para qualquer pessoa que clonar o repositório.

!!! check "Commite o `.python-version`"
    Ele é a resposta à pergunta "qual Python esse projeto usa?". Deixar de versioná-lo é como não versionar o `pyproject.toml`.

### Dois arquivos, dois papéis

Iniciante confunde os dois o tempo todo. A diferença:

| Arquivo | Diz o quê | Quem lê |
| --- | --- | --- |
| `pyproject.toml` → `requires-python` | a **faixa** que o código suporta, ex. `>=3.11` | quem instala o seu pacote (inclusive o PyPI) |
| `.python-version` | a versão **exata** que este checkout usa, ex. `3.13` | o `uv`, na sua máquina e na CI |

Um é contrato público, o outro é preferência local. Ambos convivem.

## Crie o ambiente

Um **ambiente virtual** (venv) é uma pasta com um Python e as dependências daquele projeto — isolada de todos os outros. Sem isso, dois projetos que pedem versões diferentes da mesma biblioteca brigam.

```bash
uv venv --python 3.12
```

Isso cria `.venv/` usando 3.12, ignorando o `.python-version` só desta vez. Na prática você raramente vai precisar: `uv sync` e `uv run` criam e mantêm o `.venv` sozinhos.

## Rode algo em outra versão, pontualmente

```bash
uv run --python 3.11 python -c "import sys; print(sys.version)"
```

Ou via variável de ambiente, que vale para o comando inteiro:

```bash
UV_PYTHON=3.11 uv run pytest
```

!!! example "É assim que o SDK roda os próprios gates"
    O repositório do `tempest-fastapi-sdk` executa `UV_PYTHON=3.11 make check` antes de qualquer release: se o código passa no piso, passa nas versões acima.

## Teste nas três versões suportadas

Vale para qualquer serviço que você publique:

```bash
for v in 3.11 3.12 3.13; do
    echo "=== Python $v ==="
    UV_PYTHON=$v uv run --isolated pytest -q
done
```

O `--isolated` faz o `uv` montar um ambiente temporário por rodada, em vez de reciclar o `.venv` da versão anterior.

## Remova o que não usa

```bash
uv python uninstall 3.11
```

## Quando der errado

??? failure "`No interpreter found for Python 3.X`"
    O `uv` não achou a versão pedida e não tinha permissão/rede para baixar. Instale explicitamente:

    ```bash
    uv python install 3.13
    ```

??? failure "O projeto insiste numa versão antiga"
    Alguém deixou um `.python-version` para trás, ou existe um em um diretório **acima** do seu. Verifique qual interpretador o `uv` resolveria:

    ```bash
    uv python find
    ```

    E corrija o pin:

    ```bash
    uv python pin 3.13
    ```

??? failure "`The requested interpreter resolved to Python 3.10.x, which is incompatible with the project`"
    A versão resolvida está abaixo do `requires-python`. É o SDK protegendo você de instalar algo que não roda. Instale e fixe uma versão suportada (3.11+).

## Recapitulando

- `requires-python` é a faixa suportada; `.python-version` é a versão deste checkout. Commite os dois.
- `uv python install` baixa, `uv python pin` fixa, `uv python list` mostra.
- `UV_PYTHON=<versão> uv run ...` roda algo pontual em outra versão.
- Na dúvida: **3.13**.

Próximo passo: **[Seu primeiro projeto »](first-project.md)**.

## Documentação oficial

| Recurso | Link |
| --- | --- |
| Versões do Python no `uv` | <https://docs.astral.sh/uv/concepts/python-versions/> |
| Ambientes virtuais no `uv` | <https://docs.astral.sh/uv/pip/environments/> |
| Calendário de versões do Python | <https://devguide.python.org/versions/> |
| Downloads oficiais do Python | <https://www.python.org/downloads/> |
| Módulo `venv` (documentação da linguagem) | <https://docs.python.org/3/library/venv.html> |
