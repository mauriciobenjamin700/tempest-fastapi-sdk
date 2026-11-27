# Rotas, erros do OpenAPI e permissões

Use para listar rotas, empacotar uma rota para a IA, documentar erros no OpenAPI e auditar guards de permissão.

Parte da receita [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/cli.md).

## Rotas — `tempest routes`

Lista as URLs que a aplicação **de fato** serve.

```bash
tempest routes
```

```text
METHOD  PATH                    NAME          SCHEMA  GUARDS
GET     /api/items/{item_id}    read_item     yes     require_admin
POST    /api/items/             create_item   yes     require_admin
GET     /health                 health        yes     -
GET     /painel                 painel        no      -
```

Quatro coisas nessa tabela que uma leitura de código não te dá de graça:

- **Router montado aparece.** Desde o FastAPI 0.141.1, o `include_router`
  guarda uma entrada `_IncludedRouter` em vez de achatar as rotas na
  aplicação — então `{r.path for r in app.routes}` não contém **nenhum**
  path do router incluído. O comando lê a mesma expansão que o gerador de
  OpenAPI percorre.
- **Prefixo já resolvido.** O path é o do router mais o de cada
  `include_router` acima dele.
- **Rota fora do schema também aparece**, com `SCHEMA=no` — página HTML e
  endpoint interno servem tráfego, e é justamente o que uma resposta tirada
  do `openapi.json` esconde.
- **`GUARDS` mostra a dependência herdada do include**, não só a declarada
  na rota. É como se confere que o `Depends(require_admin)` do
  `include_router` alcançou tudo que devia.

Filtros e formato:

```bash
tempest routes --match /api/orders        # só paths com esse trecho
tempest routes --method post,delete       # só esses métodos
tempest routes --all                      # inclui /docs, /redoc, /openapi.json
tempest routes --json                     # array JSON, pra script
tempest routes --app src.server:app       # app fora dos lugares convencionais
```

Sem `--app`, o comando procura o app em `src.server:app`,
`src.api.app:create_app`, `main:app` (e os mesmos sob `app/`). Quando nada
resolve, ele **lista o que tentou e por quê** antes de sair com código 2.
`--match` que não casa com nada sai com código 1, pra typo em script falhar
em vez de parecer aplicação sem rota.

## Empacotar uma rota para a IA — `tempest bundle-route`

Um endpoint deu bug e você quer perguntar pro GPT, pro Gemini ou pro Claude.
A pergunta só tem resposta boa se a IA enxergar o caminho inteiro da
requisição: o router, a dependência que monta o controller, o controller, o
service, o repository, o model e os schemas. Colar isso à mão é abrir oito
arquivos — e quase sempre falta justamente aquele onde o bug mora.

`tempest bundle-route` monta esse pacote pra você, em markdown:

```bash
tempest new demo
cd demo
cp .env.example .env
tempest bundle-route "GET /api/"
```

O começo da saída, medido num projeto recém-gerado:

```text
# Route bundle: `GET /api/`

- **Method:** `GET`
- **Path:** `/api/`
- **Endpoint:** `index`
- **Dependencies:** `get_service_info_controller`, `get_settings`

## Files (6 files, 277 lines)

| Layer | File | Lines |
| --- | --- | --- |
| router | `src/api/routers/service_info.py` | 30 |
| dependencies | `src/api/dependencies/controllers.py` | 37 |
| controllers | `src/controllers/system/service_info.py` | 40 |
| schemas | `src/schemas/service_info.py` | 23 |
| core | `src/core/settings.py` | 117 |
| other | `src/scripts/composition.py` | 30 |
```

Depois da tabela vem cada arquivo **inteiro**, numa seção `` ## `caminho` ``
com um bloco `python`, na mesma ordem. Cole tudo no chat e escreva a pergunta
embaixo.

Num domínio de verdade o pacote cresce do jeito que você espera: com o
domínio `products` que o `CLAUDE.md` gerado ensina a escrever,
`tempest bundle-route "GET /api/products"` deu **15 arquivos, 635 linhas** —
router, `api/dependencies/controllers.py` e `resources.py`, controller,
service, repository, model, schema, `core/constants/messages/errors.py`,
`core/exceptions/conflict.py` e `not_found.py`, `core/settings.py`,
`db/configs/connection.py` e `names.py` e `scripts/composition.py`.
Nenhum arquivo do `service_info`.

O resumo sai em `stderr`, então o `stdout` é só o markdown:

```text
GET /api/: 6 file(s), 277 line(s). The bundle is your source code: review it before pasting it anywhere.
```

### Como ele acha os arquivos

1. **A rota é resolvida no app vivo**, pela mesma expansão do
   `tempest routes` — prefixo do `include_router` já aplicado. O ponto de
   partida é o endpoint e **toda** a árvore de `Depends`, inclusive a
   dependência herdada do `include_router`.
2. **Daí em diante, tudo é estático.** Cada módulo do projeto é lido com
   `ast`; nada além do próprio app é importado ou executado.
3. **O caminho é por símbolo, não por módulo.** O scaffold manda importar do
   pacote (`from src.services import ProductService`), e cada `__init__.py`
   re-exporta todos os domínios. Seguir o módulo inteiro puxaria o projeto
   todo. Em vez disso, o nome é seguido pelo re-export até o arquivo que o
   **define**, e só os nomes que aquela definição usa são seguidos depois.
4. **O arquivo entra inteiro** assim que uma definição dele é alcançada. É
   por isso que o `api/dependencies/controllers.py` aparece com os providers
   dos outros domínios dentro — mas os controllers desses domínios não.
   Um `__init__.py` atravessado só como re-export não entra.

A ordem é por camada: router, dependencies, controllers, services,
repositories, models, schemas, core, e o resto. O arquivo do endpoint vem
sempre primeiro.

