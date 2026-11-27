---
description: "Tarefas em background sobre o TaskIQ: fila, agendador com lock e jobs persistidos."
---

# `tempest_fastapi_sdk.tasks`

Tarefas em background sobre o TaskIQ: fila, agendador com lock e jobs persistidos.

[« Tempo real e mensageria](../realtime-messaging.md)

---

::: tempest_fastapi_sdk.tasks
    options:
      show_root_toc_entry: false
      show_submodules: false
      members_order: source
      filters:
        - "!^_"

::: tempest_fastapi_sdk.tasks.lock.SchedulerLock
::: tempest_fastapi_sdk.tasks.lock.RedisSchedulerLock
::: tempest_fastapi_sdk.tasks.queue.TaskIQSettingsLike
::: tempest_fastapi_sdk.tasks.queue.LifecycleResource
::: tempest_fastapi_sdk.tasks.queue.LifecycleScope
::: tempest_fastapi_sdk.tasks.queue.ResultSerializer
::: tempest_fastapi_sdk.tasks.queue.Hook
::: tempest_fastapi_sdk.tasks.jobs.TERMINAL_JOB_STATUSES
::: tempest_fastapi_sdk.tasks.jobs.STALE_JOB_ERROR
::: tempest_fastapi_sdk.tasks.jobs.STORE_OWNED_JOB_COLUMNS
::: tempest_fastapi_sdk.tasks.jobs.make_job_admin_model
