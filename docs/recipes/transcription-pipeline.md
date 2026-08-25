# Pipeline de transcrição — do áudio ao resumo, com estágios

Uma reunião de cinquenta minutos entra como upload e sai como três coisas
diferentes: a transcrição, um resumo e uma lista de tarefas. Cada peça já
tem receita própria — [STT](genai.md#interpretar-audio-stt),
[jobs](jobs.md), [contabilidade de uso](genai.md#contabilidade-de-uso-por-usuario-tabela).
O que nenhuma delas mostra é a **costura**, que é onde moram as decisões
difíceis:

- a tela já busca o documento; onde o estado dos três estágios deveria
  ficar?
- o usuário clicou em "cancelar" no minuto 40 de uma transcrição que roda
  numa worker thread. E agora?
- três estágios, dois deles pagos por token e um pago por relógio. Quem
  fecha a conta?

Esta página monta o fluxo inteiro, do upload à fatura, e diz o que cada
escolha custa.

## 1. Uma tabela de jobs, ou colunas no próprio registro?

As duas formas existem no SDK e resolvem problemas diferentes:

| Forma | Quando | Receita |
| --- | --- | --- |
| `JobStore` — uma linha por unidade de trabalho | o trabalho **é** a coisa: uma exportação, uma importação, um lote noturno | [Jobs](jobs.md) |
| `StageMap` — colunas de status no registro | o trabalho **decora** um registro que a tela já está mostrando | [Jobs §9](jobs.md#9-varios-estagios-no-proprio-registro) |

Um áudio transcrito é o segundo caso. A tela abre o documento de qualquer
jeito; uma tabela de jobs à parte vira uma segunda consulta e um join para
desenhar uma página que já tinha tudo de que precisava.

!!! tip "Dá para usar as duas"
    Nada impede um `JobStore` para o processamento em lote da madrugada e
    um `StageMap` no documento para o que a tela mostra. Elas não competem
    — a pergunta é sempre "quem consulta isto, e a partir de quê".

## 2. O registro, e as nove colunas

O mapa não declara coluna nenhuma. Os `mapped_column` são seus, para que
migration, tipo e índice fiquem onde o leitor procura:

```python
# src/core/pipeline.py
from uuid import UUID

from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.tasks import StageMap

STAGES: StageMap = StageMap(
    ["transcription", "summary", "suggestions"],
    prefix="doc_",
)


class DocumentModel(BaseModel):
    """Um áudio enviado, e o que a IA produziu a partir dele."""

    __tablename__ = "documents"

    owner_id: Mapped[UUID] = mapped_column()
    filename: Mapped[str] = mapped_column()

    doc_status_transcription: Mapped[str | None] = mapped_column(default=None)
    doc_error_transcription: Mapped[str | None] = mapped_column(default=None)
    doc_result_transcription: Mapped[str | None] = mapped_column(default=None)

    doc_status_summary: Mapped[str | None] = mapped_column(default=None)
    doc_error_summary: Mapped[str | None] = mapped_column(default=None)
    doc_result_summary: Mapped[str | None] = mapped_column(default=None)

    doc_status_suggestions: Mapped[str | None] = mapped_column(default=None)
    doc_error_suggestions: Mapped[str | None] = mapped_column(default=None)
    doc_result_suggestions: Mapped[str | None] = mapped_column(default=None)
```

São nove colunas para três estágios, e é exatamente por isso que o mapa
existe: sem ele, cada estágio ganha a própria cópia de "marca rodando" e
"marca falhou", e um estágio copiado que ficou com o nome de coluna do
vizinho **compila, importa e reporta o estado do vizinho**.

!!! warning "Estágio sem coluna `result_` perde o resultado em silêncio"
    `mark(..., result=...)` faz `setattr` no nome que o template resolve.
    Se aquela coluna não existir no model, o SQLAlchemy não reclama: o
    atributo fica na instância, some no flush e a linha grava só o status.
    Medido — `doc_status_summary` gravou `'done'`, e
    `doc_result_summary` nunca chegou à tabela.

    Declare `result_` em todo estágio que devolve alguma coisa, ou use
    `result_template` para apontar para uma coluna que você já tem.

E os motores, num módulo só, porque são caros de construir e existem uma
vez por processo:

```python
# src/core/ai.py
from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import (
    AIUsageStore,
    BaseAIUsageModel,
    OpenAICompatGenerator,
)
from tempest_fastapi_sdk.genai.audio import SpeechToText


class AIUsageModel(BaseAIUsageModel):
    """Uma chamada de IA cobrada desta aplicação."""

    __tablename__ = "ai_usage"


db = AsyncDatabaseManager("postgresql+asyncpg://localhost/app")

usage: AIUsageStore[AIUsageModel] = AIUsageStore(
    db,
    model=AIUsageModel,
    price_input_per_1k=0.00014,
    price_output_per_1k=0.00028,
)

stt = SpeechToText(
    "large-v3-turbo",
    device="cpu",
    compute_type="int8",
    batch_size=8,
    condition_on_previous_text=False,
)

llm = OpenAICompatGenerator(
    "deepseek-chat",
    api_key="sk-...",
    base_url="https://api.deepseek.com/v1",
)
```

`batch_size` + `condition_on_previous_text=False` é o par que faz sentido
junto, e os [knobs de decode](genai.md#transcrever-mais-rapido-em-cpu)
explicam por quê. O `base_url` não é opcional: sem ele o SDK manda um nome
de modelo da DeepSeek para `api.openai.com`.

## 3. Estágio 1 — transcrever

O padrão que se repete nos três estágios: **sessão curta para marcar,
sessão nenhuma durante o trabalho, sessão curta para gravar**. Uma
transcrição leva minutos; segurar conexão de banco por esse tempo é como
se perde um pool.

```python
# src/tasks/transcribe.py
from uuid import UUID

from tempest_fastapi_sdk.tasks import StageStatus

from src.core.ai import db, stt, usage
from src.core.pipeline import STAGES, DocumentModel


async def transcrever(documento_id: UUID) -> None:
    """Transcreve o áudio do documento e grava o texto.

    Args:
        documento_id (UUID): O documento a processar.
    """
    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is None:
            return
        STAGES.mark(documento, "transcription", StageStatus.RUNNING)
        caminho: str = documento.filename
        dono: UUID = documento.owner_id
        await session.commit()

    resultado = await stt.transcribe(caminho)
    await usage.record_duration(
        subject_id=dono,
        seconds=resultado.duration,
        model="large-v3-turbo",
    )

    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is not None and STAGES.owns(
            documento,
            "transcription",
            StageStatus.RUNNING,
        ):
            STAGES.mark(
                documento,
                "transcription",
                StageStatus.DONE,
                result=resultado.text,
            )
        await session.commit()
```

Duas coisas que parecem detalhe e não são:

- **`owns` é relido do banco.** O objeto que você carregou antes do
  trabalho começar ainda tem o status antigo e responderia `True`
  aconteça o que acontecer. A releitura é o que faz a checagem valer.
- **`record_duration`, não `record`.** Modelo que roda no seu hardware não
  tem conta de token; o que se consome é relógio. Essas linhas ficam com
  `service=NULL` e são excluídas das somas de token, para não virarem uma
  fatia de 0% em todo gráfico de distribuição.

## 4. Cancelar uma transcrição que já começou

Aqui o caminho fácil não funciona. `run_cancellable` corre a corotina
contra um predicado e cancela de verdade — mas `transcribe()` entrega o
decode a `asyncio.to_thread`, e cancelar a corotina abandona o *wrapper*
enquanto a thread segue até o fim, ainda ocupando CPU e ainda competindo
com o próximo job.

O que **funciona** é levantar de dentro do `on_progress`. O callback roda
na worker thread, dentro do laço que consome os segmentos, e nada no
caminho engole a exceção — ela sobe pelo gerador, sai do `to_thread` e
chega em quem estava aguardando:

```python
# src/tasks/transcribe.py
import asyncio
import threading
from collections.abc import Awaitable, Callable

from tempest_fastapi_sdk.genai.audio import Transcription
from tempest_fastapi_sdk.tasks import StageInterruptedError

from src.core.ai import stt


async def transcrever_cancelavel(
    caminho: str,
    *,
    cancelado: Callable[[], Awaitable[bool]],
) -> Transcription:
    """Transcreve, desistindo no meio quando o usuário cancela.

    Args:
        caminho (str): O arquivo de áudio.
        cancelado (Callable[[], Awaitable[bool]]): Consulta assíncrona que
            responde se o cancelamento já foi pedido.

    Returns:
        Transcription: O texto, quando o decode chega ao fim.

    Raises:
        StageInterruptedError: O cancelamento chegou antes do fim.
    """
    parar = threading.Event()

    def progresso(pronto: float, total: float) -> None:
        """Aborta o decode assim que o vigia levanta a bandeira."""
        if parar.is_set():
            raise StageInterruptedError

    async def vigiar() -> None:
        """Consulta o cancelamento no event loop e avisa a thread."""
        while not parar.is_set():
            if await cancelado():
                parar.set()
                return
            await asyncio.sleep(2.0)

    vigia = asyncio.create_task(vigiar())
    try:
        return await stt.transcribe(caminho, on_progress=progresso)
    finally:
        vigia.cancel()
```

O `threading.Event` é a ponte, e é obrigatório: o callback roda **fora**
do event loop, então ele não pode `await` a consulta de cancelamento. Um
lado pergunta ao banco de 2 em 2 segundos; o outro só lê um booleano.

!!! check "Medido, não deduzido"
    Contra um decode de 600 trechos, com o cancelamento chegando em 0,25 s:
    a exceção propagou e **25 dos 600 trechos** tinham sido decodificados.
    Sem o callback, os 600 rodam até o fim.

    O intervalo do vigia é o teto do seu desperdício e o piso da sua
    granularidade: a primeira consulta é em `t=0`, a seguinte só depois do
    intervalo, então trabalho que termina dentro dele nunca chega a ver o
    cancelamento — também medido, num decode que acabou em 0,06 s com o
    vigia em 2 s. Dois segundos num trabalho de minutos é ruído; ajuste se
    o seu caso for outro.

!!! danger "O callback não pode fazer I/O"
    Ele roda na worker thread, a cada segmento. Nada de corotina lá dentro
    sem `loop.call_soon_threadsafe`, nada de query, nada de log síncrono em
    arquivo remoto — o custo entra direto no tempo de decode.

Do lado de quem pede, cancelar é escrever o status e responder na hora:

```python
# src/services/documents.py
from uuid import UUID

from src.core.ai import db
from src.core.pipeline import STAGES, DocumentModel


async def cancelar(documento_id: UUID) -> list[str]:
    """Pede para o que ainda está rodando parar.

    Args:
        documento_id (UUID): O documento a cancelar.

    Returns:
        list[str]: Os estágios que foram efetivamente cancelados; os que
        já haviam terminado saem na outra metade do par e são ignorados
        de propósito.
    """
    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is None:
            return []
        cancelados, _ignorados = STAGES.cancel(documento)
        await session.commit()
        return cancelados
```

!!! info "Não há cascata, e não precisa"
    Se cada estágio só enfileira o seguinte ao terminar bem, cancelar o
    primeiro faz o segundo nunca existir. Cancelar é parcial de propósito:
    uma tela que faz polling vai rotineiramente pedir o cancelamento de
    algo que concluiu um instante atrás, e isso não é erro.

## 5. Estágio 2 — resumir, e anotar quem pagou

Agora o trabalho é uma chamada de rede, o custo é por token, e o provedor
diz quanto gastou. `generate_with_usage` devolve os dois:

```python
# src/tasks/summarize.py
from uuid import UUID

from tempest_fastapi_sdk.tasks import StageStatus

from src.core.ai import db, llm, usage
from src.core.pipeline import STAGES, DocumentModel


async def resumir(documento_id: UUID) -> None:
    """Resume a transcrição e registra o consumo da chamada.

    Args:
        documento_id (UUID): O documento a resumir.
    """
    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is None or documento.doc_result_transcription is None:
            return
        STAGES.mark(documento, "summary", StageStatus.RUNNING)
        transcricao: str = documento.doc_result_transcription
        dono: UUID = documento.owner_id
        await session.commit()

    resumo, tokens = await llm.generate_with_usage(
        f"Resuma esta reunião em cinco linhas:\n\n{transcricao}",
    )
    await usage.record(subject_id=dono, service="summary", usage=tokens)

    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is not None and STAGES.owns(
            documento,
            "summary",
            StageStatus.RUNNING,
        ):
            STAGES.mark(documento, "summary", StageStatus.DONE, result=resumo)
        await session.commit()
```

!!! warning "`generate` devolve só o texto — e é de propósito"
    `generate()` satisfaz o `TextBackend`, que é o protocolo que todo o
    resto do SDK consome; mudar o retorno dele para uma tupla quebraria
    cada chamador. Por isso o par: `generate`/`chat` para quem quer texto,
    `generate_with_usage`/`chat_with_usage` para quem vai cobrar.

!!! danger "Provedor que não reportou uso não vira linha"
    `record(usage=None)` grava **nada**, e `TokenUsage(0, 0, 0)` também
    não. "O provedor não disse" é diferente de "a chamada foi de graça":
    uma linha zerada contaria para o total de chamadas e para "usuários
    ativos" sem contribuir token nenhum.

## 6. Estágio 3 — sugestões como lista validada

O terceiro estágio pede ao modelo um **array** de objetos, e é o estágio
que quebra. `generate_structured_list` empacota o que se escreve à mão
toda vez: acha o array mesmo cercado de prosa e de cerca de bloco,
valida item a item, e só repete a geração quando a saída não tem array
nenhum.

```python
# src/tasks/suggest.py
import json
from uuid import UUID

from pydantic import BaseModel

from tempest_fastapi_sdk.genai import generate_structured_list
from tempest_fastapi_sdk.tasks import StageStatus

from src.core.ai import db, llm
from src.core.pipeline import STAGES, DocumentModel


class Tarefa(BaseModel):
    """Uma tarefa que a reunião gerou."""

    titulo: str
    responsavel: str
    prazo: str | None = None


async def sugerir(documento_id: UUID) -> None:
    """Extrai tarefas da transcrição e grava a lista validada.

    Args:
        documento_id (UUID): O documento a minerar.
    """
    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is None or documento.doc_result_transcription is None:
            return
        STAGES.mark(documento, "suggestions", StageStatus.RUNNING)
        transcricao: str = documento.doc_result_transcription
        await session.commit()

    tarefas: list[Tarefa] = await generate_structured_list(
        llm,
        "Liste as tarefas combinadas, como um array JSON de objetos com "
        f"titulo, responsavel e prazo:\n\n{transcricao}",
        Tarefa,
    )

    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is not None and STAGES.owns(
            documento,
            "suggestions",
            StageStatus.RUNNING,
        ):
            STAGES.mark(
                documento,
                "suggestions",
                StageStatus.DONE,
                result=json.dumps([t.model_dump() for t in tarefas]),
            )
        await session.commit()
```

Só falha **estrutural** — nenhum array na saída — gasta uma tentativa, e
cada tentativa sobe a temperatura em `temperature_step`. Repetir uma
geração greedy na mesma temperatura reproduziria a saída anterior; um item
malformado no meio de dez bons não é falha de formato, e sai por
`skip_invalid`.

!!! tip "Lista vazia é sucesso"
    `[]` significa "o modelo respondeu, e a resposta é nenhum item" — uma
    reunião sem tarefa combinada. Não confunda com `StructuredFormatError`,
    que é "nenhuma tentativa produziu um array".

!!! warning "Este estágio não devolve `TokenUsage`"
    `generate_structured_list` recebe qualquer coisa com
    `generate(prompt) -> str`, e o protocolo devolve texto. A chamada
    acontece, o token é cobrado, e o retorno não tem o que gravar.

    Quando o estágio precisa entrar na conta, faça as duas metades você
    mesmo — e aceite que perde o retry:

    ```python
    # src/tasks/suggest.py
    from uuid import UUID

    from pydantic import BaseModel

    from tempest_fastapi_sdk.genai import parse_structured_list

    from src.core.ai import llm, usage


    class Tarefa(BaseModel):
        """Uma tarefa que a reunião gerou."""

        titulo: str
        responsavel: str
        prazo: str | None = None


    async def sugerir_cobrando(transcricao: str, dono: UUID) -> list[Tarefa]:
        """Extrai tarefas registrando o consumo da chamada.

        Args:
            transcricao (str): O texto de origem.
            dono (UUID): Quem paga a chamada.

        Returns:
            list[Tarefa]: As tarefas que passaram na validação.
        """
        texto, tokens = await llm.generate_with_usage(
            f"Liste as tarefas como array JSON:\n\n{transcricao}",
        )
        await usage.record(subject_id=dono, service="suggestions", usage=tokens)
        return parse_structured_list(texto, Tarefa, skip_invalid=True)
    ```

    Medido: o `TokenUsage` chega inteiro, e um array com um item inválido
    entre dois devolve os válidos em vez de levantar.

## 7. A tela

O endpoint de status não precisa saber o nome de coluna nenhum:

```python
# src/api/routers/documents.py
from uuid import UUID

from fastapi import APIRouter

from src.core.ai import db
from src.core.pipeline import STAGES, DocumentModel

router = APIRouter(prefix="/api/documents")


@router.get("/{documento_id}/status")
async def ler_status(documento_id: UUID) -> dict[str, str | None]:
    """Devolve o estado de cada estágio do documento.

    Args:
        documento_id (UUID): O documento consultado.

    Returns:
        dict[str, str | None]: Estágio para status; `None` no estágio que
        ainda não começou.
    """
    async with db.get_session_context() as session:
        documento = await session.get(DocumentModel, documento_id)
        if documento is None:
            return {}
        return {
            estagio: None if situacao is None else situacao.value
            for estagio, situacao in STAGES.snapshot(documento).items()
        }
```

Uma execução real dos três estágios devolve
`{"transcription": "done", "summary": "done", "suggestions": "done"}` —
e o front desenha a barra de progresso a partir disso, sem conhecer o
`prefix` nem os templates.

!!! info "`cancelled` é terminal, mas não é falha"
    Uma tela que destaca `failed` em vermelho deve deixar `cancelled` em
    paz, e um alerta que dispara em falha não deve tocar. Nada deu errado:
    o sistema fez o que mandaram.

## 8. A conta no fim do mês

As duas naturezas de custo — token e relógio — já estão na mesma tabela, e
saem separadas na leitura:

```python
# src/services/reports.py
from datetime import timedelta

from tempest_fastapi_sdk.genai import ServiceUsage, UsageTotals

from src.core.ai import usage


async def painel() -> tuple[UsageTotals, list[ServiceUsage]]:
    """Lê o que a tela de custo mostra.

    Returns:
        tuple[UsageTotals, list[ServiceUsage]]: Totais do período e a
        distribuição por serviço.
    """
    janela = timedelta(days=30)
    return await usage.totals(janela), await usage.by_service(janela)
```

Rodando o pipeline inteiro uma vez (uma transcrição local de 30 s e um
resumo de 3000 tokens de entrada + 800 de saída), o painel lê:

```text
UsageTotals(input_tokens=3000, output_tokens=800, total_tokens=3800,
            duration_seconds=30.0, calls=2, cost=0.000644,
            cache_hit_tokens=0)
[ServiceUsage(service='summary', total_tokens=3800, share=100.0)]
```

`calls=2` conta as duas linhas — a paga por token e a paga por relógio.
`by_service` traz só a primeira: a transcrição local ficou com
`service=NULL` e não vira uma fatia de 0% no gráfico.

!!! warning "O custo não vem arredondado"
    `0.000644` é o valor cheio. Qualquer precisão fixa erra em alguma
    escala — arredondar para centavos zera quase toda chamada isolada,
    enquanto um total mensal quer centavos. A formatação fica na borda,
    que sabe qual dos dois está mostrando. `cost is None` significa "não
    mostre custo", nunca zero.

!!! info "O preço nunca é gravado"
    O custo sai dos tokens na hora da leitura, então corrigir
    `price_input_per_1k` conserta o histórico inteiro — sem reprocessar
    nada, sem linhas discordando sobre quanto valia um token.

## Erros

| Exceção | Quando |
| --- | --- |
| `StageInterruptedError` | o cancelamento chegou no meio; não é falha, o handler só retorna |
| `StructuredFormatError` | nenhuma tentativa de `generate_structured_list` produziu um array decodificável; subclasse de `ValueError` |
| `pydantic.ValidationError` | um item não satisfaz o schema e `skip_invalid` está desligado |
| `ValueError` (na construção do `StageMap`) | lista de estágios vazia, estágio duplicado, ou dois estágios resolvendo para a mesma coluna |
| `ValueError` (na construção do `SpeechToText`) | `batch_size` sem `vad_filter=True` — é o VAD que corta o áudio nos trechos que viram um batch |

**Recap:** `StageMap` dá nome às colunas de estado sem declarar nenhuma,
e é a forma certa quando a tela já carrega o registro; cada estágio marca
`RUNNING`, solta a sessão, trabalha, relê e só grava se `owns` disser que
o estágio ainda é dele; transcrição cancela levantando de dentro do
`on_progress`, porque `to_thread` não é cancelável e a bandeira atravessa
por um `threading.Event`; `generate_with_usage` é a metade que devolve o
`TokenUsage` que `record` grava, `record_duration` cobre o modelo local
que não tem token; `generate_structured_list` acha e valida o array, e a
lista vazia é resposta, não erro.
