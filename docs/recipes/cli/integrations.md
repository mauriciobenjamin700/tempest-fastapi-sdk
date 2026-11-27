---
description: "Use para gerar integrações e clientes OpenAPI, simular o Mercado Pago, operar agentes, exportar o contrato OpenAPI e gerar descrição de PR com `tempest pr-prompt`."
---

# Integrações, agentes e contrato OpenAPI

Use para gerar integrações e clientes, simular o Mercado Pago, operar agentes, exportar o contrato OpenAPI e gerar descrição de PR com IA.

Parte da receita [CLI](../cli.md).

## Integrações — `tempest integrations`

```bash
tempest integrations list                    # o que o SDK ships + estado da credencial aqui
tempest integrations verify openpix          # uma chamada autenticada de verdade
tempest integrations verify mercado-pago
tempest integrations verify zap --base-url http://localhost:3000
```

Num projeto recém-saído do `tempest new`, sem credencial nenhuma no `.env`:

```text
openpix       tempest_fastapi_sdk.integrations.payment.openpix:OpenPixClient
              OPENPIX_APP_ID not set
mercado-pago  tempest_fastapi_sdk.integrations.payment.mercado_pago:MercadoPagoClient
              MERCADOPAGO_ACCESS_TOKEN not set
stripe        tempest_fastapi_sdk.integrations.payment.stripe:StripeClient
              no settings mixin (build the client yourself)
zap           tempest_fastapi_sdk.integrations.messaging.zap:ZapClient
              no settings mixin (build the client yourself)
```

O `verify` monta o cliente com `HTTPClient(**settings.<provedor>_kwargs())` e
faz **a leitura autenticada mais barata** que cada API oferece: `GET /company`
na OpenPix e `GET /users/me` no Mercado Pago — nenhuma das duas move dinheiro
nem cria registro. Credencial errada sai com código 1 carregando a recusa do
provedor.

!!! note "Esse endpoint é tabela mantida à mão — com guard"
    Escolher "a chamada mais barata" de cada provedor é decisão humana, e
    nome de endpoint gerado muda. Um teste assere que cada método nomeado
    ainda existe no cliente gerado **e** que não exige argumento, então
    renomeação upstream quebra um teste em vez do terminal de quem está de
    plantão.

Os dois provedores sem mixin de settings no SDK se comportam diferente:

- **zap** precisa de `--base-url`, porque o SDK não sabe onde a sua
  instância mora. O `verify` chama `health`, que não pede credencial, então
  ele mede **alcance**, não autorização. Sem `--base-url` sai com código 2;
  instância fora do ar sai com código 1:

    ```text
    error: zap has no settings mixin, so the CLI does not know where it lives. Pass --base-url.
    error: zap refused the call — ConnectTimeout:
    ```

- **stripe** não tem `verify`: a API não oferece leitura autenticada que não
  gaste dinheiro nem crie registro. O comando sai com código 2, com ou sem
  `--base-url`:

    ```text
    error: stripe exposes no cheap authenticated read, so there is nothing the CLI can call without spending money or creating a record. Construct the client in your service.
    ```

## Mercado Pago simulado — `tempest mercado-pago`

```bash
tempest mercado-pago fake-api                # API de Orders simulada em 127.0.0.1:8090
tempest mercado-pago simulate-webhook --fake-api http://127.0.0.1:8090 \
    --order-id ORDFAKE… --transition pay     # move a order e entrega a notificação assinada
```

