---
description: "Use para operar a infraestrutura pela CLI: fila e tasks, teste de e-mail, object store, feature flags, cache, modelos e diagnóstico com `tempest doctor`."
---

# Fila, e-mail, storage, flags, cache, modelos e doctor

Use para operar a infraestrutura pela linha de comando: fila e tasks, teste de e-mail, object store, feature flags, cache, modelos e diagnóstico.

Parte da receita [CLI](../cli.md).

## Fila e tasks — `tempest queue` / `tempest tasks`

```bash
# Publica uma mensagem pelo broker do projeto
tempest queue publish orders.paid '{"order_id": 7}' --json
tempest queue handlers                     # canais que este serviço consome

# Tasks de fundo
tempest tasks list                         # nomes registrados
tempest tasks run src.tasks:send_welcome --arg ana@example.com --kwarg retries=2
```

O `publish` conecta, publica e **fecha** — nessa ordem, num processo só.
Fechar é o passo que o script descartável esquece, e é ele que garante o
flush da publicação antes do comando retornar.

!!! tip "O nome da task não é o nome da função"
    O TaskIQ registra sob `<módulo>:<função>` (`src.tasks:send_welcome`), então
    o nome que o `run` recebe não é o que está no arquivo. O `list` é o que diz
    qual é qual — e ele importa `<root>.tasks.jobs` antes de listar, porque
    task só existe no broker depois que o módulo que a declara foi importado.

`tasks run` **enfileira**, não executa: quem roda é o worker. Comando que
retorna com sucesso significa "a mensagem foi aceita", não "o job deu certo".

Sem `--broker` / `--queue`, os dois procuram `<root>.queue:broker` e
`<root>.tasks:tq` — os nomes que o `tempest generate --src` escreve. Quando
nada resolve, cada tentativa é listada com a causa antes do código 2.

## E-mail — `tempest email test`

O par que mais custa tempo em configuração de SMTP é STARTTLS (porta 587,
`SMTP_USE_TLS`) contra TLS implícito (porta 465, `SMTP_USE_SSL`), e o único
jeito honesto de saber qual o servidor quer é conectar e mandar.

```bash
tempest email test --to voce@example.com
tempest email test --to voce@example.com --subject "deploy ok" --body "..."
tempest email test --to voce@example.com --html      # exercita o caminho multipart
```

Monta o cliente com `EmailUtils(**settings.email_kwargs())` — a mesma
construção que o serviço faz —, então mensagem que chega aqui chega pela
app. Recusa do servidor sai com código 1 carregando a resposta dele, mais a
linha que lembra o par TLS.

Projeto que não compõe `EmailSettings` sai com código 2 dizendo isso: compor
só os mixins que se usa é o normal, não defeito.

## Object store — `tempest storage`

```bash
tempest storage check                       # endpoint responde? bucket existe?
tempest storage ls                          # chaves do bucket default
tempest storage ls relatorios/ --flat       # só o nível imediato
tempest storage put ./nota.pdf --key notas/2026-09.pdf
tempest storage get notas/2026-09.pdf --out ./baixado.pdf
tempest storage presign notas/2026-09.pdf --expires 900
tempest storage rm notas/2026-09.pdf
```

Tudo passa pelo `AsyncMinIOClient(**settings.storage_kwargs())`, então
endpoint, credencial, região e — o que mais importa — o **endpoint público**
usado para assinar URL são os do serviço.

!!! warning "URL assinada com o endpoint interno é válida e inútil"
    Assinar contra `minio:9000` produz uma URL correta que o browser do
    usuário não resolve. Configure `STORAGE_PUBLIC_ENDPOINT`; o `presign`
    imprime, no `stderr`, para qual host ele assinou, justamente para esse
    erro aparecer antes de ir para o cliente.

Bucket vazio lista nada e sai com 0 — "nenhum objeto" é resultado, não falha,
a mesma convenção dos repositories do SDK.

