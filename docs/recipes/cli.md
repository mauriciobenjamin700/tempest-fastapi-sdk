---
description: "Índice da CLI `tempest` (extras `[cli]` e `[cli-ops]`): criar serviço e rodar gates de qualidade; mapeia cada tarefa de linha de comando à sua subpágina."
---

# CLI


Instalar o `tempest-fastapi-sdk[cli]` (ou o `[cli-ops]`, no servidor) expõe um console script `tempest`. Ele faz dois trabalhos: criar um novo serviço em camadas a partir do esqueleto preferido do SDK e rodar os quatro gates de qualidade (`ruff check`, `ruff format`, `mypy`, `pytest`) sem copiar e colar os mesmos comandos em cada projeto.

```bash
tempest --help                                  # lista todos os comandos
tempest --version                               # mostra a versão do SDK
```

!!! info "A CLI é o extra `[cli]` (desde a 0.308.0)"
    O `tempest` mora num extra, e não no pacote base. Em desenvolvimento,
    instale do jeito que você usa (para o servidor, veja
    [`[cli-ops]`](cli/new-service.md#servidor-x-desenvolvimento-cli-ops-e-cli)):

    ```bash
    uv tool install "tempest-fastapi-sdk[cli]"   # comando global
    uv add --dev "tempest-fastapi-sdk[cli]"      # no grupo dev do projeto
    ```

    O motivo é o `ruff`: o gate (`tempest check`) vem do `tempest-cli`, que
    exige `ruff>=0.8.0` em runtime. No pacote base, todo serviço que importa
    o SDK levava um formatador para produção, e um projeto que fixa
    `ruff<0.8` no grupo dev nem conseguia resolver o lock.

    Sem nenhum dos dois extras, o script continua instalado e explica o que
    falta, com saída 2 e sem traceback:

    ```text
    $ tempest --help
    error: missing typer, click. The tempest CLI needs the optional [cli-ops] or [cli] extra. Install it with:
      uv add "tempest-fastapi-sdk[cli-ops]"   (on a server, without ruff)
      uv add --dev "tempest-fastapi-sdk[cli]"   (in development, with the gate)
      uv tool install "tempest-fastapi-sdk[cli]"   (as a global command)
    ```

    `import tempest_fastapi_sdk` não precisa de extra nenhum; ler
    `tempest_fastapi_sdk.cli.app` (ou importar `tempest_fastapi_sdk.cli.main`)
    sem o `typer` levanta `ImportError` com essa instrução. Importar
    `.lint` ou `.pr_prompt`, ou ler `TempestConfig` /
    `load_tempest_config` de `tempest_fastapi_sdk.cli.config`, sem o
    `tempest-cli` levanta `ImportError` apontando o `[cli]`.

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

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [Instalar e gerar um serviço](cli/new-service.md) | Use para escolher entre `[cli-ops]` e `[cli]`, gerar um serviço novo e trazer um projeto existente ao padrão (`tempest new`, `tempest generate`). |
| [Docker: compose e Dockerfile](cli/docker.md) | Use para gerar o `docker-compose.yaml` a partir dos extras, regerá-lo num projeto existente e containerizar a app. |
| [Subir, console e banco](cli/run.md) | Use para subir o serviço (`tempest serve`), abrir o console (`tempest shell`) e operar o banco (`tempest db`). |
| [Usuários e segredos](cli/users-secrets.md) | Use para criar e administrar usuários (inclusive MFA) e gerar, preencher e girar os segredos do `.env` (`tempest user`, `tempest secrets`). |
| [Fila, e-mail, storage, flags, cache, modelos e doctor](cli/ops.md) | Use para operar a infraestrutura pela linha de comando: fila e tasks, teste de e-mail, object store, feature flags, cache, modelos e diagnóstico. |
| [Rotas, erros do OpenAPI e permissões](cli/routes-openapi.md) | Use para listar rotas, empacotar uma rota para a IA, documentar erros no OpenAPI e auditar guards de permissão. |
| [Integrações, agentes e contrato OpenAPI](cli/integrations.md) | Use para gerar integrações e clientes, simular o Mercado Pago, operar agentes, exportar o contrato OpenAPI e gerar descrição de PR com IA. |
| [Gates de qualidade e `tempest check`](cli/quality.md) | Use para rodar os gates de qualidade e saber quais passos do SDK o `tempest check` executa. |
| [Regras de camada: `tempest lint-layers`](cli/lint-layers.md) | Consulte cada regra de arquitetura que o `tempest lint-layers` cobra, com o código e como corrigir. |
| [Metadado de schema: `tempest lint-schemas`](cli/lint-schemas.md) | Use para cobrar `title`, `description` e `examples` nos campos dos schemas que chegam ao `openapi.json`. |

## Recap

O `tempest` cobre o ciclo inteiro — do scaffold (`new`),
passando pela infra (`generate --docker` / `--dockerfile` / `--src`),
migrações (`db`), usuários (`user`) e segredos (`secrets`), até os gates
de qualidade (`lint` / `fix` / `type` / `test` / `check`), com as regras de camada do SDK (`lint-layers`) dentro do `check`.

## Próximos passos

Depois de gerar o serviço, siga para as receitas relacionadas:

- [Banco de dados »](database.md) — `BaseRepository`, sessões async e o
  fluxo de migrações por trás do `tempest db`.
- [Filas e tarefas »](queue-tasks.md) — as camadas que `tempest generate
  --src` gera para os extras `[queue]` (FastStream) e `[tasks]` (TaskIQ).
- [Deploy seguro »](deploy-safety.md) — migrações destrutivas e shutdown
  gracioso ao containerizar com o `Dockerfile` gerado.
