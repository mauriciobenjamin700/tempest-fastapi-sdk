# Painel admin


UI de gerenciamento no estilo Django montada sob `/admin`. Operadores entram com uma linha de usuário do próprio banco — não há store de senha de admin separado. Cada modelo registrado passa a ser navegável pelo navegador, então a porta do banco pode ficar fechada em redes privadas.

**O que você ganha** (paridade com o Django admin):

- List view com busca, filtros ricos por campo (enum / FK / range de data) e colunas ordenáveis.
- CRUD completo (criar / editar / excluir) e ações em massa.
- Export CSV/JSON/XLSX e widgets FK-select.
- Dashboard com contagens de linhas + métricas de sistema.
- MFA TOTP opcional no login.
- Campos de upload de arquivo/imagem.
- Trilha de auditoria carimbando `created_by` / `updated_by`.

Cobre também edição inline in-place dos filhos 1-N (`Inline(editable=True)`).

Requer o extra `[admin]`:

```bash
uv add "tempest-fastapi-sdk[admin]"
```

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [Montar o painel](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/setup.md) | Use para subir o painel: modelo de usuário, registro das classes de admin (ou `automap`) e o router com tudo o que ele monta. |
| [Ações customizadas](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/actions.md) | Use para adicionar botões que rodam código seu (`@admin_action`): mostrar um segredo uma vez, pedir dados antes de rodar, criar por service. |
| [Campos do formulário](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/forms.md) | Use para ajustar o formulário: choices, upload de arquivo, FK com autocomplete, inlines, muitos-para-muitos, widgets, senha e colunas escondidas. |
| [Histórico de auditoria](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/audit.md) | Use para mostrar quem mudou o quê no detail, a atividade recente no dashboard e desfazer pelo histórico. |
| [Listagem: contagens, import e edição em massa](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/list.md) | Use para turbinar a listagem: contagem de relacionados, import CSV/XLSX, duplicar, edição em massa, lenses e contagem em tabela grande. |
| [Dashboard: cards, indicadores e mapa](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/dashboard.md) | Use para pôr métricas de negócio no dashboard: cards, áreas de indicadores com thresholds e o mapa de distribuição geográfica. |
| [Permissões: RBAC, escopo por linha e impersonate](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/permissions.md) | Use para controlar quem vê e faz o quê: RBAC por model e ação, escopo por linha na leitura e na escrita e entrar como outro usuário. |
| [Fuso, edição simultânea e idioma](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/operator.md) | Use para mostrar datas no fuso do operador, evitar que duas edições se sobrescrevam e trocar a interface para português. |
| [Busca global](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/admin/search.md) | Use para buscar um termo em todo model com `search_fields` pela caixa de busca no topo do painel. |

## Recap

- `AdminSite` + `AdminModel` transformam os seus models numa interface de CRUD
  sem você escrever template: a declaração é a tela.
- `@admin_action` põe operação de domínio na lista, e o retorno
  `AdminActionResult` é o que o operador lê de volta. O painel commita o que
  a ação deixou na sessão; `schema=` pede dados antes de rodar, e
  `action_policy=` libera ação por ação, sem `EDIT` no model.
- `audit_model=` renderiza a linha do tempo de quem mudou o quê no próprio
  detail, e `inlines=` traz os filhos 1-N para a mesma página.
- `autocomplete_fields=` troca o `<select>` que não escala por busca HTMX —
  necessário no instante em que a FK tem milhares de linhas.
- `dashboard_cards=` são métricas de negócio, não de sistema; `lenses=` são as
  visões salvas que o operador usaria de novo amanhã.
- `location_maps=[BRLocationMap(...)]` desenha onde estão as linhas de um model com `BRLocationMixin`: mapa do Brasil por estado e cidade, offline.
- `access_policy=` é RBAC por (principal, ação, model): sem ele, quem entra no
  admin pode tudo.
- `storage=StorageBrowserService(...)` abre o bucket na tela: lista,
  pré-visualiza (SVG e HTML só como código-fonte), sobe, edita texto,
  renomeia e apaga — `read_only=True` deixa só a leitura.
- `AdminTheme` cobre a aparência por campo tipado; `can_import=True` abre o
  import CSV em dois passos: a prévia valida e ensaia sem gravar, e o
  confirmar grava o arquivo assinado, tudo ou nada.
- `row_scope=` filtra **quais linhas** o principal alcança em toda rota do
  model; fora do escopo é `404`, e escrita que tira a linha do escopo
  volta.
- O form de edição traz a trava otimista de graça: save sobre uma versão
  velha volta com `409` e os valores do operador, sem gravar.
- `AdminSite(locale="pt-BR")` põe o painel em português; os atalhos de
  data do filtro saem traduzidos e no fuso do model.
- `exclude_fields=` tira a credencial que não é senha de toda superfície
  do painel; `readonly_fields` só tranca a escrita.
- `display_timezone=` é o que transforma o campo de datetime em widget:
  a tela lê e escreve no fuso do operador, a coluna continua em UTC.
- `m2m_fields=` edita um `secondary` como multi-select, e o vínculo que o
  operador não alcança fica; `related_counts=` conta os filhos por linha
  com um `GROUP BY` por página.
- `can_duplicate=True` abre o create preenchido com a cópia, e
  `bulk_edit_fields=` aplica um valor a toda a seleção num `UPDATE` só,
  com rollback se ele tirar linha do escopo.
- `AdminSite(recent_activity=N)` liga o feed das últimas mudanças
  auditadas no dashboard; o extra `[spreadsheet]` acrescenta o `.xlsx` ao
  export e ao import.
- `impersonate=` põe "Entrar como" no detail: o serviço dá a URL
  (`make_jwt_impersonation_handler` assina um access token curto com
  `act`), o painel confirma, recusa alvo admin, registra e redireciona — e
  só com `IMPERSONATE` concedido por uma `access_policy`.
- `count_strategy="estimate"` troca o `COUNT` pela estatística do
  PostgreSQL enquanto nada estreita a lista; `"none"` deixa só
  anterior/próxima.
- **Restaurar** no histórico abre o form de edição com os valores
  anteriores; o save é uma edição normal, com permissão, validação e trava.
- A busca global do topo procura em todo model com `search_fields`,
  respeitando `VIEW` e `row_scope`.