Dois comandos agem sobre o backend local (`STORAGE_BACKEND=local`), pelo
`LocalObjectStorageClient(**settings.local_storage_kwargs())`, e migram o
volume para a [cifra em repouso](../local-storage.md#cifra-em-repouso):

```bash
tempest storage encrypt-local               # cifra o que está em claro, em todo bucket
tempest storage reencrypt-local --bucket uploads   # passa para a primeira chave
```

Os dois são idempotentes e saem com código 2 sem
`STORAGE_LOCAL_ENCRYPTION_KEYS`.

## Feature flags — `tempest flags`

Flag existe para ser movida sem deploy. Com o backend Redis, essa mudança
morava num `redis-cli HSET feature_flags <nome> 1` — o tipo de comando que
sai com um dígito errado na hora errada.

```bash
tempest flags list                       # tudo que o backend guarda
tempest flags enable new-checkout        # liga para todo processo que lê esse backend
tempest flags disable new-checkout
tempest flags get new-checkout           # estado guardado
tempest flags list --key ff:staging      # outro hash, outro ambiente
```

```text
new-checkout  on
legacy-upload off
```

Os quatro falam pelo `RedisFeatureFlagBackend`, então o valor que o serviço
lê e o que a CLI escreve passam pelo mesmo código de codificação.

!!! note "`off` e `unset` são respostas diferentes"
    `get` sai com **1** quando a flag não existe no backend (`unset`), e com
    **0** quando ela existe valendo `off`. A diferença importa: flag não
    definida resolve para o *default* que o chamador passou no código; flag
    em `off` sobrescreve esse default.

A URL sai de `--redis-url` > `REDIS_URL` > settings do projeto. Redis fora do
ar sai com código 2 carregando a mensagem do driver — "connection refused"
diz o que consertar, "ocorreu um erro" não.

## Cache — `tempest cache`

```bash
tempest cache ping                       # PONG, ou código 1
tempest cache stats                      # versão, chaves, memória, hit ratio
tempest cache flush --namespace orders   # invalida o que o @cached registrou
tempest cache flush --tag user:42
tempest cache flush --key "cache:orders:list:abc"
tempest cache flush --all --yes          # FLUSHDB
```

As opções direcionadas passam pelo `CacheInvalidator` — o mesmo código que o
serviço chama —, então as chaves apagadas aqui são as que o `@cached`
escreveu lá. Passe o mesmo `--key-prefix` que os decorators usam, senão os
registries não batem.

!!! danger "`--all` é `FLUSHDB`, não 'limpar o cache'"
    Apaga **toda** chave do banco selecionado: sessão, contador de rate limit
    e feature flag junto, quando dividem o mesmo Redis. Por isso exige
    `--yes`. Na dúvida, invalide por namespace.

!!! info "O hit ratio é do servidor, não da sua aplicação"
    `keyspace_hits` / `keyspace_misses` são contadores do Redis desde o último
    restart, somando todos os clientes. O Redis não atribui hit a chamador —
    o número responde "esse servidor está sendo útil", não "meu endpoint X
    está".

## Modelos — `tempest model`

Análise, benchmark, conversão e quantização de modelos ONNX. Precisa do
extra `[modelops-onnx]`, exceto `hardware`, que só reporta o que esta
máquina consegue medir:

```bash
tempest model analyze models/classify.onnx
tempest model bench models/classify.onnx --dim height=224 --dim width=224
tempest model quantize models/classify.onnx models/classify.int8.onnx
tempest model export-ort models/classify.int8.onnx -o dist/mobile -t arm
tempest model hardware
```

| Comando | O que faz |
| --- | --- |
| `analyze` | Parâmetros, tamanho, opset e shapes, sem executar o modelo. |
| `bench` | Latência (mediana/IQR/p95/p99), RAM, GPU e energia sobre N repetições. |
| `optimize` | Persiste as otimizações de grafo do ONNX Runtime num novo `.onnx`. |
| `quantize` | Quantização dinâmica int8. |
| `export-ort` | Converte para `.ort` + `.required_operators.config`. |
| `hardware` | O que a máquina roda e qual sampler de energia está disponível. |

`analyze`, `bench` e `hardware` aceitam `--json`, o que os torna
utilizáveis como passo de CI. Extra ausente sai com código 2 e a linha de
instalação, nunca com traceback. Detalhes em
[Modelops](../modelops.md).

## Diagnóstico — `tempest doctor`

O `check-config` lê as settings e raciocina sobre elas; não abre socket
nenhum. Isso é a troca certa para um gate rápido e a errada para "por que o
serviço não sobe", cuja resposta quase sempre é uma dependência fora do ar,
inalcançável deste host, ou com credencial que ninguém atualizou.

```bash
tempest doctor
tempest doctor --json            # pra script / healthcheck de deploy
tempest doctor --timeout 2       # aperta a espera de cada sonda de rede
```

```text
OK    python         3.11.12 (/srv/app/.venv/bin/python)
OK    sdk            tempest-fastapi-sdk 0.293.0
OK    settings       Settings
OK    database       postgresql+asyncpg://app:***@db:5432/app
FAIL  redis          ConnectionError: Error 111 connecting to cache:6379
SKIP  rabbitmq       no RABBITMQ_URL
SKIP  smtp           SMTP_HOST is still the mixin default
SKIP  minio          the [minio] extra is not installed
OK    config checks  no error (2 warning(s))
```

Cada linha é uma **conexão de verdade**, não uma dedução. Sai com código 1
quando qualquer checagem falha, então serve de smoke test de deploy.

!!! warning "`skip` não é `ok`, de propósito"
    Capacidade que o projeto não configurou aparece como `skip`. Linha verde
    para um Redis que nunca foi montado é o tipo de tranquilidade que custa um
    incidente.

    O default do mixin conta como "não configurado": o `EmailSettings` nasce
    com `SMTP_HOST=localhost` e o `StorageSettings` com
    `STORAGE_ENDPOINT=localhost:9000`, então o serviço que compõe o mixin sem
    usar a capacidade pareceria configurado. A comparação é contra
    `model_fields[campo].default` — a mesma régua do `check_secrets`.

A última linha roda o registry de checagens estáticas (o mesmo do
`check-config`), então um comando responde as duas metades de "esse deploy
está sadio".
