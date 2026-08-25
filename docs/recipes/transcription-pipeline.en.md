# Transcription pipeline — from audio to summary, in stages

A fifty-minute meeting arrives as an upload and leaves as three different
things: the transcript, a summary, and a list of tasks. Each piece already
has its own recipe — [STT](genai.md#interpret-audio-stt),
[jobs](jobs.md), [usage accounting](genai.md#per-user-usage-accounting-a-table).
What none of them shows is the **seam**, which is where the hard decisions
live:

- the screen already loads the document; where should the three stages'
  state go?
- the user clicked "cancel" 40 minutes into a transcription running on a
  worker thread. Now what?
- three stages, two billed per token and one billed by the clock. Who
  closes the bill?

This page builds the whole flow, from upload to invoice, and says what
each choice costs.

## 1. A jobs table, or columns on the record itself?

Both shapes exist in the SDK and solve different problems:

| Shape | When | Recipe |
| --- | --- | --- |
| `JobStore` — one row per unit of work | the work **is** the thing: an export, an import, a nightly batch | [Jobs](jobs.md) |
| `StageMap` — status columns on the record | the work **decorates** a record the screen is already showing | [Jobs §9](jobs.en.md#9-several-stages-on-the-record-itself) |

A transcribed audio file is the second case. The screen opens the document
either way; a separate jobs table becomes a second query and a join to
draw a page that already had everything it needed.

!!! tip "You can use both"
    Nothing stops a `JobStore` for the nightly batch and a `StageMap` on
    the document for what the screen shows. They do not compete — the
    question is always "who queries this, and starting from what".

## 2. The record, and the nine columns

The map declares no columns. The `mapped_column`s are yours, so that
migrations, types and indexes stay where a reader looks for them:

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
    """An uploaded audio file, and what the AI produced from it."""

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

Nine columns for three stages — and that is exactly why the map exists:
without it, every stage gets its own copy of "mark running" and "mark
failed", and a copy-pasted stage left holding its neighbour's column name
**compiles, imports, and reports the neighbour's state**.

!!! warning "A stage with no `result_` column loses the result silently"
    `mark(..., result=...)` does a `setattr` on whatever name the template
    resolves to. If that column does not exist on the model, SQLAlchemy
    does not complain: the attribute stays on the instance, disappears at
    flush, and the row stores only the status. Measured —
    `doc_status_summary` was written as `'done'`, and `doc_result_summary`
    never reached the table.

    Declare `result_` for every stage that returns something, or point
    `result_template` at a column you already have.

And the engines, in a single module, because they are expensive to build
and exist once per process:

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
    """One billed AI call in this application."""

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

`batch_size` + `condition_on_previous_text=False` is the pair that belongs
together, and the [decode knobs](genai.md#transcribing-faster-on-cpu)
explain why. `base_url` is not optional: without it the SDK sends a
DeepSeek model name to `api.openai.com`.

## 3. Stage 1 — transcribe

The pattern that repeats across all three stages: **short session to mark,
no session during the work, short session to store**. A transcription
takes minutes; holding a database connection for that long is how a pool
is lost.

```python
# src/tasks/transcribe.py
from uuid import UUID

from tempest_fastapi_sdk.tasks import StageStatus

from src.core.ai import db, stt, usage
from src.core.pipeline import STAGES, DocumentModel


async def transcribe(document_id: UUID) -> None:
    """Transcribe the document's audio and store the text.

    Args:
        document_id (UUID): The document to process.
    """
    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is None:
            return
        STAGES.mark(document, "transcription", StageStatus.RUNNING)
        path: str = document.filename
        owner: UUID = document.owner_id
        await session.commit()

    result = await stt.transcribe(path)
    await usage.record_duration(
        subject_id=owner,
        seconds=result.duration,
        model="large-v3-turbo",
    )

    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is not None and STAGES.owns(
            document,
            "transcription",
            StageStatus.RUNNING,
        ):
            STAGES.mark(
                document,
                "transcription",
                StageStatus.DONE,
                result=result.text,
            )
        await session.commit()
```

Two things that look like details and are not:

- **`owns` reads from the database again.** The object you loaded before
  the work started still holds the old status and would answer `True`
  whatever happened. The re-read is what makes the check mean anything.
- **`record_duration`, not `record`.** A model running on your own
  hardware has no token bill; what it consumes is wall clock. Those rows
  carry `service=NULL` and are excluded from token sums, so they never
  become a 0% slice on every distribution chart.

## 4. Cancelling a transcription that already started

The easy path does not work here. `run_cancellable` races the coroutine
against a predicate and cancels for real — but `transcribe()` hands the
decode to `asyncio.to_thread`, and cancelling the coroutine abandons the
*wrapper* while the thread runs to completion, still burning CPU and still
competing with the next job.

What **does** work is raising from inside `on_progress`. The callback runs
on the worker thread, inside the loop that consumes the segments, and
nothing along the way swallows the exception — it travels up through the
generator, out of the `to_thread`, and into whoever was awaiting:

```python
# src/tasks/transcribe.py
import asyncio
import threading
from collections.abc import Awaitable, Callable

from tempest_fastapi_sdk.genai.audio import Transcription
from tempest_fastapi_sdk.tasks import StageInterruptedError

from src.core.ai import stt


async def transcribe_cancellable(
    path: str,
    *,
    cancelled: Callable[[], Awaitable[bool]],
) -> Transcription:
    """Transcribe, giving up midway when the user cancels.

    Args:
        path (str): The audio file.
        cancelled (Callable[[], Awaitable[bool]]): Async lookup answering
            whether cancellation was requested.

    Returns:
        Transcription: The text, when the decode reaches the end.

    Raises:
        StageInterruptedError: Cancellation arrived first.
    """
    stop = threading.Event()

    def progress(done: float, total: float) -> None:
        """Abort the decode as soon as the watcher raises the flag."""
        if stop.is_set():
            raise StageInterruptedError

    async def watch() -> None:
        """Poll cancellation on the event loop and tell the thread."""
        while not stop.is_set():
            if await cancelled():
                stop.set()
                return
            await asyncio.sleep(2.0)

    watcher = asyncio.create_task(watch())
    try:
        return await stt.transcribe(path, on_progress=progress)
    finally:
        watcher.cancel()
```

The `threading.Event` is the bridge, and it is mandatory: the callback
runs **outside** the event loop, so it cannot `await` the cancellation
lookup. One side asks the database every two seconds; the other only reads
a boolean.

!!! check "Measured, not deduced"
    Against a 600-segment decode, with cancellation arriving at 0.25 s:
    the exception propagated and **25 of the 600 segments** had been
    decoded. Without the callback, all 600 run to the end.

    The watcher's interval is your ceiling on waste and your floor on
    granularity: the first lookup happens at `t=0` and the next only after
    the interval, so work that finishes inside it never gets to see the
    cancellation — also measured, on a decode that ended in 0.06 s with the
    watcher at 2 s. Two seconds inside a job that takes minutes is noise;
    tune it if your case differs.

!!! danger "The callback must not do I/O"
    It runs on the worker thread, once per segment. No coroutines in there
    without `loop.call_soon_threadsafe`, no queries, no synchronous log to
    a remote file — the cost lands straight on the decode time.

On the requesting side, cancelling is writing the status and answering
immediately:

```python
# src/services/documents.py
from uuid import UUID

from src.core.ai import db
from src.core.pipeline import STAGES, DocumentModel


async def cancel(document_id: UUID) -> list[str]:
    """Ask whatever is still running to stop.

    Args:
        document_id (UUID): The document to cancel.

    Returns:
        list[str]: The stages actually cancelled; the ones that had already
        finished come back in the other half of the pair and are ignored on
        purpose.
    """
    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is None:
            return []
        cancelled, _ignored = STAGES.cancel(document)
        await session.commit()
        return cancelled
```

!!! info "There is no cascade, and none is needed"
    If each stage only enqueues the next one on success, cancelling the
    first makes the second never exist. Cancelling is partial on purpose:
    a polling screen will routinely ask to cancel something that finished
    a moment ago, and that is not an error.

## 5. Stage 2 — summarize, and note who paid

Now the work is a network call, the cost is per token, and the provider
reports what it spent. `generate_with_usage` returns both:

```python
# src/tasks/summarize.py
from uuid import UUID

from tempest_fastapi_sdk.tasks import StageStatus

from src.core.ai import db, llm, usage
from src.core.pipeline import STAGES, DocumentModel


async def summarize(document_id: UUID) -> None:
    """Summarize the transcript and record what the call consumed.

    Args:
        document_id (UUID): The document to summarize.
    """
    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is None or document.doc_result_transcription is None:
            return
        STAGES.mark(document, "summary", StageStatus.RUNNING)
        transcript: str = document.doc_result_transcription
        owner: UUID = document.owner_id
        await session.commit()

    summary, tokens = await llm.generate_with_usage(
        f"Summarize this meeting in five lines:\n\n{transcript}",
    )
    await usage.record(subject_id=owner, service="summary", usage=tokens)

    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is not None and STAGES.owns(
            document,
            "summary",
            StageStatus.RUNNING,
        ):
            STAGES.mark(document, "summary", StageStatus.DONE, result=summary)
        await session.commit()
```

!!! warning "`generate` returns text only — and that is deliberate"
    `generate()` satisfies `TextBackend`, the protocol the rest of the SDK
    consumes; changing its return type to a tuple would break every
    caller. Hence the pair: `generate`/`chat` for whoever wants text,
    `generate_with_usage`/`chat_with_usage` for whoever is going to bill.

!!! danger "A provider that reported no usage does not become a row"
    `record(usage=None)` writes **nothing**, and neither does
    `TokenUsage(0, 0, 0)`. "The provider did not say" is different from
    "the call was free": a zeroed row would count toward total calls and
    toward "active users" while contributing no tokens at all.

## 6. Stage 3 — suggestions as a validated list

The third stage asks the model for an **array** of objects, and it is the
stage that breaks. `generate_structured_list` packages what everyone
hand-writes every time: it finds the array even when wrapped in prose and
code fences, validates item by item, and only spends another generation
when the output has no array at all.

```python
# src/tasks/suggest.py
import json
from uuid import UUID

from pydantic import BaseModel

from tempest_fastapi_sdk.genai import generate_structured_list
from tempest_fastapi_sdk.tasks import StageStatus

from src.core.ai import db, llm
from src.core.pipeline import STAGES, DocumentModel


class Task(BaseModel):
    """A task the meeting produced."""

    title: str
    assignee: str
    due: str | None = None


async def suggest(document_id: UUID) -> None:
    """Extract tasks from the transcript and store the validated list.

    Args:
        document_id (UUID): The document to mine.
    """
    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is None or document.doc_result_transcription is None:
            return
        STAGES.mark(document, "suggestions", StageStatus.RUNNING)
        transcript: str = document.doc_result_transcription
        await session.commit()

    tasks: list[Task] = await generate_structured_list(
        llm,
        "List the agreed tasks, as a JSON array of objects with title, "
        f"assignee and due:\n\n{transcript}",
        Task,
    )

    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is not None and STAGES.owns(
            document,
            "suggestions",
            StageStatus.RUNNING,
        ):
            STAGES.mark(
                document,
                "suggestions",
                StageStatus.DONE,
                result=json.dumps([t.model_dump() for t in tasks]),
            )
        await session.commit()
```

Only a **structural** failure — no array in the output — costs an attempt,
and each attempt raises the temperature by `temperature_step`. Retrying a
greedy generation at the same temperature would reproduce the previous
output; one malformed item among ten good ones is not a formatting
failure, and is handled by `skip_invalid`.

!!! tip "An empty list is a success"
    `[]` means "the model answered, and the answer is no items" — a
    meeting with no task agreed. Do not confuse it with
    `StructuredFormatError`, which means "no attempt produced an array".

!!! warning "This stage does not return `TokenUsage`"
    `generate_structured_list` accepts anything with
    `generate(prompt) -> str`, and that protocol returns text. The call
    happens, the tokens are billed, and the return value has nothing to
    store.

    When the stage has to enter the bill, do both halves yourself — and
    accept that you lose the retry:

    ```python
    # src/tasks/suggest.py
    from uuid import UUID

    from pydantic import BaseModel

    from tempest_fastapi_sdk.genai import parse_structured_list

    from src.core.ai import llm, usage


    class Task(BaseModel):
        """A task the meeting produced."""

        title: str
        assignee: str
        due: str | None = None


    async def suggest_billed(transcript: str, owner: UUID) -> list[Task]:
        """Extract tasks while recording what the call consumed.

        Args:
            transcript (str): The source text.
            owner (UUID): Who pays for the call.

        Returns:
            list[Task]: The tasks that passed validation.
        """
        text, tokens = await llm.generate_with_usage(
            f"List the tasks as a JSON array:\n\n{transcript}",
        )
        await usage.record(subject_id=owner, service="suggestions", usage=tokens)
        return parse_structured_list(text, Task, skip_invalid=True)
    ```

    Measured: the `TokenUsage` arrives intact, and an array holding one
    invalid item between two valid ones returns the valid ones instead of
    raising.

## 7. The screen

The status endpoint needs to know no column name at all:

```python
# src/api/routers/documents.py
from uuid import UUID

from fastapi import APIRouter

from src.core.ai import db
from src.core.pipeline import STAGES, DocumentModel

router = APIRouter(prefix="/api/documents")


@router.get("/{document_id}/status")
async def read_status(document_id: UUID) -> dict[str, str | None]:
    """Return the state of each of the document's stages.

    Args:
        document_id (UUID): The document being queried.

    Returns:
        dict[str, str | None]: Stage to status; `None` for a stage that has
        not started.
    """
    async with db.get_session_context() as session:
        document = await session.get(DocumentModel, document_id)
        if document is None:
            return {}
        return {
            stage: None if state is None else state.value
            for stage, state in STAGES.snapshot(document).items()
        }
```

A real run of all three stages returns
`{"transcription": "done", "summary": "done", "suggestions": "done"}` —
and the front end draws its progress bar from that, knowing neither the
`prefix` nor the templates.

!!! info "`cancelled` is terminal, but it is not a failure"
    A screen that highlights `failed` in red should leave `cancelled`
    alone, and an alert that fires on failure should stay quiet. Nothing
    went wrong: the system did exactly what it was told.

## 8. The bill at the end of the month

Both natures of cost — tokens and wall clock — are already in the same
table, and come out separated on read:

```python
# src/services/reports.py
from datetime import timedelta

from tempest_fastapi_sdk.genai import ServiceUsage, UsageTotals

from src.core.ai import usage


async def dashboard() -> tuple[UsageTotals, list[ServiceUsage]]:
    """Read what the cost screen shows.

    Returns:
        tuple[UsageTotals, list[ServiceUsage]]: Period totals and the
        breakdown per service.
    """
    window = timedelta(days=30)
    return await usage.totals(window), await usage.by_service(window)
```

Running the whole pipeline once (a 30 s local transcription plus a summary
of 3000 input + 800 output tokens), the dashboard reads:

```text
UsageTotals(input_tokens=3000, output_tokens=800, total_tokens=3800,
            duration_seconds=30.0, calls=2, cost=0.000644,
            cache_hit_tokens=0)
[ServiceUsage(service='summary', total_tokens=3800, share=100.0)]
```

`calls=2` counts both rows — the one billed per token and the one billed
by the clock. `by_service` brings only the first: the local transcription
carries `service=NULL` and never becomes a 0% slice on the chart.

!!! warning "The cost is not rounded"
    `0.000644` is the full value. Any fixed precision is wrong at some
    scale — rounding to cents zeroes almost every single call, while a
    monthly total wants cents. Formatting belongs at the boundary, which
    knows which of the two it is showing. `cost is None` means "do not
    show a cost", never zero.

!!! info "The price is never stored"
    Cost is derived from the tokens at read time, so fixing
    `price_input_per_1k` fixes the entire history — with nothing
    reprocessed, and no rows disagreeing about what a token was worth.

## Errors

| Exception | When |
| --- | --- |
| `StageInterruptedError` | cancellation arrived midway; not a failure, the handler just returns |
| `StructuredFormatError` | no `generate_structured_list` attempt produced a decodable array; subclass of `ValueError` |
| `pydantic.ValidationError` | an item does not satisfy the schema and `skip_invalid` is off |
| `ValueError` (building the `StageMap`) | empty stage list, duplicate stage, or two stages resolving to the same column |
| `ValueError` (building the `SpeechToText`) | `batch_size` without `vad_filter=True` — it is the VAD that cuts the audio into the chunks a batch is made of |

**Recap:** `StageMap` names the state columns without declaring any, and
is the right shape when the screen already loads the record; each stage
marks `RUNNING`, releases the session, works, re-reads and only stores if
`owns` says the stage is still its own; a transcription cancels by raising
from inside `on_progress`, because `to_thread` is not cancellable and the
flag crosses over on a `threading.Event`; `generate_with_usage` is the
half that returns the `TokenUsage` that `record` stores, while
`record_duration` covers the local model that has no tokens;
`generate_structured_list` finds and validates the array, and an empty
list is an answer, not an error.
