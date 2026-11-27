---
description: "Índice de fila e tarefas em background sobre FastStream e TaskIQ; contém quando usar `MessageBroker`, `TaskQueue`, agendamento ou outbox e a tabela das subpáginas."
---

# Fila e Tarefas

Trabalho em background sem dor. O SDK envelopa o **FastStream** (mensageria) e o **TaskIQ** (tarefas + agendamento) em classes tipadas com um vocabulário único — o caminho comum (publicar, consumir, enfileirar, agendar) não importa `faststream` nem `taskiq` no código da aplicação. A exceção é opção específica do transporte, como o `RabbitExchange` repassado em `exchange=` nos [consumidores baseados em classe](queue-tasks/broker.md#consumidores-baseados-em-classe).

!!! tip "Qual ferramenta usar?"
    - **`MessageBroker`** (mensageria) — evento acontece, **vários** serviços/consumidores reagem. Fan-out, at-least-once, desacoplado do request. Ex.: "pedido pago" → estoque, e-mail, analytics.
    - **`TaskQueue`** (tarefas) — tirar trabalho lento **de um** handler de request pra um worker, mantendo a resposta HTTP rápida. Ex.: enviar e-mail, gerar PDF.
    - **`TaskQueue.cron` / `.interval`** (agendamento) — disparos periódicos.
    - **Outbox** — quando publicar *precisa* ser atômico com o `INSERT` no banco.

Todas as classes seguem o mesmo ciclo de vida: `connect()` / `disconnect()` / `lifespan()` / `health_check()` / `is_connected`, e expõem o objeto cru por baixo (`.broker`) como escape hatch.

## Onde está cada tarefa

Use esta tabela para abrir só a subpágina da tarefa que você tem; elas estão na ordem de leitura.

| Subpágina | O que resolve |
| --- | --- |
| [Mensageria com `MessageBroker`](queue-tasks/broker.md) | Use para publicar e consumir eventos por channel, com consumidores e publicadores em classe e Kafka (chave, grupo e commit). |
| [Topologia da fila com `QueueSpec`](queue-tasks/topology.md) | Use quando a fila precisa sobreviver a restart, ter fan-out, dead-letter reprocessável e exchange declarado. |
| [Confiabilidade do caminho de eventos](queue-tasks/events-reliability.md) | Use para não perder mensagem que falha: dead-letter, retry com atraso, métricas, prefetch, idempotência e tracing pela fila. |
| [Tarefas em background com `TaskQueue`](queue-tasks/tasks.md) | Use para tirar trabalho lento do request: enfileirar, enfileirar pelo nome, recursos do worker e tarefas em classe. |
| [Tarefas periódicas: `cron` e `interval`](queue-tasks/periodic.md) | Use para agendar tarefas periódicas no mesmo `TaskQueue`, sem scheduler separado no seu código. |
| [Confiabilidade e observabilidade das tarefas](queue-tasks/tasks-reliability.md) | Use para ligar retry tipado, dead-letter, métricas Prometheus por tarefa e o painel de dead-letter no admin. |
| [Workers, healthcheck e scheduler em produção](queue-tasks/production.md) | Use para rodar worker e scheduler em produção, checar se estão vivos e agendar no lifespan com uma réplica só. |
| [Transporte por settings e outbox](queue-tasks/from-settings.md) | Use para escolher o transporte em uma linha com `from_settings` e publicar de forma atômica com o `INSERT` (outbox). |

## Recap / próximos passos

- **`MessageBroker`** — pub/sub tipado e transport-agnostic sobre FastStream: `@mq.on("channel")` + `await mq.publish("channel", modelo)`. Fan-out at-least-once entre serviços.
- **`TaskQueue`** — tarefas sobre TaskIQ: `@tq.task` → `await task.enqueue(...)` (pro worker) ou `await task.run(...)` (inline). `.memory()` pra testes.
- **`@tq.cron` / `@tq.interval`** — periódicos no mesmo objeto; `start_scheduler()` em dev, CLI standalone em produção.
- **Heartbeat** — `TASKIQ_WORKER_HEARTBEAT_FILE` / `TASKIQ_SCHEDULER_HEARTBEAT_FILE` + `python -m tempest_fastapi_sdk.tasks.heartbeat` no `HEALTHCHECK`; o Docker marca `unhealthy`, não reinicia. A batida do worker é local a cada réplica.
- **Cron sem sintaxe** — `CronEnum` / `CronOffsetEnum` / `WeekdayEnum` + helpers (`daily`, `weekdays`, `every_n_minutes`, …) pra agendar por nome; `CronOffsetEnum.BRASILIA` no lugar de `"-03:00"`.
- **Estilos** — decorators (`@mq.on`, `@tq.task`, `@tq.cron`) **ou** classes (`Consumer` + `mq.register`, `TaskDef` + `tq.register`); as duas formas coexistem.
- **Outbox** — `BaseOutboxModel` + `OutboxRelay` + `save_with_outbox`, com o `publish` do relay apontando pro `MessageBroker`. Veja [Outbox](outbox.md).
- **Renome (v0.94.0)** — `AsyncBrokerManager` → **`AsyncQueueManager`** (wrapper fino; alias antigo mantido). Os facades `MessageBroker` / `TaskQueue` seguem recomendados; `AsyncTaskBrokerManager` / `AsyncTaskScheduler` continuam como legado funcional.
