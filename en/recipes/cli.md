# CLI


Installing `tempest-fastapi-sdk[cli]` (or `[cli-ops]`, on a server) exposes a `tempest` console script. It does two jobs: bootstrap a new layered service from the SDK's preferred skeleton, and run the four quality gates (`ruff check`, `ruff format`, `mypy`, `pytest`) without copy-pasting the same commands into every project.

```bash
tempest --help                                  # list every command
tempest --version                               # show the SDK version
```

!!! info "The CLI is the `[cli]` extra (since 0.308.0)"
    `tempest` lives in an extra rather than in the base package. In
    development, install it the way you use it (for a server, see
    [`[cli-ops]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/new-service.md#server-vs-development-cli-ops-and-cli)):

    ```bash
    uv tool install "tempest-fastapi-sdk[cli]"   # global command
    uv add --dev "tempest-fastapi-sdk[cli]"      # in the project's dev group
    ```

    The reason is `ruff`: the gate (`tempest check`) comes from
    `tempest-cli`, which requires `ruff>=0.8.0` at runtime. In the base
    package, every service importing the SDK shipped a formatter to
    production, and a project pinning `ruff<0.8` in its dev group could not
    even resolve its lock.

    Without either extra the script is still installed and explains what is
    missing, exiting with code 2 and no traceback:

    ```text
    $ tempest --help
    error: missing typer, click. The tempest CLI needs the optional [cli-ops] or [cli] extra. Install it with:
      uv add "tempest-fastapi-sdk[cli-ops]"   (on a server, without ruff)
      uv add --dev "tempest-fastapi-sdk[cli]"   (in development, with the gate)
      uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)
    ```

    `import tempest_fastapi_sdk` needs no extra at all; reading
    `tempest_fastapi_sdk.cli.app` (or importing `tempest_fastapi_sdk.cli.main`)
    without `typer` raises `ImportError` with that instruction. Importing
    `.lint` or `.pr_prompt`, or reading `TempestConfig` /
    `load_tempest_config` from `tempest_fastapi_sdk.cli.config`, without
    `tempest-cli` raises `ImportError` naming `[cli]`.

!!! tip "Usage error? The full help shows up with it"
    When you type an unknown command, an invalid option, or forget a
    required argument, `tempest` prints that command's **complete**
    `--help` (every parameter, default and description) **before** the
    error line — instead of Click's terse `Try '... --help'`. You fix
    it on the spot, without re-running with `--help`.

    ```bash
    tempest user create            # forgot --email
    # ... full `user create` help (every option) ...
    # Error: Missing option '--email' / '-e'.
    ```

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [Install and scaffold a service](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/new-service.md) | Use to choose between `[cli-ops]` and `[cli]`, scaffold a new service and bring an existing project to the standard (`tempest new`, `tempest generate`). |
| [Docker: compose and Dockerfile](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/docker.md) | Use to generate `docker-compose.yaml` from the extras, regenerate it in an existing project and containerize the app. |
| [Serve, console and database](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/run.md) | Use to run the service (`tempest serve`), open the console (`tempest shell`) and operate the database (`tempest db`). |
| [Users and secrets](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/users-secrets.md) | Use to create and manage users (MFA included) and to generate, fill in and rotate the `.env` secrets (`tempest user`, `tempest secrets`). |
| [Queue, email, storage, flags, cache, models and doctor](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/ops.md) | Use to operate the infrastructure from the command line: queue and tasks, email test, object store, feature flags, cache, models and diagnostics. |
| [Routes, OpenAPI errors and permissions](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/routes-openapi.md) | Use to list routes, bundle a route for an AI, document errors in OpenAPI and audit permission guards. |
| [Integrations, agents and the OpenAPI contract](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/integrations.md) | Use to generate integrations and clients, simulate Mercado Pago, operate agents, export the OpenAPI contract and write a PR description with AI. |
| [Quality gates and `tempest check`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/quality.md) | Use to run the quality gates and know which SDK steps `tempest check` runs. |
| [Layer rules: `tempest lint-layers`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/lint-layers.md) | Look up every architecture rule `tempest lint-layers` enforces, with its code and how to fix it. |
| [Schema metadata: `tempest lint-schemas`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/cli/lint-schemas.md) | Use to demand `title`, `description` and `examples` on the schema fields that reach `openapi.json`. |

## Recap

`tempest` covers the whole cycle — from scaffolding (`new`),
through infra (`generate --docker` / `--dockerfile` / `--src`), migrations
(`db`), users (`user`) and secrets (`secrets`), to the quality gates
(`lint` / `fix` / `type` / `test` / `check`), with the SDK's layer rules (`lint-layers`) inside `check`.

## Next steps

Once the service is generated, head to the related recipes:

- [Database »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/database.md) — `BaseRepository`, async sessions and the
  migration flow behind `tempest db`.
- [Queue and tasks »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/queue-tasks.md) — the layers `tempest generate
  --src` writes for the `[queue]` (FastStream) and `[tasks]` (TaskIQ)
  extras.
- [Safe deploys »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/deploy-safety.md) — destructive migrations and graceful
  shutdown when containerizing with the generated `Dockerfile`.
