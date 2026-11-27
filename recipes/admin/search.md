# Busca global

Use para buscar um termo em todo model com `search_fields` pela caixa de busca no topo do painel.

Parte da receita [Painel admin](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin.md).

Quando algum model que o operador vê tem `search_fields`, o topo do painel
ganha uma caixa de busca. Ela vai para `GET /admin/search?q=`, que procura
o termo em **todo** model com `search_fields`, com a mesma condição da
busca da listagem (cada palavra num `ILIKE`, em qualquer um dos campos), e
mostra até `ADMIN_SEARCH_RESULTS_PER_MODEL` (5) linhas por model, cada uma
com link para o detail. Quando há mais, o grupo leva **Ver todos** para a
listagem do model já filtrada com `?q=`. O limite é ajustável:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import UserModelAuthBackend, make_admin_router

from src.admin import site
from src.api.dependencies.resources import db
from src.core.settings import settings
from src.db.models import UserModel

app = FastAPI()

app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key=settings.ADMIN_SECRET,
        search_results_per_model=10,
    ),
)
```

A busca respeita o que o resto do painel respeita: model sem `VIEW` na
`access_policy` não entra, e cada model é consultado com o próprio
`row_scope` — uma linha fora do escopo não aparece. Model com
`repository_class` é consultado pelo `paginate` do repositório (que roda um
`COUNT` por model), com a página limitada ao `max_page_size` dele. Termo vazio
(ou só espaços) mostra a página sem rodar consulta, e termo com mais de
`ADMIN_SEARCH_MAX_QUERY_LENGTH` (200) caracteres ou mais de
`ADMIN_SEARCH_MAX_TERMS` (8) palavras responde `400` com o aviso, também
sem consulta — cada palavra vira um `ILIKE` por campo de cada model.
