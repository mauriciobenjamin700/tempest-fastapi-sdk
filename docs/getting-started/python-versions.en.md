# Pick your Python version

With `uv` installed ([previous page](uv.md)), Python stops being something you "have on the machine" and becomes **a per-project choice**. This page shows how to make that choice, and how not to get bitten by it later.

## Why the version matters

The SDK declares its floor in `pyproject.toml`:

```toml
requires-python = ">=3.11"
```

That means: **3.11 is the minimum**, and anything above works. The full policy:

| Python | Status |
| --- | --- |
| 3.13 | Primary CI matrix |
| 3.12 | Supported |
| 3.11 | Supported (minimum) |
| 3.10 and older | Not supported |

!!! tip "When in doubt, use 3.13"
    It is the version the SDK is tested on at every commit. Reach for 3.11 only when something outside your control forces it (an old Docker image, a legacy server).

!!! info "How Python numbers releases"
    `3.13.2` is `major.minor.patch`. Compatibility breaks land in the **minor** (3.12 → 3.13); the patch (3.13.1 → 3.13.2) only fixes bugs and security issues. That is why we pin "3.13" and let the patch float. Each version's support window is at <https://devguide.python.org/versions/>.

## See what you have

```bash
uv python list
```

The output lists everything `uv` knows about — versions it downloaded, versions installed by the system, and versions available to download:

```text
cpython-3.13.2-linux-x86_64-gnu     /home/you/.local/share/uv/python/cpython-3.13.2/bin/python3.13
cpython-3.12.9-linux-x86_64-gnu     <download available>
cpython-3.11.11-linux-x86_64-gnu    /usr/bin/python3.11
```

Only what is already installed:

```bash
uv python list --only-installed
```

## Install a version

```bash
uv python install 3.13
```

You can install several at once — handy for testing the same code on all three supported versions:

```bash
uv python install 3.11 3.12 3.13
```

!!! note "This does not touch your system Python"
    `uv` keeps its interpreters in a directory of its own (see it with `uv python dir`). The `python3` your operating system uses is untouched — nothing breaks.

## Pin the project's version

Inside the project directory:

```bash
uv python pin 3.13
```

The command writes a `.python-version` file with a single line:

```text
3.13
```

From then on, every `uv run`, `uv sync` and `uv venv` in that directory uses 3.13 — for you and for anyone who clones the repository.

!!! check "Commit `.python-version`"
    It is the answer to "which Python does this project use?". Leaving it unversioned is like leaving `pyproject.toml` unversioned.

### Two files, two jobs

Beginners mix these up constantly. The difference:

| File | States | Who reads it |
| --- | --- | --- |
| `pyproject.toml` → `requires-python` | the **range** the code supports, e.g. `>=3.11` | whoever installs your package (PyPI included) |
| `.python-version` | the **exact** version this checkout uses, e.g. `3.13` | `uv`, on your machine and in CI |

One is a public contract, the other a local preference. They coexist.

## Create the environment

A **virtual environment** (venv) is a folder holding a Python plus that project's dependencies — isolated from every other project. Without it, two projects that need different versions of the same library fight each other.

```bash
uv venv --python 3.12
```

That creates `.venv/` on 3.12, ignoring `.python-version` just this once. In practice you rarely need it: `uv sync` and `uv run` create and maintain `.venv` on their own.

## Run something on another version, one-off

```bash
uv run --python 3.11 python -c "import sys; print(sys.version)"
```

Or through an environment variable, which applies to the whole command:

```bash
UV_PYTHON=3.11 uv run pytest
```

!!! example "This is how the SDK runs its own gates"
    The `tempest-fastapi-sdk` repository runs `UV_PYTHON=3.11 make check` before any release: if the code passes on the floor, it passes above it.

## Test on all three supported versions

Works for any service you publish:

```bash
for v in 3.11 3.12 3.13; do
    echo "=== Python $v ==="
    UV_PYTHON=$v uv run --isolated pytest -q
done
```

`--isolated` makes `uv` build a throwaway environment per round instead of recycling the previous version's `.venv`.

## Remove what you do not use

```bash
uv python uninstall 3.11
```

## When it goes wrong

??? failure "`No interpreter found for Python 3.X`"
    `uv` could not find the requested version and had no permission/network to download it. Install it explicitly:

    ```bash
    uv python install 3.13
    ```

??? failure "The project insists on an old version"
    Someone left a `.python-version` behind, or one exists in a directory **above** yours. Check which interpreter `uv` would resolve:

    ```bash
    uv python find
    ```

    And fix the pin:

    ```bash
    uv python pin 3.13
    ```

??? failure "`The requested interpreter resolved to Python 3.10.x, which is incompatible with the project`"
    The resolved version is below `requires-python`. That is the SDK protecting you from installing something that will not run. Install and pin a supported version (3.11+).

## Recap

- `requires-python` is the supported range; `.python-version` is this checkout's version. Commit both.
- `uv python install` downloads, `uv python pin` pins, `uv python list` shows.
- `UV_PYTHON=<version> uv run ...` runs a one-off on another version.
- When in doubt: **3.13**.

Next step: **[Your first project »](first-project.md)**.

## Official documentation

| Resource | Link |
| --- | --- |
| Python versions in `uv` | <https://docs.astral.sh/uv/concepts/python-versions/> |
| Virtual environments in `uv` | <https://docs.astral.sh/uv/pip/environments/> |
| Python release cycle | <https://devguide.python.org/versions/> |
| Official Python downloads | <https://www.python.org/downloads/> |
| The `venv` module (language docs) | <https://docs.python.org/3/library/venv.html> |
