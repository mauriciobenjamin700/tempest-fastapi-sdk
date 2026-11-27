---
description: "Índice dos módulos de admin, dados e API: painel admin, models, repository, sessão, migrations, FastAPI, auth, authz, sessões, exceções, schemas base e settings."
---

# Referência: Admin, dados e API

O painel admin, a camada de dados (models, repository, sessão, migrations), a integração com o FastAPI, autenticação, autorização e sessões, as exceções, os schemas base e as settings.

[« Índice da referência](../reference.md)

---

## Módulos

Uma página por módulo; cada uma renderiza a API pública dele a partir das docstrings.

| Módulo | O que cobre |
| --- | --- |
| [`admin`](admin-data-api/admin.md) | O painel admin estilo Django: site, model admin, ações, políticas de acesso, auditoria e páginas. |
| [`admin.sql_shell`](admin-data-api/admin-sql-shell.md) | O console SQL do admin e a política que decide quem roda o quê. |
| [`admin.storage`](admin-data-api/admin-storage.md) | A fonte de dados do navegador de storage do admin: um bucket de object store. |
| [`admin.tasks`](admin-data-api/admin-tasks.md) | A fonte de dados do painel de tarefas do admin: agenda declarada e execuções persistidas. |
| [`api`](admin-data-api/api.md) | A integração com o FastAPI: app factory, middlewares, routers prontos, dependências, OAuth, cookies, SPA e webhooks. |
| [`auth`](admin-data-api/auth.md) | O fluxo de autenticação embutido: cadastro, ativação, login, reset de senha, JWT, OAuth e MFA TOTP. |
| [`authz`](admin-data-api/authz.md) | Autorização por objeto: políticas, checagem de permissão e a dependência do FastAPI. |
| [`cache`](admin-data-api/cache.md) | Cache Redis assíncrono, o decorator `@cached` e a invalidação por chave e por tag. |
| [`core`](admin-data-api/core.md) | Primitivas transversais: tipagem estrita, logging, redação de dados sensíveis e locale da documentação. |
| [`db`](admin-data-api/db.md) | A camada de dados: `BaseModel`, `BaseRepository`, sessão assíncrona, mixins de model, migrations e hooks do Alembic. |
| [`exceptions`](admin-data-api/exceptions.md) | A hierarquia `AppException`, as exceções por domínio e os handlers que as viram resposta HTTP. |
| [`schemas`](admin-data-api/schemas.md) | Os schemas Pydantic base: `BaseSchema`, paginação, envelope de resposta e filtros. |
| [`services, controllers`](admin-data-api/services-controllers.md) | As classes base da camada de serviço e de controller, e o mixin de serviço com arquivo armazenado. |
| [`sessions`](admin-data-api/sessions.md) | Sessão do lado do servidor: store, serviço, middleware e router. |
| [`settings`](admin-data-api/settings.md) | `BaseAppSettings`, os mixins de settings compostos e as credenciais tipadas. |