O `fake-api` serve a `FakeMercadoPagoOrdersAPI` (estado em memória); o
serviço aponta para ela com `MERCADOPAGO_BASE_URL=http://127.0.0.1:8090`. O
`simulate-webhook` assina a notificação com o segredo lido de
`--secret-env` (default `MERCADOPAGO_WEBHOOK_SECRET`) ou `--secret-file` —
nunca de um argumento —, posta na rota (`--url`, default
`http://127.0.0.1:8000/webhooks/mercado-pago`) e imprime o status e o corpo
da resposta. Sai com `1` quando a rota não responde 2xx e com `2` em erro de
uso. Com `--fake-api`, o `--order-id` aceita também o `external_reference`
da criação (o `correlation_id` do serviço). Passo a passo com a saída medida:
[Mercado Pago: testando webhooks »](../mercado-pago-webhooks.md#teste-manual-o-servico-rodando-o-mercado-pago-simulado).

## Agentes — `tempest agents`

```bash
tempest agents tools --agent src.agents:agent     # que ferramentas ele tem
tempest agents run "resuma o relatório" --agent src.agents:agent --trace
```

`tools` não chama modelo nenhum — nenhum token é gasto, e funciona com
backend sem credencial configurada. É a resposta rápida para "essa
configuração de agente enxerga mesmo a ferramenta que acabei de registrar?".

!!! warning "O código de saída segue `succeeded`, não 'não levantou exceção'"
    Run cortado pelo orçamento (passos ou tempo) **ainda devolve texto**.
    Tratar esse texto como resposta é o erro que o comando se recusa a
    cometer por você: `run` sai com 1 e imprime o motivo da parada
    (`max_steps`, `max_seconds`) no `stderr`, com a saída no `stdout` do
    mesmo jeito.

Não existe layer `agents` scaffoldada, então `--agent` é praticamente sempre
necessário — o comando diz isso em vez de fingir uma convenção.

## Cliente de integração — `tempest openapi-client`

Gera schemas Pydantic + um cliente HTTP tipado a partir da especificação OpenAPI
de um sistema de terceiros:

```bash
tempest openapi-client https://api.terceiro.com/openapi.json --name terceiro
tempest openapi-client ./vendor/spec.yaml --name terceiro --force
tempest openapi-client <spec> -H "Authorization: Bearer $TOKEN" --schemas-only
```

Escreve `<src|app>/integrations/<name>/` com `schemas.py` (uma classe por
componente, com `title`/`description`/`examples` da spec preenchidos) e
`client.py` (um método `async` por operação, sobre um `HTTPClient` injetado).

```text
  + src/integrations/terceiro/__init__.py
  + src/integrations/terceiro/client.py
  + src/integrations/terceiro/schemas.py
4 schema(s), 12 operation(s).
```

Detalhes, cobertura de OpenAPI e limitações na receita
[Cliente de integração (OpenAPI) »](../openapi-client.md).

---

## Exportar o contrato — `tempest openapi-export`

O `openapi-client` consome a spec de outra pessoa; este escreve a **sua**.

```bash
# Documento no stdout
tempest openapi-export

# Arquivo (JSON por padrão)
tempest openapi-export --out contracts/openapi.json

# YAML (precisa do extra [openapi])
tempest openapi-export --out contracts/openapi.yaml --yaml
```

Duas coisas saem disso. A primeira é alimentar um gerador — o próprio
`tempest openapi-client`, um gerador de cliente TypeScript, um mock server —
**sem subir o serviço**. A segunda é commitar o documento e transformar
mudança de contrato em algo que o revisor vê:

```bash
tempest openapi-export --out contracts/openapi.json --check
```

```text
error: contracts/openapi.json is out of date:
  + DELETE /api/items/{item_id}
  - GET /api/legacy
  ~ /api/items/{item_id} changed
Run 'tempest openapi-export --out <file>' to refresh it.
```

Código 1 quando difere, 0 quando está em dia. Operação que entrou e que saiu
vêm primeiro porque são as que quebram consumidor; mudança dentro de uma
operação vira `~ <path> changed`. "A spec mudou" não é mensagem revisável —
por isso o comando nomeia o que se moveu.

!!! tip "No CI"
    Rode `--check` no mesmo job que roda os testes. O dia em que alguém
    muda um status code, um campo obrigatório ou um path, o PR fica
    vermelho com a linha exata, em vez de o cliente descobrir em produção.

## Descrição de PR com IA — `tempest pr-prompt`

A branch ficou verde e falta a parte que todo mundo pula: escrever a descrição
do Pull Request. Qualquer assistente escreve uma boa — o que falta pra ele são
as duas coisas que moram no repositório: o **template** que o time combinou e o
**diff** que a branch realmente produziu.

`tempest pr-prompt` monta os dois num prompt só e joga em `stdout`, então ele
pipa direto pra IA que você usa:

```bash
tempest pr-prompt                               # compara com main, prompt no stdout
tempest pr-prompt develop                       # outra base
tempest pr-prompt | claude -p                   # pipa direto pro assistente
tempest pr-prompt --out pr_prompt.txt           # grava num arquivo pra colar
```

O prompt carrega três blocos:

1. **O template do PR.** O do próprio repositório vence — é o contrato que os
   revisores daquele projeto leem. São procurados, nessa ordem:
   `.github/pull_request_template.md`, `.github/PULL_REQUEST_TEMPLATE.md`,
   `.github/PULL_REQUEST_TEMPLATE/pull_request_template.md`,
   `.gitlab/merge_request_templates/default.md`, `docs/pull_request_template.md`,
   `.pull_request_template.md` e `pull_request_template.md`. Sem nenhum deles,
   entra o template embutido do SDK.
2. **As regras** que impedem a IA de devolver o template com os placeholders
   dentro: nada de `Sim/Não` sem escolher, nada de `_texto em itálico_` de
   instrução, nenhuma seção apagada, e nada de inventar migration, env var ou
   dependência que não aparece no diff.
3. **O contexto da branch**: assuntos dos commits, a lista `--name-status` dos
   arquivos alterados e um trecho do patch de cada arquivo.

!!! info "O diff é o mesmo que a forge mostra"
    Os patches são lidos como `base...head` (três pontos) — o diff em relação
    ao *merge-base*, que é o que o GitHub/GitLab exibe no PR. Com dois pontos,
    todo commit que entrou na `base` depois que sua branch nasceu seria
    atribuído a você.

!!! info "O que é completo e o que é amostrado"
    A lista de **commits** e a lista de **arquivos alterados** vão inteiras,
    sempre — a IA sempre sabe *o que* mudou. O que é limitado são os **trechos
    de diff**: por padrão os 10 arquivos com mais linhas alteradas, cada um
    cortado em 1500 caracteres. Ou seja, o que é amostrado é *como* mudou.

Os trechos são limitados porque o diff inteiro de uma branch grande não cabe
no contexto e é quase todo ruído (lock file, changelog, migration gerada). Os
arquivos entram **ordenados por linhas alteradas**, não em ordem alfabética —
senão o orçamento vai embora em `.github/` e `CHANGELOG.md` antes de chegar no
arquivo sobre o qual o PR é:

```bash
tempest pr-prompt --full                        # todos os arquivos, patch inteiro
tempest pr-prompt --max-files 20                # 20 arquivos com patch (default: 10)
tempest pr-prompt --max-files 0                 # só a lista de arquivos, sem patch
tempest pr-prompt --max-chars 4000              # mais patch por arquivo (default: 1500)
```

`--full` levanta os dois limites de uma vez — use numa branch pequena o
bastante pra mandar inteira. Ele **recusa** vir junto com `--max-files` /
`--max-chars` (saída `2`): sobrescrever em silêncio um número que você digitou
seria pior que reclamar.

!!! check "Nada é cortado em silêncio"
    Um patch truncado leva a marca `trecho cortado` e os arquivos que ficaram
    sem trecho viram uma linha explícita no prompt (`Mais N arquivo(s)
    alterado(s)…`). A IA lê um contexto parcial como parcial, em vez de tratar
    o pedaço como a mudança inteira. O corte também respeita a quebra de linha,
    pra não sobrar meia linha de diff — que a IA leria como código que não
    existe.

O resumo (template escolhido, contagem de commits/arquivos) sai em `stderr`,
então o pipe continua limpo:

```text
template: .github/pull_request_template.md
7 commit(s), 12 changed file(s), 10 excerpt(s), 2 file(s) without a patch.
```

Outras opções:

```bash
tempest pr-prompt --head feat/outra             # descreve outra branch, sem checkout
tempest pr-prompt --lang en                     # regras e template embutido em inglês
tempest pr-prompt -t docs/meu_template.md       # template explícito, vence todos
tempest pr-prompt -p ../outro-repo              # roda contra outro repositório
```

!!! tip "Base inexistente vira `origin/<base>`"
    Num clone recém-feito costuma não existir uma `main` local, só
    `origin/main`. Quando a base não resolve, o comando tenta
    `origin/<base>` antes de falhar. Se nem isso existir, ele sai com código
    `2` e diz qual ref não encontrou. Quando a comparação não tem nem commit
    nem arquivo, a saída é código `1` — quase sempre a base está errada.
