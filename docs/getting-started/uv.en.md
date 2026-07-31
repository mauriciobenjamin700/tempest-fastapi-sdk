# Install uv

This is the first page of the beginner track. It assumes **zero** knowledge of Python tooling: if you have never created a virtual environment in your life, start right here.

`uv` is the Python installer and project manager we use across **every** service built on `tempest-fastapi-sdk`. It is written in Rust, it is fast, and — more important when you are starting out — it replaces four different tools with one.

| What you need to do | Traditional way | With `uv` |
| --- | --- | --- |
| Install Python | download from the site, OS installer, `pyenv` | `uv python install 3.13` |
| Create a virtual environment | `python -m venv .venv` + `source .venv/bin/activate` | `uv venv` (and you never have to activate it) |
| Install dependencies | `pip install ...` + a hand-written `requirements.txt` | `uv add <package>` (writes to `pyproject.toml`) |
| Run a command in the environment | activate the venv and hope | `uv run <command>` |

!!! tip "You do not need Python installed first"
    `uv` is a single standalone binary, independent of Python. It installs Python **for** you — which is why this track starts with `uv` and not with Python.

## Install it

Pick your platform's tab. Every command is complete and copy-pasteable.

=== "Linux / macOS"

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    No `curl` on the machine? Use `wget`:

    ```bash
    wget -qO- https://astral.sh/uv/install.sh | sh
    ```

=== "Windows (PowerShell)"

    ```powershell
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    ```

    Or through the Windows package manager:

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

    If you already have a Python and no `pipx`:

    ```bash
    pip install uv
    ```

!!! info "Which one should you pick?"
    Prefer the **official script** (the first tabs). It installs a self-contained binary into `~/.local/bin` that does not depend on any Python already on the machine and knows how to update itself. `pip install uv` works, but ties `uv` to the Python that installed it — if that Python goes away, so does `uv`.

## Check that it worked

```bash
uv --version
```

Expected output (the number changes with the release of the day):

```text
uv 0.9.7
```

If you see a version, move on to the next page. If you see `command not found`, read the block below.

??? warning "`uv: command not found` — what to do"
    The script installs the binary into `~/.local/bin`, and that directory may not be on your `PATH`. Two ways out:

    **1. Source the environment file the installer created** (applies to the open terminal only):

    ```bash
    source $HOME/.local/bin/env
    ```

    **2. Make it permanent** by appending the line to your shell's startup file:

    ```bash
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
    exec bash
    ```

    On `zsh` (the macOS default), swap `~/.bashrc` for `~/.zshrc` and `exec bash` for `exec zsh`.

    On Windows, close and reopen PowerShell — the installer updates the user `PATH`, but the current session does not reload on its own.

## Let the terminal complete commands for you

Optional, but it saves typing every single day:

```bash
uv generate-shell-completion bash >> ~/.bashrc
exec bash
```

Swap `bash` for `zsh`, `fish` or `powershell` to match your shell.

## Keep it up to date

```bash
uv self update
```

!!! note "`uv self update` only exists for the script install"
    If you installed with `pip`/`pipx`/`brew`, upgrade through the same tool (`pipx upgrade uv`, `brew upgrade uv`). `uv` tells you when the command does not apply.

## The command map you will actually use

Keep this table around: it covers nearly all day-to-day usage.

| Command | What it does |
| --- | --- |
| `uv init <name>` | create a new project with a `pyproject.toml` |
| `uv add <package>` | add a dependency and record it in `pyproject.toml` |
| `uv remove <package>` | drop the dependency |
| `uv sync` | make `.venv` match exactly what the project declares |
| `uv lock` | recompute `uv.lock` (exact, reproducible versions) |
| `uv run <command>` | run a command inside the project environment |
| `uv python install <version>` | download a Python version |
| `uv tool install <package>` | install a CLI in isolation, available system-wide |
| `uvx <package>` | run a CLI without installing it (shortcut for `uv tool run`) |

!!! tip "The golden rule: prefix with `uv run`"
    Inside a project, `uv run pytest` always runs the **project environment's** `pytest`, even when you forgot to activate the venv — syncing the environment first if it drifted. Activating `.venv` by hand becomes optional.

## Recap

- `uv` is a single binary that installs Python, creates environments, resolves dependencies and runs commands.
- Install it with the official script; confirm with `uv --version`.
- `command not found` is almost always `~/.local/bin` missing from `PATH`.
- Day to day: `uv add` for dependencies, `uv run` to execute.

Next step: **[Pick your Python version »](python-versions.md)**.

## Official documentation

| Resource | Link |
| --- | --- |
| `uv` documentation | <https://docs.astral.sh/uv/> |
| Installation guide | <https://docs.astral.sh/uv/getting-started/installation/> |
| Getting started | <https://docs.astral.sh/uv/getting-started/> |
| Working on projects | <https://docs.astral.sh/uv/guides/projects/> |
| Tools (CLIs) with `uv tool` | <https://docs.astral.sh/uv/guides/tools/> |
| Environment variables | <https://docs.astral.sh/uv/reference/environment/> |

More links, covering the whole SDK stack, in **[Official reference docs »](references.md)**.
