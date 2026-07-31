# Instalar o uv

Esta é a primeira página da trilha para iniciantes. Ela assume **zero** conhecimento de ferramentas Python: se você nunca criou um ambiente virtual na vida, comece exatamente aqui.

O `uv` é o instalador e gerenciador de projetos Python que usamos em **todos** os serviços que consomem o `tempest-fastapi-sdk`. Ele é escrito em Rust, é rápido, e — mais importante para quem está começando — ele resolve de uma vez só quatro coisas que normalmente exigiriam quatro ferramentas diferentes.

| O que você precisa fazer | Jeito tradicional | Com o `uv` |
| --- | --- | --- |
| Instalar o Python | baixar do site, instalador do sistema, `pyenv` | `uv python install 3.13` |
| Criar um ambiente virtual | `python -m venv .venv` + `source .venv/bin/activate` | `uv venv` (e você nem precisa ativar) |
| Instalar dependências | `pip install ...` + `requirements.txt` na mão | `uv add <pacote>` (escreve no `pyproject.toml`) |
| Rodar um comando no ambiente | ativar o venv e torcer | `uv run <comando>` |

!!! tip "Você não precisa ter Python instalado antes"
    O `uv` é um binário único, independente do Python. Ele instala o Python **para** você — é por isso que a trilha começa por ele e não pelo Python.

## Instale

Escolha a aba do seu sistema. Todos os comandos são copiáveis e completos.

=== "Linux / macOS"

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    Sem `curl` na máquina? Use o `wget`:

    ```bash
    wget -qO- https://astral.sh/uv/install.sh | sh
    ```

=== "Windows (PowerShell)"

    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

    Ou pelo gerenciador de pacotes do Windows:

    ```powershell
    winget install --id=astral-sh.uv -e
    ```

=== "Homebrew (macOS)"

    ```bash
    brew install uv
    ```

=== "pipx / pip"

    ```bash
    pipx install uv
    ```

    Se você já tem um Python e nem `pipx`:

    ```bash
    pip install uv
    ```

!!! info "Qual escolher?"
    Prefira o **script oficial** (primeiras abas). Ele instala um binário isolado em `~/.local/bin`, que não depende de nenhum Python já presente na máquina e sabe se atualizar sozinho. `pip install uv` funciona, mas amarra o `uv` ao Python que o instalou — se aquele Python sumir, o `uv` some junto.

## Confira se funcionou

```bash
uv --version
```

Saída esperada (o número muda conforme a versão do dia):

```text
uv 0.9.7
```

Se apareceu a versão, pode ir para a próxima página. Se apareceu `command not found`, siga o bloco abaixo.

??? warning "`uv: command not found` — o que fazer"
    O script instala o binário em `~/.local/bin`, e esse diretório pode não estar no seu `PATH`. Duas saídas:

    **1. Carregue o arquivo de ambiente que o instalador criou** (vale só para o terminal aberto):

    ```bash
    source $HOME/.local/bin/env
    ```

    **2. Torne permanente**, adicionando a linha ao arquivo de inicialização do seu shell:

    ```bash
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    exec bash
    ```

    Usa `zsh` (padrão no macOS)? Troque `~/.bashrc` por `~/.zshrc` e `exec bash` por `exec zsh`.

    No Windows, feche e abra o PowerShell — o instalador ajusta o `PATH` do usuário, mas a sessão atual não recarrega sozinha.

## Deixe o terminal completar os comandos

Opcional, mas economiza digitação todos os dias:

```bash
uv generate-shell-completion bash >> ~/.bashrc
exec bash
```

Troque `bash` por `zsh`, `fish` ou `powershell` conforme o seu shell.

## Mantenha atualizado

```bash
uv self update
```

!!! note "`uv self update` só existe na instalação via script"
    Se você instalou com `pip`/`pipx`/`brew`, atualize pela mesma ferramenta (`pipx upgrade uv`, `brew upgrade uv`). O `uv` avisa quando o comando não se aplica.

## O mapa de comandos que você vai usar

Guarde esta tabela: ela cobre praticamente todo o uso do dia a dia.

| Comando | Para que serve |
| --- | --- |
| `uv init <nome>` | cria um projeto novo com `pyproject.toml` |
| `uv add <pacote>` | adiciona uma dependência e grava no `pyproject.toml` |
| `uv remove <pacote>` | remove a dependência |
| `uv sync` | deixa o `.venv` idêntico ao que o projeto declara |
| `uv lock` | recalcula o `uv.lock` (versões exatas e reproduzíveis) |
| `uv run <comando>` | roda um comando dentro do ambiente do projeto |
| `uv python install <versão>` | baixa uma versão do Python |
| `uv tool install <pacote>` | instala uma CLI isolada, disponível no sistema inteiro |
| `uvx <pacote>` | roda uma CLI sem instalar (atalho de `uv tool run`) |

!!! tip "A regra de ouro: prefixe com `uv run`"
    Dentro de um projeto, `uv run pytest` sempre roda o `pytest` do **ambiente do projeto**, mesmo que você tenha esquecido de ativar o venv — inclusive sincronizando o ambiente antes, se estiver desatualizado. Ativar o `.venv` na mão passa a ser opcional.

## Recapitulando

- O `uv` é um binário único que instala Python, cria ambientes, resolve dependências e roda comandos.
- Instale pelo script oficial; confirme com `uv --version`.
- `command not found` quase sempre é `~/.local/bin` fora do `PATH`.
- No dia a dia: `uv add` para dependências, `uv run` para executar.

Próximo passo: **[Escolher a versão do Python »](python-versions.md)**.

## Documentação oficial

| Recurso | Link |
| --- | --- |
| Documentação do `uv` | <https://docs.astral.sh/uv/> |
| Guia de instalação | <https://docs.astral.sh/uv/getting-started/installation/> |
| Primeiros passos | <https://docs.astral.sh/uv/getting-started/> |
| Trabalhando com projetos | <https://docs.astral.sh/uv/guides/projects/> |
| Ferramentas (CLIs) com `uv tool` | <https://docs.astral.sh/uv/guides/tools/> |
| Variáveis de ambiente | <https://docs.astral.sh/uv/reference/environment/> |

Mais links, cobrindo todo o stack do SDK, em **[Documentação oficial de referência »](references.md)**.