!!! warning "O pacote é o seu código-fonte"
    O comando só lê arquivos `.py` dos pacotes do projeto (`src/` ou `app/`)
    dentro da raiz — **nunca** o `.env`, a biblioteca padrão, o
    `site-packages`, uma `.venv` dentro do projeto, nem o próprio SDK. Mas o
    que ele lê é o seu código, inteiro, e pode ser proprietário. Valor
    escrito direto no código (um default de `Settings`, uma chave de teste)
    vai junto. Releia antes de colar num serviço de terceiro: quem decide
    onde o código vai é você.

### Escolher a rota

O argumento é `"MÉTODO /path"`, com o path que o `tempest routes` imprime. O
método não diferencia maiúscula de minúscula. Sem método, o path precisa ter
um só:

```bash
tempest bundle-route "GET /api/products"
tempest bundle-route /api/                               # só um método serve esse path
tempest bundle-route "get /api/"                         # método em minúscula também vale
```

Quando a escolha não é única, o comando para em vez de adivinhar:

- **Path com vários métodos e nenhum nomeado** sai com código `2` e lista os
  métodos: `tempest bundle-route /api/products`, no domínio da receita, responde `error: /api/products is served by several methods: GET, POST. Name one, e.g. 'GET /api/products'.`
- **Rota que não existe** sai com código `1` e sugere as parecidas:

```text
error: no route matches 'GET /health'.
  did you mean: GET /health/liveness
  did you mean: GET /health/readiness
  did you mean: GET /api/
  did you mean: GET /redoc
  did you mean: GET /openapi.json
  run `tempest routes` to list every route.
```

- **Rota servida por código instalado** (o `/health/liveness` do SDK, por
  exemplo) não alcança nenhum arquivo do projeto e sai com código `1`.

### Opções

```bash
tempest bundle-route "GET /api/" --output bug.md         # grava no arquivo, stdout vazio
tempest bundle-route "GET /api/" --max-depth 0           # só os arquivos que definem endpoint e dependências
tempest bundle-route "GET /api/" --app src.server:app    # app fora dos lugares convencionais
```

`--max-depth` conta os saltos a partir do endpoint e das dependências. Com
`0`, a mesma rota `GET /api/` cai para **3 arquivos, 184 linhas** (router,
`api/dependencies/controllers.py` e `core/settings.py`, onde mora o
`get_settings`). Sem a opção, tudo que é alcançável entra.

!!! tip "O app precisa importar"
    Como no `tempest routes`, o comando importa o app do projeto, então o
    `.env` (ou as variáveis de ambiente) precisa estar lá — por isso o
    `cp .env.example .env` no exemplo. Quando o import falha, ele lista o que
    tentou e por quê, e sai com código `2`.

### Recap

- `tempest bundle-route "MÉTODO /path"` junta o código de uma rota num
  markdown pronto pra colar num chat de IA.
- A rota sai do app vivo; o código é seguido por símbolo, estaticamente,
  sem atravessar para outros domínios.
- Só `.py` do projeto, nunca `.env` nem pacote instalado — e mesmo assim é o
  seu código: revise antes de colar.

## Erros documentados no OpenAPI — `tempest openapi-errors`

Compara, por rota, as `AppException` que o fluxo pode levantar com o que a rota
declarou em `error_responses(...)` / `@raises(...)`:

```bash
tempest openapi-errors                          # relatório informativo (exit 0)
tempest openapi-errors --check                  # exit 1 quando há drift (gate de CI)
tempest openapi-errors --path src --path libs   # diretórios explícitos, repetível
tempest openapi-errors --check --allow-unreachable   # só falha em undocumented
tempest openapi-errors --fix --dry-run          # diff das declarações faltantes
tempest openapi-errors --fix                    # grava (exige árvore git limpa)
```

Sem `--path`, varre `./src` ou `./app` — o que existir. A análise é estática
(`ast`, sem importar a aplicação) e percorre `router -> controller -> service ->
repository`, lendo os `raise` **e** as seções `Raises:` das docstrings.

```text
src/api/routers/jobs.py:15  POST /{service_id}/candidates
  undocumented: CandidateAlreadyExistsException, ServiceFullException
1 route(s) with drift, 2 undocumented exception(s).
```

`--fix` fecha o buraco escrevendo `responses=error_responses(...)` na rota (mais
os imports que faltarem), estendendo a declaração que já existir em vez de
substituí-la. Ele só acrescenta: findings `unreachable` nunca são removidos, já
que alcançabilidade não enxerga raise dinâmico.

Detalhes e limitações na receita [Erros no OpenAPI »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/openapi-errors.md).

---

## Guards de permissão — `tempest permissions`

Lê o contrato dos guards do `@requires` direto do código-fonte (`ast`, sem
importar a aplicação) e reporta o que o decorator não consegue ver em tempo de
import:

```bash
tempest permissions                    # relatório informativo (exit 0)
tempest permissions --check            # exit 1 se houver erro (gate de CI)
tempest permissions --check --strict   # falha também nos warnings
tempest permissions --path src --path libs
```

```text
src/api/routers/orders.py:41  delete_order
  error: guard-foreign-exception: guard 'order_owner' raises ValueError, which is
    not an AppException subclass; the API layer answers it as HTTP 500 without an
    error code
2 finding(s), 1 error(s).
```

Erros: guard que levanta fora da hierarquia `AppException`, guard-predicado
(`-> bool`), aridade errada, guard `async` em função sync, rota sem parâmetro de
usuário. Warnings: guard que nunca nega, anotação faltando, guard que o checker
não conseguiu resolver (lambda, nome duplicado, definição fora do escopo).

Detalhes na receita [Guards de permissão (`@requires`) »](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/permission-guards.md).
