# Fuso, edição simultânea e idioma

Use para mostrar datas no fuso do operador, evitar que duas edições se sobrescrevam e trocar a interface para português.

Parte da receita [Painel admin](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin.md).

## Fuso do operador (`display_timezone=`)

Uma coluna `TIMESTAMP(timezone=True)` guarda **instante**, e o form do
admin renderiza `<input type="datetime-local">`, que **não carrega
offset nenhum**. Sem dizer em que fuso a caixa é lida, o painel vira um
editor de coluna crua: a submissão chega sem `tzinfo`, e quem decide o
que ela significa passa a ser o driver — com asyncpg, o `TZ` do
processo. Num container em UTC o operador em Teresina digita `20:00` e
a linha fica com `20:00Z`, três horas à frente
([medido no consumidor](https://github.com/And-All/alofans-api/issues/114)).

Declare o fuso e as três telas passam a concordar:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import EventModel


site.register(
    AdminModel(
        model=EventModel,
        list_display=[EventModel.name, EventModel.date],
        display_timezone="America/Sao_Paulo",
    ),
)
```

O que muda, e só isso:

- **Listagem e detail** convertem cada `datetime` para o fuso e mostram
  o offset junto (`2026-06-15 20:00 -03:00`; o instante completo fica no
  tooltip), então a tela nunca é ambígua.
- **Form** pré-preenche a caixa com a hora local (`2026-06-15T20:00`) e
  escreve `Timezone: America/Sao_Paulo — stored as UTC.` embaixo, porque
  o `datetime-local` não tem onde dizer isso sozinho.
- **Submit** lê o valor como hora de parede **naquele** fuso e grava em
  UTC. Valor que já chega com offset é respeitado, não sobrescrito.
- **Filtro de intervalo de data** (`list_filter` numa coluna `datetime`)
  lê o dia nesse fuso: "de 20/11 até 20/11" vai da meia-noite de 20/11
  até a meia-noite de 21/11 em São Paulo, fim exclusivo. Um registro das
  22:00 de 20/11 (01:00 UTC de 21/11) entra no dia 20, que é o dia que a
  listagem mostra. O export filtrado segue o mesmo intervalo. Sem
  `display_timezone`, o dia é UTC.

!!! warning "Converter só na escrita é pior que não converter"
    A meia-correção passa em todo teste sobre gravar e ainda assim
    move a linha: o form mostra a hora UTC, o operador salva sem tocar
    em nada, e o valor exibido é relido como hora local. Cada save de
    um formulário intocado empurra a linha três horas. É por isso que
    as duas pontas mudam juntas, e por isso o teste que fixa a regra é
    um `POST` real de um form que ninguém editou.

!!! tip "Declare uma vez no `AdminSite`"
    O fuso é do painel, não de uma tabela: `AdminSite(display_timezone=...)`
    vale para todo model registrado depois, e um `AdminModel` que declara o
    seu próprio continua com ele.

!!! info "O export continua em UTC"
    CSV e JSON alimentam máquina. Um timestamp que muda de significado
    entre a tela e o arquivo é pior que um que sempre significa a mesma
    coisa, então a conversão fica nas telas que uma pessoa lê.

!!! tip "O fuso é validado onde é declarado"
    `display_timezone="America/Sao_Paolo"` (com o typo) levanta
    `ValueError` na construção do `AdminModel`, não no request que
    renderiza o form. Fuso resolvido tarde já custou caro aqui: a
    string de offset do cron morria dentro do loop do scheduler,
    calada.

### Atalhos no filtro de data

Todo filtro de intervalo de data (`list_filter` numa coluna `date` ou
`datetime`) ganha, embaixo das duas caixas, os links **Today**, **7 days**,
**30 days**, **This month** e **Last month** (em português com
`AdminSite(locale="pt-BR")`):

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import OrderModel


site.register(
    AdminModel(
        model=OrderModel,
        list_filter=[OrderModel.created_at, OrderModel.status],
        search_fields=[OrderModel.code],
        display_timezone="America/Sao_Paulo",
    ),
)
```

Cada link preenche `filter_created_at_from` e `filter_created_at_to` com
os dias do intervalo, ambos inclusivos: hoje; os últimos 7 e 30 dias
terminando hoje; o mês corrente do dia 1 ao último dia; o mês anterior
inteiro. "Hoje" é o dia no `display_timezone` do model (UTC sem ele) —
o mesmo dia que o filtro lê. O resto da query fica como o operador deixou
(busca, os outros filtros, a ordenação, a lens); só a página volta para a
primeira. O atalho que bate com o intervalo ativo aparece marcado. São
links montados no servidor, sem JavaScript.

## Edição simultânea: trava otimista

Dois operadores abrem o mesmo registro; o primeiro salva, o segundo
salva por cima sem saber. O form de edição evita isso sozinho, para
todo model com coluna `updated_at` — e todo `BaseModel` tem uma:

```python
from tempest_fastapi_sdk import AdminModel

from src.admin import site
from src.db.models import CustomerModel


site.register(AdminModel(model=CustomerModel))
```

O form leva, num campo oculto, o `updated_at` que leu ao abrir. No save,
antes de atribuir qualquer coluna, o painel roda
`UPDATE ... SET updated_at = updated_at WHERE id = :id AND updated_at =
:lido` na **mesma transação** do `UPDATE` que grava a edição — não há
`SELECT` antes para perguntar se mudou. Zero linhas afetadas significa
que outro save (pelo painel ou por qualquer outro caminho que passe pelo
ORM) chegou antes: nada é gravado e o form volta com `409`, o aviso
`Someone else saved this record after you opened it. Nothing was saved:
your values are below — review them and save again to overwrite.` e os
valores que o operador digitou.

O form devolvido já carrega a versão **atual** da linha. Salvar de novo,
depois de revisar, sobrescreve conscientemente.

!!! info "Onde a trava não vale"
    - Model sem coluna `updated_at`, ou um `POST` sem o campo oculto (um
      form aberto antes da atualização do SDK), salva como antes.
    - O formset do inline editável e as ações em lote/customizadas não
      levam a trava: o inline salva cada filho como está, e uma ação age
      sobre a linha que encontra.

## Interface em português (`locale=`)

Todo texto que o próprio painel escreve — rótulos, botões, estados
vazios, a paginação, os avisos de flash e os erros que o router mostra
no form (`This field is required.`, o aviso de conflito, o de escopo) —
sai de um catálogo com inglês e português do Brasil. Escolha no site:

```python
from tempest_fastapi_sdk import AdminSite

site = AdminSite(
    title="Backoffice",
    locale="pt-BR",
    display_timezone="America/Sao_Paulo",
)
```

O padrão é `"en"`, com o mesmo texto de sempre, então um painel que já
existe não muda. Um `locale` fora de `ADMIN_LOCALES` levanta `ValueError`
na construção do `AdminSite`. O `<html lang>` acompanha o locale, e o
`index_subtitle` padrão vira `Administração do site` (passado à mão, ele
fica como você escreveu).

O que **não** é traduzido é o que vem de você: nome de model e de campo,
`verbose_name`, rótulo de ação, `title`. As mensagens que outro
componente escreve também saem como ele as escreve — o
`AdminAuthError` do backend de auth (`Invalid credentials`), os erros do
`StorageBrowserService` e a mensagem da política de senha.

Para escrever um texto seu no mesmo catálogo — o retorno de uma ação
customizada, por exemplo — use `admin_text`:

```python
from tempest_fastapi_sdk.admin import admin_plural, admin_text

title = admin_text("pt-BR", "list.new", model="Pedido")
count = admin_plural("pt-BR", "list.records", 3)
```

`admin_text(locale, chave, **params)` formata o texto com `str.format`;
`admin_plural(locale, chave, n)` lê `chave.one` com `n == 1` e
`chave.other` no resto. Chave que falta no locale cai no inglês, e chave
desconhecida devolve a própria chave — o guard
`tests/test_admin_i18n_guard.py` mantém as duas línguas completas.
