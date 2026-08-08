# Busca textual (LIKE + full-text)

Duas camadas, porque respondem a perguntas diferentes. A portátil se
comporta **igual** no PostgreSQL e no SQLite e não precisa de índice,
extensão nem migration. A de full-text usa o motor do PostgreSQL — com
radicalização, peso por campo e nota de relevância — e degrada para a
primeira onde esse motor não existe.

## Camada portátil: `search()`

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def find(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Busca o termo em duas colunas.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.

    Returns:
        list[ArticleModel]: Os artigos encontrados.
    """
    return await articles.search("nota fiscal", fields=["title", "body"])
```

Cada palavra do termo é procurada, sem diferenciar maiúsculas, em **todas**
as colunas listadas; as palavras combinam com `AND`. Ou seja: digitar mais
palavras restringe o resultado, que é o que se espera de uma caixa de
busca. `nota` pode estar no título e `rodapé` no corpo — a linha entra
assim mesmo.

!!! check "O `%` do usuário é literal"
    `search("100%", fields=["title"])` procura o caractere `%`. Sem o
    escape, esse termo casaria com todas as linhas — o modo mais discreto
    de uma busca ficar errada.

Termo vazio não aplica filtro nenhum, então a caixa de busca vazia
**lista** em vez de esconder.

### Colunas tipadas

`fields` aceita o nome ou o próprio atributo mapeado, então dá para o
type-checker pegar uma coluna renomeada:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def by_author(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Busca pelo atributo mapeado, não pelo nome em string.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.

    Returns:
        list[ArticleModel]: Os artigos encontrados.
    """
    return await articles.search("joao", fields=[ArticleModel.author])
```

### Combinando com o resto

`search()` aceita `filters`, `where`, `order_by`, `with_` e `limit`:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def find_by_author(
    articles: BaseRepository[ArticleModel],
    author: str,
) -> list[ArticleModel]:
    """Combina busca textual com um filtro comum.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.
        author (str): O autor a filtrar.

    Returns:
        list[ArticleModel]: Os artigos encontrados.
    """
    return await articles.search(
        "nota",
        fields=["title", "body"],
        filters={"author": author},
        limit=20,
    )
```

## Paginando uma busca

`search()` devolve uma lista. Para paginar e contar, peça a **condição** e
passe em `where=` — daí a busca vira um filtro como qualquer outro:

```python
from typing import Any

from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


async def search_page(
    articles: BaseRepository[ArticleModel],
    term: str,
) -> dict[str, Any]:
    """Pagina e conta uma busca como qualquer outro filtro.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.
        term (str): O termo digitado pelo usuário.

    Returns:
        dict[str, Any]: A página, com total e itens.
    """
    return await articles.paginate(
        where=articles.search_condition(term, fields=["title", "body"]),
        page=2,
        page_size=20,
    )
```

Isso funciona porque `where=` passou a aceitar tanto um `Q` quanto uma
cláusula SQLAlchemy pronta. `count()`, `list()` e `cursor_paginate()`
aceitam a mesma coisa.

## Camada full-text: `full_text_search()`

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository, TextSearchLanguage


async def ranked(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Busca com radicalização e ordenada por relevância.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.

    Returns:
        list[ArticleModel]: Os artigos, do mais relevante ao menos.
    """
    return await articles.full_text_search(
        "nota fiscal",
        fields=["title", "body"],
        language=TextSearchLanguage.PORTUGUESE,
    )
```

No PostgreSQL isso compila para
`to_tsvector(...) @@ websearch_to_tsquery('portuguese', termo)` e o
resultado volta ordenado por `ts_rank`, do mais relevante para o menos.

O que a camada portátil não faz e esta faz:

- **Radicalização.** `comprou` encontra `comprar`.
- **Stop words.** `de`, `a`, `para` não estreitam a busca à toa.
- **Sintaxe de buscador.** `websearch_to_tsquery` aceita o que o usuário
  já digita — `"frase exata"` entre aspas e `-excluída` com hífen — e
  nunca levanta erro de sintaxe com pontuação solta, ao contrário do
  `to_tsquery`.
- **Nota de relevância**, que é o que permite ordenar.

### Peso por campo

Um termo no título deve valer mais que o mesmo termo no corpo:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository, TextSearchWeight


async def title_first(articles: BaseRepository[ArticleModel]) -> list[ArticleModel]:
    """Faz o título pesar mais que o corpo na nota de relevância.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.

    Returns:
        list[ArticleModel]: Os artigos, ordenados por relevância.
    """
    return await articles.full_text_search(
        "nota fiscal",
        fields=["title", "body"],
        weights={"title": TextSearchWeight.A},
    )
```

O PostgreSQL ordena `A` como o mais importante e `D` como o menos; campos
que você não listar ficam em `D`.

## No SQLite

`full_text_search()` continua devolvendo **as linhas certas** — ele cai na
camada portátil. O que não existe é a radicalização e o ranking. Pergunte
antes de prometer uma barra de relevância na interface:

```python
from src.db.models import ArticleModel
from tempest_fastapi_sdk import BaseRepository


def can_rank(articles: BaseRepository[ArticleModel]) -> bool:
    """Diz se o backend consegue ordenar por relevância.

    Args:
        articles (BaseRepository[ArticleModel]): Repositório de artigos.

    Returns:
        bool: ``True`` quando há ``ts_rank``.
    """
    return articles.supports_full_text
```

!!! warning "Não confunda com um índice"
    A busca full-text aqui é calculada na hora, sem coluna `tsvector`
    materializada nem índice GIN. Isso a torna correta e sem migration,
    mas ela lê a tabela. Em tabela grande e consulta quente, o passo
    seguinte é uma coluna gerada com índice GIN — que o SDK ainda não
    empacota.

## Recapitulando

- `search()` — igual nos dois bancos, escapa o input, `AND` entre
  palavras, `OR` entre colunas.
- `search_condition()` / `full_text_condition()` devolvem a cláusula, e é
  por isso que a busca pagina e conta como qualquer filtro.
- `full_text_search()` — radicaliza, aceita a sintaxe do usuário, ordena
  por relevância no PostgreSQL; degrada para a camada portátil no resto.
- `supports_full_text` diz qual das duas você recebeu.
- `TextSearchLanguage`, `TextSearchWeight` e `TokenMatch` são enums: nada
  de string mágica no call site.
