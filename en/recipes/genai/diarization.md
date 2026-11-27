# Who said what: diarization

Use to split audio by speaker (sherpa-onnx), recognise who it is from a voice profile and serve it over HTTP or the CLI.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

Transcription answers *what was said*. Diarization answers *who said it* —
it cuts the recording into turns and groups the voices. Together they give
you the minutes of a meeting or the record of a call:

```python
# scripts/minutes.py

import asyncio

from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeakerDiarizer,
    SpeechToText,
)


async def main() -> None:
    """Transcribe a two-party call, attributing each line."""
    transcriber = ConversationTranscriber(
        stt=SpeechToText(model_size="small"),
        diarizer=SpeakerDiarizer(num_speakers=2),
    )
    conversation = await transcriber.transcribe("call.wav", language="pt")
    print(conversation.transcript())
    print(conversation.by_speaker())


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Falante 0: Bom dia, em que posso ajudar?
Falante 1: Queria saber sobre a segunda via do boleto.
```

!!! info "Required extras"
    ```bash
    uv add "tempest-fastapi-sdk[genai-audio,genai-diarization]"
    ```
    `[genai-audio]` brings Whisper (transcription), `[genai-diarization]`
    brings `sherpa-onnx` (who spoke). Neither uses PyTorch.

## Why sherpa-onnx and not pyannote

Measured, not assumed. `pyannote.audio` 4.0.7 declares **21 runtime
dependencies** — `torch>=2.8`, `lightning`, `matplotlib`, three
OpenTelemetry packages and a client for the vendor's paid API — and its
pretrained pipeline is **gated** on HuggingFace: the container build needs
a token and a manually accepted licence.

`sherpa-onnx` declares **one** dependency, runs on ONNX Runtime with no
PyTorch, and its models are openly downloadable. On a 57-second
four-speaker recording it separated all four at **RTF 0.125** on CPU —
eight times faster than real time.

## The models are not in the wheel

They are 46 MB. Fetch them once, preferably at build time:

```python
from tempest_fastapi_sdk.genai.audio import ensure_models

ensure_models()  # honors TEMPEST_VOICE_MODEL_DIR
```

Leaving it to the first request makes one user pay the download inside
their timeout.

Both models have their SHA-256 pinned in the SDK (`SEGMENTATION_MODEL.sha256`,
`EMBEDDING_MODEL.sha256`), and `ensure_models` checks the file every time it
resolves it — fresh download or cache hit —, raising `OSError` on a mismatch.
A model swapped in the upstream release, or a corrupted cache, fails loudly
instead of changing the service's behavior with nothing in the diff. The
download uses a 60 s socket timeout: a connection that goes silent that long
raises instead of hanging the first `load()` forever (a slow but moving
download still completes).

## How many speakers? It works it out

Diarization has to answer two questions and only one is easy. *Where each turn
starts and ends* comes from the segmentation model. *How many distinct voices
there are* comes from no model — it has to be inferred from how the turns
group, and it is the part that gets a transcript wrong in ways nobody notices:
eight participants in a two-party call, or four people collapsed into one.

The default is `num_speakers="auto"`. Measured on a twelve-recording benchmark
whose speaker count is correct **by construction** — turns cut from distinct
recordings, so distinct people, rather than from the diarizer's own output:

| method | exact | mean error |
| --- | --- | --- |
| threshold 0.5 | 4/10 | 1.90 |
| threshold 0.7 | 8/10 | 0.40 |
| threshold 0.9 | 8/10 | 0.20 |
| **automatic** | **12/12** | **0.00** |

The automatic mode wins for a structural reason rather than a lucky constant: a
threshold asks *how close is close enough*, an answer that moves with the
microphone, the language and the room, while the spectral method asks *where
does this affinity matrix naturally split*, which is a property of the
recording itself.

It costs a second pass — one embedding per turn plus an eigendecomposition —
negligible next to the segmentation that produced the turns.

!!! tip "Know the count? Say so."
    ```python
    from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer

    diarizer = SpeakerDiarizer(num_speakers=2)
    ```
    On a two-party call you do. It is exact and skips the second pass.
    `num_speakers=None` returns to threshold-only clustering, the weakest
    option, kept for callers who want the previous behaviour.

    When the count varies per request, pass it **per call** instead of
    setting it on the object:

    ```python
    turns = await diarizer.diarize(audio, num_speakers=2)
    ```

    A diarizer is built once and shared by every request, so writing to it
    affects the requests already in flight: two concurrent calls asking for 2
    and 5 speakers both saw whichever was written last, and the one that asked
    for 2 got 5 with no error anywhere. The per-call argument does not have
    that problem, and
    `ConversationTranscriber.transcribe(num_speakers=...)` forwards through
    it.

!!! warning "A monologue used to come back as a conversation"
    The spectral gap search **always** finds a split, including where there is
    none: a real six-turn dictation returned two speakers.

    A single voice is *uniformly* similar to itself — even its most distant
    pair of turns is close — while two voices produce pairs that are genuinely
    far apart. Measured across the twelve recordings, the 10th percentile of
    pairwise similarity was 0.490-0.667 for one speaker and -0.080-0.166 for
    more than one; the veto sits in the middle of that gap.

    That number is a property of the **bundled model's similarity scale**, not
    a universal constant: swapping the model means re-measuring it.

## How attribution works, and where it fails

The recording is transcribed **once**, not once per turn: handing Whisper
two-second clips throws away the context it uses for punctuation and
spelling, and costs one inference per turn. Each transcribed span then
goes to the turn it overlaps most in time.

The price of that choice: a Whisper span can **straddle** a speaker
change, and the whole span lands on whoever holds more of it. Speech the
diarizer dropped as too short still transcribes — that text comes back
with `speaker = -1` and is never lost. Dropping words silently is worse
than admitting the speaker is unknown.

## Recognising who it is: voice profiles

Diarization separates speaker 0 from speaker 1. Identification says speaker 0
**is Ana**, by matching the voice against an enrolled profile.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import VoiceProfileService

from src.db.models import UserModel

VoiceProfileModel = make_voice_profile_model(user_table="users")
profiles = VoiceProfileService(profile_model=VoiceProfileModel)
```

Enrolment requires consent — not optional, not configurable:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

profiles = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def enrol_voice(session: AsyncSession, user: UserModel) -> None:
    """Enrol a user's voice after they consented to it."""
    await profiles.enroll(
        session,
        user_id=user.id,
        audio="enrolment.wav",
        consent_reference="biometrics-policy-v3",
        label="onboarding",
    )
```

After that the transcript comes out named:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

profiles = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def minutes(session: AsyncSession, participants: list[UserModel]) -> str:
    """Transcribe a meeting with each line attributed to a person."""
    conversation = await transcriber.transcribe(
        "meeting.wav",
        identify_with=profiles,
        session=session,
        user_ids=[p.id for p in participants],
    )
    return conversation.transcript()
```

!!! danger "This is biometric data"
    A voiceprint identifies a person the way a fingerprint template does.
    Under Brazil's LGPD it is **sensitive personal data** (Art. 5, II), and
    processing it needs consent that is **specific and highlighted** for that
    purpose (Art. 11, I) — general terms of service do not cover it.

    So `consent_reference` is required and a blank one raises
    `ConsentRequired`. The SDK stores the vector and the consent on the same
    row, and **never writes the audio**: the vector cannot be played back,
    which makes a leak of this table cost far less than a leak of the
    recordings.

    `forget_user()` is a method rather than an example in the docs, because
    "delete my biometric data" is an unconditional right (Art. 18, VI) and must
    not depend on each project getting the `WHERE` clause right.

!!! tip "Restrict to the participants"
    `user_ids=[...]` turns "who in the whole database is this voice" into
    "which of these five people is it". Faster, and far less likely to put a
    stranger's name on a line.

Measured with real voices: enrolling from one turn and identifying a
**different** turn by the same person scored 0.687 and 0.734; an unenrolled
speaker came back `None`. The default threshold is 0.5 — raise it for anything
that grants access, where the expensive error stops being "did not recognise"
and becomes "recognised the wrong person".

Swapping the embedding model invalidates every enrolled profile: the vectors
stop being comparable and people silently stop being recognised.
`stale_profiles()` finds who needs re-enrolling.

## Serving it over HTTP and from the shell

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.genai.audio import make_voice_router

from src.api.dependencies.auth import current_user_id
from src.core.resources import db, profiles, transcriber


def create_app() -> FastAPI:
    """Mount the voice routes behind the service's own auth."""
    app = FastAPI()
    app.include_router(
        make_voice_router(
            session_factory=db.session_dependency,
            transcriber=transcriber,
            profiles=profiles,
            current_user_id=current_user_id,
            dependencies=[Depends(current_user_id)],
        ),
    )
    return app
```

Four routes: `POST /voice/transcribe`, plus `POST` / `GET` / `DELETE`
`/voice/profiles`.

!!! warning "`current_user_id` is required alongside `profiles`"
    Enrolling or erasing against a user id taken from the request **body**
    would let anyone write biometric data into somebody else's account. The
    router refuses that combination at wiring time — failing in production
    would be too late.

The listing and deletion routes are not a courtesy: they are the person's right
over their own data. And the listing **does not return the embedding** — they
need to know the profile exists, not to receive a copy of their own biometric
template over HTTP.

!!! info "The `tempest` command needs the `[cli]` extra (or `[cli-ops]` on a server)"
    Without either, `tempest` exits with code 2 and prints how to install it.
    In development: `uv add --dev "tempest-fastapi-sdk[cli]"`. Details in
    [Installation → CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/installation.md#cli).

From the shell:

```bash
tempest voice models                        # fetch the models (do this at build)
tempest voice diarize meeting.wav -n 2      # who spoke when
tempest voice transcribe meeting.wav -n 2   # who said what
```

`diarize` never loads Whisper — it is the quick way to check the speaker count
and threshold before paying for transcription.
